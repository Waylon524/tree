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
