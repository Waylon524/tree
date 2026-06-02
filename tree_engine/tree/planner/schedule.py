"""Ready-node scheduling for NodeRun execution.

A node is ready when every direct prerequisite parent has already been covered
by the finished-output ledger. The scheduler is deterministic and only starts
nodes that are not already scheduled.
"""

from __future__ import annotations

from collections import deque
from typing import Any

from tree.state.models import CoverageSnapshot, NodeExecutionRecord, NodeRunRecord, PipelineState


def start_ready_node_runs(
    state: PipelineState,
    dag: dict[str, Any],
    *,
    covered_node_ids: set[str],
    max_active: int,
    finished_output_ids: list[str] | None = None,
    now: str = "",
) -> PipelineState:
    nodes = sorted(
        dag.get("nodes") or [],
        key=lambda n: (n.get("source_order_index", 0), n.get("node_id", "")),
    )
    parents, children = _prereq_adjacency(dag)
    scheduled = {item.node_id for item in state.node_executions}
    active = sum(1 for item in state.node_executions if item.status == "in_progress")

    for node in nodes:
        if active >= max_active:
            break
        node_id = node.get("node_id", "")
        if not node_id or node_id in covered_node_ids or node_id in scheduled:
            continue
        direct_parents = set(parents.get(node_id, []))
        if not direct_parents <= covered_node_ids:
            continue

        run_id = f"{node_id}::run"
        ancestors = _ancestors(parents, node_id)
        snapshot = CoverageSnapshot(
            started_at=now,
            finished_output_ids=list(finished_output_ids or []),
            covered_node_ids=sorted(covered_node_ids),
            snapshot_visible_ancestor_node_ids=sorted(ancestors & covered_node_ids),
            forbidden_future_node_ids=sorted(_descendants(children, node_id)),
        )
        state.node_executions.append(
            NodeExecutionRecord(
                node_id=node_id,
                status="in_progress",
                source_collections=sorted(node.get("collections") or []),
                node_run_id=run_id,
            )
        )
        state.node_runs.append(
            NodeRunRecord(
                node_id=node_id,
                run_id=run_id,
                status="running",
                coverage_snapshot=snapshot,
            )
        )
        scheduled.add(node_id)
        active += 1

    return state


def _prereq_adjacency(dag: dict[str, Any]) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    parents: dict[str, list[str]] = {}
    children: dict[str, list[str]] = {}
    for edge in dag.get("edges") or []:
        if edge.get("relation") != "prerequisite":
            continue
        src, dst = edge.get("from_node_id", ""), edge.get("to_node_id", "")
        if not src or not dst:
            continue
        parents.setdefault(dst, []).append(src)
        children.setdefault(src, []).append(dst)
    for values in parents.values():
        values.sort()
    for values in children.values():
        values.sort()
    return parents, children


def _ancestors(parents: dict[str, list[str]], node_id: str) -> set[str]:
    found: set[str] = set()
    queue = deque(parents.get(node_id, []))
    while queue:
        current = queue.popleft()
        if current in found:
            continue
        found.add(current)
        queue.extend(parents.get(current, []))
    return found


def _descendants(children: dict[str, list[str]], node_id: str) -> set[str]:
    found: set[str] = set()
    queue = deque(children.get(node_id, []))
    while queue:
        current = queue.popleft()
        if current in found:
            continue
        found.add(current)
        queue.extend(children.get(current, []))
    return found


__all__ = ["start_ready_node_runs"]
