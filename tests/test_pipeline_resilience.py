import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

import tree.engine as engine_module
from tree.curriculum.candidate_nodes import save_candidate_nodes
from tree.curriculum.graph import load_knowledge_graph
from tree.engine import TreeEngine, _attach_graph_selection, _pending_materials
from tree.io import paths
from tree.rag.client import RAGClient
from tree.state.manager import StateManager
from tree.state.models import ChapterRecord, ChapterScanResult, ExamSections, IterationState, PipelineState, Route


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


def test_attach_graph_selection_uses_planner_required_nodes_over_examiner_output() -> None:
    scan_result = ChapterScanResult(
        chapter_name="tree-999",
        source_collection="examiner-source",
        source_collections=["examiner-source"],
        graph_node_id="candidate:wrong",
        required_nodes=["finished:outputs/tree-999/99.examiner.md"],
        parent_output="finished:outputs/tree-999/99.examiner.md",
        exam_sections=ExamSections(
            knowledge_point="变量",
            blind_exam="Q",
            answer_key="A",
            writer_instructions="W",
        ),
    )
    knowledge_graph = {
        "planner": {"selected_node": "candidate:loops", "selection_mode": "branch"},
        "nodes": [
            {
                "node_id": "candidate:loops",
                "kind": "candidate",
                "status": "planned",
                "primary_source_collection": "planner-source",
                "source_collections": ["planner-source"],
                "required_nodes": [
                    "finished:outputs/tree-001/01.variables.md",
                    "finished:outputs/tree-001/02.conditionals.md",
                ],
                "parent_output": "finished:outputs/tree-001/02.conditionals.md",
                "is_new_root": False,
            }
        ],
    }

    attached = _attach_graph_selection(scan_result, knowledge_graph)

    assert attached is not None
    assert attached.graph_node_id == "candidate:loops"
    assert attached.required_nodes == [
        "finished:outputs/tree-001/01.variables.md",
        "finished:outputs/tree-001/02.conditionals.md",
    ]
    assert attached.parent_output == "finished:outputs/tree-001/02.conditionals.md"
    assert attached.source_collection == "planner-source"
    assert attached.source_collections == ["planner-source"]
    assert attached.selection_mode == "branch"


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


def test_handle_pass_raises_when_no_draft_exists(tmp_path: Path) -> None:
    state_mgr = StateManager(paths.pipeline_state_path(tmp_path))
    state_mgr.save(PipelineState(chapters=[ChapterRecord(chapter_name="tree-001", status="in_progress")]))

    fake_engine = SimpleNamespace(
        settings=SimpleNamespace(project_root=tmp_path),
        state_mgr=state_mgr,
    )
    iter_state = IterationState(
        chapter="tree-001",
        file_seq="01",
        knowledge_point="变量",
        draft_path=None,
    )

    with pytest.raises(RuntimeError, match="Cannot PASS without a persisted draft"):
        asyncio.run(TreeEngine._handle_pass(fake_engine, iter_state, None))

    state = state_mgr.load()
    assert state.chapters[0].files_completed == []


def test_handle_pass_refreshes_knowledge_graph_after_saving_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    draft_dir = paths.drafts_root(tmp_path) / "tree-001"
    draft_dir.mkdir(parents=True)
    draft = draft_dir / "01.变量.md"
    draft.write_text("# 变量\n", encoding="utf-8")
    save_candidate_nodes(
        tmp_path,
        {
            "version": 1,
            "chapter_candidates": [
                {
                    "candidate_id": "candidate:variables",
                    "status": "pending",
                    "title_hint": "变量",
                    "primary_source_collection": "lesson",
                    "source_collections": ["lesson"],
                    "core_concepts": ["变量"],
                    "prerequisite_concepts": [],
                    "prerequisite_candidates": [],
                    "representative_chunks": [{"chunk_ref": "lesson#001"}],
                    "selection_priority": 0.8,
                }
            ],
        },
    )

    state_mgr = StateManager(paths.pipeline_state_path(tmp_path))
    state_mgr.save(
        PipelineState(
            chapters=[
                ChapterRecord(
                    chapter_name="tree-001",
                    status="in_progress",
                    graph_node_id="candidate:variables",
                    source_collections=["lesson"],
                )
            ]
        )
    )

    class SuccessfulIndexer:
        def index_finished_file(self, root: Path, chapter: str, path: Path) -> int:
            return 1

    class FakeEngine:
        _index_finished_output_or_raise = TreeEngine._index_finished_output_or_raise
        _refresh_knowledge_graph_from_ledger = TreeEngine._refresh_knowledge_graph_from_ledger

    fake_engine = FakeEngine()
    fake_engine.settings = SimpleNamespace(project_root=tmp_path)
    fake_engine.state_mgr = state_mgr
    fake_engine.rag_indexer = SuccessfulIndexer()
    monkeypatch.setattr("tree.engine.git_ops.git_add_commit", lambda *args, **kwargs: False)
    iter_state = IterationState(
        chapter="tree-001",
        file_seq="01",
        knowledge_point="变量",
        draft_path=draft,
    )

    asyncio.run(TreeEngine._handle_pass(fake_engine, iter_state, None))

    graph = load_knowledge_graph(tmp_path)
    assert graph["stats"]["finished_count"] == 1
    assert graph["nodes"][0]["node_id"] == "finished:outputs/tree-001/01.变量.md"


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


def test_process_chapter_closes_active_node_after_one_pass(tmp_path: Path) -> None:
    state_mgr = StateManager(paths.pipeline_state_path(tmp_path))
    state_mgr.save(PipelineState(chapters=[ChapterRecord(chapter_name="tree-001", status="in_progress")]))

    class FakeProgress:
        def learning_stage(self, **kwargs):
            pass

    class FakeTracer:
        def log_step(self, *args, **kwargs):
            pass

    async def step1_compose(chapter, next_seq):
        return (
            ExamSections(
                knowledge_point="变量",
                blind_exam="Q1",
                answer_key="A1",
                writer_instructions="teach",
            ),
            False,
        )

    async def iteration_loop(iter_state, chapter_name):
        return None

    fake_engine = SimpleNamespace(
        settings=SimpleNamespace(project_root=tmp_path),
        state_mgr=state_mgr,
        progress=FakeProgress(),
        tracer=FakeTracer(),
        _raise_if_stop_requested=lambda: None,
        _reconcile_finished_outputs=lambda state, chapter_name: state,
        _step1_compose=step1_compose,
        _iteration_loop=iteration_loop,
        _mark_active_node_complete=lambda chapter_name: state_mgr.save(
            state_mgr.complete_chapter(state_mgr.load(), chapter_name)
        ),
    )

    asyncio.run(TreeEngine.process_chapter(fake_engine, "tree-001"))

    state = state_mgr.load()
    assert state.chapters[0].status == "completed"


def test_scan_next_chapter_returns_none_when_planner_has_no_selected_node(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def no_candidate_nodes(*args, **kwargs):
        return {"version": 1, "chapter_candidates": []}

    class ExaminerShouldNotRun:
        async def scan_next_chapter(self, *args, **kwargs):
            raise AssertionError("examiner should not decide woods completion")

    fake_engine = SimpleNamespace(
        settings=SimpleNamespace(project_root=tmp_path),
        archivist=SimpleNamespace(),
        examiner=ExaminerShouldNotRun(),
        _rebuild_source_inventory_from_rag=lambda: {},
    )

    async def rebuild_inventory():
        return {}

    fake_engine._rebuild_source_inventory_from_rag = rebuild_inventory
    monkeypatch.setattr("tree.engine.rebuild_candidate_nodes_with_ai", no_candidate_nodes)

    result = asyncio.run(TreeEngine._scan_next_chapter(fake_engine, PipelineState()))

    assert result is None
