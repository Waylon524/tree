"""Text dashboard rendering used by ``tre watch``."""

from __future__ import annotations

from pathlib import Path

from tree.cli.dashboard.dag_view import render_dag
from tree.cli.dashboard.model import build_watch_model


def render_watch(root: Path) -> str:
    model = build_watch_model(root)
    lines = [
        "TREE Watch",
        f"phase: {model['phase']}",
        f"message: {model['message']}",
        f"materials: {model['material_count']}",
        f"nodes: {model['node_count']}",
        f"edges: {model['edge_count']}",
        f"branches: {model['branch_count']}",
        "active: " + (", ".join(model["active_branch_runs"]) or "-"),
        "",
        render_dag(model),
    ]
    return "\n".join(lines)
