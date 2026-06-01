"""Deterministic DAG -> linear KnowledgeBranch cutting (stage ④).

No LLM. A branch starts at a root (in-degree 0), a merge point (in-degree > 1),
or a child of a branch point (out-degree > 1), then walks the unique chain while
out-degree == 1 and the next node's in-degree == 1.

Only HARD ("prerequisite") edges define the branch topology; soft "order" edges
are ignored here. See docs/REBUILD-DESIGN.md §4 ④.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from tree.planner.ids import prefixed_id


def build_branches(dag: dict[str, Any]) -> dict[str, Any]:
    nodes = sorted(
        dag.get("nodes") or [],
        key=lambda n: (n.get("source_order_index", 0), n.get("node_id", "")),
    )
    node_ids = [n["node_id"] for n in nodes]
    order = {node_id: i for i, node_id in enumerate(node_ids)}

    outgoing: dict[str, list[str]] = defaultdict(list)
    incoming: dict[str, list[str]] = defaultdict(list)
    for edge in dag.get("edges") or []:
        if edge.get("relation") != "prerequisite":
            continue
        src, dst = edge["from_node_id"], edge["to_node_id"]
        outgoing[src].append(dst)
        incoming[dst].append(src)
    for values in outgoing.values():
        values.sort(key=lambda x: order.get(x, 0))

    starts = {nid for nid in node_ids if len(incoming[nid]) != 1}
    for nid in node_ids:
        if len(outgoing[nid]) > 1:
            starts.update(outgoing[nid])
    ordered_starts = sorted(starts, key=lambda x: order.get(x, 0))

    branches: list[dict[str, Any]] = []
    for start in ordered_starts:
        path = _walk(start, outgoing, incoming, order)
        branches.append(
            {
                "branch_id": prefixed_id("kb", path),
                "node_ids": path,
                "coverage_node_ids": _coverage(path, incoming),
                "start_node_id": path[0],
                "end_node_id": path[-1],
                "upstream_branch_ids": [],
                "downstream_branch_ids": [],
                "display_order": len(branches),
            }
        )

    _link(branches, incoming)
    return {"version": 1, "branches": branches, "diagnostics": []}


def _walk(
    start: str,
    outgoing: dict[str, list[str]],
    incoming: dict[str, list[str]],
    order: dict[str, int],
) -> list[str]:
    path = [start]
    current = start
    while len(outgoing[current]) == 1:
        nxt = outgoing[current][0]
        if len(incoming[nxt]) > 1 or nxt in path:
            break
        path.append(nxt)
        current = nxt
    return sorted(path, key=lambda x: order.get(x, 0))


def _coverage(path: list[str], incoming: dict[str, list[str]]) -> list[str]:
    if len(path) == 1 and incoming[path[0]]:
        return [path[0]]
    if len(path) > 1 and len(incoming[path[0]]) > 1:
        return path[1:] or [path[0]]
    return path


def _link(branches: list[dict[str, Any]], incoming: dict[str, list[str]]) -> None:
    by_covered = {
        node_id: branch
        for branch in branches
        for node_id in branch["coverage_node_ids"]
    }
    for branch in branches:
        upstream: set[str] = set()
        for parent in incoming.get(branch["start_node_id"], []):
            parent_branch = by_covered.get(parent)
            if parent_branch and parent_branch["branch_id"] != branch["branch_id"]:
                upstream.add(parent_branch["branch_id"])
        branch["upstream_branch_ids"] = sorted(upstream)
        for up in upstream:
            up_branch = next(b for b in branches if b["branch_id"] == up)
            up_branch["downstream_branch_ids"].append(branch["branch_id"])
    for branch in branches:
        branch["downstream_branch_ids"] = sorted(set(branch["downstream_branch_ids"]))
