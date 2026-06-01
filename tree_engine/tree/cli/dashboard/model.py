"""Build the watch view-model from runtime artifacts."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from tree.engine.branch_run import ledger_covered_node_ids
from tree.ingest.pipeline import MATERIAL_EXTENSIONS
from tree.io import paths
from tree.observability.progress import load_progress
from tree.planner.pipeline import load_branches, load_dag, load_nodes
from tree.state.manager import StateManager


def build_watch_model(root: Path) -> dict[str, Any]:
    """Return a compact dashboard model for status/watch rendering."""
    root = Path(root)
    progress = load_progress(root)
    state = StateManager(paths.pipeline_state_path(root)).load()
    dag = load_dag(root)
    nodes = load_nodes(root)
    branches = load_branches(root)
    covered = ledger_covered_node_ids(root)
    active = [be.execution_path for be in state.branch_executions if be.status == "in_progress"]
    completed = [be.execution_path for be in state.branch_executions if be.status == "completed"]

    return {
        "phase": progress.get("phase", "idle"),
        "message": progress.get("message", ""),
        "updated_at": progress.get("updated_at", ""),
        "progress": progress,
        "material_count": len(_material_paths(root)),
        "node_count": len(nodes),
        "edge_count": len(dag.get("edges", [])),
        "branch_count": len(branches),
        "dag": dag,
        "nodes": nodes,
        "branches": branches,
        "covered_node_ids": sorted(covered),
        "active_branch_runs": active,
        "completed_branch_runs": completed,
        "running_branch_ids": sorted({run.branch_id for run in state.branch_runs if run.status == "running"}),
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
