from pathlib import Path

from tree.engine import _mark_inventory_progress, _mark_planner_progress
from tree.observability.progress import ProgressTracker


def test_learning_stage_writes_branch_run_progress_and_keeps_legacy_loop(tmp_path: Path) -> None:
    tracker = ProgressTracker(tmp_path)

    tracker.learning_stage(
        stage="student_blind_test",
        stage_label="Student blind test",
        stage_index=3,
        stage_total=6,
        execution_path="tree-001/branch-001",
        tree_id="tree-001",
        branch_id="branch-001",
        branch_run_id="run-001",
        file_seq="02",
        span_title="波动光学",
        iteration=2,
        message="Student is answering",
    )

    state = tracker.read()

    assert state["learning_loop"]["stage"] == "student_blind_test"
    assert state["learning_loop"]["span_title"] == "波动光学"
    assert state["branch_run_progress"]["run-001"]["stage"] == "student_blind_test"
    assert state["branch_run_progress"]["run-001"]["tree_id"] == "tree-001"
    assert state["branch_run_progress"]["run-001"]["branch_id"] == "branch-001"
    assert state["branch_run_progress"]["run-001"]["execution_path"] == "tree-001/branch-001"
    assert state["branch_run_progress"]["run-001"]["file_seq"] == "02"
    assert state["branch_run_progress"]["run-001"]["span_title"] == "波动光学"
    assert state["branch_run_progress"]["run-001"]["iteration"] == 2
    assert state["branch_run_progress"]["run-001"]["updated_at"]


def test_learning_stage_uses_execution_path_as_branch_progress_key_when_run_id_missing(tmp_path: Path) -> None:
    tracker = ProgressTracker(tmp_path)

    tracker.learning_stage(
        stage="writer_drafting",
        stage_label="Writer drafting",
        stage_index=5,
        stage_total=6,
        execution_path="tree-001/branch-002",
    )

    state = tracker.read()

    assert state["branch_run_progress"]["tree-001/branch-002"]["stage"] == "writer_drafting"


def test_planner_stage_writes_independent_planner_progress(tmp_path: Path) -> None:
    tracker = ProgressTracker(tmp_path)

    tracker.planner_stage(
        stage="knowledge_dag",
        stage_label="Building KnowledgeDAG",
        stage_index=4,
        details={"nodes": 12},
        diagnostics=[{"type": "canonical_merge_pending", "count": 2}],
    )

    state = tracker.read()

    assert state["phase"] == "planner"
    assert state["message"] == "Building KnowledgeDAG"
    assert state["planner_progress"]["stage"] == "knowledge_dag"
    assert state["planner_progress"]["stage_index"] == 4
    assert state["planner_progress"]["stage_total"] == 6
    assert state["planner_progress"]["details"] == {"nodes": 12}
    assert state["planner_progress"]["diagnostics"][0]["type"] == "canonical_merge_pending"
    assert state["planner_progress"]["updated_at"]


def test_learning_stage_does_not_overwrite_planner_progress(tmp_path: Path) -> None:
    tracker = ProgressTracker(tmp_path)

    tracker.planner_stage(stage="knowledge_branches", stage_label="Building KnowledgeBranches", stage_index=5)
    tracker.learning_stage(
        stage="student_blind_test",
        stage_label="Student blind test",
        stage_index=3,
        stage_total=6,
        execution_path="tree-001/branch-001",
    )

    state = tracker.read()

    assert state["learning_loop"]["stage"] == "student_blind_test"
    assert state["planner_progress"]["stage"] == "knowledge_branches"
    assert state["planner_progress"]["stage_index"] == 5


def test_planner_marker_helpers_write_real_six_stage_indexes(tmp_path: Path) -> None:
    tracker = ProgressTracker(tmp_path)

    _mark_inventory_progress(tracker)
    assert tracker.read()["planner_progress"]["stage"] == "source_inventory"
    assert tracker.read()["planner_progress"]["stage_index"] == 1

    _mark_planner_progress(
        tracker,
        stage="schedule_branch_runs",
        label="Scheduling BranchRuns",
        stage_index=6,
        message="Planner is scheduling ready BranchRuns",
    )

    state = tracker.read()

    assert state["planner_progress"]["stage"] == "schedule_branch_runs"
    assert state["planner_progress"]["stage_index"] == 6
    assert state["planner_progress"]["stage_total"] == 6
