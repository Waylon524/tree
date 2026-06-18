"""FastAPI app for the local TREE GUI.

Every route reuses an existing TREE function (lifecycle / progress / planner /
config); the GUI adds no pipeline logic. The server binds loopback only and is
gated by a per-launch token (cookie or ?token=) — a local web server is still a
server.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import secrets
import shutil
import subprocess
import sys
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import markdown as _md  # type: ignore[import-untyped]
from fastapi import (
    Body,
    FastAPI,
    File,
    Form,
    HTTPException,
    Request,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from jinja2 import Environment, FileSystemLoader, select_autoescape

from tree.cli.commands import config_cmd, inspect as inspect_cmd
from tree.cli.commands.lifecycle import engine_status, quit_tree, start_engine, stop_engine
from tree.cli.dashboard.model import build_watch_model
from tree.ingest.pipeline import MATERIAL_EXTENSIONS
from tree.io import paths
from tree.observability.progress import STAGES
from tree.planner.pipeline import load_dag
from tree.planner.store import read_json, write_json_atomic
from tree.planner.svg import write_dag_svg
from tree.rag.service import (
    embedding_bringup,
    embedding_extension_status,
    embedding_service_status,
    local_embed_backend_status,
    start_embedding_service,
    start_embedding_extension_install,
    stop_embedding_service,
)

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
    paths.ensure_workspace_dirs(root)  # opening a folder initializes it (== /init)
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

    @app.post("/api/quit")
    def api_quit(request: Request) -> dict[str, str]:
        _require(request)
        return {"message": quit_tree(root).message}

    @app.get("/api/status")
    def api_status(request: Request) -> dict[str, Any]:
        _require(request)
        return _status(root)

    @app.get("/api/dag")
    def api_dag(request: Request) -> dict[str, Any]:
        _require(request)
        return _dag_model(root)

    @app.get("/api/extension")
    def api_extension(request: Request) -> dict[str, object]:
        _require(request)
        return embedding_extension_status()

    @app.post("/api/extension/install")
    def api_install_extension(request: Request) -> dict[str, object]:
        _require(request)
        start_embedding_extension_install()
        return embedding_extension_status()

    @app.get("/api/settings")
    def api_settings(request: Request) -> dict[str, Any]:
        _require(request)
        return config_cmd.read_settings_config(root, env_path=paths.global_config_path())

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
        except (WebSocketDisconnect, asyncio.CancelledError):
            return

    @app.get("/dag.svg")
    def dag_svg(request: Request) -> Response:
        _require(request)
        svg_path = _ensure_dag_svg(root)
        if svg_path is None:
            raise HTTPException(status_code=404, detail="DAG not generated yet.")
        return Response(svg_path.read_text(encoding="utf-8"), media_type="image/svg+xml")

    @app.post("/api/open-dag", response_class=HTMLResponse)
    def api_open_dag(request: Request) -> HTMLResponse:
        _require(request)
        svg_path = _ensure_dag_svg(root)
        if svg_path is None:
            return HTMLResponse(
                '<span class="muted">DAG not generated yet — run the pipeline first.</span>'
            )
        try:
            _open_in_default_app(svg_path)
        except Exception as exc:  # noqa: BLE001
            return HTMLResponse(f'<span class="muted">Could not open: {exc}</span>')
        return HTMLResponse(f'<span class="ok">Opened {svg_path.name} in your default viewer.</span>')

    @app.get("/partials/outputs", response_class=HTMLResponse)
    def outputs_partial(request: Request) -> HTMLResponse:
        _require(request)
        files = _list_outputs(root)
        return HTMLResponse(_env.get_template("_outputs.html").render(files=files))

    @app.get("/api/outputs")
    def api_outputs(request: Request) -> dict[str, list[str]]:
        _require(request)
        return {"files": _list_outputs(root)}

    @app.get("/api/outputs/{name}/raw")
    def api_output_raw(request: Request, name: str) -> dict[str, Any]:
        _require(request)
        target = _safe_output_path(root, name)
        stat = target.stat()
        return {
            "name": name,
            "markdown": target.read_text(encoding="utf-8"),
            "size_bytes": stat.st_size,
            "updated_at": datetime.fromtimestamp(stat.st_mtime, timezone.utc)
            .isoformat()
            .replace("+00:00", "Z"),
        }

    @app.post("/api/exports")
    def api_exports(request: Request, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
        _require(request)
        destination_raw = str(payload.get("destination") or "").strip()
        if not destination_raw:
            raise HTTPException(status_code=400, detail="Export destination is required")
        mode = str(payload.get("mode") or "copy")
        if mode != "copy":
            raise HTTPException(status_code=400, detail="Only copy export mode is supported")
        files = payload.get("files")
        if not isinstance(files, list) or not files:
            raise HTTPException(status_code=400, detail="At least one output file is required")

        selected: list[tuple[str, Path]] = []
        for item in files:
            name = str(item or "")
            try:
                selected.append((name, _safe_output_path(root, name)))
            except HTTPException as exc:
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid output file: {name}",
                ) from exc

        destination = Path(destination_raw).expanduser()
        if destination.exists() and not destination.is_dir():
            raise HTTPException(status_code=400, detail="Export destination must be a folder")
        destination.mkdir(parents=True, exist_ok=True)

        exported: list[dict[str, str]] = []
        skipped: list[dict[str, str]] = []
        failed: list[dict[str, str]] = []
        for name, source in selected:
            target = destination / name
            try:
                if source.resolve() == target.resolve():
                    skipped.append({"name": name, "reason": "source and destination are the same"})
                    continue
                shutil.copy2(source, target)
                exported.append({"name": name, "destination": str(target)})
            except Exception as exc:  # pragma: no cover - OS copy failures vary
                failed.append({"name": name, "error": str(exc)})
        return {"exported": exported, "skipped": skipped, "failed": failed}

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

    @app.post("/api/settings")
    def api_save_settings(
        request: Request,
        payload: dict[str, Any] = Body(...),
    ) -> dict[str, Any]:
        _require(request)
        config_cmd.write_settings_config(
            root,
            env_path=paths.global_config_path(),
            settings=payload,
        )
        return config_cmd.read_settings_config(root, env_path=paths.global_config_path())

    @app.get("/api/materials")
    def api_materials(request: Request) -> dict[str, list[str]]:
        _require(request)
        return {"materials": _list_materials(root)}

    @app.get("/api/imported-files")
    def api_imported_files(request: Request) -> dict[str, list[dict[str, Any]]]:
        _require(request)
        return {"files": _imported_files(root)}

    @app.post("/api/materials")
    async def api_add_materials(
        request: Request,
        collection: str = Form("default"),
        files: list[UploadFile] = File(...),
    ) -> dict[str, Any]:
        _require(request)
        coll = Path(collection).name or "default"  # no path traversal via collection
        dest_dir = paths.materials_root(root) / coll
        saved: list[str] = []
        skipped: list[str] = []
        manifest = _load_import_manifest(root)
        for upload in files:
            name = Path(upload.filename or "").name
            if not name:
                continue
            if Path(name).suffix.lower() not in MATERIAL_EXTENSIONS:
                skipped.append(name)
                continue
            content = await upload.read()
            dest_dir.mkdir(parents=True, exist_ok=True)
            target = _unique_material_path(dest_dir, name)
            target.write_bytes(content)
            relative_path = target.relative_to(paths.materials_root(root)).as_posix()
            manifest["files"].append(
                {
                    "id": f"src_{uuid.uuid4().hex}",
                    "original_name": name,
                    "stored_name": target.name,
                    "relative_path": relative_path,
                    "collection": coll,
                    "size_bytes": len(content),
                    "sha256": hashlib.sha256(content).hexdigest(),
                    "imported_at": _utc_now(),
                    "status": "active",
                }
            )
            saved.append(relative_path)
        if saved:
            _save_import_manifest(root, manifest)
        return {"saved": saved, "skipped": skipped}

    @app.get("/api/embedding")
    def api_embedding(request: Request) -> dict[str, str]:
        _require(request)
        bringup = embedding_bringup()
        return {
            "status": embedding_service_status(),
            "backend": local_embed_backend_status(),
            "phase": bringup["phase"],
            "detail": bringup["message"],
        }

    @app.post("/api/embedding/start")
    def api_embedding_start(request: Request) -> dict[str, str]:
        _require(request)
        # First run downloads the model/binary, which can take minutes; run it off
        # the request thread and let the UI poll /api/embedding for status.
        threading.Thread(target=_safe_start_embedding, name="tree-embedding-start",
                         daemon=True).start()
        return {"status": "starting"}

    @app.post("/api/embedding/stop")
    def api_embedding_stop(request: Request) -> dict[str, str]:
        _require(request)
        stop_embedding_service(force=True)
        return {"status": embedding_service_status()}

    @app.post("/api/clean")
    def api_clean(request: Request) -> dict[str, str]:
        _require(request)
        return {"message": inspect_cmd.clean_runtime(root)}

    return app


def _safe_start_embedding() -> None:
    try:
        start_embedding_service()
    except Exception:  # noqa: BLE001 - surfaced via /api/embedding status polling
        return


# --- config ------------------------------------------------------------------

def _cors_origins() -> list[str]:
    """Allowed cross-origin callers for the React SPA and Tauri WebView."""
    override = os.environ.get("TREE_GUI_CORS_ORIGINS", "").strip()
    if override:
        return [origin.strip() for origin in override.split(",") if origin.strip()]
    return [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://tauri.localhost",
        "https://tauri.localhost",
        "tauri://localhost",
    ]


def _ws_interval() -> float:
    try:
        return max(0.1, float(os.environ.get("TREE_GUI_WS_INTERVAL_SEC", "1.0")))
    except ValueError:
        return 1.0


# --- DAG ----------------------------------------------------------------------

def _ensure_dag_svg(root: Path) -> Path | None:
    """Return the DAG SVG path, generating it from the planner DAG if needed."""
    svg_path = paths.outputs_dag_svg_path(root)
    if not svg_path.exists() and paths.knowledge_dag_path(root).exists():
        write_dag_svg(root, load_dag(root))
    return svg_path if svg_path.exists() else None


def _open_in_default_app(path: Path) -> None:
    """Open a local file with the OS default application.

    The GUI server runs on the user's own machine (loopback only), so this opens
    the file in their desktop session — e.g. the DAG SVG in their default viewer.
    """
    if sys.platform == "win32":
        os.startfile(str(path))  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])


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
        "engine": engine_status(root),
        "embedding_server": embedding_service_status(),
        "embedding_backend": local_embed_backend_status(),
        "errors": model.get("errors") or [],
        "rows": _stage_rows(model),
    }


def _dag_model(root: Path) -> dict[str, Any]:
    model = build_watch_model(root)
    dag = model.get("dag") or {}
    nodes = list(dag.get("nodes") or model.get("nodes") or [])
    edges = [edge for edge in (dag.get("edges") or []) if edge.get("relation") == "prerequisite"]
    labels = model.get("node_display_labels") or {}
    covered = set(model.get("covered_node_ids") or [])
    active = set((model.get("active_node_runs") or []) + (model.get("running_node_ids") or []))
    failed = _failed_node_ids(model)
    parents, children = _dag_adjacency(edges)
    outputs = _dag_output_paths(root)

    payload_nodes = []
    for node in sorted(nodes, key=lambda n: (n.get("source_order_index", 0), n.get("node_id", ""))):
        node_id = str(node.get("node_id") or "")
        if not node_id:
            continue
        status = _dag_node_status(node_id, parents, covered, active, failed)
        payload_nodes.append(
            {
                "id": node_id,
                "title": str(node.get("title") or node_id),
                "label": labels.get(node_id, node_id),
                "status": status,
                "defines": [str(item) for item in (node.get("defines") or [])],
                "collections": [str(item) for item in (node.get("collections") or [])],
                "summary": str(node.get("summary") or ""),
                "prerequisites": sorted(parents.get(node_id, set())),
                "dependents": sorted(children.get(node_id, set())),
                "source_order_index": int(node.get("source_order_index") or 0),
                "output_paths": outputs.get(node_id, []),
            }
        )

    payload_edges = [
        {
            "from": str(edge.get("from_node_id") or ""),
            "to": str(edge.get("to_node_id") or ""),
            "relation": str(edge.get("relation") or "prerequisite"),
            "confidence": float(edge.get("confidence") or 1.0),
            "required_defines": [str(item) for item in (edge.get("required_defines") or [])],
        }
        for edge in edges
        if edge.get("from_node_id") and edge.get("to_node_id")
    ]
    status_counts = {status: 0 for status in ("locked", "ready", "running", "complete", "failed")}
    for node in payload_nodes:
        status_counts[str(node["status"])] += 1
    roots = dag.get("roots") or sorted(
        node["id"] for node in payload_nodes if not parents.get(str(node["id"]))
    )
    return {
        "nodes": payload_nodes,
        "edges": payload_edges,
        "roots": [str(item) for item in roots],
        "stats": {
            "nodes": len(payload_nodes),
            "edges": len(payload_edges),
            "statuses": status_counts,
        },
        "updated_at": str(model.get("updated_at") or ""),
    }


def _dag_adjacency(
    edges: list[dict[str, Any]]
) -> tuple[dict[str, set[str]], dict[str, set[str]]]:
    parents: dict[str, set[str]] = {}
    children: dict[str, set[str]] = {}
    for edge in edges:
        parent = str(edge.get("from_node_id") or "")
        child = str(edge.get("to_node_id") or "")
        if not parent or not child:
            continue
        parents.setdefault(child, set()).add(parent)
        children.setdefault(parent, set()).add(child)
    return parents, children


def _dag_node_status(
    node_id: str,
    parents: dict[str, set[str]],
    covered: set[str],
    active: set[str],
    failed: set[str],
) -> str:
    if node_id in failed:
        return "failed"
    if node_id in active:
        return "running"
    if node_id in covered:
        return "complete"
    if parents.get(node_id, set()) <= covered:
        return "ready"
    return "locked"


def _failed_node_ids(model: dict[str, Any]) -> set[str]:
    failed: set[str] = set()
    state = (model.get("progress") or {}).get("stages", {}).get("noderun") or {}
    if str(state.get("status") or "").lower() in {"failed", "blocked", "error"}:
        failed.update(str(item) for item in (state.get("active") or []) if str(item))
    for key in (
        "failed_node_ids",
        "failed_running_node_ids",
        "blocked_node_ids",
        "error_node_ids",
    ):
        failed.update(str(item) for item in (model.get(key) or []) if str(item))
    return failed


def _dag_output_paths(root: Path) -> dict[str, list[str]]:
    path = paths.knowledge_ledger_path(root)
    if not path.exists():
        return {}
    try:
        ledger = read_json(path)
    except Exception:
        return {}
    outputs: dict[str, list[str]] = {}
    records = ledger.get("records") if isinstance(ledger, dict) else []
    if not isinstance(records, list):
        return {}
    for record in records:
        if not isinstance(record, dict):
            continue
        output_path = str(record.get("output_path") or "")
        node_ids = record.get("node_ids") or ([record.get("node_id")] if record.get("node_id") else [])
        if not output_path or not isinstance(node_ids, list):
            continue
        for node_id in node_ids:
            key = str(node_id or "")
            if key:
                outputs.setdefault(key, []).append(output_path)
    return outputs


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


def _list_materials(root: Path) -> list[str]:
    materials_root = paths.materials_root(root)
    if not materials_root.exists():
        return []
    return sorted(
        str(p.relative_to(materials_root))
        for p in materials_root.rglob("*")
        if p.is_file() and not p.name.startswith(".") and p.suffix.lower() in MATERIAL_EXTENSIONS
    )


def _empty_import_manifest() -> dict[str, Any]:
    return {"schema": "tree.import-manifest.ui", "version": 1, "files": []}


def _load_import_manifest(root: Path) -> dict[str, Any]:
    path = paths.import_manifest_path(root)
    if not path.exists():
        return _empty_import_manifest()
    loaded = read_json(path)
    files = loaded.get("files") if isinstance(loaded, dict) else []
    return {
        "schema": "tree.import-manifest.ui",
        "version": 1,
        "files": files if isinstance(files, list) else [],
    }


def _save_import_manifest(root: Path, manifest: dict[str, Any]) -> None:
    manifest["schema"] = "tree.import-manifest.ui"
    manifest["version"] = 1
    if not isinstance(manifest.get("files"), list):
        manifest["files"] = []
    write_json_atomic(paths.import_manifest_path(root), manifest)


def _imported_files(root: Path) -> list[dict[str, Any]]:
    manifest = _load_import_manifest(root)
    materials_root = paths.materials_root(root)
    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in manifest["files"]:
        if not isinstance(raw, dict):
            continue
        record = _normalize_import_record(raw)
        rel = record["relative_path"]
        target = _material_target(root, rel)
        record["status"] = "active" if target and target.is_file() else "missing"
        records.append(record)
        seen.add(rel)

    for rel in _list_materials(root):
        if rel in seen:
            continue
        target = materials_root / rel
        records.append(_legacy_import_record(root, rel, target))
    return records


def _normalize_import_record(raw: dict[str, Any]) -> dict[str, Any]:
    rel = str(raw.get("relative_path") or "")
    stored_name = str(raw.get("stored_name") or Path(rel).name)
    collection = str(raw.get("collection") or _collection_for_material(rel))
    return {
        "id": str(raw.get("id") or f"legacy:{rel}"),
        "original_name": str(raw.get("original_name") or stored_name),
        "stored_name": stored_name,
        "relative_path": rel,
        "collection": collection,
        "size_bytes": int(raw.get("size_bytes") or 0),
        "sha256": str(raw.get("sha256") or ""),
        "imported_at": str(raw.get("imported_at") or ""),
        "status": str(raw.get("status") or "active"),
    }


def _legacy_import_record(root: Path, rel: str, target: Path) -> dict[str, Any]:
    return {
        "id": f"legacy:{rel}",
        "original_name": target.name,
        "stored_name": target.name,
        "relative_path": rel,
        "collection": _collection_for_material(rel),
        "size_bytes": target.stat().st_size if target.exists() else 0,
        "sha256": _sha256_file(target) if target.exists() else "",
        "imported_at": "",
        "status": "active" if _material_target(root, rel) else "missing",
    }


def _collection_for_material(rel: str) -> str:
    parts = Path(rel).parts
    return parts[0] if len(parts) > 1 else "default"


def _material_target(root: Path, rel: str) -> Path | None:
    if not rel or "\\" in rel:
        return None
    materials_root = paths.materials_root(root).resolve()
    target = (materials_root / rel).resolve()
    try:
        target.relative_to(materials_root)
    except ValueError:
        return None
    return target


def _unique_material_path(dest_dir: Path, name: str) -> Path:
    candidate = dest_dir / name
    if not candidate.exists():
        return candidate
    path = Path(name)
    stem = path.stem or path.name
    suffix = path.suffix
    index = 2
    while True:
        candidate = dest_dir / f"{stem} {index}{suffix}"
        if not candidate.exists():
            return candidate
        index += 1


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _safe_output_path(root: Path, name: str) -> Path:
    """Resolve an output filename, rejecting path traversal."""
    if "/" in name or "\\" in name or not name.endswith(".md"):
        raise HTTPException(status_code=404, detail="Not found")
    out = paths.outputs_root(root).resolve()
    target = (out / name).resolve()
    if target.parent != out or not target.is_file():
        raise HTTPException(status_code=404, detail="Not found")
    return target
