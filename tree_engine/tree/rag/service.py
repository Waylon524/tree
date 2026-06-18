"""Lifecycle management for the shared local embedding server."""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from tree.io import paths, process
from tree.rag import llama_server
from tree.rag.model_cache import ensure_embedding_model

DEFAULT_EMBED_API_URL = "http://localhost:8788"

# Python deps for the in-process llama-cpp-python server (the [local-embed] extra).
# The prebuilt llama-server binary backend needs none of these; the embedding
# client and external endpoints need none either.
_LOCAL_SERVER_MODULES = ("llama_cpp", "fastapi", "uvicorn")


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
        _set_bringup("running", "Embedding server running")
        return EmbeddingServiceResult("running", "embedding server running")

    _set_bringup("preparing", "Preparing embedding server")
    root = Path.cwd()
    pid_path = paths.service_pid_path(root, "embedding")
    if pid_path.exists():
        pid = _read_pid(pid_path)
        if pid is not None and process.pid_alive(pid):
            if _wait_for_health(base_url, timeout_sec=timeout_sec):
                _set_bringup("running", "Embedding server running")
                return EmbeddingServiceResult("running", f"embedding server running (pid {pid})")
            process.terminate_pid(pid)
        pid_path.unlink(missing_ok=True)

    host, port = _host_port(base_url)
    argv, backend, model_source = _resolve_server_launch(host, port)
    log_path = paths.service_log_path(root, "embedding")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log = log_path.open("ab")
    proc = process.spawn_detached(argv, stdout=log, stderr=log)
    pid_path.write_text(str(proc.pid), encoding="utf-8")
    _set_bringup("starting", "Starting embedding server")
    if not _wait_for_health(base_url, timeout_sec=timeout_sec):
        _set_bringup("failed", "Embedding server did not become healthy in time")
        raise RuntimeError(
            f"Embedding server did not become healthy within {_server_start_timeout(timeout_sec):.0f}s. "
            f"See {log_path}."
        )
    _set_bringup("running", f"Embedding server running (backend {backend})")
    return EmbeddingServiceResult(
        "started",
        f"embedding server started (pid {proc.pid}, backend {backend}, model {model_source})",
    )


def stop_embedding_service(*, force: bool = False) -> EmbeddingServiceResult:
    root = Path.cwd()
    pid_path = paths.service_pid_path(root, "embedding")
    if not pid_path.exists():
        return EmbeddingServiceResult("not found", "embedding server not found")
    pid = _read_pid(pid_path)
    if pid is not None:
        process.terminate_pid(pid, force=force)
    pid_path.unlink(missing_ok=True)
    _set_bringup("stopped", "Embedding server stopped")
    return EmbeddingServiceResult("stopped", f"embedding server stopped (pid {pid})")


def embedding_service_status() -> str:
    base_url = _embed_base_url()
    if not _env_bool("EMBED_AUTO_START", True):
        # User brings their own endpoint (e.g. Ollama on loopback); not TREE-managed.
        return "external"
    if not _is_local_embed_url(base_url):
        return "external"
    if _embedding_health(base_url):
        return "running"
    pid = _read_pid(paths.service_pid_path(Path.cwd(), "embedding"))
    if pid is not None and process.pid_alive(pid):
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
            if resp.status != 200:
                return False
            data = json.loads(resp.read())
    except Exception:
        # llama-server returns HTTP 503 while still loading the model -> not ready.
        return False
    if not isinstance(data, dict):
        return False
    # legacy FastAPI server reports {"loaded": true}; llama-server reports {"status": "ok"}.
    return data.get("loaded") is True or data.get("status") == "ok"


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


def _resolve_server_launch(host: str, port: int) -> tuple[list[str], str, str]:
    """Pick a local embedding backend and return (argv, backend_label, model_source).

    Fails fast with actionable guidance instead of timing out on a dead server.
    """
    backend = os.environ.get("EMBED_SERVER_BACKEND", "auto").strip().lower()
    if backend not in {"auto", "llama-server", "python"}:
        backend = "auto"

    if backend == "python":
        _require_python_server_deps()
        return _python_launch(host, port)
    if backend == "llama-server":
        return _llama_server_launch(host, port)

    # auto: keep existing [local-embed] installs on the in-process server (no
    # behaviour change); otherwise fall back to the prebuilt llama-server binary.
    if _python_server_deps_available():
        return _python_launch(host, port)
    try:
        return _llama_server_launch(host, port)
    except llama_server.LlamaServerError as exc:
        raise RuntimeError(
            "Cannot host a local embedding server: the [local-embed] python deps are "
            f"not installed and no llama-server binary is available ({exc}). Either "
            "install 'tree-engine[local-embed]', allow TREE to download llama-server "
            "(default), or set EMBED_API_URL to an external OpenAI-compatible endpoint "
            "(e.g. Ollama) together with EMBED_AUTO_START=false."
        ) from exc


def _python_launch(host: str, port: int) -> tuple[list[str], str, str]:
    _set_bringup("downloading", "Downloading embedding model (first run only)…")
    model = ensure_embedding_model()
    argv = [sys.executable, "-m", "tree.rag.server", "--host", host, "--port", str(port)]
    return argv, "python (llama-cpp-python)", model.source


def _llama_server_launch(host: str, port: int) -> tuple[list[str], str, str]:
    _set_bringup("downloading", "Downloading llama-server (first run only)…")
    binary = llama_server.ensure_llama_server()
    _set_bringup("downloading", "Downloading embedding model (first run only)…")
    model = ensure_embedding_model()
    argv = llama_server.build_argv(binary, model.path, host=host, port=port)
    return argv, f"llama-server ({binary.name})", model.source


def _python_server_deps_available() -> bool:
    return all(importlib.util.find_spec(name) is not None for name in _LOCAL_SERVER_MODULES)


def local_embed_backend_status() -> str:
    """Read-only summary of how a local embedding server would be hosted (no download)."""
    if _python_server_deps_available():
        return "python (llama-cpp-python)"
    found = llama_server.resolve_llama_server()
    if found is not None:
        return f"llama-server ({found.name})"
    if not _env_bool("LLAMA_SERVER_AUTO_DOWNLOAD", True):
        return "unavailable (set EMBED_API_URL or LLAMA_SERVER_BIN)"
    return "llama-server (auto-download on first run)"


def _require_python_server_deps() -> None:
    missing = [name for name in _LOCAL_SERVER_MODULES if importlib.util.find_spec(name) is None]
    if missing:
        raise RuntimeError(
            "EMBED_SERVER_BACKEND=python needs the [local-embed] extra "
            f"(missing: {', '.join(missing)}). Install 'tree-engine[local-embed]', use "
            "EMBED_SERVER_BACKEND=llama-server, or point EMBED_API_URL at an external endpoint."
        )


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off"}


# --- bringup status (first-run download / startup feedback for the GUI) -------

def _bringup_path() -> Path:
    return paths.global_services_root() / "embedding-bringup.json"


def _set_bringup(phase: str, message: str = "") -> None:
    """Record coarse embedding bringup phase for the GUI (best-effort, never raises)."""
    try:
        path = _bringup_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"phase": phase, "message": message}), encoding="utf-8")
    except Exception:
        return


def embedding_bringup() -> dict[str, str]:
    """Read the last bringup phase/message: idle|preparing|downloading|starting|running|failed|stopped."""
    path = _bringup_path()
    if not path.exists():
        return {"phase": "idle", "message": ""}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return {"phase": str(data.get("phase", "idle")), "message": str(data.get("message", ""))}
    except Exception:
        return {"phase": "idle", "message": ""}
