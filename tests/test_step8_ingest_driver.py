"""Tests for Step 8 source ingest + embedding orchestration."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from tree.engine.ingest_driver import (
    prepare_sources,
    remove_ocr_image_html,
    split_raw_markdown_for_cleaning,
)
from tree.io import paths
from tree.observability.progress import ProgressTracker
from tree.planner.pipeline import load_dag, load_nodes
from tree.planner.store import read_envelope_data


def _raw_lines(count: int) -> str:
    return "\n".join(f"raw line {index}" for index in range(1, count + 1))


class _FakeArchivist:
    def __init__(self):
        self.clean_inputs = []

    async def clean(self, raw_markdown: str, *, timeout_sec=None, repair_attempts: int = 1) -> str:
        self.clean_inputs.append(raw_markdown)
        return raw_markdown.replace("raw", "clean")

    async def cut_mtus(
        self,
        cleaned_markdown: str,
        *,
        collection: str,
        source_file: str,
        order_offset: int = 0,
        timeout_sec=None,
        repair_attempts: int = 1,
    ):
        from tree.planner.mtu import build_mtus

        units = [
            {
                "start_line": 1,
                "end_line": 31,
                "title": "A",
                "defines": ["ka"],
                "summary": "",
                "unit_kind": "concept",
            },
            {
                "start_line": 32,
                "end_line": 62,
                "title": "B",
                "defines": ["kb"],
                "summary": "",
                "unit_kind": "concept",
            },
        ]
        return build_mtus(units, collection=collection, source_file=source_file, order_offset=order_offset)


class _EchoDagger:
    async def build_nodes(self, payload, *, timeout_sec=None):
        metas = [p for p in payload if "mtu_id" in p]
        return {
            "nodes": [
                {
                    "title": p["title"],
                    "member_mtu_ids": [p["mtu_id"]],
                    "defines": [p["mtu_id"]],
                }
                for p in metas
            ]
        }

    async def build_prerequisites(self, payload, *, timeout_sec=None):
        nodes = list(payload.get("nodes") or [])
        return {
            "node_prerequisites": [
                {
                    "node_id": node["node_id"],
                    "required_defines": [] if index == 0 else [nodes[index - 1]["defines"][0]],
                    "reason": "first node" if index == 0 else "continues the previous node",
                }
                for index, node in enumerate(nodes)
            ]
        }


class _FakeIndexer:
    def __init__(self):
        self.indexed = []
        self.updated_node_ids = {}

    def index_mtu(self, mtu, text: str, *, node_id: str = "") -> int:
        self.indexed.append((mtu.mtu_id, text, node_id))
        return 1

    def is_mtu_indexed(self, mtu_id: str) -> bool:
        return any(indexed_id == mtu_id for indexed_id, _text, _node_id in self.indexed)

    def update_mtu_node_ids(self, mapping: dict[str, str]) -> None:
        self.updated_node_ids.update(mapping)


class _FakeProgress:
    def __init__(self):
        self.patches = []

    def update(self, patch):
        self.patches.append(patch)


def _settings(tmp_path):
    return SimpleNamespace(
        project_root=tmp_path,
        archivist_mtu_cut_timeout_sec=1.0,
        archivist_mtu_repair_attempts=0,
        dagger_build_timeout_sec=1.0,
        dagger_repair_attempts=0,
        dagger_max_nodes_per_call=400,
        max_retries=3,
    )


async def test_prepare_sources_builds_planner_indexes_mtus_and_deletes_markdown(tmp_path, monkeypatch):
    material = tmp_path / "materials" / "课件" / "ch1.md"
    material.parent.mkdir(parents=True)
    material.write_text(_raw_lines(62), encoding="utf-8")
    monkeypatch.setattr(
        "tree.engine.ingest_driver.extract_text",
        lambda path: material.read_text(encoding="utf-8"),
    )

    indexer = _FakeIndexer()
    settings = SimpleNamespace(
        project_root=tmp_path,
        archivist_mtu_cut_timeout_sec=1.0,
        archivist_mtu_repair_attempts=0,
        dagger_build_timeout_sec=1.0,
        dagger_repair_attempts=0,
        dagger_max_nodes_per_call=400,
    )
    engine = SimpleNamespace(
        settings=settings,
        archivist=_FakeArchivist(),
        agents=SimpleNamespace(dagger=_EchoDagger()),
        rag_indexer=indexer,
        progress=ProgressTracker(tmp_path),
    )

    summary = await prepare_sources(engine)

    assert summary["mtu_count"] == 2
    assert len(load_nodes(tmp_path)) == 2
    assert len(load_dag(tmp_path)["edges"]) == 1
    assert len(indexer.indexed) == 2
    assert "clean line 1" in indexer.indexed[0][1]
    assert "clean line 31" in indexer.indexed[0][1]
    assert "clean line 32" in indexer.indexed[1][1]
    assert "clean line 62" in indexer.indexed[1][1]
    assert not any(node_id for _, _, node_id in indexer.indexed)
    assert set(indexer.updated_node_ids) == {mtu_id for mtu_id, _, _ in indexer.indexed}
    assert all(indexer.updated_node_ids.values())
    assert not any(paths.source_markdown_root(tmp_path).rglob("*.md"))
    stages = engine.progress.load()["stages"]
    assert stages["ocr"]["done"] == 1
    assert stages["ocr"]["total"] == 1
    assert stages["clean"]["done"] == 1
    assert stages["cut"]["done"] == 1
    assert stages["embed"]["done"] == 2
    assert stages["embed"]["status"] == "complete"
    assert stages["cluster"]["status"] == "complete"
    assert stages["link"]["done"] == 2


async def test_prepare_sources_persists_ocr_markdown_checkpoint_before_archivist(
    tmp_path, monkeypatch
):
    material = tmp_path / "materials" / "课件" / "ch1.md"
    material.parent.mkdir(parents=True)
    raw = _raw_lines(62)
    material.write_text(raw, encoding="utf-8")
    monkeypatch.setattr("tree.engine.ingest_driver.extract_text", lambda path: raw)

    indexer = _FakeIndexer()
    archivist = _FakeArchivist()
    progress = _FakeProgress()
    settings = SimpleNamespace(
        project_root=tmp_path,
        archivist_mtu_cut_timeout_sec=1.0,
        archivist_mtu_repair_attempts=0,
        dagger_build_timeout_sec=1.0,
        dagger_repair_attempts=0,
        dagger_max_nodes_per_call=400,
    )
    engine = SimpleNamespace(
        settings=settings,
        archivist=archivist,
        agents=SimpleNamespace(dagger=_EchoDagger()),
        rag_indexer=indexer,
        progress=progress,
    )

    await prepare_sources(engine)

    checkpoint = paths.ocr_markdown_path(tmp_path, "课件", "ch1.md")
    assert checkpoint.read_text(encoding="utf-8") == raw
    assert archivist.clean_inputs == [raw]
    assert any(
        patch.get("source_ingest", {}).get("checkpoint") == "ocr_markdown"
        and patch["source_ingest"]["path"] == "ocr/课件/ch1.md.md"
        for patch in progress.patches
    )


def test_remove_ocr_image_html_removes_centered_img_div_blocks():
    raw = (
        "before\n"
        '<div style="text-align: center;"><img src="https://pplines-online.bj.bcebos.com/deploy/'
        'official/paddleocr/pp-ocr-vl-16-online//x/markdown_1/imgs/img_in_image_box.jpg?authorization=abc" '
        'alt="Image" width="57%" /></div>\n'
        "after"
    )

    cleaned = remove_ocr_image_html(raw)

    assert "before" in cleaned
    assert "after" in cleaned
    assert "<img" not in cleaned
    assert "pplines-online" not in cleaned


def test_remove_ocr_image_html_removes_ocr_html_tables():
    raw = (
        "before\n"
        "<table border=1 style='margin: auto; width: max-content;'> "
        "<thead><tr><th style='text-align: center;'>t</th>"
        "<th style='text-align: center;'>过阻尼</th></tr></thead> "
        "<tbody><tr><td style='text-align: center;'>0</td>"
        "<td style='text-align: center;'>0.0</td></tr></tbody> "
        "</table>\n"
        "after"
    )

    cleaned = remove_ocr_image_html(raw)

    assert "before" in cleaned
    assert "after" in cleaned
    assert "<table" not in cleaned
    assert "过阻尼" not in cleaned


async def test_prepare_sources_persists_ocr_checkpoint_after_removing_image_html(tmp_path, monkeypatch):
    material = tmp_path / "materials" / "课件" / "ch1.md"
    material.parent.mkdir(parents=True)
    raw = (
        "raw line 1\nraw line 2\n"
        '<div style="text-align: center;"><img src="https://pplines-online.bj.bcebos.com/x.jpg" '
        'alt="Image" width="57%" /></div>\n'
        "raw line 3\nraw line 4"
    )
    material.write_text(raw, encoding="utf-8")
    monkeypatch.setattr("tree.engine.ingest_driver.extract_text", lambda path: raw)

    archivist = _FakeArchivist()
    settings = SimpleNamespace(
        project_root=tmp_path,
        archivist_mtu_cut_timeout_sec=1.0,
        archivist_mtu_repair_attempts=0,
        dagger_build_timeout_sec=1.0,
        dagger_repair_attempts=0,
        dagger_max_nodes_per_call=400,
    )
    engine = SimpleNamespace(
        settings=settings,
        archivist=archivist,
        agents=SimpleNamespace(dagger=_EchoDagger()),
        rag_indexer=_FakeIndexer(),
    )

    await prepare_sources(engine)

    checkpoint = paths.ocr_markdown_path(tmp_path, "课件", "ch1.md")
    checkpoint_text = checkpoint.read_text(encoding="utf-8")
    assert "<img" not in checkpoint_text
    assert "pplines-online" not in checkpoint_text
    assert archivist.clean_inputs == [checkpoint_text]


def test_split_raw_markdown_prefers_nearest_level_one_heading_in_window():
    raw = "a" * 70_010 + "\n# 第一章\n" + "b" * 40_000

    chunks = split_raw_markdown_for_cleaning(raw)

    assert len(chunks) == 2
    assert chunks[0] == "a" * 70_010 + "\n"
    assert chunks[1].startswith("# 第一章")


def test_split_raw_markdown_falls_back_from_h1_to_h2_to_h3_then_70k():
    h2_raw = "a" * 70_020 + "\n## 二级标题\n" + "b" * 40_000
    h3_raw = "a" * 70_030 + "\n### 三级标题\n" + "b" * 40_000
    plain_raw = "a" * 110_000

    h2_chunks = split_raw_markdown_for_cleaning(h2_raw)
    h3_chunks = split_raw_markdown_for_cleaning(h3_raw)
    plain_chunks = split_raw_markdown_for_cleaning(plain_raw)

    assert h2_chunks[1].startswith("## 二级标题")
    assert h3_chunks[1].startswith("### 三级标题")
    assert [len(chunk) for chunk in plain_chunks] == [70_000, 40_000]


def test_split_raw_markdown_does_not_treat_window_start_as_line_start():
    raw = "a" * 70_000 + "# fake heading in same line" + "b" * 40_000

    chunks = split_raw_markdown_for_cleaning(raw)

    assert [len(chunk) for chunk in chunks] == [70_000, len(raw) - 70_000]


async def test_prepare_sources_cleans_and_cuts_each_long_chunk_independently(tmp_path, monkeypatch):
    material = tmp_path / "materials" / "课件" / "long.md"
    material.parent.mkdir(parents=True)
    raw = "raw line 1\nraw line 2\n" + "x" * 70_000 + "\n# Next\nraw line 3\nraw line 4\n" + "y" * 40_000
    material.write_text(raw, encoding="utf-8")
    monkeypatch.setattr("tree.engine.ingest_driver.extract_text", lambda path: raw)

    indexer = _FakeIndexer()
    archivist = _FakeArchivist()
    settings = SimpleNamespace(
        project_root=tmp_path,
        archivist_mtu_cut_timeout_sec=1.0,
        archivist_mtu_repair_attempts=0,
        dagger_build_timeout_sec=1.0,
        dagger_repair_attempts=0,
        dagger_max_nodes_per_call=400,
    )
    engine = SimpleNamespace(
        settings=settings,
        archivist=archivist,
        agents=SimpleNamespace(dagger=_EchoDagger()),
        rag_indexer=indexer,
    )

    await prepare_sources(engine)

    assert len(archivist.clean_inputs) == 2
    indexed_texts = [text for _, text, _ in indexer.indexed]
    assert any("clean line 1" in text and "clean line 2" in text for text in indexed_texts)
    assert any("clean line 3" in text for text in indexed_texts)
    assert any("clean line 4" in text for text in indexed_texts)
    source_files = {
        raw_mtu["source_file"]
        for raw_mtu in read_envelope_data(paths.mtus_path(tmp_path)).get("mtus", [])
    }
    assert source_files == {"long.md.part-001", "long.md.part-002"}


class _BlockingArchivist:
    def __init__(self):
        self.active = 0
        self.max_active = 0
        self.started_five = asyncio.Event()
        self.release = asyncio.Event()

    async def clean(self, raw_markdown: str, *, timeout_sec=None, repair_attempts: int = 1) -> str:
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        if self.active == 5:
            self.started_five.set()
        try:
            await self.release.wait()
            return raw_markdown.replace("raw", "clean")
        finally:
            self.active -= 1

    async def cut_mtus(
        self,
        cleaned_markdown: str,
        *,
        collection: str,
        source_file: str,
        order_offset: int = 0,
        timeout_sec=None,
        repair_attempts: int = 1,
    ):
        from tree.planner.mtu import build_mtus

        return build_mtus(
            [
                {
                    "start_line": 1,
                    "end_line": 31,
                    "title": "A",
                    "defines": ["ka"],
                    "summary": "",
                    "unit_kind": "concept",
                }
            ],
            collection=collection,
            source_file=source_file,
            order_offset=order_offset,
        )


async def test_prepare_sources_processes_chunks_with_max_five_concurrent_parts(tmp_path, monkeypatch):
    material = tmp_path / "materials" / "课件" / "long.md"
    material.parent.mkdir(parents=True)
    raw = "ignored"
    material.write_text(raw, encoding="utf-8")
    chunks = [f"raw line 1\nraw line 2\nraw line 3\nraw line 4\nchunk {i}" for i in range(6)]
    monkeypatch.setattr("tree.engine.ingest_driver.extract_text", lambda path: raw)
    monkeypatch.setattr("tree.engine.ingest_driver.split_raw_markdown_for_cleaning", lambda text: chunks)

    archivist = _BlockingArchivist()
    engine = SimpleNamespace(
        settings=_settings(tmp_path),
        archivist=archivist,
        agents=SimpleNamespace(dagger=_EchoDagger()),
        rag_indexer=_FakeIndexer(),
    )

    task = asyncio.create_task(prepare_sources(engine))
    try:
        await asyncio.wait_for(archivist.started_five.wait(), timeout=1)
        assert archivist.active == 5
        archivist.release.set()
        await task
    finally:
        archivist.release.set()
        if not task.done():
            task.cancel()

    assert archivist.max_active == 5


class RateLimitError(Exception):
    pass


class _RetryingArchivist:
    def __init__(self):
        self.events = []
        self.attempts = {"overloaded": 0}
        self.slow_started = asyncio.Event()
        self.overload_failed = asyncio.Event()

    async def clean(self, raw_markdown: str, *, timeout_sec=None, repair_attempts: int = 1) -> str:
        if raw_markdown.startswith("slow"):
            self.events.append("slow-start")
            self.slow_started.set()
            await self.overload_failed.wait()
            await asyncio.sleep(0.01)
            self.events.append("slow-complete")
            return raw_markdown.replace("raw", "clean")

        if raw_markdown.startswith("overloaded"):
            try:
                await asyncio.wait_for(self.slow_started.wait(), timeout=0.05)
            except TimeoutError:
                pass
            self.attempts["overloaded"] += 1
            self.events.append(f"overloaded-attempt-{self.attempts['overloaded']}")
            if self.attempts["overloaded"] == 1:
                self.overload_failed.set()
                raise RateLimitError("too many concurrent requests")
            return raw_markdown.replace("raw", "clean")

        return raw_markdown.replace("raw", "clean")

    async def cut_mtus(
        self,
        cleaned_markdown: str,
        *,
        collection: str,
        source_file: str,
        order_offset: int = 0,
        timeout_sec=None,
        repair_attempts: int = 1,
    ):
        from tree.planner.mtu import build_mtus

        return build_mtus(
            [
                {
                    "start_line": 1,
                    "end_line": 31,
                    "title": source_file,
                    "defines": ["ka"],
                    "summary": "",
                    "unit_kind": "concept",
                }
            ],
            collection=collection,
            source_file=source_file,
            order_offset=order_offset,
        )


async def test_prepare_sources_waits_for_completed_part_before_retrying_concurrency_error(
    tmp_path, monkeypatch
):
    material = tmp_path / "materials" / "课件" / "long.md"
    material.parent.mkdir(parents=True)
    raw = "ignored"
    material.write_text(raw, encoding="utf-8")
    chunks = ["overloaded raw line 1\nraw line 2\nraw line 3\nraw line 4", "slow raw line 1\nraw line 2\nraw line 3\nraw line 4"]
    monkeypatch.setattr("tree.engine.ingest_driver.extract_text", lambda path: raw)
    monkeypatch.setattr("tree.engine.ingest_driver.split_raw_markdown_for_cleaning", lambda text: chunks)

    archivist = _RetryingArchivist()
    engine = SimpleNamespace(
        settings=_settings(tmp_path),
        archivist=archivist,
        agents=SimpleNamespace(dagger=_EchoDagger()),
        rag_indexer=_FakeIndexer(),
    )

    await prepare_sources(engine)

    assert archivist.events.index("slow-complete") < archivist.events.index("overloaded-attempt-2")


def _ingest_engine(tmp_path, indexer=None):
    return SimpleNamespace(
        settings=_settings(tmp_path),
        archivist=_FakeArchivist(),
        agents=SimpleNamespace(dagger=_EchoDagger()),
        rag_indexer=indexer or _FakeIndexer(),
        progress=ProgressTracker(tmp_path),
    )


def test_reuse_ocr_checkpoint_validates_fingerprint(tmp_path):
    from tree.engine.ingest_driver import (
        _reuse_ocr_checkpoint,
        _write_ocr_fingerprint,
        persist_ocr_markdown,
    )

    ocr_path = persist_ocr_markdown(tmp_path, "课件", "ch1.md", "hello")
    assert _reuse_ocr_checkpoint(ocr_path, "100-200") is None  # no fingerprint yet
    _write_ocr_fingerprint(ocr_path, "100-200")
    assert _reuse_ocr_checkpoint(ocr_path, "100-200") == "hello"
    assert _reuse_ocr_checkpoint(ocr_path, "999-999") is None  # material changed
    assert _reuse_ocr_checkpoint(ocr_path, "") is None  # no fingerprint -> never reuse


async def test_clean_cut_totals_count_materials_not_chunks(tmp_path, monkeypatch):
    material = tmp_path / "materials" / "课件" / "long.md"
    material.parent.mkdir(parents=True)
    raw = "raw line 1\nraw line 2\n" + "x" * 70_000 + "\n# Next\nraw line 3\nraw line 4\n" + "y" * 40_000
    material.write_text(raw, encoding="utf-8")
    monkeypatch.setattr("tree.engine.ingest_driver.extract_text", lambda path: raw)

    engine = _ingest_engine(tmp_path)
    await prepare_sources(engine)

    stages = engine.progress.load()["stages"]
    # One material that split into two chunks: counters are per-material, not per-chunk.
    assert stages["clean"]["total"] == 1
    assert stages["clean"]["done"] == 1
    assert stages["clean"]["status"] == "complete"
    assert stages["cut"]["total"] == 1
    assert stages["cut"]["done"] == 1
    assert stages["cut"]["status"] == "complete"


async def test_unchanged_material_resumes_from_cache_without_reocr(tmp_path, monkeypatch):
    material = tmp_path / "materials" / "课件" / "ch1.md"
    material.parent.mkdir(parents=True)
    raw = _raw_lines(62)
    material.write_text(raw, encoding="utf-8")
    calls = {"n": 0}

    def fake_extract(path):
        calls["n"] += 1
        return raw

    monkeypatch.setattr("tree.engine.ingest_driver.extract_text", fake_extract)

    # Shared indexer mirrors the persistent Qdrant store across runs (already
    # embedded MTUs are skipped, just like a real resume).
    indexer = _FakeIndexer()
    await prepare_sources(_ingest_engine(tmp_path, indexer=indexer))
    assert calls["n"] == 1
    cache_file = paths.planner_root(tmp_path) / "mtu-cache" / "课件" / "ch1.md.json"
    assert cache_file.exists()

    # Second run, material unchanged: per-material cache hit, producer not invoked.
    await prepare_sources(_ingest_engine(tmp_path, indexer=indexer))
    assert calls["n"] == 1


async def test_unchanged_material_skips_planner_rebuild_when_artifacts_ready(tmp_path, monkeypatch):
    material = tmp_path / "materials" / "课件" / "ch1.md"
    material.parent.mkdir(parents=True)
    raw = _raw_lines(62)
    material.write_text(raw, encoding="utf-8")
    monkeypatch.setattr("tree.engine.ingest_driver.extract_text", lambda path: raw)

    indexer = _FakeIndexer()
    await prepare_sources(_ingest_engine(tmp_path, indexer=indexer))

    async def boom(*args, **kwargs):
        raise AssertionError("unchanged materials should resume from existing planner artifacts")

    monkeypatch.setattr("tree.engine.ingest_driver.rebuild_planner", boom)
    summary = await prepare_sources(_ingest_engine(tmp_path, indexer=indexer))

    assert summary["resumed"] is True
    assert summary["mtu_count"] == 2


async def test_clean_failure_marks_stage_failed_without_advancing_done(tmp_path, monkeypatch):
    material = tmp_path / "materials" / "课件" / "ch1.md"
    material.parent.mkdir(parents=True)
    raw = _raw_lines(62)
    material.write_text(raw, encoding="utf-8")
    monkeypatch.setattr("tree.engine.ingest_driver.extract_text", lambda path: raw)

    class FailingArchivist(_FakeArchivist):
        async def clean(self, raw_markdown: str, *, timeout_sec=None, repair_attempts: int = 1) -> str:
            raise ValueError("bad clean json")

    engine = _ingest_engine(tmp_path)
    engine.archivist = FailingArchivist()

    with pytest.raises(ValueError, match="bad clean json"):
        await prepare_sources(engine)

    progress = engine.progress.load()
    assert progress["stages"]["clean"]["status"] == "failed"
    assert progress["stages"]["clean"]["done"] == 0
    assert progress["stages"]["cut"]["status"] == "failed"
    assert progress["stages"]["cut"]["done"] == 0
    assert "bad clean json" in progress["errors"][0]


async def test_resumes_via_ocr_checkpoint_when_mtu_cache_missing(tmp_path, monkeypatch):
    import shutil

    material = tmp_path / "materials" / "课件" / "ch1.md"
    material.parent.mkdir(parents=True)
    raw = _raw_lines(62)
    material.write_text(raw, encoding="utf-8")
    monkeypatch.setattr("tree.engine.ingest_driver.extract_text", lambda path: raw)

    await prepare_sources(_ingest_engine(tmp_path))

    # Simulate a crash before MTUs were persisted: drop both MTU caches but keep
    # the OCR checkpoint. The material is still unchanged, so the producer reruns
    # and must reuse the OCR checkpoint instead of re-extracting.
    paths.mtus_path(tmp_path).unlink()
    shutil.rmtree(paths.planner_root(tmp_path) / "mtu-cache")

    def boom(path):
        raise AssertionError("extract_text should not be called; OCR checkpoint must be reused")

    monkeypatch.setattr("tree.engine.ingest_driver.extract_text", boom)
    summary = await prepare_sources(_ingest_engine(tmp_path))
    assert summary["mtu_count"] == 2
