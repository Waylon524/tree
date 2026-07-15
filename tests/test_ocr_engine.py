"""Tests for PaddleOCR file-level scheduling."""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from tree.ingest.ocr_engine import (
    OCREngine,
    PdfCryptoDependencyError,
    PdfPasswordRequiredError,
    pdf_crypto_runtime_status,
    set_job_store_root,
)


@pytest.fixture(autouse=True)
def reset_ocr_engine_singleton():
    OCREngine._instance = None
    set_job_store_root(None)
    yield
    if OCREngine._instance is not None:
        OCREngine._instance.close()
    OCREngine._instance = None
    set_job_store_root(None)


def test_pdf_chunks_are_ocrd_with_max_five_concurrent_files(tmp_path, monkeypatch):
    engine = OCREngine(token="test", pdf_max_pages_per_job=1)
    pdf = tmp_path / "book.pdf"
    pdf.write_bytes(b"%PDF-test")
    chunks = [tmp_path / f"chunk-{index}.pdf" for index in range(6)]
    for chunk in chunks:
        chunk.write_bytes(b"%PDF-chunk")

    active = 0
    max_active = 0
    lock = threading.Lock()
    five_started = threading.Event()
    release = threading.Event()

    monkeypatch.setattr(engine, "_pdf_page_count", lambda path: 6 if Path(path) == pdf else 1)
    monkeypatch.setattr(engine, "_split_pdf", lambda pdf_path, output_dir, max_pages: chunks)

    def fake_ocr_single(path, context=None):
        nonlocal active, max_active
        with lock:
            active += 1
            max_active = max(max_active, active)
            if active == 5:
                five_started.set()
        try:
            release.wait(timeout=0.2)
            return Path(path).stem
        finally:
            with lock:
                active -= 1

    monkeypatch.setattr(engine, "_ocr_single_local", fake_ocr_single)

    result_holder: dict[str, str] = {}
    worker = threading.Thread(target=lambda: result_holder.setdefault("text", engine._ocr_local_pdf(pdf)))
    worker.start()
    try:
        assert five_started.wait(timeout=0.2)
        release.set()
        worker.join(timeout=1)
    finally:
        release.set()
        worker.join(timeout=1)

    assert max_active == 5
    assert result_holder["text"].split("\n\n") == [chunk.stem for chunk in chunks]


def test_direct_ocr_files_share_global_five_file_gate(tmp_path, monkeypatch):
    engine = OCREngine(token="test", ocr_concurrency=5)
    files = [tmp_path / f"file-{index}.png" for index in range(6)]
    for file in files:
        file.write_bytes(b"image")

    active = 0
    max_active = 0
    lock = threading.Lock()
    five_started = threading.Event()
    release = threading.Event()

    def fake_unlocked(path, context=None):
        nonlocal active, max_active
        with lock:
            active += 1
            max_active = max(max_active, active)
            if active == 5:
                five_started.set()
        try:
            release.wait(timeout=0.2)
            return Path(path).stem
        finally:
            with lock:
                active -= 1

    monkeypatch.setattr(engine, "_ocr_single_local_unlocked", fake_unlocked)

    results: list[str] = []
    threads = [
        threading.Thread(target=lambda path=file: results.append(engine._ocr_single_local(path)))
        for file in files
    ]
    for thread in threads:
        thread.start()
    try:
        assert five_started.wait(timeout=0.2)
        release.set()
        for thread in threads:
            thread.join(timeout=1)
    finally:
        release.set()
        for thread in threads:
            thread.join(timeout=1)

    assert max_active == 5
    assert sorted(results) == [file.stem for file in files]


class _OcrRateLimitError(Exception):
    pass


def test_pdf_chunk_retry_waits_for_completed_file_after_concurrency_error(tmp_path, monkeypatch):
    engine = OCREngine(token="test", pdf_max_pages_per_job=1)
    pdf = tmp_path / "book.pdf"
    pdf.write_bytes(b"%PDF-test")
    overloaded = tmp_path / "overloaded.pdf"
    slow = tmp_path / "slow.pdf"
    overloaded.write_bytes(b"%PDF-overloaded")
    slow.write_bytes(b"%PDF-slow")

    events: list[str] = []
    attempts = {"overloaded": 0}
    slow_started = threading.Event()
    overload_failed = threading.Event()

    monkeypatch.setattr(engine, "_pdf_page_count", lambda path: 2 if Path(path) == pdf else 1)
    monkeypatch.setattr(engine, "_split_pdf", lambda pdf_path, output_dir, max_pages: [overloaded, slow])

    def fake_ocr_single(path, context=None):
        name = Path(path).stem
        if name == "slow":
            events.append("slow-start")
            slow_started.set()
            overload_failed.wait(timeout=0.2)
            time.sleep(0.01)
            events.append("slow-complete")
            return "slow text"
        attempts["overloaded"] += 1
        events.append(f"overloaded-attempt-{attempts['overloaded']}")
        if attempts["overloaded"] == 1:
            slow_started.wait(timeout=0.2)
            overload_failed.set()
            raise _OcrRateLimitError("too many concurrent OCR jobs")
        return "overloaded text"

    monkeypatch.setattr(engine, "_ocr_single_local", fake_ocr_single)

    assert engine._ocr_local_pdf(pdf).split("\n\n") == ["overloaded text", "slow text"]
    assert events.index("slow-complete") < events.index("overloaded-attempt-2")


def test_failed_init_does_not_cache_broken_singleton(monkeypatch):
    """A mid-init failure must not leave a half-built singleton (no '_client')."""
    import tree.ingest.ocr_engine as oe

    real_client = oe.httpx.Client
    calls = {"n": 0}

    def flaky_client(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("boom")
        return real_client(*args, **kwargs)

    monkeypatch.setattr(oe.httpx, "Client", flaky_client)

    with pytest.raises(RuntimeError, match="boom"):
        oe.OCREngine(token="t")
    # The real cause surfaced and no broken instance was cached.
    assert oe.OCREngine._instance is None

    engine = oe.OCREngine(token="t")
    assert engine._client is not None  # would AttributeError under the old bug


def test_close_without_client_is_safe():
    bare = object.__new__(OCREngine)
    bare.close()  # must not raise AttributeError: '_client'


def test_pdf_chunks_reject_empty_partial_result(tmp_path, monkeypatch):
    engine = OCREngine(token="test")
    chunks = [tmp_path / "one.pdf", tmp_path / "two.pdf"]
    for chunk in chunks:
        chunk.write_bytes(b"pdf")

    monkeypatch.setattr(
        engine,
        "_ocr_single_local",
        lambda path, context=None: "complete" if Path(path).name == "one.pdf" else "",
    )

    with pytest.raises(RuntimeError, match=r"missing PDF chunks: \[2\]"):
        engine._ocr_pdf_chunks([(chunks[0], {}), (chunks[1], {})])


def test_pdf_crypto_runtime_round_trip_is_available():
    ok, detail = pdf_crypto_runtime_status()

    assert ok, detail


def test_pdf_page_count_names_file_when_aes_dependency_is_missing(tmp_path, monkeypatch):
    import pypdf
    from pypdf.errors import DependencyError

    pdf = tmp_path / "encrypted lecture.pdf"
    pdf.write_bytes(b"%PDF-test")
    monkeypatch.setattr(
        pypdf,
        "PdfReader",
        lambda path: (_ for _ in ()).throw(DependencyError("crypto missing")),
    )

    with pytest.raises(PdfCryptoDependencyError, match="encrypted lecture.pdf"):
        OCREngine._pdf_page_count(pdf)


def test_pdf_page_count_names_file_when_password_is_required(tmp_path, monkeypatch):
    import pypdf
    from pypdf.errors import WrongPasswordError

    pdf = tmp_path / "locked handout.pdf"
    pdf.write_bytes(b"%PDF-test")
    monkeypatch.setattr(
        pypdf,
        "PdfReader",
        lambda path: (_ for _ in ()).throw(WrongPasswordError("wrong password")),
    )

    with pytest.raises(PdfPasswordRequiredError, match="locked handout.pdf"):
        OCREngine._pdf_page_count(pdf)


def test_remote_job_id_is_resumed_after_poll_timeout(tmp_path, monkeypatch):
    engine = OCREngine(token="test")
    source = tmp_path / "page.png"
    source.write_bytes(b"image bytes")
    set_job_store_root(tmp_path / "ocr-jobs")
    calls = {"submit": 0, "poll": 0, "download": 0}

    def submit(path):
        calls["submit"] += 1
        return "job-123"

    def poll(job_id, context):
        calls["poll"] += 1
        assert job_id == "job-123"
        if calls["poll"] == 1:
            raise TimeoutError("temporary timeout")
        return "https://result.test/job.jsonl"

    def download(url, context):
        calls["download"] += 1
        return "recovered OCR text"

    monkeypatch.setattr(engine, "_submit_local", submit)
    monkeypatch.setattr(engine, "_poll", poll)
    monkeypatch.setattr(engine, "_download_result", download)

    with pytest.raises(TimeoutError):
        engine._ocr_single_local_unlocked(source)
    assert engine._ocr_single_local_unlocked(source) == "recovered OCR text"
    # A third attempt uses the persisted chunk result without network work.
    assert engine._ocr_single_local_unlocked(source) == "recovered OCR text"
    assert calls == {"submit": 1, "poll": 2, "download": 1}
