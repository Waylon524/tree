import asyncio
from pathlib import Path

import pytest

from tree.ingest import ingest_file
from tree.observability.progress import ProgressTracker


class TimeoutArchivist:
    async def structure(self, raw_text: str) -> str:
        raise TimeoutError("archivist timed out")


class MixedArchivist:
    def __init__(self) -> None:
        self.calls = 0

    async def structure(self, raw_text: str) -> str:
        self.calls += 1
        if self.calls == 1:
            return "# Structured first"
        raise TimeoutError("archivist timed out")


class CancelArchivist:
    async def structure(self, raw_text: str) -> str:
        raise asyncio.CancelledError()


def test_ingest_file_falls_back_to_raw_ocr_when_archivist_times_out(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_text = "# Raw OCR\n\nA section from OCR."
    source = tmp_path / "lesson.pdf"
    source.write_text("pdf placeholder", encoding="utf-8")
    monkeypatch.setattr("tree.ingest.extract_text", lambda path: raw_text)

    outputs = asyncio.run(
        ingest_file(
            source,
            tmp_path / "source",
            archivist_chunk_chars=10000,
            ocr_upload_interval_sec=0,
            archivist=TimeoutArchivist(),
        )
    )

    assert len(outputs) == 1
    assert outputs[0].read_text(encoding="utf-8") == raw_text


def test_ingest_file_falls_back_per_chunk_and_keeps_successful_structuring(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_text = "# First\n\nAlpha.\n\n# Second\n\nBeta."
    source = tmp_path / "lesson.pdf"
    source.write_text("pdf placeholder", encoding="utf-8")
    monkeypatch.setattr("tree.ingest.extract_text", lambda path: raw_text)

    outputs = asyncio.run(
        ingest_file(
            source,
            tmp_path / "source",
            archivist_chunk_chars=20,
            ocr_upload_interval_sec=0,
            archivist=MixedArchivist(),
        )
    )

    assert [path.name for path in outputs] == ["lesson__part-01.md", "lesson__part-02.md"]
    assert outputs[0].read_text(encoding="utf-8") == "# Structured first"
    assert outputs[1].read_text(encoding="utf-8") == "# Second\n\nBeta."


def test_ingest_file_does_not_swallow_cancelled_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "lesson.pdf"
    source.write_text("pdf placeholder", encoding="utf-8")
    monkeypatch.setattr("tree.ingest.extract_text", lambda path: "# Raw OCR")

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(
            ingest_file(
                source,
                tmp_path / "source",
                archivist_chunk_chars=10000,
                ocr_upload_interval_sec=0,
                archivist=CancelArchivist(),
            )
        )


def test_archivist_degraded_progress_records_raw_ocr_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "lesson.pdf"
    source.write_text("pdf placeholder", encoding="utf-8")
    monkeypatch.setattr("tree.ingest.extract_text", lambda path: "# Raw OCR")
    tracker = ProgressTracker(tmp_path)
    tracker.source_ingest_start(1)

    asyncio.run(
        ingest_file(
            source,
            tmp_path / "source",
            archivist_chunk_chars=10000,
            ocr_upload_interval_sec=0,
            archivist=TimeoutArchivist(),
            progress=tracker,
        )
    )

    state = tracker.read()
    assert state["source_ingest"]["archivist"]["state"] == "degraded"
    assert state["source_ingest"]["archivist"]["current_file"] == "lesson.pdf"
    assert state["source_ingest"]["archivist"]["chunk_index"] == 1
    assert state["source_ingest"]["archivist"]["error_type"] == "TimeoutError"
    assert state["source_ingest"]["archivist"]["fallback"] == "raw_ocr"
