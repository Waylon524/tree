"""Tests for bounded, prompt-free LLM operation telemetry."""

from __future__ import annotations

from pathlib import Path

from tree.cli.commands.inspect import logs_text
from tree.observability import operation_log


def test_operation_log_rotates_and_remains_visible_to_cli(tmp_path, monkeypatch):
    monkeypatch.setattr(operation_log, "_MAX_LOG_BYTES", 180)
    log = operation_log.OperationLog(tmp_path)

    for index in range(8):
        log.append({"event": "complete", "operation": f"writer.create.{index}"})

    assert log.path.exists()
    assert log.path.with_name(f"{log.path.name}.1").exists()
    assert "llm-operations.jsonl" in logs_text(tmp_path)
    records = operation_log.recent_operation_events(tmp_path, limit=3)
    assert [item["operation"] for item in records] == [
        "writer.create.5",
        "writer.create.6",
        "writer.create.7",
    ]


def test_operation_log_ignores_unwritable_destination(tmp_path, monkeypatch):
    log = operation_log.OperationLog(tmp_path)

    def fail(*_args, **_kwargs):
        raise OSError("disk unavailable")

    monkeypatch.setattr(Path, "mkdir", fail)
    log.append({"event": "complete", "operation": "student.answer"})
