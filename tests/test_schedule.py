"""Tests for ready-node scheduling."""

from __future__ import annotations

from tree.planner.schedule import start_ready_node_runs
from tree.state.models import NodeExecutionRecord, PipelineState


_DAG = {
    "nodes": [
        {"node_id": "n1", "collections": ["课件"], "source_order_index": 0},
        {"node_id": "n2", "collections": ["课件"], "source_order_index": 1},
        {"node_id": "n3", "collections": ["作业"], "source_order_index": 2},
    ],
    "edges": [
        {"from_node_id": "n1", "to_node_id": "n2", "relation": "prerequisite"},
        {"from_node_id": "n2", "to_node_id": "n3", "relation": "prerequisite"},
    ],
}


def test_root_node_ready_when_nothing_covered():
    state = start_ready_node_runs(PipelineState(), _DAG, covered_node_ids=set(), max_active=5)

    assert [item.node_id for item in state.node_executions] == ["n1"]
    assert state.node_executions[0].source_collections == ["课件"]
    snap = state.node_runs[0].coverage_snapshot
    assert snap.forbidden_future_node_ids == ["n2", "n3"]


def test_downstream_node_ready_after_parent_covered():
    state = start_ready_node_runs(PipelineState(), _DAG, covered_node_ids={"n1"}, max_active=5)

    assert [item.node_id for item in state.node_executions] == ["n2"]
    snap = state.node_runs[0].coverage_snapshot
    assert snap.snapshot_visible_ancestor_node_ids == ["n1"]


def test_max_active_limits_concurrent_node_runs():
    dag = {
        "nodes": [
            {"node_id": "x", "collections": []},
            {"node_id": "y", "collections": []},
        ],
        "edges": [],
    }
    state = start_ready_node_runs(PipelineState(), dag, covered_node_ids=set(), max_active=1)

    assert len(state.node_executions) == 1


def test_does_not_reschedule_existing_node():
    state = PipelineState(node_executions=[NodeExecutionRecord(node_id="n1", status="in_progress")])

    state = start_ready_node_runs(state, _DAG, covered_node_ids=set(), max_active=5)

    assert [item.node_id for item in state.node_executions] == ["n1"]
