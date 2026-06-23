"""Build the watch view-model from runtime artifacts."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from tree.engine.branch_run import ledger_covered_node_ids
from tree.ingest.pipeline import MATERIAL_EXTENSIONS
from tree.io import paths
from tree.observability.progress import load_progress
from tree.planner.pipeline import load_dag, load_nodes
from tree.state.manager import StateManager


def build_watch_model(root: Path) -> dict[str, Any]:
    """Return a compact dashboard model for status/watch rendering."""
    root = Path(root)
    progress = load_progress(root)
    state = StateManager(paths.pipeline_state_path(root)).load()
    dag = load_dag(root)
    nodes = load_nodes(root)
    covered = ledger_covered_node_ids(root)
    active = [be.node_id for be in state.node_executions if be.status == "in_progress"]
    completed = [be.node_id for be in state.node_executions if be.status == "completed"]
    failed = [
        be.node_id
        for be in state.node_executions
        if str(be.status).lower() in {"failed", "blocked", "error"}
    ]
    labels = _node_display_labels(nodes)

    node_count = _watch_node_count(progress, nodes)
    return {
        "phase": progress.get("phase", "idle"),
        "message": progress.get("message", ""),
        "updated_at": progress.get("updated_at", ""),
        "progress": progress,
        "stages": progress.get("stages", {}),
        "material_count": len(_material_paths(root)),
        "node_count": node_count,
        "edge_count": len(dag.get("edges", [])),
        "dag": dag,
        "nodes": nodes,
        "covered_node_ids": sorted(covered),
        "active_node_runs": active,
        "completed_node_runs": completed,
        "failed_node_ids": sorted(failed),
        "running_node_ids": sorted({run.node_id for run in state.node_runs if run.status == "running"}),
        "failed_running_node_ids": sorted(
            {
                run.node_id
                for run in state.node_runs
                if str(run.status).lower() in {"failed", "blocked", "error"}
            }
        ),
        "node_display_labels": labels,
        "errors": _collect_errors(root, progress),
    }


def _material_paths(root: Path) -> list[Path]:
    material_root = paths.materials_root(root)
    if not material_root.exists():
        return []
    return [
        path
        for path in sorted(material_root.rglob("*"))
        if path.is_file()
        and not path.name.startswith(".")
        and path.suffix.lower() in MATERIAL_EXTENSIONS
    ]


def _node_display_labels(nodes: list[dict[str, Any]]) -> dict[str, str]:
    ordered = sorted(nodes, key=lambda n: (n.get("source_order_index", 0), n.get("node_id", "")))
    labels: dict[str, str] = {}
    for index, node in enumerate(ordered, start=1):
        node_id = str(node.get("node_id") or "")
        if not node_id:
            continue
        title = str(node.get("title") or node_id)
        labels[node_id] = f"{index:03d}. {title}"
    return labels


def _watch_node_count(progress: dict[str, Any], nodes: list[dict[str, Any]]) -> int:
    planner = progress.get("planner") or {}
    if isinstance(planner, dict):
        try:
            return max(0, int(planner["node_count"]))
        except (KeyError, TypeError, ValueError):
            pass
    return len(nodes)


def _collect_errors(root: Path, progress: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    phase = str(progress.get("phase") or "").strip().lower()
    message = str(progress.get("message") or "").strip()
    if phase in {"blocked", "failed", "error"} and message:
        errors.append(message)

    raw_errors = progress.get("errors") or progress.get("error") or []
    if isinstance(raw_errors, str):
        raw_errors = [raw_errors]
    if isinstance(raw_errors, list):
        for item in raw_errors:
            if isinstance(item, dict):
                detail = item.get("message") or item.get("error") or item.get("detail")
                if detail:
                    errors.append(str(detail))
            elif item:
                errors.append(str(item))

    for stage in (progress.get("stages") or {}).values():
        if not isinstance(stage, dict):
            continue
        status = str(stage.get("status") or "").strip().lower()
        if status not in {"blocked", "failed", "error"}:
            continue
        label = str(stage.get("label") or "stage")
        detail = str(stage.get("message") or "").strip()
        errors.append(f"{label}: {status}" + (f" - {detail}" if detail else ""))

    errors.extend(_recent_log_errors(root))
    return _dedupe(errors)[:8]


def _recent_log_errors(root: Path) -> list[str]:
    candidates = [paths.service_log_path(root, "engine")]
    temp_root = paths.pipeline_temp_root(root)
    if temp_root.exists():
        candidates.extend(sorted(temp_root.glob("*.log")))

    lines: list[str] = []
    for path in candidates:
        if not path.exists() or not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines()[-80:]:
            stripped = line.strip()
            if stripped and _looks_like_error(stripped) and not _is_shutdown_noise(stripped):
                lines.append(stripped)
    return lines[-5:]


def _looks_like_error(line: str) -> bool:
    lowered = line.lower()
    return any(
        needle in lowered
        for needle in (
            "traceback",
            "runtimeerror",
            "valueerror",
            "exception",
            "failed",
            " error",
            "error:",
            "blocked",
        )
    )


def _is_shutdown_noise(line: str) -> bool:
    lowered = line.lower()
    return (
        "qdrantclient.__del__" in lowered
        or ("qdrantclient" in lowered and "sys.meta_path is none" in lowered)
        or "python is likely shutting down" in lowered
    )


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for item in items:
        clean = item.strip()
        if not clean or clean in seen:
            continue
        seen.add(clean)
        unique.append(clean)
    return unique
