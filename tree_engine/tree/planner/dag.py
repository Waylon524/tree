"""Dagger orchestration + deterministic DAG validation (stage ③ program side).

The LLM (DaggerAgent) merges MTUs into canonical nodes and proposes edges
referenced by node title. This module:
  - decides one-shot vs per-collection batched calls (token bound)
  - maps titles -> node_id, ensures every MTU is covered exactly once
  - resolves/dedupes edges, breaks cycles deterministically
  - falls back to a source-order chain if the LLM output is unusable

See docs/REBUILD-DESIGN.md §4 ③.
"""

from __future__ import annotations

import logging
from typing import Any

from tree.planner.ids import normalize_concepts, normalize_text_key, prefixed_id

logger = logging.getLogger(__name__)

_VALID_RELATIONS = {"prerequisite", "order"}


async def build_dag(agent: Any, mtus: list[Any], *, settings: Any) -> dict[str, Any]:
    """Build canonical nodes + edges from MTUs. Returns envelope `data` dict."""
    if not mtus:
        return {"nodes": [], "edges": [], "roots": [], "diagnostics": []}

    timeout = getattr(settings, "dagger_build_timeout_sec", 300.0)
    repair = getattr(settings, "dagger_repair_attempts", 1)
    max_nodes = getattr(settings, "dagger_max_nodes_per_call", 400)

    if len(mtus) <= max_nodes:
        raw = await _build_with_repair(agent, [_meta(m) for m in mtus], timeout=timeout, repair=repair)
        nodes_raw = raw.get("nodes") or []
        edges_raw = raw.get("edges") or []
    else:
        logger.info("Dagger batched build: %d MTUs > %d per call", len(mtus), max_nodes)
        nodes_raw, edges_raw = await _build_batched(agent, mtus, timeout=timeout, repair=repair)

    return _canonicalize(mtus, nodes_raw, edges_raw)


async def _build_with_repair(agent: Any, payload: list[dict], *, timeout: float, repair: int) -> dict:
    feedback = ""
    for attempt in range(repair + 1):
        try:
            extra = [{"_note": feedback}] if feedback else []
            return await agent.build(payload + extra, timeout_sec=timeout)
        except (ValueError, KeyError) as exc:
            logger.warning("Dagger build invalid (attempt %d): %s", attempt + 1, exc)
            feedback = f"Previous output was invalid JSON ({exc}); re-emit the strict schema."
    # Deterministic fallback: no merge, source-order soft chain.
    return {"nodes": [], "edges": []}


async def _build_batched(agent: Any, mtus: list[Any], *, timeout: float, repair: int) -> tuple[list, list]:
    """Per-collection DAGs, merged across collections by exact title match."""
    by_collection: dict[str, list[Any]] = {}
    for mtu in mtus:
        by_collection.setdefault(mtu.collection, []).append(mtu)

    nodes_raw: list[dict] = []
    edges_raw: list[dict] = []
    for collection, group in by_collection.items():
        raw = await _build_with_repair(agent, [_meta(m) for m in group], timeout=timeout, repair=repair)
        nodes_raw.extend(raw.get("nodes") or [])
        edges_raw.extend(raw.get("edges") or [])
    return _merge_raw_nodes_by_title(nodes_raw), edges_raw


def _merge_raw_nodes_by_title(nodes_raw: list[dict]) -> list[dict]:
    merged: dict[str, dict] = {}
    for node in nodes_raw:
        key = normalize_text_key(str(node.get("title") or ""))
        if not key:
            continue
        if key in merged:
            existing = merged[key]
            existing["member_mtu_ids"] = list(existing.get("member_mtu_ids", [])) + list(
                node.get("member_mtu_ids", [])
            )
            existing["keywords"] = list(existing.get("keywords", [])) + list(node.get("keywords", []))
        else:
            merged[key] = dict(node)
    return list(merged.values())


def _canonicalize(mtus: list[Any], nodes_raw: list[dict], edges_raw: list[dict]) -> dict[str, Any]:
    by_id = {m.mtu_id: m for m in mtus}
    assigned: set[str] = set()
    nodes: list[dict] = []
    title_to_node: dict[str, str] = {}
    diagnostics: list[dict] = []

    for raw in nodes_raw:
        member_ids = [
            mid for mid in (raw.get("member_mtu_ids") or [])
            if mid in by_id and mid not in assigned
        ]
        if not member_ids:
            continue
        assigned.update(member_ids)
        node = _node_from_members(
            member_ids, by_id,
            title=raw.get("title"), summary=raw.get("summary"), keywords=raw.get("keywords"),
        )
        nodes.append(node)
        title_to_node.setdefault(normalize_text_key(node["title"]), node["node_id"])
        if raw.get("title"):
            title_to_node.setdefault(normalize_text_key(str(raw["title"])), node["node_id"])

    # Any MTU the LLM dropped becomes a conservative singleton node.
    for mtu in mtus:
        if mtu.mtu_id in assigned:
            continue
        node = _node_from_members([mtu.mtu_id], by_id)
        nodes.append(node)
        title_to_node.setdefault(normalize_text_key(node["title"]), node["node_id"])
        diagnostics.append({
            "severity": "warning",
            "stage": "dagger",
            "reason_code": "mtu_unassigned",
            "mtu_id": mtu.mtu_id,
            "message": "MTU not placed by Dagger; kept as a singleton node.",
        })

    nodes.sort(key=lambda n: (n["source_order_index"], n["node_id"]))
    node_ids = {n["node_id"] for n in nodes}
    edges = _resolve_edges(edges_raw, title_to_node)
    edges = break_cycles(node_ids, edges)

    prereq_targets = {e["to_node_id"] for e in edges if e["relation"] == "prerequisite"}
    roots = sorted(node_ids - prereq_targets)
    return {"nodes": nodes, "edges": edges, "roots": roots, "diagnostics": diagnostics}


def _resolve_edges(edges_raw: list[dict], title_to_node: dict[str, str]) -> list[dict]:
    by_key: dict[tuple[str, str], dict] = {}
    for raw in edges_raw:
        src = title_to_node.get(normalize_text_key(str(raw.get("from_title") or raw.get("from") or "")))
        dst = title_to_node.get(normalize_text_key(str(raw.get("to_title") or raw.get("to") or "")))
        if not src or not dst or src == dst:
            continue
        relation = str(raw.get("relation") or "prerequisite").strip().lower()
        if relation not in _VALID_RELATIONS:
            relation = "order"
        try:
            confidence = float(raw.get("confidence", 1.0))
        except (TypeError, ValueError):
            confidence = 1.0
        key = (src, dst)
        if key in by_key:
            if confidence > by_key[key]["confidence"]:
                by_key[key]["confidence"] = confidence
                by_key[key]["relation"] = relation
            continue
        by_key[key] = {
            "from_node_id": src,
            "to_node_id": dst,
            "relation": relation,
            "confidence": confidence,
        }
    return list(by_key.values())


def break_cycles(node_ids: set[str], edges: list[dict]) -> list[dict]:
    """Drop the lowest-confidence prerequisite edge on each cycle until acyclic.

    Only prerequisite edges define scheduling order, so only they are cycle-broken.
    """
    prereq = [e for e in edges if e["relation"] == "prerequisite"]
    other = [e for e in edges if e["relation"] != "prerequisite"]

    while True:
        cycle = _find_cycle(node_ids, prereq)
        if not cycle:
            break
        on_cycle = [e for e in prereq if (e["from_node_id"], e["to_node_id"]) in cycle]
        weakest = min(on_cycle, key=lambda e: e["confidence"])
        prereq.remove(weakest)
        logger.info("break_cycles: dropped %s->%s", weakest["from_node_id"], weakest["to_node_id"])

    return prereq + other


def _find_cycle(node_ids: set[str], edges: list[dict]) -> set[tuple[str, str]]:
    """Return the directed-edge set of one cycle, or empty if the graph is acyclic."""
    adj: dict[str, list[str]] = {nid: [] for nid in node_ids}
    for edge in edges:
        adj.setdefault(edge["from_node_id"], []).append(edge["to_node_id"])

    WHITE, GRAY, BLACK = 0, 1, 2
    color = {nid: WHITE for nid in adj}
    parent: dict[str, str] = {}

    def visit(start: str) -> set[tuple[str, str]] | None:
        stack = [(start, iter(adj[start]))]
        color[start] = GRAY
        while stack:
            node, it = stack[-1]
            advanced = False
            for nxt in it:
                if color.get(nxt, WHITE) == GRAY:  # back edge -> cycle
                    cycle = {(node, nxt)}
                    cur = node
                    while cur != nxt and cur in parent:
                        cycle.add((parent[cur], cur))
                        cur = parent[cur]
                    return cycle
                if color.get(nxt, WHITE) == WHITE:
                    parent[nxt] = node
                    color[nxt] = GRAY
                    stack.append((nxt, iter(adj[nxt])))
                    advanced = True
                    break
            if not advanced:
                color[node] = BLACK
                stack.pop()
        return None

    for nid in adj:
        if color[nid] == WHITE:
            found = visit(nid)
            if found:
                return found
    return set()


def _node_from_members(
    member_ids: list[str],
    by_id: dict[str, Any],
    *,
    title: str | None = None,
    summary: str | None = None,
    keywords: Any = None,
) -> dict[str, Any]:
    members = [by_id[mid] for mid in member_ids]
    ordered_ids = sorted(member_ids)
    merged_keywords = normalize_concepts(
        list(keywords or []) + [kw for m in members for kw in m.keywords]
    )
    return {
        "node_id": prefixed_id("kn", ordered_ids),
        "title": (title or members[0].title).strip(),
        "member_mtu_ids": ordered_ids,
        "keywords": merged_keywords,
        "summary": (summary or members[0].summary or "").strip(),
        "collections": sorted({m.collection for m in members if m.collection}),
        "source_order_index": min(m.source_order_index for m in members),
    }


def _meta(mtu: Any) -> dict[str, Any]:
    return {
        "mtu_id": mtu.mtu_id,
        "title": mtu.title,
        "keywords": list(mtu.keywords),
        "summary": mtu.summary,
        "collection": mtu.collection,
        "source_order_index": mtu.source_order_index,
    }
