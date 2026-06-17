"""FastAPI app for the local TREE GUI.

Every route reuses an existing TREE function (lifecycle / progress / planner /
config); the GUI adds no pipeline logic. The server binds loopback only and is
gated by a per-launch token (cookie or ?token=) — a local web server is still a
server.
"""

from __future__ import annotations

import asyncio
import os
import secrets
from pathlib import Path
from typing import Any

import markdown as _md  # type: ignore[import-untyped]
from fastapi import FastAPI, Form, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from jinja2 import Environment, FileSystemLoader, select_autoescape

from tree.cli.commands import config_cmd
from tree.cli.commands.lifecycle import start_engine, stop_engine
from tree.cli.dashboard.model import build_watch_model
from tree.io import paths
from tree.observability.progress import STAGES
from tree.planner.pipeline import load_dag
from tree.planner.svg import write_dag_svg
from tree.rag.service import embedding_service_status, local_embed_backend_status

COOKIE_NAME = "tree_gui_token"
_GUI_DIR = Path(__file__).parent
_env = Environment(
    loader=FileSystemLoader(str(_GUI_DIR / "templates")),
    autoescape=select_autoescape(["html"]),
)

_BADGES = {
    "complete": "done",
    "completed": "done",
    "running": "running",
    "in_progress": "running",
    "active": "running",
    "failed": "failed",
    "blocked": "failed",
    "error": "failed",
    "pending": "wait",
    "idle": "wait",
}


def create_app(root: Path, *, token: str) -> FastAPI:
    root = Path(root)
    app = FastAPI(title="TREE GUI", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.root = root
    app.state.token = token
    app.mount("/static", StaticFiles(directory=str(_GUI_DIR / "static")), name="static")
    # Allow a cross-origin React/Vite dev server (and a future Tauri webview) to
    # call the API. Requests are still gated by the per-launch token, so this does
    # not loosen auth; it only lets the browser read responses from another origin.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins(),
        allow_methods=["*"],
        allow_headers=["*"],
        allow_credentials=False,
    )

    def _authed(request: Request) -> bool:
        supplied = request.cookies.get(COOKIE_NAME) or request.query_params.get("token")
        return supplied is not None and secrets.compare_digest(supplied, token)

    def _require(request: Request) -> None:
        if not _authed(request):
            raise HTTPException(status_code=403, detail="Invalid or missing token")

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request) -> HTMLResponse:
        _require(request)
        body = _env.get_template("index.html").render(
            status=_status(root), workspace=str(root)
        )
        resp = HTMLResponse(body)
        resp.set_cookie(COOKIE_NAME, token, httponly=True, samesite="strict")
        return resp

    @app.get("/partials/progress", response_class=HTMLResponse)
    def progress_partial(request: Request) -> HTMLResponse:
        _require(request)
        return HTMLResponse(_render_progress(root))

    @app.post("/api/run", response_class=HTMLResponse)
    def api_run(request: Request) -> HTMLResponse:
        _require(request)
        start_engine(root)
        return HTMLResponse(_render_progress(root))

    @app.post("/api/stop", response_class=HTMLResponse)
    def api_stop(request: Request) -> HTMLResponse:
        _require(request)
        stop_engine(root)
        return HTMLResponse(_render_progress(root))

    @app.get("/api/status")
    def api_status(request: Request) -> dict[str, Any]:
        _require(request)
        return _status(root)

    @app.websocket("/ws/progress")
    async def ws_progress(websocket: WebSocket) -> None:
        supplied = websocket.query_params.get("token")
        if supplied is None or not secrets.compare_digest(supplied, token):
            await websocket.close(code=1008)  # policy violation
            return
        await websocket.accept()
        interval = _ws_interval()
        last: dict[str, Any] | None = None
        try:
            while True:
                payload = _status(root)
                if payload != last:
                    await websocket.send_json(payload)
                    last = payload
                await asyncio.sleep(interval)
        except WebSocketDisconnect:
            return

    @app.get("/dag.svg")
    def dag_svg(request: Request) -> Response:
        _require(request)
        svg_path = paths.outputs_dag_svg_path(root)
        if not svg_path.exists() and paths.knowledge_dag_path(root).exists():
            write_dag_svg(root, load_dag(root))
        if not svg_path.exists():
            raise HTTPException(status_code=404, detail="DAG not generated yet.")
        return Response(svg_path.read_text(encoding="utf-8"), media_type="image/svg+xml")

    @app.get("/partials/outputs", response_class=HTMLResponse)
    def outputs_partial(request: Request) -> HTMLResponse:
        _require(request)
        files = _list_outputs(root)
        return HTMLResponse(_env.get_template("_outputs.html").render(files=files))

    @app.get("/api/outputs")
    def api_outputs(request: Request) -> dict[str, list[str]]:
        _require(request)
        return {"files": _list_outputs(root)}

    @app.get("/outputs/{name}", response_class=HTMLResponse)
    def output_view(request: Request, name: str) -> HTMLResponse:
        _require(request)
        target = _safe_output_path(root, name)
        html = _md.markdown(
            target.read_text(encoding="utf-8"), extensions=["fenced_code", "tables"]
        )
        return HTMLResponse(
            _env.get_template("_output.html").render(name=name, html=html)
        )

    @app.post("/api/setup", response_class=HTMLResponse)
    def api_setup(
        request: Request,
        llm_api_key: str = Form(""),
        llm_base_url: str = Form(""),
        llm_model: str = Form(""),
        paddleocr_api_token: str = Form(""),
    ) -> HTMLResponse:
        _require(request)
        config_cmd.write_quick_config(
            root,
            env_path=paths.global_config_path(),
            llm_api_key=llm_api_key,
            llm_base_url=llm_base_url,
            llm_model=llm_model,
            paddleocr_api_token=paddleocr_api_token,
        )
        return HTMLResponse('<p class="ok">Saved global configuration.</p>')

    return app


# --- config ------------------------------------------------------------------

def _cors_origins() -> list[str]:
    """Allowed cross-origin callers (the React/Vite dev server by default)."""
    override = os.environ.get("TREE_GUI_CORS_ORIGINS", "").strip()
    if override:
        return [origin.strip() for origin in override.split(",") if origin.strip()]
    return [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ]


def _ws_interval() -> float:
    try:
        return max(0.1, float(os.environ.get("TREE_GUI_WS_INTERVAL_SEC", "1.0")))
    except ValueError:
        return 1.0


# --- view models (reuse build_watch_model; no duplicated pipeline logic) -----

def _status(root: Path) -> dict[str, Any]:
    model = build_watch_model(root)
    active = set((model.get("active_node_runs") or []) + (model.get("running_node_ids") or []))
    return {
        "phase": model.get("phase", "idle"),
        "message": model.get("message", ""),
        "materials": model.get("material_count", 0),
        "nodes": model.get("node_count", 0),
        "edges": model.get("edge_count", 0),
        "active": len(active),
        "embedding_server": embedding_service_status(),
        "embedding_backend": local_embed_backend_status(),
        "errors": model.get("errors") or [],
        "rows": _stage_rows(model),
    }


def _stage_rows(model: dict[str, Any]) -> list[dict[str, Any]]:
    stages = model.get("stages") or {}
    labels = model.get("node_display_labels") or {}
    rows = []
    for key, label in STAGES:
        stage = stages.get(key) or {}
        done = int(stage.get("done") or 0)
        total = int(stage.get("total") or 0)
        status = str(stage.get("status") or "pending")
        if total:
            pct = max(0, min(100, round(done / total * 100)))
        else:
            pct = 100 if status in {"complete", "completed"} else 0
        active = [str(item) for item in (stage.get("active") or []) if str(item)]
        if key == "noderun":
            active = [labels.get(item, item) for item in active]
        rows.append(
            {
                "label": stage.get("label") or label,
                "done": done,
                "total": total,
                "pct": pct,
                "badge": _BADGES.get(status, "wait"),
                "current": str(stage.get("message") or "") or ", ".join(active),
            }
        )
    return rows


def _render_progress(root: Path) -> str:
    return _env.get_template("_progress.html").render(status=_status(root))


def _list_outputs(root: Path) -> list[str]:
    out = paths.outputs_root(root)
    if not out.exists():
        return []
    return sorted(p.name for p in out.glob("*.md"))


def _safe_output_path(root: Path, name: str) -> Path:
    """Resolve an output filename, rejecting path traversal."""
    if "/" in name or "\\" in name or not name.endswith(".md"):
        raise HTTPException(status_code=404, detail="Not found")
    out = paths.outputs_root(root).resolve()
    target = (out / name).resolve()
    if target.parent != out or not target.is_file():
        raise HTTPException(status_code=404, detail="Not found")
    return target
