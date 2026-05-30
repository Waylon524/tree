import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

import tree.engine as engine_module
from tree.curriculum.candidate_nodes import rebuild_candidate_nodes, save_candidate_nodes
from tree.curriculum.graph import build_selected_node_context, load_knowledge_graph, rebuild_knowledge_graph
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


def _planning_chunk(
    chunk_ref: str,
    collection: str,
    concepts: list[str],
    *,
    methods: list[str] | None = None,
    formulas: list[str] | None = None,
    section_id: str = "",
) -> dict:
    return {
        "chunk_ref": chunk_ref,
        "chunk_id": chunk_ref,
        "chunk_index": int(chunk_ref.rsplit("#", 1)[-1]),
        "source_collection": collection,
        "path": f"{collection}.md",
        "section_id": section_id,
        "core_concepts": concepts,
        "prerequisites": [],
        "methods": methods or [],
        "formulas": formulas or [],
        "summary": " ".join(concepts),
    }


def _planning_candidate(
    candidate_id: str,
    concepts: list[str],
    *,
    chunks: list[str],
    sources: list[str] | None = None,
    priority: float = 0.5,
    estimated_output_lines: int | None = None,
) -> dict:
    candidate = {
        "candidate_id": candidate_id,
        "status": "pending",
        "title_hint": candidate_id.rsplit(":", 1)[-1],
        "primary_source_collection": (sources or ["source"])[0],
        "source_collections": sources or ["source"],
        "core_concepts": concepts,
        "prerequisite_concepts": [],
        "prerequisite_candidates": [],
        "representative_chunks": [
            {"chunk_ref": chunk, "core_concepts": concepts, "summary": " ".join(concepts)}
            for chunk in chunks
        ],
        "selection_priority": priority,
    }
    if estimated_output_lines is not None:
        candidate["estimated_output_lines"] = estimated_output_lines
    return candidate


def _planning_candidate_nodes(*candidates: dict) -> dict:
    return {"version": 1, "kind": "candidate_nodes", "chapter_candidates": list(candidates)}


def _planning_ledger_record(path: str, concepts: list[str], *, chunks: list[str], sources: list[str]) -> dict:
    return {
        "chapter": "chapter",
        "file_seq": path.split("/")[-1].split(".", 1)[0],
        "filename": path.split("/")[-1],
        "path": path,
        "knowledge_point": concepts[0],
        "covered_concepts": concepts,
        "prerequisites": [],
        "hit_chunks": chunks,
        "source_collections": sources,
        "graph_node_id": None,
        "required_nodes": [],
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


def test_writeable_chunk_cluster_merges_thin_same_section_chunks(tmp_path: Path) -> None:
    inventory = {
        "version": 1,
        "chunks": [
            _planning_chunk(
                "8/a#001",
                "8. 沉淀溶解平衡",
                ["溶解度阈值"],
                methods=["沉淀分离判据"],
                formulas=["c < threshold"],
                section_id="沉淀分离阈值",
            ),
            _planning_chunk(
                "8/a#002",
                "8. 沉淀溶解平衡",
                ["相对溶解度"],
                methods=["分离效果比较"],
                formulas=["S1 / S2"],
                section_id="沉淀分离阈值",
            ),
            _planning_chunk(
                "8/a#003",
                "8. 沉淀溶解平衡",
                ["分离完全条件"],
                methods=["定量分离判据"],
                formulas=["99.9%"],
                section_id="沉淀分离阈值",
            ),
            _planning_chunk("8/a#004", "8. 沉淀溶解平衡", ["晶格能"], section_id="晶体结构"),
        ],
        "collections": [
            {
                "source_collection": "8. 沉淀溶解平衡",
                "core_concepts": ["溶解度阈值", "相对溶解度", "分离完全条件", "晶格能"],
                "related_collections": [],
            }
        ],
    }

    nodes = rebuild_candidate_nodes(tmp_path, inventory)

    merged = next(
        item
        for item in nodes["chapter_candidates"]
        if {"8/a#001", "8/a#002", "8/a#003"}.issubset(
            {chunk["chunk_ref"] for chunk in item["representative_chunks"]}
        )
    )

    assert "8/a#004" not in {chunk["chunk_ref"] for chunk in merged["representative_chunks"]}
    assert merged["chunk_count"] == 3
    assert merged["estimated_output_lines"] >= 300
    assert merged["size_band"] == "fit"


def test_finished_trunk_absorbs_candidate_covered_by_multiple_outputs(tmp_path: Path) -> None:
    graph = rebuild_knowledge_graph(
        tmp_path,
        _planning_candidate_nodes(
            _planning_candidate(
                "candidate:covered-threshold",
                ["溶解度阈值", "相对溶解度"],
                chunks=["source#001", "source#002"],
                sources=["8. 沉淀溶解平衡"],
            ),
            _planning_candidate(
                "candidate:new-complex",
                ["配位解离平衡"],
                chunks=["source#003"],
                sources=["9. 配位解离平衡"],
            ),
        ),
        {
            "version": 1,
            "records": [
                _planning_ledger_record(
                    "outputs/tree-001/01.threshold.md",
                    ["溶解度阈值"],
                    chunks=["source#001"],
                    sources=["8. 沉淀溶解平衡"],
                ),
                _planning_ledger_record(
                    "outputs/tree-001/02.relative.md",
                    ["相对溶解度"],
                    chunks=["source#002"],
                    sources=["8. 沉淀溶解平衡"],
                ),
            ],
        },
    )

    covered = next(node for node in graph["nodes"] if node["node_id"] == "candidate:covered-threshold")
    selected = next(node for node in graph["nodes"] if node["planner_selected"])

    assert covered["status"] == "covered"
    assert covered["coverage_reason"] == "absorbed by finished trunk solvability"
    assert covered["finished_solvability"] >= 0.8
    assert set(covered["covered_by_outputs"]) == {
        "finished:outputs/tree-001/01.threshold.md",
        "finished:outputs/tree-001/02.relative.md",
    }
    assert selected["node_id"] == "candidate:new-complex"


def test_planner_prefers_writeable_size_candidate_and_exposes_scope_context(tmp_path: Path) -> None:
    graph = rebuild_knowledge_graph(
        tmp_path,
        _planning_candidate_nodes(
            _planning_candidate(
                "candidate:thin",
                ["术语碎片"],
                chunks=["source#001"],
                priority=0.95,
                estimated_output_lines=150,
            ),
            _planning_candidate(
                "candidate:writeable",
                ["完整方法组"],
                chunks=["source#002", "source#003", "source#004"],
                priority=0.50,
                estimated_output_lines=360,
            ),
        ),
        {"version": 1, "records": []},
    )

    selected = next(node for node in graph["nodes"] if node["planner_selected"])
    context = build_selected_node_context(graph)

    assert selected["node_id"] == "candidate:writeable"
    assert selected["size_fit"] > 0.9
    assert "Expected output size: 360 lines" in context
    assert "Chunk cluster size: 3 chunks" in context


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
