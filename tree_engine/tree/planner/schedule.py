"""Ready-branch scheduling.

A branch is ready when all its upstream branches are completed and it is neither
completed nor already scheduled. Newly started branches get a frozen
CoverageSnapshot (visible prerequisite ancestors + forbidden downstream branches)
so the BranchRun's prior scope is deterministic.

Pure function (ledger I/O happens in the caller). See docs/REBUILD-DESIGN.md §4/§6.
"""

from __future__ import annotations

from collections import deque
from typing import Any

from tree.state.models import (
    BranchExecutionRecord,
    BranchRunRecord,
    CoverageSnapshot,
    PipelineState,
)


def start_ready_branch_runs(
    state: PipelineState,
    branches: list[dict[str, Any]],
    dag: dict[str, Any],
    *,
    covered_node_ids: set[str],
    max_active: int,
    finished_output_ids: list[str] | None = None,
    now: str = "",
) -> PipelineState:
    by_id = {b["branch_id"]: b for b in branches}
    completed = {
        b["branch_id"]
        for b in branches
        if b.get("coverage_node_ids") and set(b["coverage_node_ids"]) <= covered_node_ids
    }
    scheduled = {be.branch_id for be in state.branch_executions if be.branch_id}
    active = sum(1 for be in state.branch_executions if be.status == "in_progress")

    node_collections = _node_collection_lookup(dag)
    prereq_edges = [e for e in dag.get("edges", []) if e.get("relation") == "prerequisite"]

    for branch in sorted(branches, key=lambda b: b.get("display_order", 0)):
        if active >= max_active:
            break
        branch_id = branch["branch_id"]
        if branch_id in scheduled or branch_id in completed:
            continue
        upstream = branch.get("upstream_branch_ids") or []
        if not all(up in completed for up in upstream):
            continue

        run_id = f"{branch_id}::run"
        snapshot = CoverageSnapshot(
            started_at=now,
            finished_output_ids=list(finished_output_ids or []),
            covered_node_ids=sorted(covered_node_ids),
            completed_branch_ids=sorted(completed),
            snapshot_visible_ancestor_node_ids=sorted(
                _prereq_ancestors(prereq_edges, branch.get("start_node_id", ""))
            ),
            forbidden_future_branch_ids=sorted(_downstream_closure(by_id, branch_id)),
        )
        state.branch_executions.append(
            BranchExecutionRecord(
                execution_path=branch_id,
                status="in_progress",
                coverage_node_ids=list(branch.get("coverage_node_ids") or []),
                current_start_node_id=branch.get("start_node_id"),
                source_collections=sorted(
                    {c for nid in branch.get("node_ids", []) for c in node_collections.get(nid, [])}
                ),
                branch_id=branch_id,
                branch_run_id=run_id,
            )
        )
        state.branch_runs.append(
            BranchRunRecord(
                branch_id=branch_id,
                run_id=run_id,
                status="running",
                coverage_snapshot=snapshot,
                execution_path=branch_id,
            )
        )
        scheduled.add(branch_id)
        active += 1

    return state


def _node_collection_lookup(dag: dict[str, Any]) -> dict[str, list[str]]:
    return {n["node_id"]: list(n.get("collections") or []) for n in dag.get("nodes", [])}


def _prereq_ancestors(prereq_edges: list[dict[str, Any]], start_node_id: str) -> set[str]:
    """All nodes reachable backward along prerequisite edges from start_node_id."""
    if not start_node_id:
        return set()
    reverse: dict[str, list[str]] = {}
    for edge in prereq_edges:
        reverse.setdefault(edge["to_node_id"], []).append(edge["from_node_id"])
    ancestors: set[str] = set()
    queue = deque([start_node_id])
    while queue:
        node = queue.popleft()
        for parent in reverse.get(node, []):
            if parent not in ancestors:
                ancestors.add(parent)
                queue.append(parent)
    return ancestors


def _downstream_closure(by_id: dict[str, dict[str, Any]], branch_id: str) -> set[str]:
    closure: set[str] = set()
    queue = deque(by_id.get(branch_id, {}).get("downstream_branch_ids", []))
    while queue:
        bid = queue.popleft()
        if bid in closure:
            continue
        closure.add(bid)
        queue.extend(by_id.get(bid, {}).get("downstream_branch_ids", []))
    return closure
