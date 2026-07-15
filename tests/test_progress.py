"""Tests for progress stage helpers used by ``tre watch``."""

from __future__ import annotations

from tree.observability.progress import ProgressTracker


def test_progress_tracker_initializes_all_watch_stages(tmp_path):
    progress = ProgressTracker(tmp_path)
    progress.reset()

    stages = progress.load()["stages"]

    assert list(stages) == ["ocr", "clean", "cut", "embed", "cluster", "link", "noderun"]
    assert stages["cluster"]["label"] == "Cluster"
    assert stages["ocr"]["done"] == 0
    assert "source_ingest" in progress.load()


def test_progress_tracker_updates_and_clamps_stage_counts(tmp_path):
    progress = ProgressTracker(tmp_path)
    progress.reset()

    progress.set_stage("ocr", total=2, done=0, status="running", active=["a", "b", "c", "d", "e", "f"])
    progress.advance_stage("ocr", step=3, message="done")

    stage = progress.load()["stages"]["ocr"]
    assert stage["done"] == 2
    assert stage["total"] == 2
    assert stage["status"] == "complete"
    assert stage["active"] == []
    assert stage["message"] == "done"

    progress.add_stage_total("clean", 2, status="running")
    progress.complete_stage("clean", "complete")
    clean = progress.load()["stages"]["clean"]
    assert clean["done"] == 2
    assert clean["status"] == "complete"


def test_begin_run_preserves_cumulative_counts_and_replaces_run_identity(tmp_path):
    progress = ProgressTracker(tmp_path)
    progress.reset()
    progress.set_stage("ocr", total=6, done=6, status="complete")
    progress.set_stage("noderun", total=124, done=123, status="failed", active="n124")

    run_id = progress.begin_run()
    state = progress.load()

    assert state["run_id"] == run_id
    assert state["phase"] == "running"
    assert state["stages"]["ocr"] == {
        "label": "OCR",
        "done": 6,
        "total": 6,
        "active": [],
        "status": "complete",
        "message": "",
    }
    assert state["stages"]["noderun"]["done"] == 123
    assert state["stages"]["noderun"]["total"] == 124
    assert state["stages"]["noderun"]["status"] == "pending"


def test_late_updates_from_an_old_run_are_ignored(tmp_path):
    old = ProgressTracker(tmp_path)
    old.reset()
    old.begin_run()
    old.set_stage("ocr", total=6, done=2, status="running")

    current = ProgressTracker(tmp_path)
    current.begin_run()
    current.set_stage("ocr", total=6, done=4, status="running")
    old.set_stage("ocr", done=3, status="failed", message="stale failure")

    stage = current.load()["stages"]["ocr"]
    assert stage["done"] == 4
    assert stage["status"] == "running"
    assert stage["message"] == ""


def test_record_error_is_structured_and_deduplicated(tmp_path):
    progress = ProgressTracker(tmp_path)
    progress.reset()
    run_id = progress.begin_run()
    progress.set_generation_id("gen:123")

    for retry_count in (0, 2):
        progress.record_error(
            stage="ocr",
            code="pdf_crypto_missing",
            resource="encrypted.pdf",
            message="AES provider is unavailable",
            retry_count=retry_count,
            recoverable=True,
            action="Install the crypto runtime and resume.",
        )

    errors = progress.load()["errors"]
    assert len(errors) == 1
    assert errors[0] == {
        "run_id": run_id,
        "generation_id": "gen:123",
        "stage": "ocr",
        "code": "pdf_crypto_missing",
        "resource": "encrypted.pdf",
        "message": "AES provider is unavailable",
        "retry_count": 2,
        "recoverable": True,
        "action": "Install the crypto runtime and resume.",
        "timestamp": errors[0]["timestamp"],
    }
