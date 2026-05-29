"""Background service helpers for TREE and the embedding server."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from tree.io import paths


@dataclass(frozen=True)
class ServiceStatus:
    name: str
    running: bool
    pid: int | None
    log_path: Path
    detail: str = ""


@dataclass(frozen=True)
class ServiceStart:
    started: bool
    pid: int | None
    log_path: Path
    message: str


def service_status(root: Path, name: str) -> ServiceStatus:
    log_path = paths.service_log_path(root, name)
    pid = _read_pid(paths.service_pid_path(root, name))
    if pid is None:
        return ServiceStatus(name=name, running=False, pid=None, log_path=log_path)
    if _pid_running(pid):
        return ServiceStatus(name=name, running=True, pid=pid, log_path=log_path)
    _clear_pid(root, name)
    return ServiceStatus(name=name, running=False, pid=None, log_path=log_path, detail="stale pid removed")


def start_embedding(root: Path) -> ServiceStart:
    status = service_status(root, "embedding")
    if status.running:
        return ServiceStart(False, status.pid, status.log_path, "embedding server already running")

    _clear_stop(root, "embedding")
    env = _service_env(root)
    port = env.get("EMBED_PORT", "8788")
    n_gpu_layers = env.get("EMBED_N_GPU_LAYERS", "-1")
    n_ctx = env.get("EMBED_N_CTX", "32768")
    n_seq_max = env.get("EMBED_N_SEQ_MAX", "1")
    cmd = [
        sys.executable,
        "-m",
        "rag.server",
        "--port",
        port,
        "--n-gpu-layers",
        n_gpu_layers,
        "--n-ctx",
        n_ctx,
        "--n-seq-max",
        n_seq_max,
    ]
    pid = _spawn(root, "embedding", cmd, env)
    return ServiceStart(True, pid, paths.service_log_path(root, "embedding"), "embedding server started")


def start_tree(root: Path) -> ServiceStart:
    status = service_status(root, "tree")
    if status.running:
        return ServiceStart(False, status.pid, status.log_path, "TREE already running")

    _clear_stop(root, "tree")
    env = _service_env(root)
    cmd = [sys.executable, "-m", "tree.cli", "run"]
    pid = _spawn(root, "tree", cmd, env)
    return ServiceStart(True, pid, paths.service_log_path(root, "tree"), "TREE started")


def request_tree_stop(root: Path) -> None:
    paths.services_root(root).mkdir(parents=True, exist_ok=True)
    paths.service_stop_path(root, "tree").write_text(str(time.time()), encoding="utf-8")


def stop_service(root: Path, name: str, force: bool = False) -> ServiceStatus:
    if name == "tree":
        request_tree_stop(root)
    status = service_status(root, name)
    if not status.running or status.pid is None:
        if name == "tree":
            _clear_stop(root, "tree")
        return status
    if force:
        _terminate_pid(status.pid)
        deadline = time.time() + 5
        while time.time() < deadline:
            if not _pid_running(status.pid):
                _clear_pid(root, name)
                if name == "tree":
                    _clear_stop(root, "tree")
                return ServiceStatus(name=name, running=False, pid=None, log_path=status.log_path)
            time.sleep(0.2)
    return service_status(root, name)


def stop_requested(root: Path, name: str = "tree") -> bool:
    return paths.service_stop_path(root, name).exists()


def clear_stop(root: Path, name: str = "tree") -> None:
    _clear_stop(root, name)


def wait_for_embedding(root: Path, timeout_sec: int | None = None) -> bool:
    env = _service_env(root)
    base_url = env.get("EMBED_API_URL", "http://localhost:8788").rstrip("/")
    deadline = None if timeout_sec is None else time.time() + timeout_sec
    while deadline is None or time.time() < deadline:
        try:
            with urllib.request.urlopen(f"{base_url}/health", timeout=3) as resp:
                if resp.status == 200:
                    return True
        except (OSError, urllib.error.URLError):
            pass
        status = service_status(root, "embedding")
        if status.pid is not None and not status.running:
            return False
        time.sleep(2)
    return False


def embedding_health(root: Path) -> tuple[bool, str]:
    env = _service_env(root)
    base_url = env.get("EMBED_API_URL", "http://localhost:8788").rstrip("/")
    try:
        with urllib.request.urlopen(f"{base_url}/health", timeout=3) as resp:
            body = resp.read().decode("utf-8", errors="replace")
        return True, body
    except (OSError, urllib.error.URLError) as exc:
        return False, f"{base_url}/health unavailable: {exc}"


def _spawn(root: Path, name: str, cmd: list[str], env: dict[str, str]) -> int:
    paths.services_root(root).mkdir(parents=True, exist_ok=True)
    log_path = paths.service_log_path(root, name)
    log = log_path.open("a", encoding="utf-8")
    log.write(f"\n--- starting {name}: {' '.join(cmd)} ---\n")
    log.flush()
    kwargs: dict = {
        "cwd": str(root),
        "env": env,
        "stdin": subprocess.DEVNULL,
        "stdout": log,
        "stderr": subprocess.STDOUT,
        "close_fds": os.name != "nt",
    }
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    proc = subprocess.Popen(cmd, **kwargs)
    paths.service_pid_path(root, name).write_text(str(proc.pid), encoding="utf-8")
    return proc.pid


def _service_env(root: Path) -> dict[str, str]:
    env = os.environ.copy()
    env.update(_read_env(root / ".env"))
    env["PYTHONPATH"] = f"{root / 'tree_engine'}{os.pathsep}{env.get('PYTHONPATH', '')}"
    env["PYTHONUNBUFFERED"] = "1"
    return env


def _read_env(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    values = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        values[key.strip()] = _unquote(value.strip())
    return values


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def _read_pid(path: Path) -> int | None:
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


def _clear_pid(root: Path, name: str) -> None:
    paths.service_pid_path(root, name).unlink(missing_ok=True)


def _clear_stop(root: Path, name: str) -> None:
    paths.service_stop_path(root, name).unlink(missing_ok=True)


def _pid_running(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        result = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
            capture_output=True,
            text=True,
            check=False,
        )
        return str(pid) in result.stdout
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _terminate_pid(pid: int) -> None:
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], check=False)
        return
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
