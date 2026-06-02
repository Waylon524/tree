"""Small text renderer for the current knowledge DAG."""

from __future__ import annotations

from typing import Any


def render_dag(model: dict[str, Any]) -> str:
    """Render a compact, terminal-safe DAG view."""
    nodes = model.get("nodes") or model.get("dag", {}).get("nodes", [])
    edges = model.get("dag", {}).get("edges", [])
    covered = set(model.get("covered_node_ids", []))
    active_nodes = set(model.get("active_node_runs", [])) | set(model.get("running_node_ids", []))

    lines = ["Knowledge DAG"]
    for index, node in enumerate(nodes, start=1):
        node_id = node.get("node_id", "")
        marker = "✓" if node_id in covered else "▶" if node_id in active_nodes else " "
        title = node.get("title") or node_id
        lines.append(f"{index:02d}. {marker} {node_id} | {title}")
    if edges:
        lines.append("Edges")
        for edge in edges:
            if edge.get("relation") != "prerequisite":
                continue
            lines.append(f"  {edge.get('from_node_id')} -> {edge.get('to_node_id')}")
    return "\n".join(lines)
