"""Launch the local TREE GUI server (foreground, loopback only)."""

from __future__ import annotations

import importlib.util
import secrets
import socket
import threading
import webbrowser
from pathlib import Path

_GUI_MODULES = ("fastapi", "uvicorn", "jinja2", "markdown", "multipart")
_DEFAULT_PORT = 8799


class GuiDependencyError(RuntimeError):
    """Raised when the [gui] extra is not installed."""


def require_gui_deps() -> None:
    missing = [name for name in _GUI_MODULES if importlib.util.find_spec(name) is None]
    if missing:
        raise GuiDependencyError(
            "TREE GUI needs the [gui] extra (missing: "
            f"{', '.join(missing)}). Install it with `pip install 'tree-engine[gui]'`."
        )


def run_gui(
    root: Path,
    *,
    host: str = "127.0.0.1",
    port: int | None = None,
    open_browser: bool = True,
) -> None:
    require_gui_deps()
    import uvicorn

    from tree.gui.server import create_app

    token = secrets.token_urlsafe(16)
    chosen = _resolve_port(host, port)
    app = create_app(root, token=token)
    url = f"http://{host}:{chosen}/?token={token}"
    print(f"TREE GUI ready: {url}", flush=True)
    print("Press Ctrl+C to stop.", flush=True)
    if open_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    uvicorn.run(app, host=host, port=chosen, log_level="warning")


def _resolve_port(host: str, port: int | None) -> int:
    if port:
        return port
    if _port_free(host, _DEFAULT_PORT):
        return _DEFAULT_PORT
    return _free_port(host)


def _port_free(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, port))
            return True
        except OSError:
            return False


def _free_port(host: str) -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((host, 0))
        return int(sock.getsockname()[1])
