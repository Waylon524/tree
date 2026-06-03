"""Lifecycle management for the shared local embedding server."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from tree.io import paths
from tree.rag.model_cache import ensure_embedding_model

DEFAULT_EMBED_API_URL = "http://localhost:8788"


@dataclass(frozen=True)
class EmbeddingServiceResult:
    status: str
    message: str


def start_embedding_service(*, timeout_sec: float | None = None) -> EmbeddingServiceResult:
    if not _env_bool("EMBED_AUTO_START", True):
        return EmbeddingServiceResult("disabled", "embedding auto-start disabled")

    base_url = _embed_base_url()
    if not _is_local_embed_url(base_url):
        return EmbeddingServiceResult("external", f"using external embedding endpoint: {base_url}")

    if _embedding_health(base_url):
        return EmbeddingServiceResult("running", "embedding server running")

    root = Path.cwd()
    pid_path = paths.service_pid_path(root, "embedding")
    if pid_path.exists():
        pid = _read_pid(pid_path)
        if pid is not None and _pid_alive(pid):
            if _wait_for_health(base_url, timeout_sec=timeout_sec):
                return EmbeddingServiceResult("running", f"embedding server running (pid {pid})")
            _kill_pid(pid)
        pid_path.unlink(missing_ok=True)

    model = ensure_embedding_model()
    host, port = _host_port(base_url)
    log_path = paths.service_log_path(root, "embedding")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log = log_path.open("ab")
    proc = subprocess.Popen(
        [sys.executable, "-m", "tree.rag.server", "--host", host, "--port", str(port)],
        stdout=log,
        stderr=log,
        start_new_session=True,
    )
    pid_path.write_text(str(proc.pid), encoding="utf-8")
    if not _wait_for_health(base_url, timeout_sec=timeout_sec):
        raise RuntimeError(
            f"Embedding server did not become healthy within {_server_start_timeout(timeout_sec):.0f}s. "
            f"See {log_path}."
        )
    return EmbeddingServiceResult(
        "started", f"embedding server started (pid {proc.pid}, model {model.source})"
    )


def stop_embedding_service(*, force: bool = False) -> EmbeddingServiceResult:
    root = Path.cwd()
    pid_path = paths.service_pid_path(root, "embedding")
    if not pid_path.exists():
        return EmbeddingServiceResult("not found", "embedding server not found")
    pid = _read_pid(pid_path)
    if pid is not None:
        _kill_pid(pid, force=force)
    pid_path.unlink(missing_ok=True)
    return EmbeddingServiceResult("stopped", f"embedding server stopped (pid {pid})")


def embedding_service_status() -> str:
    base_url = _embed_base_url()
    if not _is_local_embed_url(base_url):
        return "external"
    if _embedding_health(base_url):
        return "running"
    pid = _read_pid(paths.service_pid_path(Path.cwd(), "embedding"))
    if pid is not None and _pid_alive(pid):
        return "starting"
    return "not found"


def _embed_base_url() -> str:
    return os.environ.get("EMBED_API_URL", DEFAULT_EMBED_API_URL).rstrip("/")


def _is_local_embed_url(base_url: str) -> bool:
    parsed = urlparse(base_url)
    host = (parsed.hostname or "").lower()
    return parsed.scheme in {"http", ""} and host in {"localhost", "127.0.0.1", "::1"}


def _host_port(base_url: str) -> tuple[str, int]:
    parsed = urlparse(base_url)
    host = parsed.hostname or "127.0.0.1"
    if host == "localhost":
        host = "127.0.0.1"
    return host, parsed.port or 8788


def _embedding_health(base_url: str) -> bool:
    try:
        with urllib.request.urlopen(f"{base_url}/health", timeout=1) as resp:
            data = json.loads(resp.read())
    except Exception:
        return False
    return bool(data.get("loaded"))


def _wait_for_health(base_url: str, *, timeout_sec: float | None = None) -> bool:
    deadline = time.monotonic() + _server_start_timeout(timeout_sec)
    while time.monotonic() <= deadline:
        if _embedding_health(base_url):
            return True
        time.sleep(0.25)
    return False


def _server_start_timeout(timeout_sec: float | None) -> float:
    if timeout_sec is not None:
        return timeout_sec
    raw = os.environ.get("EMBED_SERVER_START_TIMEOUT_SEC", "300")
    try:
        return float(raw)
    except ValueError:
        return 300.0


def _read_pid(path: Path) -> int | None:
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except Exception:
        return None


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False


def _kill_pid(pid: int, *, force: bool = False) -> None:
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    if force:
        time.sleep(0.1)
        if _pid_alive(pid):
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                return


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off"}
