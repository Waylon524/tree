import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

import tree.engine as engine_module
from tree.agents.parsers import ParseError, parse_exam_output
from tree.agents.prompts import EXAMINER_PROMPT, STUDENT_PROMPT, WRITER_PROMPT
from tree.curriculum.candidate_nodes import rebuild_candidate_nodes, rebuild_candidate_nodes_with_ai, save_candidate_nodes
from tree.curriculum.graph import build_selected_node_context, load_knowledge_graph, rebuild_knowledge_graph
from tree.curriculum.inventory import rebuild_source_inventory, rebuild_source_inventory_with_ai
from tree.curriculum.ledger import load_ledger
from tree.engine import TreeEngine, _pending_materials, persist_writer_result
from tree.io import paths
from tree.rag.client import RAGClient
from tree.state.manager import StateManager
from tree.state.models import ChapterRecord, ExamSections, IterationState, PipelineState, Route, WriterResult
from tree.agents.writer import WriterAgent


def test_parse_exam_output_requires_covered_node_ids() -> None:
    raw = """## [Next_Knowledge_Point]
01. 变量

## [Blind_Exam]
Q1

## [Answer_Key]
A1

## [Writer_Instructions]
teach
"""

    with pytest.raises(ParseError, match="Covered_Node_IDs"):
        parse_exam_output(raw)


def test_parse_exam_output_reads_multiple_covered_node_ids() -> None:
    raw = """## [Next_Knowledge_Point]
01. 变量与表达式

## [Covered_Node_IDs]
candidate:variables, candidate:expressions

## [Blind_Exam]
Q1

## [Answer_Key]
A1

## [Writer_Instructions]
teach
"""

    sections = parse_exam_output(raw)

    assert sections.covered_node_ids == ["candidate:variables", "candidate:expressions"]


def test_branchrun_prompt_contract_removes_phase_c_and_adds_span_scope() -> None:
    assert "Chapter Continuation Scan" not in EXAMINER_PROMPT
    assert "Phase C" not in EXAMINER_PROMPT
    assert "Covered_Node_IDs" in EXAMINER_PROMPT
    assert "branch span" in WRITER_PROMPT
    assert "exactly one knowledge point" not in WRITER_PROMPT
    assert "BranchRun snapshot" in STUDENT_PROMPT


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
    prerequisites: list[str] | None = None,
    methods: list[str] | None = None,
    formulas: list[str] | None = None,
    section_id: str = "",
    source_type: str = "lecture",
    teaching_role: str = "concept",
) -> dict:
    return {
        "chunk_ref": chunk_ref,
        "chunk_id": chunk_ref,
        "chunk_index": int(chunk_ref.rsplit("#", 1)[-1]),
        "source_collection": collection,
        "path": f"{collection}.md",
        "section_id": section_id,
        "core_concepts": concepts,
        "prerequisites": prerequisites or [],
        "methods": methods or [],
        "formulas": formulas or [],
        "summary": " ".join(concepts),
        "source_type": source_type,
        "teaching_role": teaching_role,
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


def test_isolated_section_id_noise_does_not_become_core_concept_or_title(tmp_path: Path) -> None:
    inventory = rebuild_source_inventory(
        tmp_path,
        [
            {
                "chunk_id": "noise-001",
                "text": "这段正文介绍材料在外界条件干预下产生的现象，但没有出现这个误识别标题。",
                "metadata": {
                    "source_collection": "lesson",
                    "filename": "lesson.md",
                    "chunk_index": 1,
                    "section_id": "划重奖",
                    "chunk_type": "narrative",
                },
            }
        ],
    )

    chunk = inventory["chunks"][0]
    nodes = rebuild_candidate_nodes(tmp_path, inventory)
    candidate = nodes["chapter_candidates"][0]

    assert "划重奖" not in chunk["core_concepts"]
    assert "划重奖" not in candidate["core_concepts"]
    assert "划重奖" not in candidate["title_hint"]


def test_candidate_node_ids_are_stable_when_unrelated_earlier_cluster_is_added(tmp_path: Path) -> None:
    base_inventory = {
        "version": 1,
        "chunks": [
            _planning_chunk("b/a#001", "b", ["循环"]),
        ],
        "collections": [
            {
                "source_collection": "a",
                "core_concepts": ["网络协议"],
                "related_collections": [],
            },
            {
                "source_collection": "b",
                "core_concepts": ["循环"],
                "related_collections": [],
            },
        ],
    }
    expanded_inventory = {
        **base_inventory,
        "chunks": [
            _planning_chunk("a/a#001", "a", ["网络协议"]),
            *base_inventory["chunks"],
        ],
    }

    base_nodes = rebuild_candidate_nodes(tmp_path, base_inventory)
    expanded_nodes = rebuild_candidate_nodes(tmp_path, expanded_inventory)

    base_loop = next(
        item
        for item in base_nodes["chapter_candidates"]
        if {chunk["chunk_ref"] for chunk in item["representative_chunks"]} == {"b/a#001"}
    )
    expanded_loop = next(
        item
        for item in expanded_nodes["chapter_candidates"]
        if {chunk["chunk_ref"] for chunk in item["representative_chunks"]} == {"b/a#001"}
    )

    assert expanded_loop["candidate_id"] == base_loop["candidate_id"]


def test_inventory_ai_empty_prerequisites_can_clear_rule_prerequisites(tmp_path: Path) -> None:
    class EmptyPrerequisiteAnalyzer:
        async def analyze_source_chunk(self, chunk: dict) -> dict:
            return {
                "core_concepts": ["应用案例"],
                "methods": [],
                "misconceptions": [],
                "prerequisites": [],
                "source_type": "application",
                "teaching_role": "application",
                "summary": "应用案例",
            }

    inventory = asyncio.run(
        rebuild_source_inventory_with_ai(
            tmp_path,
            [
                {
                    "chunk_id": "app-001",
                    "text": "先修：基础概念。随后讲解一个应用案例。",
                    "metadata": {
                        "source_collection": "lesson",
                        "filename": "lesson.md",
                        "chunk_index": 1,
                        "section_id": "应用案例",
                    },
                }
            ],
            EmptyPrerequisiteAnalyzer(),
        )
    )

    assert inventory["chunks"][0]["prerequisites"] == []
    assert inventory["chunks"][0]["core_concepts"] == ["应用案例"]


def test_ai_candidate_keeps_fallback_and_representative_prerequisites(tmp_path: Path) -> None:
    inventory = {
        "version": 1,
        "chunks": [
            _planning_chunk(
                "lesson#001",
                "lesson",
                ["应用案例"],
                prerequisites=["基础概念"],
                section_id="应用案例",
                source_type="application",
                teaching_role="application",
            )
        ],
        "collections": [{"source_collection": "lesson", "core_concepts": ["应用案例"], "related_collections": []}],
    }
    fallback = rebuild_candidate_nodes(tmp_path, inventory)
    fallback_candidate = fallback["chapter_candidates"][0]

    class EmptyPrerequisiteBuilder:
        async def build_candidate_nodes(self, inventory_summary: dict, completed_collections: list[str]) -> dict:
            return {
                "chapter_candidates": [
                    {
                        "candidate_id": fallback_candidate["candidate_id"],
                        "title_hint": "应用案例",
                        "primary_source_collection": "lesson",
                        "source_collections": ["lesson"],
                        "core_concepts": ["应用案例"],
                        "prerequisite_concepts": [],
                        "prerequisite_candidates": [],
                        "representative_chunks": ["lesson#001"],
                        "reason": "application node",
                    }
                ]
            }

    nodes = asyncio.run(rebuild_candidate_nodes_with_ai(tmp_path, inventory, EmptyPrerequisiteBuilder()))
    candidate = nodes["chapter_candidates"][0]

    assert "基础概念" in candidate["prerequisite_concepts"]


def test_candidate_ai_canonical_title_does_not_reintroduce_low_confidence_section_noise(tmp_path: Path) -> None:
    inventory = {
        "version": 2,
        "knowledge_groups": [
            {
                "group_id": "kg:noise",
                "source_collection": "lesson",
                "source_chunks": ["lesson#001"],
                "source_paths": ["lesson.md"],
                "section_ids": ["划重奖"],
                "low_confidence_section_terms": ["划重奖"],
                "title_hint": "应用案例",
                "core_concepts": ["应用案例"],
                "prerequisites": [],
                "representative_chunks": [
                    {
                        "chunk_ref": "lesson#001",
                        "section_id": "划重奖",
                        "low_confidence_section_terms": ["划重奖"],
                        "core_concepts": ["应用案例"],
                    }
                ],
                "length_stats": {"estimated_output_lines": 320},
            }
        ],
    }

    class NoisyTitleBuilder:
        async def build_candidate_nodes(self, inventory_summary: dict, completed_collections: list[str]) -> dict:
            return {
                "chapter_candidates": [
                    {
                        "candidate_id": "candidate:lesson:noisy",
                        "merged_group_ids": ["kg:noise"],
                        "canonical_title": "划重奖、应用案例",
                        "title_hint": "划重奖、应用案例",
                        "primary_source_collection": "lesson",
                        "source_collections": ["lesson"],
                        "core_concepts": ["划重奖", "应用案例"],
                        "prerequisite_concepts": [],
                        "representative_chunks": ["lesson#001"],
                    }
                ]
            }

    nodes = asyncio.run(rebuild_candidate_nodes_with_ai(tmp_path, inventory, NoisyTitleBuilder()))
    candidate = nodes["chapter_candidates"][0]

    assert "划重奖" not in candidate["title_hint"]
    assert "划重奖" not in candidate["canonical_title"]
    assert "划重奖" not in candidate["core_concepts"]


def test_root_selector_penalizes_noisy_post_foundation_node_with_missing_prerequisites(tmp_path: Path) -> None:
    graph = rebuild_knowledge_graph(
        tmp_path,
        _planning_candidate_nodes(
            {
                "candidate_id": "candidate:noisy-application",
                "status": "pending",
                "title_hint": "划重奖、应用案例",
                "primary_source_collection": "lesson",
                "source_collections": ["lesson"],
                "core_concepts": ["划重奖", "应用案例", "操作结果", "检查方法"],
                "prerequisite_concepts": [],
                "prerequisite_candidates": [],
                "representative_chunks": [
                    {
                        "chunk_ref": "lesson#002",
                        "section_id": "划重奖",
                        "core_concepts": ["划重奖", "应用案例"],
                        "prerequisites": ["基础概念"],
                        "source_type": "mixed",
                        "teaching_role": "application",
                    }
                ],
                "selection_priority": 0.95,
                "estimated_output_lines": 360,
            },
            _planning_candidate(
                "candidate:foundation",
                ["基础概念", "基本定义"],
                chunks=["lesson#001"],
                sources=["lesson"],
                priority=0.10,
                estimated_output_lines=320,
            ),
        ),
        {"version": 1, "records": []},
    )

    selected = next(node for node in graph["nodes"] if node["planner_selected"])
    noisy = next(node for node in graph["nodes"] if node["node_id"] == "candidate:noisy-application")

    assert selected["node_id"] == "candidate:foundation"
    assert noisy["warnings"]
    assert noisy["root_score"] < 0.5


def test_clean_foundation_node_with_no_prerequisites_can_remain_root(tmp_path: Path) -> None:
    graph = rebuild_knowledge_graph(
        tmp_path,
        _planning_candidate_nodes(
            _planning_candidate(
                "candidate:foundation",
                ["基础概念", "基本定义"],
                chunks=["lesson#001"],
                sources=["lesson"],
                priority=0.10,
                estimated_output_lines=320,
            )
        ),
        {"version": 1, "records": []},
    )

    selected = next(node for node in graph["nodes"] if node["planner_selected"])

    assert selected["node_id"] == "candidate:foundation"
    assert selected["root_score"] > 0.45


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


def test_finished_trunk_does_not_absorb_candidate_from_concept_subset_only(tmp_path: Path) -> None:
    graph = rebuild_knowledge_graph(
        tmp_path,
        _planning_candidate_nodes(
            _planning_candidate(
                "candidate:details",
                ["溶解度阈值", "相对溶解度"],
                chunks=["source#009"],
                sources=["8. 沉淀溶解平衡"],
            ),
        ),
        {
            "version": 1,
            "records": [
                _planning_ledger_record(
                    "outputs/tree-001/01.overview.md",
                    ["沉淀溶解平衡概览", "溶解度阈值", "相对溶解度", "晶格能", "离子积"],
                    chunks=["source#001"],
                    sources=["8. 沉淀溶解平衡"],
                )
            ],
        },
    )

    candidate = next(node for node in graph["nodes"] if node["node_id"] == "candidate:details")

    assert candidate["status"] == "planned"
    assert candidate["finished_solvability"] < 0.82


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


def test_planner_keeps_size_observation_without_split_needed_edges(tmp_path: Path) -> None:
    graph = rebuild_knowledge_graph(
        tmp_path,
        _planning_candidate_nodes(
            _planning_candidate(
                "candidate:broad",
                [f"概念{i}" for i in range(24)],
                chunks=[f"source#{i:03d}" for i in range(12)],
                sources=["source-a", "source-b"],
                estimated_output_lines=760,
            ),
        ),
        {"version": 1, "records": []},
    )

    broad = next(node for node in graph["nodes"] if node["node_id"] == "candidate:broad")

    assert broad["estimated_output_lines"] == 760
    assert broad["size_fit"] < 0.6
    assert not [edge for edge in graph["edges"] if edge["relation"] == "split_needed"]
    assert "split_needed_count" not in graph["stats"]


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


def test_refresh_planner_artifacts_does_not_call_examiner_chapter_scan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def no_candidate_nodes(*args, **kwargs):
        return {"version": 1, "chapter_candidates": []}

    class ExaminerShouldNotRun:
        async def compose_exam(self, *args, **kwargs):
            raise AssertionError("examiner should not choose branch scheduling")

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

    result = asyncio.run(TreeEngine._refresh_planner_artifacts(fake_engine, PipelineState()))

    assert result is None


def test_run_marks_inventory_stage_before_rebuilding_source_inventory(tmp_path: Path) -> None:
    class FakeProgress:
        def __init__(self):
            self.calls = []

        def reset(self):
            self.calls.append({"stage": "reset"})

        def learning_stage(self, **kwargs):
            self.calls.append(kwargs)

    class FakeTracer:
        def log_pipeline_start(self):
            pass

    progress = FakeProgress()

    async def prepare_sources():
        return None

    async def rebuild_inventory():
        assert progress.calls[-1]["stage"] == "source_inventory"
        assert progress.calls[-1]["stage_index"] == 1
        raise RuntimeError("stop after inventory stage")

    fake_engine = SimpleNamespace(
        settings=SimpleNamespace(project_root=tmp_path),
        tracer=FakeTracer(),
        progress=progress,
        _raise_if_stop_requested=lambda: None,
        _prepare_source_materials_for_loop=prepare_sources,
        _rebuild_source_inventory_from_rag=rebuild_inventory,
    )

    with pytest.raises(RuntimeError, match="stop after inventory stage"):
        asyncio.run(TreeEngine.run(fake_engine))


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
    ledger = load_ledger(tmp_path)

    assert graph["stats"]["finished_count"] == 1
    assert graph["nodes"][0]["node_id"] == "finished:outputs/tree-001/01.变量.md"
    assert ledger["records"][0]["hit_chunks"] == ["lesson#001"]


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


def test_writer_agent_treats_exam_too_broad_text_as_draft_content() -> None:
    class FakeClient:
        async def call(self, *args):
            return "EXAM_TOO_BROAD\n# 01. 仍然写成草稿"

    class FakeLoader:
        def load(self, name: str) -> str:
            assert name == "writer"
            return "writer system"

    writer = WriterAgent(FakeClient(), FakeLoader())

    result = asyncio.run(
        writer.create_or_optimize(
            "01. 测试知识点",
            "01",
            "Bottleneck Report",
            [],
            [],
        )
    )

    assert result.is_exam_too_broad is False
    assert result.draft_content.startswith("EXAM_TOO_BROAD")


def test_persist_writer_result_rejects_obsolete_exam_too_broad_control(tmp_path: Path) -> None:
    iter_state = IterationState(chapter="tree-001", file_seq="01", knowledge_point="测试")

    with pytest.raises(ValueError, match="obsolete EXAM_TOO_BROAD"):
        persist_writer_result(
            tmp_path,
            iter_state,
            WriterResult(is_exam_too_broad=True, bloat_description="too long"),
        )


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


def test_refresh_planner_artifacts_returns_none_when_planner_has_no_selected_node(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def no_candidate_nodes(*args, **kwargs):
        return {"version": 1, "chapter_candidates": []}

    class ExaminerShouldNotRun:
        async def compose_exam(self, *args, **kwargs):
            raise AssertionError("examiner should not decide branch scheduling")

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

    result = asyncio.run(TreeEngine._refresh_planner_artifacts(fake_engine, PipelineState()))

    assert result is None
