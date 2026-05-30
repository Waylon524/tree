from pathlib import Path

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
