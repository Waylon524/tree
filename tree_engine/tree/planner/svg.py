"""Deterministic SVG rendering for planner knowledge DAG artifacts."""

from __future__ import annotations

from html import escape
from pathlib import Path
from typing import Any

from tree.io import paths

_NODE_WIDTH = 280
_NODE_HEIGHT = 66
_COL_GAP = 110
_ROW_GAP = 30
_MARGIN_X = 80
_MARGIN_TOP = 130
_MARGIN_BOTTOM = 70
_TITLE_Y = 42
_SUBTITLE_Y = 72
_PALETTE = [
    ("#dbeafe", "#2563eb"),
    ("#dcfce7", "#16a34a"),
    ("#fef3c7", "#d97706"),
    ("#ffe4e6", "#e11d48"),
    ("#ede9fe", "#7c3aed"),
    ("#e0f2fe", "#0284c7"),
    ("#f1f5f9", "#64748b"),
]


def render_dag_svg(dag: dict[str, Any], *, title: str = "Knowledge DAG") -> str:
    """Render a static SVG knowledge graph from a ``knowledge-dag`` payload."""
    nodes = list(dag.get("nodes") or [])
    edges = [edge for edge in dag.get("edges") or [] if edge.get("relation") == "prerequisite"]
    roots = list(dag.get("roots") or [])
    if not nodes:
        return _empty_svg(title)

    node_ids = {str(node.get("node_id")) for node in nodes if node.get("node_id")}
    edges = [
        edge
        for edge in edges
        if str(edge.get("from_node_id")) in node_ids and str(edge.get("to_node_id")) in node_ids
    ]
    seq_by_id = _sequence_by_id(nodes)
    depth_by_id = _depth_by_id(nodes, edges)
    grouped = _group_nodes(nodes, depth_by_id)
    positions = _positions(grouped)
    collections = _collection_styles(nodes)

    width = max(_MARGIN_X * 2 + 360, _MARGIN_X * 2 + (max(grouped) + 1) * _NODE_WIDTH + max(grouped) * _COL_GAP)
    max_rows = max(len(group) for group in grouped.values())
    height = _MARGIN_TOP + max_rows * _NODE_HEIGHT + max(0, max_rows - 1) * _ROW_GAP + _MARGIN_BOTTOM

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}">',
        "<defs>",
        '<marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" '
        'markerWidth="7" markerHeight="7" orient="auto-start-reverse">'
        '<path d="M 0 0 L 10 5 L 0 10 z" fill="#64748b"/></marker>',
        '<filter id="shadow" x="-10%" y="-20%" width="130%" height="150%">'
        '<feDropShadow dx="0" dy="2" stdDeviation="2" flood-color="#000" flood-opacity="0.14"/>'
        "</filter>",
        "</defs>",
        '<rect width="100%" height="100%" fill="#f8fafc"/>',
        _text(_MARGIN_X, _TITLE_Y, title, size=28, weight=700, fill="#0f172a"),
        _text(
            _MARGIN_X,
            _SUBTITLE_Y,
            f"{len(nodes)} nodes · {len(edges)} prerequisite edges · {len(roots)} roots · left-to-right prerequisite depth",
            size=15,
            fill="#475569",
        ),
    ]

    nodes_by_id = {str(node.get("node_id")): node for node in nodes}
    for edge in edges:
        rendered = _render_edge(edge, nodes_by_id, positions)
        if rendered:
            parts.append(rendered)
    for node in sorted(nodes, key=lambda n: (depth_by_id.get(str(n.get("node_id")), 0), _node_sort_key(n))):
        parts.append(_render_node(node, seq_by_id, positions, collections))

    parts.append("</svg>")
    return "\n".join(parts)


def write_dag_svg(root: Path, dag: dict[str, Any], *, title: str = "Knowledge DAG") -> Path:
    """Write the planner DAG SVG artifact and a user-facing outputs copy."""
    svg = render_dag_svg(dag, title=title)
    path = paths.knowledge_dag_svg_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(svg, encoding="utf-8")
    output_path = paths.outputs_dag_svg_path(root)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(svg, encoding="utf-8")
    return path


def _empty_svg(title: str) -> str:
    width = 760
    height = 240
    return "\n".join(
        [
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
            f'viewBox="0 0 {width} {height}">',
            '<rect width="100%" height="100%" fill="#f8fafc"/>',
            _text(80, 48, title, size=28, weight=700, fill="#0f172a"),
            _text(80, 96, "No knowledge nodes", size=18, fill="#475569"),
            "</svg>",
        ]
    )


def _sequence_by_id(nodes: list[dict[str, Any]]) -> dict[str, str]:
    ordered = sorted(nodes, key=_node_sort_key)
    return {str(node.get("node_id")): f"{index:03d}" for index, node in enumerate(ordered, start=1)}


def _depth_by_id(nodes: list[dict[str, Any]], edges: list[dict[str, Any]]) -> dict[str, int]:
    ordered_ids = [str(node.get("node_id")) for node in sorted(nodes, key=_node_sort_key)]
    children = {node_id: [] for node_id in ordered_ids}
    indegree = {node_id: 0 for node_id in ordered_ids}
    for edge in edges:
        from_id = str(edge.get("from_node_id"))
        to_id = str(edge.get("to_node_id"))
        if from_id not in children or to_id not in indegree:
            continue
        children[from_id].append(to_id)
        indegree[to_id] += 1

    depth = {node_id: 0 for node_id in ordered_ids}
    queue = [node_id for node_id in ordered_ids if indegree[node_id] == 0]
    visited: set[str] = set()
    while queue:
        node_id = queue.pop(0)
        visited.add(node_id)
        for child_id in sorted(children[node_id], key=lambda child: ordered_ids.index(child)):
            depth[child_id] = max(depth[child_id], depth[node_id] + 1)
            indegree[child_id] -= 1
            if indegree[child_id] == 0:
                queue.append(child_id)

    # DAG construction should prevent cycles. If a malformed artifact slips in,
    # still render deterministically instead of failing the inspection command.
    for node_id in ordered_ids:
        if node_id not in visited:
            depth[node_id] = max(depth.values(), default=0) + 1
    return depth


def _group_nodes(nodes: list[dict[str, Any]], depth_by_id: dict[str, int]) -> dict[int, list[dict[str, Any]]]:
    grouped: dict[int, list[dict[str, Any]]] = {}
    for node in nodes:
        grouped.setdefault(depth_by_id.get(str(node.get("node_id")), 0), []).append(node)
    for group in grouped.values():
        group.sort(key=_node_sort_key)
    return grouped


def _positions(grouped: dict[int, list[dict[str, Any]]]) -> dict[str, tuple[float, float]]:
    positions: dict[str, tuple[float, float]] = {}
    for depth, group in grouped.items():
        x = _MARGIN_X + depth * (_NODE_WIDTH + _COL_GAP)
        for row, node in enumerate(group):
            y = _MARGIN_TOP + row * (_NODE_HEIGHT + _ROW_GAP)
            positions[str(node.get("node_id"))] = (x, y)
    return positions


def _collection_styles(nodes: list[dict[str, Any]]) -> dict[str, tuple[str, str]]:
    names: list[str] = []
    for node in nodes:
        collection = _node_collection(node)
        if collection not in names:
            names.append(collection)
    return {name: _PALETTE[index % len(_PALETTE)] for index, name in enumerate(sorted(names))}


def _render_edge(
    edge: dict[str, Any],
    nodes_by_id: dict[str, dict[str, Any]],
    positions: dict[str, tuple[float, float]],
) -> str:
    from_id = str(edge.get("from_node_id"))
    to_id = str(edge.get("to_node_id"))
    if from_id not in positions or to_id not in positions:
        return ""
    x1, y1 = positions[from_id]
    x2, y2 = positions[to_id]
    start_x = x1 + _NODE_WIDTH
    start_y = y1 + _NODE_HEIGHT / 2
    end_x = x2
    end_y = y2 + _NODE_HEIGHT / 2
    curve = max(50.0, (end_x - start_x) / 2)
    tooltip = _edge_tooltip(edge, nodes_by_id)
    return (
        f'<path d="M {start_x:.1f} {start_y:.1f} C {start_x + curve:.1f} {start_y:.1f}, '
        f'{end_x - curve:.1f} {end_y:.1f}, {end_x:.1f} {end_y:.1f}" '
        'fill="none" stroke="#64748b" stroke-width="1.25" stroke-opacity="0.48" '
        'marker-end="url(#arrow)">'
        f"<title>{escape(tooltip)}</title></path>"
    )


def _render_node(
    node: dict[str, Any],
    seq_by_id: dict[str, str],
    positions: dict[str, tuple[float, float]],
    collections: dict[str, tuple[str, str]],
) -> str:
    node_id = str(node.get("node_id"))
    x, y = positions[node_id]
    fill, stroke = collections.get(_node_collection(node), _PALETTE[-1])
    label = f"{seq_by_id.get(node_id, '000')}. {node.get('title') or node_id}"
    lines = _wrap_label(label)
    tooltip = _node_tooltip(node, seq_by_id.get(node_id, "000"))
    parts = [
        f'<g filter="url(#shadow)"><title>{escape(tooltip)}</title>',
        f'<rect x="{x:.1f}" y="{y:.1f}" width="{_NODE_WIDTH}" height="{_NODE_HEIGHT}" '
        f'rx="8" fill="{fill}" stroke="{stroke}" stroke-width="1.4"/>',
    ]
    text_y = y + 27 if len(lines) == 1 else y + 22
    for index, line in enumerate(lines[:2]):
        parts.append(_text(x + 14, text_y + index * 19, line, size=14, weight=700, fill="#0f172a"))
    parts.append("</g>")
    return "".join(parts)


def _wrap_label(label: str, *, limit: int = 18) -> list[str]:
    if len(label) <= limit:
        return [label]
    first = label[:limit]
    rest = label[limit:]
    if len(rest) > limit:
        rest = rest[: limit - 1] + "…"
    return [first, rest]


def _node_tooltip(node: dict[str, Any], seq: str) -> str:
    defines = node.get("defines") or node.get("keywords") or []
    return "\n".join(
        [
            f"{seq}. {node.get('title') or node.get('node_id')}",
            f"node_id: {node.get('node_id')}",
            "defines: " + (", ".join(str(item) for item in defines) or "-"),
        ]
    )


def _edge_tooltip(edge: dict[str, Any], nodes_by_id: dict[str, dict[str, Any]]) -> str:
    from_node = nodes_by_id.get(str(edge.get("from_node_id")), {})
    to_node = nodes_by_id.get(str(edge.get("to_node_id")), {})
    required = edge.get("required_defines") or []
    return "\n".join(
        [
            f"{from_node.get('title') or edge.get('from_node_id')} -> {to_node.get('title') or edge.get('to_node_id')}",
            "required_defines: " + (", ".join(str(item) for item in required) or "-"),
        ]
    )


def _node_collection(node: dict[str, Any]) -> str:
    collections = node.get("collections") or []
    return str(collections[0]) if collections else "default"


def _node_sort_key(node: dict[str, Any]) -> tuple[Any, ...]:
    return (
        node.get("source_order_index", 0),
        str(node.get("title") or ""),
        str(node.get("node_id") or ""),
    )


def _text(
    x: float,
    y: float,
    content: str,
    *,
    size: int,
    fill: str,
    weight: int | None = None,
) -> str:
    weight_attr = f' font-weight="{weight}"' if weight else ""
    return (
        f'<text x="{x:.1f}" y="{y:.1f}" font-family="Arial, PingFang SC, '
        f'Microsoft YaHei, sans-serif" font-size="{size}"{weight_attr} fill="{fill}">'
        f"{escape(content)}</text>"
    )
