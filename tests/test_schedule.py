"""Tests for ready-branch scheduling (step 6)."""

from __future__ import annotations

from tree.planner.schedule import start_ready_branch_runs
from tree.state.models import BranchExecutionRecord, PipelineState

_BRANCHES = [
    {
        "branch_id": "kb:1",
        "node_ids": ["n1", "n2"],
        "coverage_node_ids": ["n1", "n2"],
        "start_node_id": "n1",
        "end_node_id": "n2",
        "upstream_branch_ids": [],
        "downstream_branch_ids": ["kb:2"],
        "display_order": 0,
    },
    {
        "branch_id": "kb:2",
        "node_ids": ["n3"],
        "coverage_node_ids": ["n3"],
        "start_node_id": "n3",
        "end_node_id": "n3",
        "upstream_branch_ids": ["kb:1"],
        "downstream_branch_ids": [],
        "display_order": 1,
    },
]
_DAG = {
    "nodes": [
        {"node_id": "n1", "collections": ["课件"]},
        {"node_id": "n2", "collections": ["课件"]},
        {"node_id": "n3", "collections": ["作业"]},
    ],
    "edges": [{"from_node_id": "n2", "to_node_id": "n3", "relation": "prerequisite", "confidence": 1.0}],
}


def test_only_root_branch_ready_when_nothing_covered():
    state = start_ready_branch_runs(
        PipelineState(), _BRANCHES, _DAG, covered_node_ids=set(), max_active=2
    )
    assert [be.branch_id for be in state.branch_executions] == ["kb:1"]
    exec_record = state.branch_executions[0]
    assert exec_record.source_collections == ["课件"]
    snap = state.branch_runs[0].coverage_snapshot
    assert snap.forbidden_future_branch_ids == ["kb:2"]


def test_downstream_branch_ready_after_upstream_covered():
    state = start_ready_branch_runs(
        PipelineState(), _BRANCHES, _DAG, covered_node_ids={"n1", "n2"}, max_active=2
    )
    assert [be.branch_id for be in state.branch_executions] == ["kb:2"]
    snap = state.branch_runs[0].coverage_snapshot
    assert snap.snapshot_visible_ancestor_node_ids == ["n2"]
    assert snap.completed_branch_ids == ["kb:1"]


def test_max_active_limits_concurrent_runs():
    branches = [
        {"branch_id": "a", "node_ids": ["x"], "coverage_node_ids": ["x"], "start_node_id": "x",
         "end_node_id": "x", "upstream_branch_ids": [], "downstream_branch_ids": [], "display_order": 0},
        {"branch_id": "b", "node_ids": ["y"], "coverage_node_ids": ["y"], "start_node_id": "y",
         "end_node_id": "y", "upstream_branch_ids": [], "downstream_branch_ids": [], "display_order": 1},
    ]
    dag = {"nodes": [{"node_id": "x", "collections": []}, {"node_id": "y", "collections": []}], "edges": []}
    state = start_ready_branch_runs(PipelineState(), branches, dag, covered_node_ids=set(), max_active=1)
    assert len(state.branch_executions) == 1


def test_does_not_reschedule_existing_branch():
    state = PipelineState(
        branch_executions=[BranchExecutionRecord(execution_path="kb:1", status="in_progress", branch_id="kb:1")]
    )
    state = start_ready_branch_runs(state, _BRANCHES, _DAG, covered_node_ids=set(), max_active=2)
    # kb:1 already scheduled; kb:2 not ready -> no new executions
    assert len(state.branch_executions) == 1
