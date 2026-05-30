import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

import tree.engine as engine_module
from tree.engine import TreeEngine, _pending_materials
from tree.io import paths
from tree.rag.client import RAGClient
from tree.state.manager import StateManager
from tree.state.models import ChapterRecord, ExamSections, IterationState, PipelineState, Route


class _Point:
    def __init__(self, chunk_id: str):
        self.payload = {
            "chunk_id": chunk_id,
            "text": f"text {chunk_id}",
            "content_kind": "source",
            "is_draft": False,
        }


def test_scroll_chunks_reads_all_qdrant_pages() -> None:
    class FakeQdrant:
        def __init__(self):
            self.calls = []

        def scroll(self, **kwargs):
            self.calls.append(kwargs.get("offset"))
            if len(self.calls) == 1:
                return [_Point("doc-000")], "next-page"
            if len(self.calls) == 2:
                return [_Point("doc-001")], None
            raise AssertionError("scroll called after final page")

    rag = object.__new__(RAGClient)
    rag._client = FakeQdrant()

    chunks = rag.scroll_chunks(limit=1)

    assert [chunk["chunk_id"] for chunk in chunks] == ["doc-000", "doc-001"]
    assert rag._client.calls == [None, "next-page"]


def test_manifest_embedded_flag_is_cleared_when_source_vectors_are_missing(tmp_path: Path) -> None:
    materials = paths.materials_root(tmp_path)
    materials.mkdir()
    source = materials / "lesson.pdf"
    source.write_text("pdf placeholder", encoding="utf-8")
    output = paths.source_root(tmp_path) / "lesson" / "lesson.md"

    manifest = {
        "materials/lesson.pdf": {
            "collection": "lesson",
            "embedded": True,
            "fingerprint": f"{source.stat().st_size}:{source.stat().st_mtime_ns}",
            "outputs": ["lesson.md", str(output.relative_to(tmp_path))],
        }
    }

    class MissingSourceIndexer:
        def is_source_file_indexed(self, root: Path, collection: str, path: Path) -> bool:
            return False

    assert hasattr(engine_module, "_refresh_manifest_index_status")
    engine_module._refresh_manifest_index_status(tmp_path, manifest, MissingSourceIndexer())

    assert manifest["materials/lesson.pdf"]["embedded"] is False
    assert _pending_materials(tmp_path, manifest) == [(source, "lesson")]


def test_handle_pass_raises_when_finished_output_indexing_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    draft_dir = paths.drafts_root(tmp_path) / "tree-001"
    draft_dir.mkdir(parents=True)
    draft = draft_dir / "01.变量.md"
    draft.write_text("# 变量", encoding="utf-8")

    state_mgr = StateManager(paths.pipeline_state_path(tmp_path))
    state_mgr.save(PipelineState(chapters=[ChapterRecord(chapter_name="tree-001", status="in_progress")]))

    class FailingIndexer:
        def index_finished_file(self, root: Path, chapter: str, path: Path) -> int:
            raise RuntimeError("embedding unavailable")

    class FakeEngine:
        _index_finished_output_or_raise = TreeEngine._index_finished_output_or_raise

    fake_engine = FakeEngine()
    fake_engine.settings = SimpleNamespace(project_root=tmp_path)
    fake_engine.state_mgr = state_mgr
    fake_engine.rag_indexer = FailingIndexer()
    monkeypatch.setattr("tree.engine.git_ops.git_add_commit", lambda *args, **kwargs: False)
    iter_state = IterationState(
        chapter="tree-001",
        file_seq="01",
        knowledge_point="变量",
        draft_path=draft,
    )

    with pytest.raises(RuntimeError, match="embedding unavailable"):
        asyncio.run(TreeEngine._handle_pass(fake_engine, iter_state, None))

    state = state_mgr.load()
    assert state.chapters[0].files_completed == []


def test_student_blind_test_receives_prior_finished_file_contents(tmp_path: Path) -> None:
    output_dir = paths.outputs_root(tmp_path) / "tree-001"
    output_dir.mkdir(parents=True)
    (output_dir / "01.prior.md").write_text("prior theorem", encoding="utf-8")

    class CapturingStudent:
        def __init__(self):
            self.prior_file_contents = None

        async def blind_test(self, blind_exam, prior_file_contents, *args, **kwargs):
            self.prior_file_contents = prior_file_contents
            return "answer"

    student = CapturingStudent()
    fake_engine = SimpleNamespace(
        settings=SimpleNamespace(project_root=tmp_path),
        student=student,
        _finished_rag_query=lambda *args, **kwargs: [],
    )
    iter_state = IterationState(
        chapter="tree-001",
        file_seq="02",
        knowledge_point="新知识",
        exam_sections=ExamSections(
            knowledge_point="新知识",
            blind_exam="Q1",
            answer_key="A1",
            writer_instructions="teach",
        ),
    )

    result = asyncio.run(TreeEngine._step2_blind_test(fake_engine, iter_state))

    assert result == "answer"
    assert student.prior_file_contents == ["prior theorem"]


def test_audit_context_includes_examiner_only_source_rag(tmp_path: Path) -> None:
    class CapturingExaminer:
        def __init__(self):
            self.retrieved_context = None

        async def audit(self, *args, retrieved_context=None, **kwargs):
            self.retrieved_context = retrieved_context
            return SimpleNamespace(route=Route.FAIL_KNOWLEDGE_GAP)

    examiner = CapturingExaminer()
    fake_engine = SimpleNamespace(
        settings=SimpleNamespace(project_root=tmp_path),
        examiner=examiner,
        _finished_rag_query=lambda *args, **kwargs: [
            {"text": "finished", "metadata": {"content_kind": "finished"}}
        ],
        _rag_query=lambda *args, **kwargs: [
            {"text": "source", "metadata": {"content_kind": "source"}}
        ],
        _source_collections_for_chapter=lambda chapter: ["lesson"],
    )
    iter_state = IterationState(
        chapter="tree-001",
        file_seq="01",
        knowledge_point="变量",
        exam_sections=ExamSections(
            knowledge_point="变量",
            blind_exam="Q1",
            answer_key="A1",
            writer_instructions="teach",
        ),
    )

    asyncio.run(TreeEngine._step3_audit(fake_engine, iter_state, "student answer"))

    kinds = [hit["metadata"]["content_kind"] for hit in examiner.retrieved_context]
    assert kinds == ["finished", "source"]
