"""DAG branch planning and BranchRun scheduling for TREE."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tree.io import paths
from tree.state.models import BranchRunRecord, CoverageSnapshot, PipelineState


@dataclass(frozen=True)
class BranchPriorScope:
    """Finished outputs visible to one BranchRun at a specific coverage start."""

    allowed_paths: set[str]
    ancestor_node_ids: set[str]
    current_branch_prior_node_ids: set[str]
    start_node_id: str


def load_knowledge_dag(root: Path) -> dict[str, Any]:
    return _load_json(paths.knowledge_dag_path(root), {"version": 1, "nodes": [], "edges": [], "diagnostics": []})


def load_knowledge_branches(root: Path) -> dict[str, Any]:
    return _load_json(paths.knowledge_branches_path(root), {"version": 1, "branches": [], "diagnostics": []})


def save_knowledge_dag(root: Path, dag: dict[str, Any]) -> None:
    _save_json(paths.knowledge_dag_path(root), dag)


def save_knowledge_branches(root: Path, branches: dict[str, Any]) -> None:
    _save_json(paths.knowledge_branches_path(root), branches)


def rebuild_branch_plan(
    root: Path,
    knowledge_graph: dict[str, Any],
    ledger: dict[str, Any],
    *,
    running_branch_ids: set[str] | None = None,
) -> dict[str, Any]:
    """Build and persist KnowledgeDAG plus executable KnowledgeBranches."""
    running_branch_ids = running_branch_ids or set()
    dag = build_knowledge_dag(knowledge_graph)
    branches = build_knowledge_branches(dag, ledger, running_branch_ids=running_branch_ids)
    save_knowledge_dag(root, dag)
    save_knowledge_branches(root, branches)
    return {"dag": dag, "branches": branches}


def build_knowledge_dag(knowledge_graph: dict[str, Any]) -> dict[str, Any]:
    nodes = [
        _knowledge_node_payload(node)
        for node in knowledge_graph.get("nodes", [])
        if isinstance(node, dict) and node.get("kind") in {"candidate", "knowledge_node"}
    ]
    node_ids = {node["node_id"] for node in nodes}
    diagnostics = [
        item
        for item in knowledge_graph.get("diagnostics", [])
        if isinstance(item, dict)
    ]
    diagnostics.extend(_canonical_merge_leak_diagnostics(knowledge_graph))
    edges = [
        _dag_edge(edge)
        for edge in knowledge_graph.get("edges", [])
        if isinstance(edge, dict)
        and edge.get("relation") == "prerequisite"
        and edge.get("from") in node_ids
        and edge.get("to") in node_ids
    ]
    edges, cycle_diagnostics = _break_cycles(nodes, edges)
    diagnostics.extend(cycle_diagnostics)
    structural_roles = _structural_roles(nodes, edges)
    return {
        "version": 1,
        "kind": "knowledge_dag",
        "nodes": nodes,
        "edges": edges,
        "structural_roles": structural_roles,
        "diagnostics": diagnostics,
        "cluster_quality": knowledge_graph.get("cluster_quality", {}),
    }


def build_knowledge_branches(
    dag: dict[str, Any],
    ledger: dict[str, Any],
    *,
    running_branch_ids: set[str] | None = None,
) -> dict[str, Any]:
    running_branch_ids = running_branch_ids or set()
    node_ids = [node["node_id"] for node in dag.get("nodes", []) if isinstance(node, dict)]
    node_lookup = {node["node_id"]: node for node in dag.get("nodes", []) if isinstance(node, dict)}
    outgoing, incoming = _adjacency(dag.get("edges", []))
    roles = dag.get("structural_roles", {})
    blocked_nodes = _diagnostic_node_ids(dag.get("diagnostics", []))
    branch_paths = _branch_paths(node_ids, outgoing, roles)
    covered_nodes = _covered_node_ids(ledger, node_lookup)
    initial = []
    for path in branch_paths:
        branch = _branch_payload(path, node_lookup, incoming, roles)
        if branch["branch_id"] in running_branch_ids:
            branch["status"] = "running"
        elif set(branch["node_ids"]) & blocked_nodes:
            branch["status"] = "blocked"
            branch["blocked_reason"] = "canonical_merge_leak"
        elif any(not node_lookup.get(node_id, {}).get("schedulable", True) for node_id in branch["coverage_node_ids"]):
            branch["status"] = "blocked"
            branch["blocked_reason"] = "not_schedulable"
        elif set(branch["coverage_node_ids"]).issubset(covered_nodes):
            branch["status"] = "complete"
        else:
            branch["status"] = "blocked"
        branch["coverage"] = {
            "covered_node_ids": [node_id for node_id in branch["coverage_node_ids"] if node_id in covered_nodes],
            "missing_node_ids": [node_id for node_id in branch["coverage_node_ids"] if node_id not in covered_nodes],
            "complete": set(branch["coverage_node_ids"]).issubset(covered_nodes),
        }
        initial.append(branch)
    branches = _attach_branch_dependencies(initial)
    completed = {branch["branch_id"] for branch in branches if branch["status"] == "complete"}
    for branch in branches:
        if branch["status"] != "blocked" or branch.get("blocked_reason"):
            continue
        if all(upstream in completed for upstream in branch.get("upstream_branch_ids", [])):
            branch["status"] = "ready"
    branches.sort(key=lambda item: _branch_sort_key(item, roles))
    return {
        "version": 1,
        "kind": "knowledge_branches",
        "branches": branches,
        "diagnostics": dag.get("diagnostics", []),
    }


def start_ready_branch_runs(
    state: PipelineState,
    branches_doc: dict[str, Any],
    ledger: dict[str, Any],
    *,
    max_active_branch_runs: int = 2,
    now: str = "",
) -> PipelineState:
    """Start ready BranchRuns up to the configured concurrency limit."""
    existing = list(state.branch_runs)
    running_ids = {run.branch_id for run in existing if run.status == "running"}
    running_count = len(running_ids)
    slots = max(0, max_active_branch_runs - running_count)
    if slots <= 0:
        return state
    branches = [
        branch
        for branch in branches_doc.get("branches", [])
        if isinstance(branch, dict)
        and branch.get("status") == "ready"
        and branch.get("branch_id") not in running_ids
    ]
    branches.sort(key=lambda item: (-float(item.get("priority") or 0), item.get("branch_id", "")))
    completed = [
        branch.get("branch_id")
        for branch in branches_doc.get("branches", [])
        if isinstance(branch, dict) and branch.get("status") == "complete"
    ]
    additions = [
        BranchRunRecord(
            branch_id=branch["branch_id"],
            run_id=_run_id(branch["branch_id"], now, existing),
            status="running",
            coverage_snapshot=_coverage_snapshot(branch, branches_doc, ledger, completed, now),
        )
        for branch in branches[:slots]
    ]
    if not additions:
        return state
    return state.model_copy(update={"branch_runs": [*existing, *additions]})


def branch_context_for_run(
    run: BranchRunRecord,
    branches_doc: dict[str, Any],
    ledger: dict[str, Any],
) -> str:
    """Format ActiveBranch context using the run's fixed coverage snapshot."""
    _ = ledger
    branch = next(
        (item for item in branches_doc.get("branches", []) if item.get("branch_id") == run.branch_id),
        {},
    )
    snapshot = run.coverage_snapshot
    finished = ", ".join(snapshot.finished_output_ids) or "none"
    covered = ", ".join(snapshot.covered_node_ids) or "none"
    forbidden = ", ".join(snapshot.forbidden_future_branch_ids) or "none"
    return "\n".join(
        [
            "## ActiveBranch Context",
            f"Branch run id: {run.run_id}",
            f"Branch id: {run.branch_id}",
            f"Start node: {branch.get('start_node_id') or 'unknown'}",
            f"End node: {branch.get('end_node_id') or 'unknown'}",
            f"Branch nodes: {', '.join(branch.get('node_ids', [])) or 'none'}",
            f"Coverage target nodes: {', '.join(branch.get('coverage_node_ids', [])) or 'none'}",
            f"Snapshot started at: {snapshot.started_at or 'unknown'}",
            f"Snapshot finished outputs: {finished}",
            f"Snapshot covered nodes: {covered}",
            f"Forbidden future/sibling branches: {forbidden}",
            "Examiner may choose a contiguous coverage span inside this branch only.",
        ]
    ).strip()


def validate_branch_covered_node_ids(covered_node_ids: list[str], branch: dict[str, Any]) -> list[str]:
    """Validate that examiner-declared node ids are a contiguous span from first missing."""
    normalized = _unique(covered_node_ids)
    if not normalized:
        raise ValueError("Covered_Node_IDs must contain at least one KnowledgeNode id")
    coverage_nodes = _string_list(branch.get("coverage_node_ids"))
    outside = [node_id for node_id in normalized if node_id not in coverage_nodes]
    if outside:
        raise ValueError(f"Covered_Node_IDs outside active branch: {', '.join(outside)}")
    missing = _string_list((branch.get("coverage") or {}).get("missing_node_ids")) or coverage_nodes
    first_missing = missing[0] if missing else ""
    if first_missing and normalized[0] != first_missing:
        raise ValueError(
            f"Covered_Node_IDs must start at first missing node {first_missing}; got {normalized[0]}"
        )
    start_index = coverage_nodes.index(normalized[0])
    expected = coverage_nodes[start_index : start_index + len(normalized)]
    if normalized != expected:
        raise ValueError("Covered_Node_IDs must be contiguous inside the active branch")
    return normalized


def build_branch_prior_scope(
    run: BranchRunRecord,
    dag: dict[str, Any],
    branches_doc: dict[str, Any],
    ledger: dict[str, Any],
    *,
    covered_node_ids: list[str] | None = None,
) -> BranchPriorScope:
    """Return prior finished paths visible to examiner/writer/student for one branch span."""
    branch = next(
        (
            item
            for item in branches_doc.get("branches", [])
            if isinstance(item, dict) and item.get("branch_id") == run.branch_id
        ),
        {},
    )
    coverage_nodes = _string_list(branch.get("coverage_node_ids"))
    start_node = (covered_node_ids or _string_list((branch.get("coverage") or {}).get("missing_node_ids")) or coverage_nodes)
    start_node_id = start_node[0] if start_node else ""
    ancestor_nodes = _ancestor_node_ids(dag, start_node_id)
    current_prior_nodes = _current_branch_prior_nodes(branch, start_node_id)
    snapshot_paths = {
        item.removeprefix("finished:")
        for item in run.coverage_snapshot.finished_output_ids
        if item
    }
    allowed_paths: set[str] = set()
    for record in ledger.get("records", []):
        if not isinstance(record, dict):
            continue
        path = str(record.get("path") or "")
        if not path:
            continue
        record_nodes = _record_covered_node_ids(record)
        in_snapshot_upstream = path in snapshot_paths and bool(record_nodes & ancestor_nodes)
        in_current_chapter_path = bool(run.chapter_name) and (
            record.get("chapter") == run.chapter_name
            or path.startswith(f"outputs/{run.chapter_name}/")
        )
        in_current_branch_prefix = (
            in_current_chapter_path
            and bool(record_nodes & current_prior_nodes)
        )
        if in_snapshot_upstream or in_current_branch_prefix:
            allowed_paths.add(path)
    return BranchPriorScope(
        allowed_paths=allowed_paths,
        ancestor_node_ids=ancestor_nodes,
        current_branch_prior_node_ids=current_prior_nodes,
        start_node_id=start_node_id,
    )


def _knowledge_node_payload(node: dict[str, Any]) -> dict[str, Any]:
    return {
        "node_id": str(node.get("node_id") or ""),
        "title": str(node.get("title") or ""),
        "status": str(node.get("status") or ""),
        "core_concepts": _string_list(node.get("core_concepts")),
        "prerequisites": _string_list(node.get("prerequisites")),
        "hit_chunks": _string_list(node.get("hit_chunks")),
        "source_collections": _string_list(node.get("source_collections")),
        "estimated_output_lines": int(node.get("estimated_output_lines") or 0),
        "title_quality": str(node.get("title_quality") or "clean"),
        "schedulable": bool(node.get("schedulable", True)),
        "blocked_reason": str(node.get("blocked_reason") or ""),
        "merged_group_ids": _string_list(node.get("merged_group_ids")),
        "pending_merge_group_ids": _string_list(node.get("pending_merge_group_ids")),
    }


def _dag_edge(edge: dict[str, Any]) -> dict[str, Any]:
    scores = edge.get("scores") if isinstance(edge.get("scores"), dict) else {}
    confidence = max(
        float(scores.get("prerequisite") or 0),
        float(scores.get("prerequisite_ab") or 0),
        float(scores.get("prerequisite_ba") or 0),
        float(edge.get("confidence") or 0),
    )
    return {
        "from": edge.get("from"),
        "to": edge.get("to"),
        "relation": "prerequisite",
        "confidence": round(confidence, 4),
        "scores": scores,
    }


def _canonical_merge_leak_diagnostics(graph: dict[str, Any]) -> list[dict[str, Any]]:
    diagnostics = []
    for edge in graph.get("edges", []):
        if not isinstance(edge, dict):
            continue
        relation = edge.get("relation")
        scores = edge.get("scores") if isinstance(edge.get("scores"), dict) else {}
        high_sim_adjacent = relation == "adjacent" and (
            float(scores.get("concept") or 0) >= 0.55
            or float(scores.get("chunk") or 0) >= 0.62
            or max(float(scores.get("prerequisite_ab") or 0), float(scores.get("prerequisite_ba") or 0)) >= 0.55
        )
        if relation not in {"duplicate", "merge_needed"} and not high_sim_adjacent:
            continue
        left = str(edge.get("from") or "")
        right = str(edge.get("to") or "")
        if left.startswith("candidate:") and right.startswith("candidate:"):
            diagnostics.append(
                {
                    "kind": "canonical_merge_leak",
                    "nodes": [left, right],
                    "edge": f"{left}->{right}",
                    "relation": edge.get("relation"),
                    "reason": "Planned KnowledgeNodes still overlap after canonical merge.",
                }
            )
    return diagnostics


def _break_cycles(
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    diagnostics = []
    node_ids = {node["node_id"] for node in nodes}
    remaining = list(edges)
    while True:
        cycle = _find_cycle(node_ids, remaining)
        if not cycle:
            return remaining, diagnostics
        cycle_edges = [edge for edge in remaining if (edge.get("from"), edge.get("to")) in cycle]
        remove = sorted(
            cycle_edges,
            key=lambda edge: (float(edge.get("confidence") or 0), str(edge.get("from")), str(edge.get("to"))),
        )[0]
        remaining.remove(remove)
        diagnostics.append(
            {
                "kind": "dag_cycle_diagnostic",
                "removed_edge": f"{remove.get('from')}->{remove.get('to')}",
                "reason": "Removed lowest-confidence edge to keep KnowledgeDAG acyclic.",
            }
        )


def _find_cycle(node_ids: set[str], edges: list[dict[str, Any]]) -> set[tuple[str, str]]:
    outgoing, _ = _adjacency(edges)
    visiting: set[str] = set()
    visited: set[str] = set()
    stack: list[str] = []

    def visit(node: str) -> set[tuple[str, str]]:
        visiting.add(node)
        stack.append(node)
        for child in outgoing.get(node, []):
            if child in visited:
                continue
            if child in visiting:
                cycle_nodes = stack[stack.index(child):] + [child]
                return set(zip(cycle_nodes, cycle_nodes[1:]))
            found = visit(child)
            if found:
                return found
        stack.pop()
        visiting.remove(node)
        visited.add(node)
        return set()

    for node in sorted(node_ids):
        if node in visited:
            continue
        found = visit(node)
        if found:
            return found
    return set()


def _structural_roles(nodes: list[dict[str, Any]], edges: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    outgoing, incoming = _adjacency(edges)
    roles = {}
    for node in nodes:
        node_id = node["node_id"]
        in_degree = len(incoming.get(node_id, []))
        out_degree = len(outgoing.get(node_id, []))
        is_root = in_degree == 0
        is_tip = out_degree == 0
        is_branch = in_degree != 1 or out_degree != 1
        if is_root:
            role = "root"
        elif is_tip:
            role = "tip"
        elif is_branch:
            role = "branch"
        else:
            role = "linear"
        roles[node_id] = {
            "role": role,
            "in_degree": in_degree,
            "out_degree": out_degree,
            "is_root": is_root,
            "is_branch_node": is_branch,
            "is_tip": is_tip,
        }
    return roles


def _branch_paths(
    node_ids: list[str],
    outgoing: dict[str, list[str]],
    roles: dict[str, dict[str, Any]],
) -> list[list[str]]:
    starts = [
        node_id
        for node_id in node_ids
        if roles.get(node_id, {}).get("is_branch_node")
    ]
    paths = []
    for start in sorted(starts):
        children = outgoing.get(start, [])
        if not children:
            if roles.get(start, {}).get("is_root"):
                paths.append([start])
            continue
        for child in sorted(children):
            path = [start, child]
            current = child
            while not roles.get(current, {}).get("is_branch_node") and outgoing.get(current):
                current = outgoing[current][0]
                path.append(current)
            paths.append(path)
    return paths


def _branch_payload(
    path: list[str],
    node_lookup: dict[str, dict[str, Any]],
    incoming: dict[str, list[str]],
    roles: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    start = path[0]
    end = path[-1]
    start_is_root = roles.get(start, {}).get("is_root", False)
    coverage_nodes = list(path if start_is_root else path[1:])
    estimated = sum(int(node_lookup.get(node, {}).get("estimated_output_lines") or 0) for node in path)
    source_collections = _unique(
        collection
        for node in path
        for collection in node_lookup.get(node, {}).get("source_collections", [])
    )
    branch = {
        "branch_id": _branch_id(path),
        "start_node_id": start,
        "end_node_id": end,
        "node_ids": path,
        "coverage_node_ids": coverage_nodes,
        "source_collections": source_collections,
        "upstream_branch_ids": [],
        "downstream_branch_ids": [],
        "required_start_node_ids": sorted(incoming.get(start, [])),
        "status": "blocked",
        "coverage": {},
        "priority": 1.0 if start_is_root else 0.6,
        "length_stats": {
            "node_count": len(path),
            "coverage_node_count": len(coverage_nodes),
            "estimated_output_lines": estimated,
        },
    }
    return branch


def _attach_branch_dependencies(branches: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_start: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_end: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for branch in branches:
        by_start[branch["start_node_id"]].append(branch)
        by_end[branch["end_node_id"]].append(branch)
    for branch in branches:
        branch["upstream_branch_ids"] = sorted(
            item["branch_id"]
            for item in by_end.get(branch["start_node_id"], [])
            if item["branch_id"] != branch["branch_id"]
        )
        branch["downstream_branch_ids"] = sorted(
            item["branch_id"]
            for item in by_start.get(branch["end_node_id"], [])
            if item["branch_id"] != branch["branch_id"]
        )
    return branches


def _coverage_snapshot(
    branch: dict[str, Any],
    branches_doc: dict[str, Any],
    ledger: dict[str, Any],
    completed_branch_ids: list[str],
    now: str,
) -> CoverageSnapshot:
    finished_ids = [
        f"finished:{record.get('path')}"
        for record in ledger.get("records", [])
        if isinstance(record, dict) and record.get("path")
    ]
    covered_nodes = _covered_node_ids(ledger, {})
    future = [
        item.get("branch_id")
        for item in branches_doc.get("branches", [])
        if isinstance(item, dict)
        and item.get("branch_id") != branch.get("branch_id")
        and item.get("status") not in {"complete"}
    ]
    return CoverageSnapshot(
        started_at=now,
        finished_output_ids=sorted(finished_ids),
        covered_node_ids=sorted(covered_nodes),
        completed_branch_ids=sorted(completed_branch_ids),
        snapshot_visible_ancestor_node_ids=sorted(covered_nodes),
        forbidden_future_branch_ids=sorted(item for item in future if item),
    )


def _covered_node_ids(ledger: dict[str, Any], node_lookup: dict[str, dict[str, Any]]) -> set[str]:
    _ = node_lookup
    covered = set()
    for record in ledger.get("records", []):
        if not isinstance(record, dict):
            continue
        covered.update(_record_covered_node_ids(record))
    return covered


def _record_covered_node_ids(record: dict[str, Any]) -> set[str]:
    nodes = set(_string_list(record.get("covered_node_ids")))
    graph_node_id = str(record.get("graph_node_id") or "")
    if graph_node_id:
        nodes.add(graph_node_id)
    return nodes


def _ancestor_node_ids(dag: dict[str, Any], node_id: str) -> set[str]:
    _, incoming = _adjacency(dag.get("edges", []))
    ancestors: set[str] = set()
    stack = list(incoming.get(node_id, []))
    while stack:
        current = stack.pop()
        if current in ancestors:
            continue
        ancestors.add(current)
        stack.extend(incoming.get(current, []))
    return ancestors


def _current_branch_prior_nodes(branch: dict[str, Any], start_node_id: str) -> set[str]:
    coverage_nodes = _string_list(branch.get("coverage_node_ids"))
    if start_node_id not in coverage_nodes:
        return set()
    return set(coverage_nodes[: coverage_nodes.index(start_node_id)])


def _diagnostic_node_ids(diagnostics: list[dict[str, Any]]) -> set[str]:
    blocked = set()
    for item in diagnostics:
        if not isinstance(item, dict) or item.get("kind") not in {
            "canonical_merge_leak",
            "canonical_merge_pending",
        }:
            continue
        blocked.update(str(node) for node in item.get("nodes", []) if node)
    return blocked


def _adjacency(edges: list[dict[str, Any]]) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    outgoing: dict[str, list[str]] = defaultdict(list)
    incoming: dict[str, list[str]] = defaultdict(list)
    for edge in edges:
        source = str(edge.get("from") or "")
        target = str(edge.get("to") or "")
        if not source or not target:
            continue
        outgoing[source].append(target)
        incoming[target].append(source)
    for mapping in (outgoing, incoming):
        for key, values in list(mapping.items()):
            mapping[key] = sorted(set(values))
    return outgoing, incoming


def _branch_id(path: list[str]) -> str:
    basis = "->".join(path)
    return f"branch:{hashlib.sha1(basis.encode('utf-8')).hexdigest()[:12]}"


def _run_id(branch_id: str, now: str, existing: list[BranchRunRecord]) -> str:
    basis = f"{branch_id}:{now}:{len(existing)}"
    return f"run:{hashlib.sha1(basis.encode('utf-8')).hexdigest()[:12]}"


def _branch_sort_key(branch: dict[str, Any], roles: dict[str, dict[str, Any]]) -> tuple[Any, ...]:
    start = branch.get("start_node_id", "")
    return (
        branch.get("status") != "ready",
        not roles.get(start, {}).get("is_root", False),
        -float(branch.get("priority") or 0),
        branch.get("branch_id", ""),
    )


def _string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value] if value else []
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _unique(values: Any) -> list[str]:
    seen = set()
    result = []
    for value in values:
        value = str(value).strip()
        if not value or value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _load_json(path: Path, fallback: dict[str, Any]) -> dict[str, Any]:
    if not path.exists():
        return fallback
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return fallback
    return loaded if isinstance(loaded, dict) else fallback


def _save_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)
