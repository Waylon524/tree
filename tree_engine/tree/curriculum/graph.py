"""Knowledge graph derived from source candidates and finished outputs."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from tree.io import paths

_TERM_RE = re.compile(r"[\u4e00-\u9fffA-Za-z][\u4e00-\u9fffA-Za-z0-9_`()+\-]*")
_SPLIT_RE = re.compile(r"[，,、/；;：:（）()\[\]【】\s的与和及或]+")
_MIN_DUPLICATE_CONCEPT = 0.58
_MIN_DUPLICATE_CHUNK = 0.45
_MIN_MERGE_CHUNK = 0.62
_MIN_PREREQUISITE = 0.25
_MIN_ADJACENT = 0.12
_MIN_BACKBONE_AFFINITY = 0.05
_NEW_ROOT_PARENT_SCORE_THRESHOLD = 0.18
_NEW_ROOT_PREREQUISITE_THRESHOLD = 0.25
_MULTI_PARENT_SCORE_THRESHOLD = 0.30
_MULTI_PARENT_PREREQ_THRESHOLD = 0.25
_MAX_SUPPORTING_PARENTS = 4
_STRONG_PARENT_SOURCE = 0.50
_STRONG_PARENT_CHUNK = 0.45
_FINISHED_TRUNK_SOLVABILITY = 0.82


def load_knowledge_graph(root: Path) -> dict[str, Any]:
    path = paths.knowledge_graph_path(root)
    if not path.exists():
        return _empty_graph()
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return _empty_graph()
    if not isinstance(loaded, dict):
        return _empty_graph()
    loaded.setdefault("version", 1)
    loaded.setdefault("nodes", [])
    loaded.setdefault("edges", [])
    loaded.setdefault("stats", {})
    return loaded


def save_knowledge_graph(root: Path, graph: dict[str, Any]) -> None:
    path = paths.knowledge_graph_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(
        json.dumps(graph, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    tmp.replace(path)


def rebuild_knowledge_graph(
    root: Path,
    candidate_nodes: dict[str, Any],
    ledger: dict[str, Any],
) -> dict[str, Any]:
    """Build and persist a derived knowledge graph."""
    finished_nodes = [
        _finished_node(record)
        for record in ledger.get("records", [])
        if isinstance(record, dict)
    ]
    planned_nodes = [
        _candidate_node(candidate)
        for candidate in candidate_nodes.get("chapter_candidates", [])
        if isinstance(candidate, dict)
    ]
    nodes = finished_nodes + planned_nodes
    edges = _relation_edges(nodes)
    _resolve_finished_coverage(nodes, edges)
    _attach_node_links(nodes, edges)
    planner = _apply_incremental_forest_planner(nodes, edges)
    graph = {
        "version": 1,
        "nodes": nodes,
        "edges": edges,
        "planner": planner,
        "stats": _graph_stats(nodes, edges),
    }
    save_knowledge_graph(root, graph)
    return graph


def relation_pair_scores(left: dict[str, Any], right: dict[str, Any]) -> dict[str, float]:
    """Return the shared relation-classification similarity scores for two nodes."""
    return _pair_scores(left, right)


def relation_affinity(scores: dict[str, float]) -> float:
    """Return the shared relation-classification weighted affinity."""
    return _affinity(scores)


def build_selected_node_context(graph: dict[str, Any], node_id: str | None = None) -> str:
    """Format a planner-selected or chapter-bound node as the examiner's primary scope."""
    nodes = [item for item in graph.get("nodes", []) if isinstance(item, dict)]
    edges = [item for item in graph.get("edges", []) if isinstance(item, dict)]
    selected_node_id = node_id or graph.get("planner", {}).get("selected_node")
    selected = _node_by_id(nodes, selected_node_id)
    lines = [
        "## Selected Node Context",
        "This is the fixed next growth node selected by the deterministic planner or already bound to the active chapter.",
        "Examiner must compose the next exam inside this node's scope.",
        "",
    ]
    if not selected:
        lines.append("Selected node: none")
        return "\n".join(lines)

    warning_edges = [
        edge
        for edge in edges
        if selected.get("node_id") in {edge.get("from"), edge.get("to")}
        and edge.get("relation") in {"duplicate", "merge_needed", "split_needed"}
    ]
    prerequisite_edges = [
        edge
        for edge in edges
        if edge.get("to") == selected.get("node_id") and edge.get("relation") == "prerequisite"
    ]
    lines.extend(
        [
            f"Selected node: {selected.get('node_id')}",
            f"Title: {selected.get('title')}",
            f"Primary source collection: {selected.get('primary_source_collection') or 'n/a'}",
            f"Source collections: {', '.join(selected.get('source_collections', [])) or 'n/a'}",
            f"Required nodes: {', '.join(selected.get('required_nodes', [])) or 'none'}",
            f"Parent output: {selected.get('parent_output') or 'none'}",
            f"Supporting parents: {_supporting_parent_text(selected.get('supporting_parents', [])) or 'none'}",
            f"New root: {'yes' if selected.get('is_new_root') else 'no'}",
            f"Branch score: {selected.get('branch_score', 0):.2f}",
            f"Support score: {selected.get('support_score', 0):.2f}",
            f"Tree distance: {selected.get('tree_distance', 0):.2f}",
            f"Tree depth: {selected.get('tree_depth', 0)}",
            f"Expected output size: {int(selected.get('estimated_output_lines') or 0)} lines",
            f"Size fit: {float(selected.get('size_fit') or 0):.2f}",
            f"Chunk cluster size: {int(selected.get('chunk_count') or len(selected.get('hit_chunks', [])))} chunks",
            f"Why selected: {selected.get('why_selected') or 'n/a'}",
            "",
            "Allowed scope:",
            f"- Core concepts: {', '.join(selected.get('core_concepts', [])[:14]) or 'n/a'}",
            f"- Prerequisites to assume/cite: {', '.join(selected.get('prerequisites', [])[:10]) or 'none'}",
            f"- Source chunk refs: {', '.join(selected.get('hit_chunks', [])[:10]) or 'n/a'}",
            "",
            "Out of scope:",
            "- Do not reteach required nodes; cite them as prerequisites.",
            "- Do not expand into sibling or child nodes unless needed for a tiny prerequisite bridge.",
            "- If warnings below show duplicate/merge/split risk, narrow or skip rather than changing direction.",
            "",
            "Prerequisite evidence:",
        ]
    )
    if not prerequisite_edges:
        lines.append("- none")
    for edge in prerequisite_edges[:8]:
        evidence = edge.get("evidence", {})
        hits = ", ".join(evidence.get("prerequisite_hits", [])[:8]) or "n/a"
        lines.append(f"- {edge.get('from')} -> {edge.get('to')}: {hits}")
    lines.append("")
    lines.append("Warnings for selected node:")
    if not warning_edges:
        lines.append("- none")
    for edge in warning_edges[:8]:
        evidence = edge.get("evidence", {})
        lines.append(
            f"- {edge.get('relation')}: {edge.get('from')} -> {edge.get('to')} | "
            f"concepts={', '.join(evidence.get('matched_concepts', [])[:6]) or 'n/a'} | "
            f"chunks={', '.join(evidence.get('matched_chunks', [])[:4]) or 'n/a'}"
        )
    return "\n".join(lines).strip()


def build_knowledge_graph_context(graph: dict[str, Any], limit_nodes: int = 12, limit_edges: int = 18) -> str:
    """Format graph state for examiner selection."""
    nodes = [item for item in graph.get("nodes", []) if isinstance(item, dict)]
    edges = [item for item in graph.get("edges", []) if isinstance(item, dict)]
    eligible = [node for node in nodes if node.get("status") == "planned" and node.get("eligible")]
    blocked = [node for node in nodes if node.get("status") == "planned" and not node.get("eligible")]
    warnings = [
        edge
        for edge in edges
        if edge.get("relation") in {"duplicate", "merge_needed", "split_needed"}
    ]

    lines = [
        "## Knowledge Graph",
        "Use this graph as the primary structure: finished outputs are real tree nodes, and candidate nodes are possible next growth points.",
        "The incremental forest planner selects the root or branch. Examiner should compose for the selected node.",
        "",
        f"- finished_nodes: {graph.get('stats', {}).get('finished_count', 0)}",
        f"- planned_nodes: {graph.get('stats', {}).get('planned_count', 0)}",
        f"- eligible_planned_nodes: {graph.get('stats', {}).get('eligible_count', 0)}",
        f"- blocked_planned_nodes: {graph.get('stats', {}).get('blocked_count', 0)}",
        f"- planner_selected: {graph.get('planner', {}).get('selected_node') or 'none'}",
        f"- root_nodes: {', '.join(graph.get('planner', {}).get('root_nodes', [])) or 'none'}",
        "",
        "### Planner Selected Node",
    ]
    selected = _node_by_id(nodes, graph.get("planner", {}).get("selected_node"))
    if selected:
        lines.append(_node_line(selected))
    else:
        lines.append("- (none)")

    lines.append("")
    lines.append("### Eligible Frontier Nodes")
    if not eligible:
        lines.append("- (none)")
    for node in eligible[:limit_nodes]:
        lines.append(_node_line(node))

    lines.append("")
    lines.append("### Blocked Planned Nodes")
    if not blocked:
        lines.append("- (none)")
    for node in blocked[: max(4, limit_nodes // 2)]:
        required = ", ".join(node.get("required_nodes", [])[:5]) or "n/a"
        lines.append(f"- {node.get('node_id')}: {node.get('title')} | requires: {required}")

    lines.append("")
    lines.append("### Graph Warnings")
    if not warnings:
        lines.append("- (none)")
    for edge in warnings[:limit_edges]:
        lines.append(
            f"- {edge.get('relation')}: {edge.get('from')} -> {edge.get('to')} | "
            f"concept={edge.get('scores', {}).get('concept', 0):.2f}, "
            f"chunk={edge.get('scores', {}).get('chunk', 0):.2f}, "
            f"source={edge.get('scores', {}).get('source', 0):.2f} | {edge.get('reason', '')}"
        )
    return "\n".join(lines).strip()


def _empty_graph() -> dict[str, Any]:
    return {
        "version": 1,
        "nodes": [],
        "edges": [],
        "stats": {
            "finished_count": 0,
            "planned_count": 0,
            "eligible_count": 0,
            "blocked_count": 0,
        },
    }


def _finished_node(record: dict[str, Any]) -> dict[str, Any]:
    path = str(record.get("path") or "")
    return {
        "node_id": f"finished:{path}",
        "kind": "finished",
        "status": "finished",
        "title": str(record.get("knowledge_point") or record.get("filename") or path),
        "path": path,
        "chapter": str(record.get("chapter") or ""),
        "file_seq": str(record.get("file_seq") or ""),
        "core_concepts": _string_list(record.get("covered_concepts")),
        "prerequisites": _string_list(record.get("prerequisites")),
        "source_collections": _string_list(record.get("source_collections")),
        "hit_chunks": _string_list(record.get("hit_chunks")),
        "graph_node_id": record.get("graph_node_id"),
        "required_nodes": _string_list(record.get("required_nodes")),
        "related_nodes": [],
    }


def _candidate_node(candidate: dict[str, Any]) -> dict[str, Any]:
    node_id = str(candidate.get("candidate_id") or "")
    if not node_id.startswith("candidate:"):
        node_id = f"candidate:{candidate.get('primary_source_collection') or candidate.get('title_hint') or 'unknown'}"
    return {
        "node_id": node_id,
        "kind": "candidate",
        "status": "planned" if candidate.get("status") != "completed" else "covered",
        "title": str(candidate.get("title_hint") or candidate.get("primary_source_collection") or node_id),
        "path": "",
        "primary_source_collection": str(candidate.get("primary_source_collection") or ""),
        "source_collections": _string_list(candidate.get("source_collections")),
        "core_concepts": _string_list(candidate.get("core_concepts")),
        "prerequisites": _string_list(candidate.get("prerequisite_concepts")),
        "hit_chunks": _chunk_refs(candidate.get("representative_chunks")),
        "methods": _string_list(candidate.get("methods")),
        "formulas": _string_list(candidate.get("formulas")),
        "required_nodes": _string_list(candidate.get("prerequisite_candidates")),
        "related_nodes": [],
        "reason": str(candidate.get("reason") or ""),
        "selection_priority": float(candidate.get("selection_priority") or 0),
        "chunk_count": int(candidate.get("chunk_count") or len(candidate.get("representative_chunks", []) or [])),
        "estimated_output_lines": _candidate_estimated_output_lines(candidate),
        "size_band": str(candidate.get("size_band") or _size_band(_candidate_estimated_output_lines(candidate))),
        "cluster_cohesion": float(candidate.get("cluster_cohesion") or 0.0),
    }


def _relation_edges(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    edges = []
    seen = set()
    for i, left in enumerate(nodes):
        if left.get("status") == "covered":
            continue
        if _needs_split(left):
            edges.append(_split_edge(left))
        for right in nodes[i + 1 :]:
            if right.get("status") == "covered":
                continue
            pair_edges = _classify_pair(left, right)
            for edge in pair_edges:
                key = (edge["from"], edge["to"], edge["relation"])
                if key in seen:
                    continue
                seen.add(key)
                edges.append(edge)
    edges.sort(key=lambda item: (_edge_rank(item.get("relation", "")), item.get("from", ""), item.get("to", "")))
    return edges


def _classify_pair(left: dict[str, Any], right: dict[str, Any]) -> list[dict[str, Any]]:
    scores = _pair_scores(left, right)
    left_requires_right = scores["prerequisite_ba"] >= _MIN_PREREQUISITE
    right_requires_left = scores["prerequisite_ab"] >= _MIN_PREREQUISITE
    if left_requires_right and not right_requires_left:
        return [_edge(right, left, "prerequisite", scores, "Right node covers prerequisites needed by left node.")]
    if right_requires_left and not left_requires_right:
        return [_edge(left, right, "prerequisite", scores, "Left node covers prerequisites needed by right node.")]

    if scores["concept"] >= _MIN_DUPLICATE_CONCEPT and (
        scores["chunk"] >= _MIN_DUPLICATE_CHUNK or scores["concept"] >= 0.78
    ):
        return [_edge(left, right, "duplicate", scores, "Core concepts and source chunks overlap strongly.")]
    if (
        left.get("status") == "planned"
        and right.get("status") == "planned"
        and scores["chunk"] >= _MIN_MERGE_CHUNK
    ):
        return [_edge(left, right, "merge_needed", scores, "Planned nodes hit substantially the same source chunks.")]
    if max(scores["concept"], scores["source"], scores["prerequisite_ab"], scores["prerequisite_ba"]) >= _MIN_ADJACENT:
        return [_edge(left, right, "adjacent", scores, "Nodes share nearby concepts, sources, or weak prerequisite signals.")]
    return []


def _pair_scores(left: dict[str, Any], right: dict[str, Any]) -> dict[str, float]:
    left_concepts = _term_set(left.get("core_concepts", []))
    right_concepts = _term_set(right.get("core_concepts", []))
    left_prereqs = _term_set(left.get("prerequisites", []))
    right_prereqs = _term_set(right.get("prerequisites", []))
    return {
        "concept": _overlap_score(left_concepts, right_concepts),
        "chunk": _overlap_score(set(left.get("hit_chunks", [])), set(right.get("hit_chunks", []))),
        "source": _overlap_score(set(left.get("source_collections", [])), set(right.get("source_collections", []))),
        "prerequisite_ab": _overlap_score(left_concepts, right_prereqs),
        "prerequisite_ba": _overlap_score(right_concepts, left_prereqs),
    }


def _edge(
    source: dict[str, Any],
    target: dict[str, Any],
    relation: str,
    scores: dict[str, float],
    reason: str,
) -> dict[str, Any]:
    return {
        "from": source.get("node_id", ""),
        "to": target.get("node_id", ""),
        "relation": relation,
        "scores": {key: round(value, 4) for key, value in scores.items()},
        "evidence": _edge_evidence(source, target),
        "reason": reason,
    }


def _split_edge(node: dict[str, Any]) -> dict[str, Any]:
    return {
        "from": node.get("node_id", ""),
        "to": node.get("node_id", ""),
        "relation": "split_needed",
        "scores": {
            "concept": min(1.0, len(node.get("core_concepts", [])) / 28),
            "chunk": min(1.0, len(node.get("hit_chunks", [])) / 10),
            "source": min(1.0, len(node.get("source_collections", [])) / 4),
            "prerequisite_ab": 0.0,
            "prerequisite_ba": 0.0,
        },
        "evidence": {
            "matched_concepts": node.get("core_concepts", [])[:16],
            "matched_chunks": node.get("hit_chunks", [])[:12],
            "matched_sources": node.get("source_collections", [])[:8],
            "prerequisite_hits": [],
        },
        "reason": "Candidate spans many concepts, chunks, or source collections; examiner should narrow the next knowledge point.",
    }


def _needs_split(node: dict[str, Any]) -> bool:
    if node.get("status") != "planned":
        return False
    return (
        len(node.get("source_collections", [])) >= 4
        or len(node.get("hit_chunks", [])) >= 10
        or (len(node.get("core_concepts", [])) >= 20 and len(node.get("source_collections", [])) >= 2)
    )


def _resolve_finished_coverage(nodes: list[dict[str, Any]], edges: list[dict[str, Any]]) -> None:
    by_id = {node.get("node_id"): node for node in nodes}
    finished = [node for node in nodes if node.get("kind") == "finished"]
    finished_by_graph_id = {
        node.get("graph_node_id"): node
        for node in nodes
        if node.get("kind") == "finished" and node.get("graph_node_id")
    }
    for node in nodes:
        if node.get("kind") != "candidate" or node.get("status") != "planned":
            continue
        source = finished_by_graph_id.get(node.get("node_id"))
        if source:
            _mark_candidate_covered(node, source, "covered by finished output graph_node_id")
    for node in nodes:
        if node.get("kind") != "candidate" or node.get("status") != "planned":
            continue
        solvability, supporting_outputs = _finished_trunk_solvability(node, finished)
        node["finished_solvability"] = round(solvability, 4)
        if solvability >= _FINISHED_TRUNK_SOLVABILITY:
            _mark_candidate_covered(
                node,
                supporting_outputs[0] if supporting_outputs else {},
                "absorbed by finished trunk solvability",
                supporting_outputs=supporting_outputs,
            )
    for edge in edges:
        if edge.get("relation") != "duplicate":
            continue
        left = by_id.get(edge.get("from"))
        right = by_id.get(edge.get("to"))
        if not left or not right:
            continue
        if left.get("status") == "covered" or right.get("status") == "covered":
            continue
        if left.get("kind") == "finished" and right.get("kind") == "candidate":
            _mark_candidate_covered(right, left, "covered by finished output duplicate relation")
        elif right.get("kind") == "finished" and left.get("kind") == "candidate":
            _mark_candidate_covered(left, right, "covered by finished output duplicate relation")


def _finished_trunk_solvability(
    candidate: dict[str, Any],
    finished: list[dict[str, Any]],
) -> tuple[float, list[dict[str, Any]]]:
    if not finished:
        return 0.0, []
    candidate_concepts = _term_set(candidate.get("core_concepts", []))
    candidate_chunks = set(candidate.get("hit_chunks", []))
    candidate_sources = set(candidate.get("source_collections", []))
    finished_concepts: set[str] = set()
    finished_chunks: set[str] = set()
    finished_sources: set[str] = set()
    supporting = []
    for node in finished:
        node_concepts = _term_set(node.get("core_concepts", []))
        node_chunks = set(node.get("hit_chunks", []))
        node_sources = set(node.get("source_collections", []))
        finished_concepts.update(node_concepts)
        finished_chunks.update(node_chunks)
        finished_sources.update(node_sources)
        if (
            node_concepts & candidate_concepts
            or node_chunks & candidate_chunks
            or node_sources & candidate_sources
        ):
            supporting.append(node)
    concept_coverage = _overlap_score(finished_concepts, candidate_concepts)
    chunk_coverage = _overlap_score(finished_chunks, candidate_chunks)
    source_coverage = _overlap_score(finished_sources, candidate_sources)
    solvability = max(
        concept_coverage,
        chunk_coverage,
        concept_coverage * 0.50 + chunk_coverage * 0.30 + source_coverage * 0.20,
    )
    return min(1.0, solvability), supporting


def _mark_candidate_covered(
    candidate: dict[str, Any],
    finished: dict[str, Any],
    reason: str,
    *,
    supporting_outputs: list[dict[str, Any]] | None = None,
) -> None:
    candidate["status"] = "covered"
    candidate["covered_by_output"] = finished.get("node_id")
    if supporting_outputs is not None:
        candidate["covered_by_outputs"] = [
            node.get("node_id") for node in supporting_outputs if node.get("node_id")
        ]
    candidate["coverage_reason"] = reason
    candidate["eligible"] = False


def _apply_incremental_forest_planner(nodes: list[dict[str, Any]], edges: list[dict[str, Any]]) -> dict[str, Any]:
    candidates = [node for node in nodes if node.get("kind") == "candidate"]
    finished = [node for node in nodes if node.get("kind") == "finished"]
    planned = [node for node in candidates if node.get("status") == "planned"]
    for node in nodes:
        node["planner_selected"] = False
        if node.get("kind") == "finished":
            node["is_root"] = not bool(node.get("required_nodes"))
            node["tree_depth"] = len(node.get("required_nodes", []) or [])
    for node in candidates:
        node["is_root"] = False
        node["is_new_root"] = False
        node["root_score"] = round(_root_score(node, candidates, edges), 4)
        node["backbone_parent"] = None
        node["backbone_children"] = []
        node["parent_output"] = None
        node["branch_score"] = 0.0
        node["support_score"] = 0.0
        node["nearest_finished_output"] = None
        node["tree_distance"] = 1.0
        node["distance_components"] = _empty_distance_components()
        node["supporting_parents"] = []
        node["size_fit"] = round(_size_fit(node), 4)
        node["evidence_strength"] = round(_evidence_strength(node), 4)
        node.setdefault("tree_depth", 0)

    planner = {
        "mode": "incremental_forest_v1",
        "root_nodes": _finished_root_nodes(finished),
        "frontier_nodes": [],
        "selected_node": None,
        "selection_mode": "none",
        "selection_evidence": {},
        "boundary_edges": _boundary_edges(edges),
        "new_root_thresholds": {
            "parent_score": _NEW_ROOT_PARENT_SCORE_THRESHOLD,
            "prerequisite_coverage": _NEW_ROOT_PREREQUISITE_THRESHOLD,
        },
        "multi_parent_thresholds": {
            "parent_score": _MULTI_PARENT_SCORE_THRESHOLD,
            "prerequisite_coverage": _MULTI_PARENT_PREREQ_THRESHOLD,
            "max_supporting_parents": _MAX_SUPPORTING_PARENTS,
        },
    }
    if not planned:
        planner["trace"] = _planner_trace(planner, [])
        return planner
    if not finished:
        selected = _select_root_candidate(planned, candidates, edges)
        _mark_selected_root(selected, planner, edges, "initial_root")
        planner["trace"] = _planner_trace(planner, planned)
        return planner

    for node in planned:
        parents = _parent_outputs(node, finished)
        _attach_parent_metrics(node, parents)

    frontier = [node for node in planned if node.get("eligible", True)]
    if not frontier:
        planner["trace"] = _planner_trace(planner, planned)
        return planner
    frontier.sort(key=lambda node: _branch_sort_key(node, edges))
    best_branch = frontier[0]
    if _should_start_new_root(best_branch):
        selected = _select_root_candidate(frontier, candidates, edges)
        _mark_selected_root(selected, planner, edges, "new_root")
    else:
        _mark_selected_branch(best_branch, planner, edges)
    planner["frontier_nodes"] = [node.get("node_id", "") for node in frontier]
    planner["trace"] = _planner_trace(planner, frontier)
    return planner


def _finished_root_nodes(finished: list[dict[str, Any]]) -> list[str]:
    roots = []
    for node in finished:
        if node.get("required_nodes"):
            continue
        roots.append(str(node.get("node_id") or ""))
    return [item for item in roots if item]


def _select_root_candidate(
    planned: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
    edges: list[dict[str, Any]],
) -> dict[str, Any]:
    for node in planned:
        node["root_score"] = round(_root_score(node, candidates, edges), 4)
    return sorted(
        planned,
        key=lambda node: (
            _node_warning_penalty(node, edges),
            -float(node.get("root_score", 0)),
            -float(node.get("evidence_strength", 0)),
            -float(node.get("selection_priority", 0)),
            str(node.get("node_id", "")),
        ),
    )[0]


def _mark_selected_root(node: dict[str, Any], planner: dict[str, Any], edges: list[dict[str, Any]], mode: str) -> None:
    node["planner_selected"] = True
    node["is_root"] = True
    node["is_new_root"] = True
    node["parent_output"] = None
    node["branch_score"] = 0.0
    node["tree_depth"] = 0
    node["selection_evidence"] = _selection_evidence(node, edges)
    node["why_selected"] = (
        "first root selected by root selector"
        if mode == "initial_root"
        else "remaining candidates are distant from the finished tree; root selector opened a new root"
    )
    planner["selection_mode"] = mode
    planner["selected_node"] = node.get("node_id")
    planner["selection_evidence"] = node["selection_evidence"]
    if node.get("node_id") not in planner["root_nodes"]:
        planner["root_nodes"].append(node.get("node_id"))
    planner["frontier_nodes"] = [node.get("node_id", "")]


def _mark_selected_branch(node: dict[str, Any], planner: dict[str, Any], edges: list[dict[str, Any]]) -> None:
    parent = str(node.get("parent_output") or "")
    node["planner_selected"] = True
    node["is_new_root"] = False
    required = node.setdefault("required_nodes", [])
    supporting_parent_ids = [
        str(item.get("node_id") or "")
        for item in node.get("supporting_parents", [])
        if item.get("node_id")
    ]
    if parent and parent not in supporting_parent_ids:
        supporting_parent_ids.insert(0, parent)
    for parent_id in supporting_parent_ids:
        if parent_id not in required:
            required.append(parent_id)
    if parent:
        if not any(
            edge.get("relation") == "branch"
            and edge.get("from") == parent
            and edge.get("to") == node.get("node_id")
            for edge in edges
        ):
            edges.append(_branch_edge(parent, node))
    for parent_id in supporting_parent_ids:
        if parent_id == parent:
            continue
        if not any(
            edge.get("relation") == "supporting_parent"
            and edge.get("from") == parent_id
            and edge.get("to") == node.get("node_id")
            for edge in edges
        ):
            edges.append(_supporting_parent_edge(parent_id, node))
    node["selection_evidence"] = _selection_evidence(node, edges)
    node["why_selected"] = "best attachable branch from existing finished tree"
    planner["selection_mode"] = "branch"
    planner["selected_node"] = node.get("node_id")
    planner["selection_evidence"] = node["selection_evidence"]


def _parent_outputs(candidate: dict[str, Any], finished: list[dict[str, Any]]) -> list[dict[str, Any]]:
    parents = []
    for parent in finished:
        scores = _pair_scores(parent, candidate)
        prerequisite_coverage = _prerequisite_coverage(parent, candidate)
        parent_score = (
            prerequisite_coverage * 0.45
            + _affinity(scores) * 0.35
            + scores["source"] * 0.10
            + scores["chunk"] * 0.10
        )
        parents.append(
            {
                "node": parent,
                "score": parent_score,
                "scores": scores,
                "prerequisite_coverage": prerequisite_coverage,
            }
        )
    parents.sort(
        key=lambda item: (
            -float(item.get("score") or 0),
            -float(item.get("prerequisite_coverage") or 0),
            str(item.get("node", {}).get("node_id", "")),
        )
    )
    return parents


def _attach_parent_metrics(node: dict[str, Any], parents: list[dict[str, Any]]) -> None:
    primary = parents[0] if parents else {"node": None, "score": 0.0, "scores": {}, "prerequisite_coverage": 0.0}
    parent_node = primary.get("node")
    scores = primary.get("scores", {})
    supporting = _supporting_parent_outputs(parents)
    node["parent_output"] = parent_node.get("node_id") if parent_node else None
    node["branch_score"] = round(float(primary.get("score") or 0), 4)
    node["best_parent_affinity"] = round(_affinity(scores), 4) if scores else 0.0
    node["prerequisite_coverage"] = round(float(primary.get("prerequisite_coverage") or 0), 4)
    node["parent_relation_scores"] = {key: round(value, 4) for key, value in scores.items()}
    node["supporting_parents"] = [_supporting_parent_summary(item) for item in supporting]
    node["combined_prerequisite_coverage"] = round(_combined_prerequisite_coverage(supporting, node), 4)
    node["support_score"] = round(_support_score(node), 4)
    node["nearest_finished_output"] = node["parent_output"]
    node["tree_distance"] = round(max(0.0, min(1.0, 1 - float(node.get("support_score") or 0))), 4)
    node["distance_components"] = _distance_components(
        scores,
        float(node.get("combined_prerequisite_coverage") or 0),
    )
    node["tree_depth"] = int(parent_node.get("tree_depth", 0)) + 1 if parent_node else 0


def _should_start_new_root(node: dict[str, Any]) -> bool:
    scores = node.get("parent_relation_scores", {})
    parent_signal = max(float(node.get("branch_score") or 0), float(node.get("support_score") or 0))
    prerequisite_signal = max(
        float(node.get("prerequisite_coverage") or 0),
        float(node.get("combined_prerequisite_coverage") or 0),
    )
    return (
        parent_signal < _NEW_ROOT_PARENT_SCORE_THRESHOLD
        and prerequisite_signal < _NEW_ROOT_PREREQUISITE_THRESHOLD
        and float(scores.get("source") or 0) < _STRONG_PARENT_SOURCE
        and float(scores.get("chunk") or 0) < _STRONG_PARENT_CHUNK
    )


def _branch_sort_key(node: dict[str, Any], edges: list[dict[str, Any]]) -> tuple[Any, ...]:
    return (
        _node_warning_penalty(node, edges),
        -float(node.get("support_score") or 0),
        -float(node.get("branch_score") or 0),
        -float(node.get("combined_prerequisite_coverage") or 0),
        -float(node.get("prerequisite_coverage") or 0),
        -_size_fit(node),
        -float(node.get("best_parent_affinity") or 0),
        -float(node.get("evidence_strength") or 0),
        str(node.get("node_id", "")),
    )


def _prerequisite_coverage(parent: dict[str, Any], candidate: dict[str, Any]) -> float:
    prereqs = _term_set(candidate.get("prerequisites", []))
    if not prereqs:
        return 0.0
    parent_terms = _term_set(parent.get("core_concepts", []))
    return _overlap_score(parent_terms, prereqs)


def _supporting_parent_outputs(parents: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not parents:
        return []
    primary = parents[0]
    supporting = [primary]
    for parent in parents[1:]:
        if not _is_supporting_parent(parent):
            continue
        supporting.append(parent)
        if len(supporting) >= _MAX_SUPPORTING_PARENTS:
            break
    return supporting


def _is_supporting_parent(parent: dict[str, Any]) -> bool:
    scores = parent.get("scores", {})
    semantic_signal = (
        float(parent.get("prerequisite_coverage") or 0) > 0
        or float(scores.get("concept") or 0) > 0
        or float(scores.get("chunk") or 0) > 0
    )
    if not semantic_signal:
        return False
    return (
        float(parent.get("score") or 0) >= _MULTI_PARENT_SCORE_THRESHOLD
        or float(parent.get("prerequisite_coverage") or 0) >= _MULTI_PARENT_PREREQ_THRESHOLD
    )


def _supporting_parent_summary(parent: dict[str, Any]) -> dict[str, Any]:
    node = parent.get("node") or {}
    scores = parent.get("scores", {})
    return {
        "node_id": node.get("node_id"),
        "score": round(float(parent.get("score") or 0), 4),
        "prerequisite_coverage": round(float(parent.get("prerequisite_coverage") or 0), 4),
        "affinity": round(_affinity(scores), 4) if scores else 0.0,
        "concept": round(float(scores.get("concept") or 0), 4),
        "chunk": round(float(scores.get("chunk") or 0), 4),
        "source": round(float(scores.get("source") or 0), 4),
    }


def _combined_prerequisite_coverage(parents: list[dict[str, Any]], candidate: dict[str, Any]) -> float:
    prereqs = _term_set(candidate.get("prerequisites", []))
    if not prereqs:
        return 0.0
    parent_terms: set[str] = set()
    for parent in parents:
        node = parent.get("node") or {}
        parent_terms.update(_term_set(node.get("core_concepts", [])))
    return _overlap_score(parent_terms, prereqs)


def _support_score(node: dict[str, Any]) -> float:
    supporting_count = len(node.get("supporting_parents", []) or [])
    supporting_bonus = min(1.0, supporting_count / max(1, _MAX_SUPPORTING_PARENTS))
    return min(
        1.0,
        float(node.get("branch_score") or 0) * 0.65
        + float(node.get("combined_prerequisite_coverage") or 0) * 0.25
        + supporting_bonus * 0.10,
    )


def _empty_distance_components() -> dict[str, float]:
    return {
        "concept_distance": 1.0,
        "chunk_distance": 1.0,
        "source_distance": 1.0,
        "affinity_distance": 1.0,
        "prerequisite_gap": 1.0,
    }


def _distance_components(scores: dict[str, float], combined_prerequisite_coverage: float) -> dict[str, float]:
    if not scores:
        return _empty_distance_components()
    return {
        "concept_distance": round(1 - float(scores.get("concept") or 0), 4),
        "chunk_distance": round(1 - float(scores.get("chunk") or 0), 4),
        "source_distance": round(1 - float(scores.get("source") or 0), 4),
        "affinity_distance": round(1 - _affinity(scores), 4),
        "prerequisite_gap": round(1 - combined_prerequisite_coverage, 4),
    }


def _planner_trace(planner: dict[str, Any], candidates: list[dict[str, Any]]) -> dict[str, Any]:
    selected_id = planner.get("selected_node")
    ranked = sorted(candidates, key=lambda node: _trace_sort_key(node))
    return {
        "mode": planner.get("mode"),
        "selection_mode": planner.get("selection_mode"),
        "selected_node": selected_id,
        "candidate_count": len(candidates),
        "candidate_ranking": [
            _trace_candidate_entry(index, node, selected_id, planner.get("selection_mode"))
            for index, node in enumerate(ranked, start=1)
        ],
    }


def _trace_sort_key(node: dict[str, Any]) -> tuple[Any, ...]:
    return (
        float(node.get("tree_distance") if node.get("tree_distance") is not None else 1.0),
        -float(node.get("support_score") or 0),
        -float(node.get("branch_score") or 0),
        -float(node.get("root_score") or 0),
        str(node.get("node_id", "")),
    )


def _trace_candidate_entry(
    rank: int,
    node: dict[str, Any],
    selected_id: Any,
    selection_mode: Any,
) -> dict[str, Any]:
    selected = node.get("node_id") == selected_id
    return {
        "rank": rank,
        "node_id": node.get("node_id"),
        "selected": selected,
        "reason": _trace_reason(selected, selection_mode, node),
        "parent_output": node.get("parent_output"),
        "nearest_finished_output": node.get("nearest_finished_output"),
        "tree_distance": round(float(node.get("tree_distance") or 0), 4),
        "branch_score": round(float(node.get("branch_score") or 0), 4),
        "support_score": round(float(node.get("support_score") or 0), 4),
        "root_score": round(float(node.get("root_score") or 0), 4),
        "prerequisite_coverage": round(float(node.get("prerequisite_coverage") or 0), 4),
        "combined_prerequisite_coverage": round(float(node.get("combined_prerequisite_coverage") or 0), 4),
        "supporting_parent_count": len(node.get("supporting_parents", []) or []),
        "distance_components": node.get("distance_components", _empty_distance_components()),
    }


def _trace_reason(selected: bool, selection_mode: Any, node: dict[str, Any]) -> str:
    if selected:
        return "selected"
    if selection_mode == "new_root" and _should_start_new_root(node):
        return "new_root_distance"
    if selection_mode in {"initial_root", "new_root"}:
        return "lower_root_score"
    return "lower_support_score"


def _apply_backbone_planner(nodes: list[dict[str, Any]], edges: list[dict[str, Any]]) -> dict[str, Any]:
    candidates = [
        node
        for node in nodes
        if node.get("kind") == "candidate" and node.get("status") in {"planned", "covered"}
    ]
    for node in candidates:
        node["is_root"] = False
        node["root_score"] = round(_root_score(node, candidates, edges), 4)
        node["backbone_parent"] = None
        node["backbone_children"] = []
        node["tree_depth"] = 0
        node["planner_selected"] = False
        node["evidence_strength"] = round(_evidence_strength(node), 4)

    forest = _maximum_spanning_forest(candidates)
    adjacency: dict[str, list[tuple[str, float]]] = {node["node_id"]: [] for node in candidates}
    for left, right, affinity in forest:
        adjacency.setdefault(left, []).append((right, affinity))
        adjacency.setdefault(right, []).append((left, affinity))

    by_id = {node.get("node_id"): node for node in candidates}
    root_nodes: list[str] = []
    backbone_edges = []
    visited: set[str] = set()
    for component in _connected_components(adjacency, by_id):
        root_id = _component_root(component, by_id)
        if not root_id:
            continue
        root_nodes.append(root_id)
        by_id[root_id]["is_root"] = True
        queue = [(root_id, None, 0)]
        visited.add(root_id)
        while queue:
            current, parent, depth = queue.pop(0)
            current_node = by_id[current]
            current_node["tree_depth"] = depth
            if parent is not None:
                current_node["backbone_parent"] = parent
                parent_children = by_id[parent].setdefault("backbone_children", [])
                if current not in parent_children:
                    parent_children.append(current)
            for neighbor, affinity in sorted(adjacency.get(current, []), key=lambda item: item[1], reverse=True):
                if neighbor in visited:
                    continue
                visited.add(neighbor)
                queue.append((neighbor, current, depth + 1))
                backbone_edges.append(_backbone_edge(by_id[current], by_id[neighbor], affinity))

    edges.extend(backbone_edges)
    return {
        "mode": "deterministic_mst_v1",
        "root_nodes": root_nodes,
        "frontier_nodes": [],
        "selected_node": None,
        "boundary_edges": _boundary_edges(edges),
    }


def _select_frontier(nodes: list[dict[str, Any]], edges: list[dict[str, Any]], planner: dict[str, Any]) -> None:
    planned = [node for node in nodes if node.get("status") == "planned"]
    frontier = [node for node in planned if node.get("eligible")]
    for node in planned:
        node["planner_selected"] = False
    frontier.sort(key=lambda node: _frontier_sort_key(node, edges))
    selected = frontier[0] if frontier else None
    if selected:
        selected["planner_selected"] = True
        selected["selection_evidence"] = _selection_evidence(selected, edges)
        selected["why_selected"] = _why_selected(selected)
    planner["frontier_nodes"] = [node.get("node_id", "") for node in frontier]
    planner["selected_node"] = selected.get("node_id") if selected else None
    planner["selection_evidence"] = selected.get("selection_evidence", {}) if selected else {}


def _maximum_spanning_forest(nodes: list[dict[str, Any]]) -> list[tuple[str, str, float]]:
    pairs = []
    for i, left in enumerate(nodes):
        for right in nodes[i + 1 :]:
            scores = _pair_scores(left, right)
            affinity = _affinity(scores)
            if affinity < _MIN_BACKBONE_AFFINITY:
                continue
            pairs.append((left["node_id"], right["node_id"], affinity))
    pairs.sort(key=lambda item: (-item[2], item[0], item[1]))
    union = _UnionFind([node["node_id"] for node in nodes])
    forest = []
    for left, right, affinity in pairs:
        if union.find(left) == union.find(right):
            continue
        union.union(left, right)
        forest.append((left, right, affinity))
    return forest


def _connected_components(
    adjacency: dict[str, list[tuple[str, float]]],
    by_id: dict[str, dict[str, Any]],
) -> list[list[str]]:
    seen: set[str] = set()
    components = []
    for node_id in sorted(by_id):
        if node_id in seen:
            continue
        queue = [node_id]
        seen.add(node_id)
        component = []
        while queue:
            current = queue.pop(0)
            component.append(current)
            for neighbor, _ in adjacency.get(current, []):
                if neighbor in seen:
                    continue
                seen.add(neighbor)
                queue.append(neighbor)
        components.append(component)
    return components


def _component_root(component: list[str], by_id: dict[str, dict[str, Any]]) -> str | None:
    if not component:
        return None
    ranked = sorted(
        component,
        key=lambda node_id: (
            -float(by_id[node_id].get("root_score", 0)),
            int(by_id[node_id].get("tree_depth", 0)),
            node_id,
        ),
    )
    return ranked[0]


def _root_score(node: dict[str, Any], candidates: list[dict[str, Any]], edges: list[dict[str, Any]]) -> float:
    prerequisite_count = len(node.get("prerequisites", [])) + len(node.get("required_nodes", []))
    low_prerequisite = 1 / (1 + prerequisite_count)
    outgoing_support = _outgoing_prerequisite_support(node, candidates)
    duplicate_penalty = 0.35 if _has_duplicate_with_finished(node, edges) else 0.0
    return max(
        0.0,
        min(
            1.0,
            low_prerequisite * 0.35
            + outgoing_support * 0.30
            + _evidence_strength(node) * 0.20
            + _size_fit(node) * 0.15
            + float(node.get("selection_priority") or 0) * 0.10
            - duplicate_penalty,
        ),
    )


def _outgoing_prerequisite_support(node: dict[str, Any], candidates: list[dict[str, Any]]) -> float:
    if len(candidates) <= 1:
        return 0.0
    concepts = _term_set(node.get("core_concepts", []))
    supported = 0
    for other in candidates:
        if other is node:
            continue
        if _overlap_score(concepts, _term_set(other.get("prerequisites", []))) >= _MIN_PREREQUISITE:
            supported += 1
    return supported / max(1, len(candidates) - 1)


def _evidence_strength(node: dict[str, Any]) -> float:
    return min(
        1.0,
        len(node.get("core_concepts", [])) / 12 * 0.45
        + len(node.get("hit_chunks", [])) / 6 * 0.35
        + len(node.get("source_collections", [])) / 3 * 0.20,
    )


def _size_fit(node: dict[str, Any]) -> float:
    lines = int(node.get("estimated_output_lines") or 0)
    if lines <= 0:
        lines = (
            130
            + len(node.get("hit_chunks", [])) * 45
            + min(len(node.get("core_concepts", [])), 12) * 20
            + min(len(node.get("methods", [])), 8) * 15
            + min(len(node.get("formulas", [])), 8) * 10
        )
    if 300 <= lines <= 500:
        return 1.0
    if lines < 300:
        return max(0.0, round(lines / 300, 4))
    return max(0.0, round(1 - (lines - 500) / 500, 4))


def _size_band(estimated_lines: int) -> str:
    if estimated_lines < 260:
        return "thin"
    if estimated_lines > 560:
        return "broad"
    return "fit"


def _candidate_estimated_output_lines(candidate: dict[str, Any]) -> int:
    explicit = candidate.get("estimated_output_lines")
    if explicit is not None:
        try:
            return int(explicit)
        except (TypeError, ValueError):
            pass
    return int(
        130
        + len(candidate.get("representative_chunks", []) or []) * 45
        + min(len(candidate.get("core_concepts", []) or []), 12) * 20
        + min(len(candidate.get("methods", []) or []), 8) * 15
        + min(len(candidate.get("formulas", []) or []), 8) * 10
    )


def _has_duplicate_with_finished(node: dict[str, Any], edges: list[dict[str, Any]]) -> bool:
    node_id = node.get("node_id")
    for edge in edges:
        if edge.get("relation") != "duplicate":
            continue
        if node_id not in {edge.get("from"), edge.get("to")}:
            continue
        other = edge.get("to") if edge.get("from") == node_id else edge.get("from")
        if str(other).startswith("finished:"):
            return True
    return False


def _frontier_sort_key(node: dict[str, Any], edges: list[dict[str, Any]]) -> tuple[Any, ...]:
    warning_penalty = _node_warning_penalty(node, edges)
    return (
        warning_penalty,
        int(node.get("tree_depth", 0)),
        -float(node.get("evidence_strength", 0)),
        -float(node.get("root_score", 0)),
        -float(node.get("selection_priority", 0)),
        str(node.get("node_id", "")),
    )


def _node_warning_penalty(node: dict[str, Any], edges: list[dict[str, Any]]) -> int:
    node_id = node.get("node_id")
    penalty = 0
    for edge in edges:
        if node_id not in {edge.get("from"), edge.get("to")}:
            continue
        relation = edge.get("relation")
        if relation == "duplicate":
            penalty += 4
        elif relation in {"merge_needed", "split_needed"}:
            penalty += 3
    return penalty


def _selection_evidence(node: dict[str, Any], edges: list[dict[str, Any]]) -> dict[str, Any]:
    node_id = node.get("node_id")
    incoming_prerequisites = [
        edge
        for edge in edges
        if edge.get("to") == node_id and edge.get("relation") == "prerequisite"
    ]
    warnings = [
        edge
        for edge in edges
        if node_id in {edge.get("from"), edge.get("to")}
        and edge.get("relation") in {"duplicate", "merge_needed", "split_needed"}
    ]
    return {
        "tree_depth": int(node.get("tree_depth", 0)),
        "root_score": round(float(node.get("root_score") or 0), 4),
        "evidence_strength": round(float(node.get("evidence_strength") or 0), 4),
        "selection_priority": round(float(node.get("selection_priority") or 0), 4),
        "warning_penalty": _node_warning_penalty(node, edges),
        "backbone_parent": node.get("backbone_parent"),
        "parent_output": node.get("parent_output"),
        "supporting_parents": node.get("supporting_parents", [])[:_MAX_SUPPORTING_PARENTS],
        "is_new_root": bool(node.get("is_new_root")),
        "branch_score": round(float(node.get("branch_score") or 0), 4),
        "support_score": round(float(node.get("support_score") or 0), 4),
        "tree_distance": round(float(node.get("tree_distance") or 0), 4),
        "nearest_finished_output": node.get("nearest_finished_output"),
        "distance_components": node.get("distance_components", _empty_distance_components()),
        "prerequisite_coverage": round(float(node.get("prerequisite_coverage") or 0), 4),
        "combined_prerequisite_coverage": round(float(node.get("combined_prerequisite_coverage") or 0), 4),
        "best_parent_affinity": round(float(node.get("best_parent_affinity") or 0), 4),
        "incoming_prerequisites": [
            {
                "from": edge.get("from"),
                "hits": edge.get("evidence", {}).get("prerequisite_hits", [])[:8],
            }
            for edge in incoming_prerequisites[:8]
        ],
        "warnings": [
            {
                "relation": edge.get("relation"),
                "node": edge.get("to") if edge.get("from") == node_id else edge.get("from"),
                "matched_concepts": edge.get("evidence", {}).get("matched_concepts", [])[:8],
                "matched_chunks": edge.get("evidence", {}).get("matched_chunks", [])[:6],
            }
            for edge in warnings[:8]
        ],
    }


def _why_selected(node: dict[str, Any]) -> str:
    evidence = node.get("selection_evidence", {})
    if node.get("is_new_root"):
        return "root selector opened a new root because no existing output is a strong parent"
    if node.get("parent_output"):
        return (
            f"best attachable branch under {node.get('parent_output')}; "
            f"branch_score={float(node.get('branch_score') or 0):.2f}; "
            f"supporting_parents={len(node.get('supporting_parents', []) or [])}"
        )
    parts = [
        f"eligible frontier node at depth {evidence.get('tree_depth', 0)}",
        f"evidence_strength={evidence.get('evidence_strength', 0):.2f}",
        f"root_score={evidence.get('root_score', 0):.2f}",
    ]
    penalty = int(evidence.get("warning_penalty") or 0)
    if penalty:
        parts.append(f"warning_penalty={penalty}")
    else:
        parts.append("no duplicate/merge/split warning penalty")
    return "; ".join(parts)


def _backbone_edge(source: dict[str, Any], target: dict[str, Any], affinity: float) -> dict[str, Any]:
    scores = _pair_scores(source, target)
    return {
        "from": source.get("node_id", ""),
        "to": target.get("node_id", ""),
        "relation": "backbone",
        "scores": {
            "affinity": round(affinity, 4),
            "concept": round(scores["concept"], 4),
            "chunk": round(scores["chunk"], 4),
            "source": round(scores["source"], 4),
            "prerequisite": round(max(scores["prerequisite_ab"], scores["prerequisite_ba"]), 4),
        },
        "evidence": _edge_evidence(source, target),
        "confidence": round(min(1.0, affinity / 0.72), 4),
        "reason": "Deterministic maximum-spanning backbone edge.",
    }


def _branch_edge(parent_id: str, target: dict[str, Any]) -> dict[str, Any]:
    scores = target.get("parent_relation_scores", {})
    return {
        "from": parent_id,
        "to": target.get("node_id", ""),
        "relation": "branch",
        "scores": {
            "affinity": round(float(target.get("best_parent_affinity") or 0), 4),
            "concept": round(float(scores.get("concept") or 0), 4),
            "chunk": round(float(scores.get("chunk") or 0), 4),
            "source": round(float(scores.get("source") or 0), 4),
            "prerequisite": round(float(target.get("prerequisite_coverage") or 0), 4),
            "branch": round(float(target.get("branch_score") or 0), 4),
        },
        "evidence": {
            "matched_concepts": [],
            "matched_chunks": target.get("hit_chunks", [])[:8],
            "matched_sources": target.get("source_collections", [])[:8],
            "prerequisite_hits": target.get("prerequisites", [])[:8],
        },
        "confidence": round(min(1.0, float(target.get("branch_score") or 0) / 0.72), 4),
        "reason": "Incremental forest planner inserted this branch under the best finished output parent.",
    }


def _supporting_parent_edge(parent_id: str, target: dict[str, Any]) -> dict[str, Any]:
    support = next(
        (
            item
            for item in target.get("supporting_parents", [])
            if item.get("node_id") == parent_id
        ),
        {},
    )
    return {
        "from": parent_id,
        "to": target.get("node_id", ""),
        "relation": "supporting_parent",
        "scores": {
            "affinity": round(float(support.get("affinity") or 0), 4),
            "concept": round(float(support.get("concept") or 0), 4),
            "chunk": round(float(support.get("chunk") or 0), 4),
            "source": round(float(support.get("source") or 0), 4),
            "prerequisite": round(float(support.get("prerequisite_coverage") or 0), 4),
            "support": round(float(support.get("score") or 0), 4),
        },
        "evidence": {
            "matched_concepts": [],
            "matched_chunks": target.get("hit_chunks", [])[:8],
            "matched_sources": target.get("source_collections", [])[:8],
            "prerequisite_hits": target.get("prerequisites", [])[:8],
        },
        "confidence": round(min(1.0, float(support.get("score") or 0) / 0.72), 4),
        "reason": "Additional finished output parent exceeds the multi-parent support threshold.",
    }


def _affinity(scores: dict[str, float]) -> float:
    return (
        scores["concept"] * 0.42
        + scores["chunk"] * 0.28
        + scores["source"] * 0.18
        + max(scores["prerequisite_ab"], scores["prerequisite_ba"]) * 0.12
    )


def _edge_evidence(source: dict[str, Any], target: dict[str, Any]) -> dict[str, Any]:
    return {
        "matched_concepts": _matched_values(
            source.get("core_concepts", []),
            target.get("core_concepts", []),
        )[:12],
        "matched_chunks": sorted(
            set(source.get("hit_chunks", [])) & set(target.get("hit_chunks", []))
        )[:12],
        "matched_sources": sorted(
            set(source.get("source_collections", [])) & set(target.get("source_collections", []))
        )[:8],
        "prerequisite_hits": _matched_values(
            source.get("core_concepts", []),
            target.get("prerequisites", []),
        )[:12],
        "reverse_prerequisite_hits": _matched_values(
            target.get("core_concepts", []),
            source.get("prerequisites", []),
        )[:12],
    }


def _matched_values(left_values: Any, right_values: Any) -> list[str]:
    if isinstance(left_values, str):
        left_values = [left_values]
    if isinstance(right_values, str):
        right_values = [right_values]
    if not isinstance(left_values, list) or not isinstance(right_values, list):
        return []
    right_terms = _term_set(right_values)
    matches = []
    for value in left_values:
        if _term_set(str(value)) & right_terms:
            matches.append(str(value))
    return _unique(matches)


def _boundary_edges(edges: list[dict[str, Any]]) -> list[dict[str, Any]]:
    boundary = []
    for edge in edges:
        relation = edge.get("relation")
        scores = edge.get("scores", {})
        if relation == "backbone" and float(edge.get("confidence") or 0) < 0.35:
            boundary.append({"edge": f"{edge.get('from')}->{edge.get('to')}", "reason": "low_backbone_confidence"})
        elif relation == "duplicate" and 0.5 <= float(scores.get("concept") or 0) <= 0.72:
            boundary.append({"edge": f"{edge.get('from')}->{edge.get('to')}", "reason": "near_duplicate_threshold"})
        elif relation in {"merge_needed", "split_needed"}:
            boundary.append({"edge": f"{edge.get('from')}->{edge.get('to')}", "reason": relation})
    return boundary[:20]


class _UnionFind:
    def __init__(self, values: list[str]):
        self.parent = {value: value for value in values}

    def find(self, value: str) -> str:
        parent = self.parent[value]
        if parent != value:
            self.parent[value] = self.find(parent)
        return self.parent[value]

    def union(self, left: str, right: str) -> None:
        left_root = self.find(left)
        right_root = self.find(right)
        if left_root != right_root:
            self.parent[right_root] = left_root


def _attach_node_links(nodes: list[dict[str, Any]], edges: list[dict[str, Any]]) -> None:
    by_id = {node.get("node_id"): node for node in nodes}
    satisfied_ids = {
        node.get("node_id")
        for node in nodes
        if node.get("status") in {"finished", "covered"}
    }
    changed = True
    while changed:
        changed = False
        for edge in edges:
            if edge.get("relation") != "duplicate":
                continue
            source = edge.get("from")
            target = edge.get("to")
            if source in satisfied_ids and target and target not in satisfied_ids:
                satisfied_ids.add(target)
                changed = True
            if target in satisfied_ids and source and source not in satisfied_ids:
                satisfied_ids.add(source)
                changed = True
    for edge in edges:
        source = edge.get("from")
        target = edge.get("to")
        relation = edge.get("relation")
        if relation == "prerequisite" and target in by_id:
            required = by_id[target].setdefault("required_nodes", [])
            if source not in required:
                required.append(source)
        if source in by_id and target != source:
            related = by_id[source].setdefault("related_nodes", [])
            if target not in related:
                related.append(target)
        if target in by_id and target != source:
            related = by_id[target].setdefault("related_nodes", [])
            if source not in related:
                related.append(source)
    for node in nodes:
        required = node.get("required_nodes", [])
        if node.get("status") == "planned":
            node["eligible"] = all(req in satisfied_ids for req in required)
        else:
            node["eligible"] = False


def _graph_stats(nodes: list[dict[str, Any]], edges: list[dict[str, Any]]) -> dict[str, Any]:
    finished = [node for node in nodes if node.get("status") == "finished"]
    planned = [node for node in nodes if node.get("status") == "planned"]
    return {
        "finished_count": len(finished),
        "planned_count": len(planned),
        "eligible_count": len([node for node in planned if node.get("eligible")]),
        "blocked_count": len([node for node in planned if not node.get("eligible")]),
        "root_count": len([node for node in nodes if node.get("is_root")]),
        "selected_count": len([node for node in planned if node.get("planner_selected")]),
        "edge_count": len(edges),
        "backbone_count": len([edge for edge in edges if edge.get("relation") == "backbone"]),
        "branch_count": len([edge for edge in edges if edge.get("relation") == "branch"]),
        "supporting_parent_count": len([edge for edge in edges if edge.get("relation") == "supporting_parent"]),
        "duplicate_count": len([edge for edge in edges if edge.get("relation") == "duplicate"]),
        "prerequisite_count": len([edge for edge in edges if edge.get("relation") == "prerequisite"]),
        "split_needed_count": len([edge for edge in edges if edge.get("relation") == "split_needed"]),
        "merge_needed_count": len([edge for edge in edges if edge.get("relation") == "merge_needed"]),
    }


def _node_line(node: dict[str, Any]) -> str:
    concepts = ", ".join(node.get("core_concepts", [])[:8]) or "n/a"
    required = ", ".join(node.get("required_nodes", [])[:5]) or "none"
    chunks = ", ".join(node.get("hit_chunks", [])[:4]) or "n/a"
    parent = node.get("parent_output") or "root" if node.get("is_new_root") else node.get("parent_output") or "none"
    return (
        f"- {node.get('node_id')}: {node.get('title')} | "
        f"parent: {parent} | requires: {required} | concepts: {concepts} | chunks: {chunks}"
    )


def _node_by_id(nodes: list[dict[str, Any]], node_id: Any) -> dict[str, Any] | None:
    if not node_id:
        return None
    for node in nodes:
        if node.get("node_id") == node_id:
            return node
    return None


def _chunk_refs(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    refs = []
    for item in value:
        if isinstance(item, str):
            refs.append(item)
        elif isinstance(item, dict) and item.get("chunk_ref"):
            refs.append(str(item["chunk_ref"]))
    return _unique(refs)


def _string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        value = re.split(r"[,\n，、]+", value)
    if not isinstance(value, list):
        return []
    return _unique(str(item).strip() for item in value if str(item).strip())


def _term_set(values: Any) -> set[str]:
    terms = set()
    if isinstance(values, str):
        values = [values]
    if not isinstance(values, list):
        return terms
    for value in values:
        text = str(value)
        for raw in _TERM_RE.findall(text):
            token = _clean_term(raw)
            if len(token) >= 2:
                terms.add(token.lower())
            for piece in _SPLIT_RE.split(token):
                piece = _clean_term(piece)
                if len(piece) >= 2:
                    terms.add(piece.lower())
    return terms


def _overlap_score(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / max(1, min(len(left), len(right)))


def _clean_term(value: str) -> str:
    value = re.sub(r"`([^`]+)`", r"\1", value)
    value = re.sub(r"\s+", "", value)
    return value.strip(" -—:：。；;，,")


def _unique(values: Any) -> list[str]:
    seen = set()
    result = []
    for value in values:
        if not value:
            continue
        key = str(value)
        if key in seen:
            continue
        seen.add(key)
        result.append(key)
    return result


def _edge_rank(relation: str) -> int:
    order = {
        "duplicate": 0,
        "merge_needed": 1,
        "split_needed": 2,
        "prerequisite": 3,
        "branch": 4,
        "supporting_parent": 5,
        "adjacent": 6,
    }
    return order.get(relation, 9)


def _supporting_parent_text(items: Any) -> str:
    if not isinstance(items, list):
        return ""
    parts = []
    for item in items[:_MAX_SUPPORTING_PARENTS]:
        if not isinstance(item, dict):
            continue
        parts.append(f"{item.get('node_id')}:{float(item.get('score') or 0):.2f}")
    return ", ".join(parts)
