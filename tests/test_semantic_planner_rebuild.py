import asyncio
import time
from pathlib import Path

import pytest

from rag.chunker import chunk_markdown
from tree.curriculum.candidate_nodes import rebuild_candidate_nodes_with_ai
from tree.curriculum.knowledge_nodes import load_knowledge_nodes, rebuild_knowledge_nodes
from tree.curriculum.graph import rebuild_knowledge_graph, rebuild_knowledge_graph_with_ai
from tree.curriculum.inventory import load_inventory, rebuild_source_inventory_with_ai
from tree.rag.client import RAGClient


class _FakeEmbedder:
    def embed(self, texts: str | list[str]) -> list[list[float]]:
        if isinstance(texts, str):
            texts = [texts]
        return [[0.1, 0.2, 0.3, 0.4] for _ in texts]


def _merge_group(group_id: str, title: str, index: int, *, concept: str | None = None) -> dict:
    concept = concept or title
    return {
        "group_id": group_id,
        "title_hint": title,
        "source_chunks": [f"physics/{group_id}#000"],
        "source_paths": [f"{group_id}.md"],
        "source_collection": "physics",
        "chunk_range": {"start": index, "end": index},
        "core_concepts": [concept],
        "formula_signatures": [],
        "teaching_role": "concept",
        "length_stats": {"token_estimate": 120, "estimated_output_lines": 300},
    }


def _paired_merge_inventory(pair_count: int) -> dict:
    groups = []
    for index in range(pair_count):
        title = f"主题{index}"
        groups.append(_merge_group(f"kg:{index}:a", title, index * 2))
        groups.append(_merge_group(f"kg:{index}:b", title, index * 2 + 1))
    return {"version": 2, "knowledge_groups": groups}


def test_chunk_metadata_exposes_weak_signals_and_formula_signatures(tmp_path: Path) -> None:
    text = "## 划重奖\n\n正文讲解折射率，公式为 $n = c / v$。\n\n**折射率** 表示光速关系。"

    chunks = chunk_markdown("lesson", text, chapter="physics")

    assert chunks[0]["section_id"] == "划重奖"
    assert chunks[0]["heading_path"] == ["划重奖"]
    assert chunks[0]["weak_concepts"] == ["折射率"]
    assert chunks[0]["raw_formulas"] == ["n = c / v"]
    assert chunks[0]["formula_signatures"] == ["n=c/v"]

    client = RAGClient(
        store_path=tmp_path / "rag-store",
        dimensions=4,
        embedder=_FakeEmbedder(),
    )
    try:
        count = client.index_file(
            "lesson",
            "lesson.md",
            text,
            chapter="physics",
            content_kind="source",
            source_collection="physics",
            path="unit/lesson.md",
        )
        indexed = client.scroll_chunks(filters={"content_kind": "source"}, include_drafts=False)
    finally:
        client.close()

    assert count == 1
    metadata = indexed[0]["metadata"]
    assert metadata["heading_path"] == ["划重奖"]
    assert metadata["token_estimate"] > 0
    assert metadata["weak_concepts"] == ["折射率"]
    assert metadata["raw_formulas"] == ["n = c / v"]
    assert metadata["formula_signatures"] == ["n=c/v"]


def test_inventory_ai_groups_chunks_sequentially_with_previous_active_group(tmp_path: Path) -> None:
    calls: list[dict] = []

    class SequentialAnalyzer:
        async def analyze_inventory_chunk(self, payload: dict) -> dict:
            calls.append(payload)
            chunk_index = payload["chunk"]["metadata"]["chunk_index"]
            if chunk_index == 0:
                assert payload["active_group"] is None
                return {
                    "merge_with_previous": False,
                    "is_complete_knowledge_point": False,
                    "title_hint": "折射定律",
                    "core_concepts": ["折射定律"],
                    "methods": ["建立折射率关系"],
                    "misconceptions": [],
                    "prerequisites": ["光线传播"],
                    "formula_roles": [{"formula": "n_1 sin theta_1 = n_2 sin theta_2", "role": "law"}],
                    "source_type": "lecture",
                    "teaching_role": "foundation",
                    "completeness": "partial",
                    "evidence_spans": ["折射定律描述"],
                    "summary": "介绍折射定律。",
                }
            assert payload["active_group"]["title_hint"] == "折射定律"
            assert payload["pair_metrics"]["chunk_index_distance"] == 1
            return {
                "merge_with_previous": True,
                "is_complete_knowledge_point": True,
                "title_hint": "折射定律",
                "core_concepts": ["折射定律", "证明"],
                "methods": ["建立折射率关系", "几何证明"],
                "misconceptions": [],
                "prerequisites": ["光线传播"],
                "formula_roles": [{"formula": "n_1 sin theta_1 = n_2 sin theta_2", "role": "derivation"}],
                "source_type": "lecture",
                "teaching_role": "foundation",
                "completeness": "complete",
                "evidence_spans": ["由几何关系推出"],
                "summary": "介绍并证明折射定律。",
            }

    inventory = asyncio.run(
        rebuild_source_inventory_with_ai(
            tmp_path,
            [
                {
                    "chunk_id": "physics-000",
                    "text": "折射定律描述入射角与折射角的关系。",
                    "metadata": {
                        "source_collection": "physics",
                        "filename": "optics.md",
                        "path": "optics.md",
                        "chunk_index": 0,
                        "section_id": "折射定律",
                        "token_estimate": 80,
                    },
                },
                {
                    "chunk_id": "physics-001",
                    "text": "由几何关系推出公式。",
                    "metadata": {
                        "source_collection": "physics",
                        "filename": "optics.md",
                        "path": "optics.md",
                        "chunk_index": 1,
                        "section_id": "折射定律",
                        "token_estimate": 60,
                    },
                },
            ],
            SequentialAnalyzer(),
        )
    )

    assert len(calls) == 2
    assert len(inventory["knowledge_groups"]) == 1
    group = inventory["knowledge_groups"][0]
    assert group["title_hint"] == "折射定律"
    assert group["source_chunks"] == ["physics/optics#000", "physics/optics#001"]
    assert group["core_concepts"] == ["折射定律", "证明"]
    assert group["completeness"] == "complete"


def test_inventory_rebuild_reuses_cached_ai_chunk_analysis(tmp_path: Path) -> None:
    calls = 0
    source_hit = {
        "chunk_id": "physics-000",
        "text": "折射定律描述入射角与折射角的关系。",
        "metadata": {
            "source_collection": "physics",
            "filename": "optics.md",
            "path": "optics.md",
            "chunk_index": 0,
            "section_id": "折射定律",
            "token_estimate": 80,
        },
    }

    class FirstAnalyzer:
        async def analyze_inventory_chunk(self, payload: dict) -> dict:
            nonlocal calls
            calls += 1
            return {
                "merge_with_previous": False,
                "is_complete_knowledge_point": True,
                "title_hint": "折射定律",
                "core_concepts": ["折射定律"],
                "methods": ["建立折射率关系"],
                "misconceptions": [],
                "prerequisites": ["几何光学"],
                "formula_roles": [],
                "source_type": "lecture",
                "teaching_role": "foundation",
                "completeness": "complete",
                "evidence_spans": ["折射定律描述"],
                "summary": "介绍折射定律。",
            }

    class FailingAnalyzer:
        async def analyze_inventory_chunk(self, payload: dict) -> dict:
            raise AssertionError("cached AI analysis should have been reused")

    first = asyncio.run(rebuild_source_inventory_with_ai(tmp_path, [source_hit], FirstAnalyzer()))
    second = asyncio.run(rebuild_source_inventory_with_ai(tmp_path, [source_hit], FailingAnalyzer()))

    assert calls == 1
    assert second["chunks"][0]["analysis_mode"] == "ai"
    assert second["chunks"][0]["core_concepts"] == first["chunks"][0]["core_concepts"]
    assert second["knowledge_groups"][0]["title_hint"] == "折射定律"


def test_inventory_rebuild_saves_completed_ai_chunks_before_cancellation(tmp_path: Path) -> None:
    source_hits = [
        {
            "chunk_id": "physics-000",
            "text": "折射定律描述入射角与折射角的关系。",
            "metadata": {
                "source_collection": "physics",
                "filename": "optics.md",
                "path": "optics.md",
                "chunk_index": 0,
                "section_id": "折射定律",
                "token_estimate": 80,
            },
        },
        {
            "chunk_id": "physics-001",
            "text": "证明使用几何关系。",
            "metadata": {
                "source_collection": "physics",
                "filename": "optics.md",
                "path": "optics.md",
                "chunk_index": 1,
                "section_id": "折射定律",
                "token_estimate": 60,
            },
        },
    ]

    class CancellingAnalyzer:
        async def analyze_inventory_chunk(self, payload: dict) -> dict:
            if payload["chunk"]["metadata"]["chunk_index"] == 1:
                raise asyncio.CancelledError()
            return {
                "merge_with_previous": False,
                "is_complete_knowledge_point": True,
                "title_hint": "折射定律",
                "core_concepts": ["折射定律"],
                "methods": [],
                "misconceptions": [],
                "prerequisites": [],
                "formula_roles": [],
                "source_type": "lecture",
                "teaching_role": "foundation",
                "completeness": "complete",
                "evidence_spans": ["折射定律描述"],
                "summary": "介绍折射定律。",
            }

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(rebuild_source_inventory_with_ai(tmp_path, source_hits, CancellingAnalyzer()))

    cached = load_inventory(tmp_path)

    assert len(cached["chunks"]) == 1
    assert cached["chunks"][0]["analysis_mode"] == "ai"
    assert cached["chunks"][0]["core_concepts"] == ["折射定律"]


def test_inventory_rebuild_updates_planner_progress_per_chunk(tmp_path: Path) -> None:
    class ProgressRecorder:
        def __init__(self) -> None:
            self.calls: list[dict] = []

        def planner_stage(self, **kwargs: object) -> None:
            self.calls.append(dict(kwargs))

    class Analyzer:
        async def analyze_inventory_chunk(self, payload: dict) -> dict:
            return {
                "merge_with_previous": False,
                "title_hint": "折射定律",
                "core_concepts": ["折射定律"],
                "methods": [],
                "misconceptions": [],
                "prerequisites": [],
                "formula_roles": [],
                "source_type": "lecture",
                "teaching_role": "foundation",
                "completeness": "complete",
                "evidence_spans": [],
                "summary": "介绍折射定律。",
            }

    progress = ProgressRecorder()

    asyncio.run(
        rebuild_source_inventory_with_ai(
            tmp_path,
            [
                {
                    "chunk_id": "physics-000",
                    "text": "折射定律描述入射角与折射角的关系。",
                    "metadata": {
                        "source_collection": "physics",
                        "filename": "optics.md",
                        "path": "optics.md",
                        "chunk_index": 0,
                    },
                },
                {
                    "chunk_id": "physics-001",
                    "text": "证明使用几何关系。",
                    "metadata": {
                        "source_collection": "physics",
                        "filename": "optics.md",
                        "path": "optics.md",
                        "chunk_index": 1,
                    },
                },
            ],
            Analyzer(),
            progress=progress,
        )
    )

    assert [call["details"]["chunks_done"] for call in progress.calls] == [0, 1, 1, 2]
    assert progress.calls[-1]["stage"] == "source_inventory"
    assert progress.calls[-1]["stage_index"] == 1
    assert progress.calls[-1]["details"]["chunks_total"] == 2
    assert progress.calls[-1]["details"]["current_chunk_index"] == 1


def test_inventory_rebuild_processes_files_concurrently_but_chunks_in_order(tmp_path: Path) -> None:
    events: list[tuple[str, str, int, float]] = []
    active_files = 0
    max_active_files = 0
    lock = asyncio.Lock()

    class Analyzer:
        async def analyze_inventory_chunk(self, payload: dict) -> dict:
            nonlocal active_files, max_active_files
            metadata = payload["chunk"]["metadata"]
            path = metadata["path"]
            chunk_index = metadata["chunk_index"]
            async with lock:
                events.append(("start", path, chunk_index, time.monotonic()))
                active_files += 1
                max_active_files = max(max_active_files, active_files)
            await asyncio.sleep(0.03)
            async with lock:
                events.append(("end", path, chunk_index, time.monotonic()))
                active_files -= 1
            return {
                "merge_with_previous": chunk_index == 1,
                "title_hint": f"{path} 知识点",
                "core_concepts": [f"{path} 概念 {chunk_index}"],
                "methods": [],
                "misconceptions": [],
                "prerequisites": [],
                "formula_roles": [],
                "source_type": "lecture",
                "teaching_role": "foundation",
                "completeness": "complete",
                "evidence_spans": [],
                "summary": f"{path} chunk {chunk_index}",
            }

    hits = []
    for filename in ("a.md", "b.md", "c.md"):
        for chunk_index in (0, 1):
            hits.append(
                {
                    "chunk_id": f"{filename}-{chunk_index}",
                    "text": f"{filename} chunk {chunk_index}",
                    "metadata": {
                        "source_collection": "physics",
                        "filename": filename,
                        "path": filename,
                        "chunk_index": chunk_index,
                    },
                }
            )

    inventory = asyncio.run(rebuild_source_inventory_with_ai(tmp_path, hits, Analyzer(), concurrency=2))

    assert max_active_files == 2
    assert len(inventory["knowledge_groups"]) == 3
    assert all(len(group["source_chunks"]) == 2 for group in inventory["knowledge_groups"])
    for filename in ("a.md", "b.md", "c.md"):
        starts = [event for event in events if event[0] == "start" and event[1] == filename]
        ends = [event for event in events if event[0] == "end" and event[1] == filename]
        assert [event[2] for event in starts] == [0, 1]
        assert [event[2] for event in ends] == [0, 1]
        assert starts[1][3] >= ends[0][3]


def test_inventory_merge_preserves_weak_group_metrics(tmp_path: Path) -> None:
    class SequentialAnalyzer:
        async def analyze_inventory_chunk(self, payload: dict) -> dict:
            chunk_index = payload["chunk"]["metadata"]["chunk_index"]
            return {
                "merge_with_previous": chunk_index == 1,
                "title_hint": "折射定律",
                "core_concepts": ["折射定律"] if chunk_index == 0 else ["证明"],
                "methods": [],
                "misconceptions": [],
                "prerequisites": [],
                "formula_roles": [],
                "source_type": "lecture",
                "teaching_role": "foundation",
                "completeness": "complete",
                "evidence_spans": [],
                "summary": "折射定律。",
            }

    inventory = asyncio.run(
        rebuild_source_inventory_with_ai(
            tmp_path,
            [
                {
                    "chunk_id": "physics-000",
                    "text": "折射定律：$n_1 sin theta_1 = n_2 sin theta_2$。",
                    "metadata": {
                        "source_collection": "physics",
                        "filename": "optics.md",
                        "path": "optics.md",
                        "chunk_index": 0,
                        "section_id": "第一章",
                        "heading_path": ["第一章", "折射定律"],
                        "weak_concepts": ["斯涅尔定律"],
                        "formula_signatures": ["n_1sintheta_1=n_2sintheta_2"],
                    },
                },
                {
                    "chunk_id": "physics-001",
                    "text": "证明使用几何关系。",
                    "metadata": {
                        "source_collection": "physics",
                        "filename": "optics.md",
                        "path": "optics.md",
                        "chunk_index": 1,
                        "section_id": "第一章",
                        "heading_path": ["第一章", "证明"],
                        "weak_concepts": ["几何证明"],
                    },
                },
            ],
            SequentialAnalyzer(),
        )
    )

    group = inventory["knowledge_groups"][0]

    assert group["heading_path"] == ["第一章", "折射定律", "证明"]
    assert "斯涅尔定律" in group["weak_concepts"]
    assert "几何证明" in group["weak_concepts"]
    assert group["formula_signatures"] == ["n_1sintheta_1=n_2sintheta_2"]


def test_inventory_postprocess_merges_review_header_fragment_into_adjacent_group(tmp_path: Path) -> None:
    class FragmentAnalyzer:
        async def analyze_inventory_chunk(self, payload: dict) -> dict:
            chunk_index = payload["chunk"]["metadata"]["chunk_index"]
            if chunk_index == 0:
                return {
                    "merge_with_previous": False,
                    "title_hint": "折射定律",
                    "core_concepts": ["折射定律"],
                    "methods": [],
                    "misconceptions": [],
                    "prerequisites": ["几何光学"],
                    "formula_roles": [],
                    "source_type": "lecture",
                    "teaching_role": "foundation",
                    "completeness": "complete",
                    "fragment_role": "complete",
                    "merge_confidence": "high",
                    "new_topic_reason": "明确介绍一个可教学主题。",
                    "evidence_spans": ["折射定律描述角度关系"],
                    "summary": "折射定律。",
                }
            return {
                "merge_with_previous": False,
                "title_hint": "温故知新",
                "core_concepts": [],
                "methods": [],
                "misconceptions": [],
                "prerequisites": [],
                "formula_roles": [],
                "source_type": "lecture",
                "teaching_role": "review",
                "completeness": "fragment",
                "fragment_role": "review",
                "merge_confidence": "low",
                "new_topic_reason": "",
                "evidence_spans": ["温故知新"],
                "summary": "复习提示。",
            }

    inventory = asyncio.run(
        rebuild_source_inventory_with_ai(
            tmp_path,
            [
                {
                    "chunk_id": "physics-000",
                    "text": "折射定律描述角度关系。",
                    "metadata": {
                        "source_collection": "physics",
                        "filename": "optics.md",
                        "path": "optics.md",
                        "chunk_index": 0,
                        "section_id": "折射定律",
                    },
                },
                {
                    "chunk_id": "physics-001",
                    "text": "温故知新",
                    "metadata": {
                        "source_collection": "physics",
                        "filename": "optics.md",
                        "path": "optics.md",
                        "chunk_index": 1,
                        "section_id": "温故知新",
                    },
                },
            ],
            FragmentAnalyzer(),
        )
    )

    assert len(inventory["knowledge_groups"]) == 1
    group = inventory["knowledge_groups"][0]
    assert group["title_hint"] == "折射定律"
    assert group["source_chunks"] == ["physics/optics#000", "physics/optics#001"]
    assert group["auxiliary_group_ids"]
    assert group["representative_chunks"][1]["fragment_role"] == "review"


def test_candidate_ai_merges_cross_file_groups_without_output_line_cap(tmp_path: Path) -> None:
    inventory = {
        "version": 2,
        "knowledge_groups": [
            {
                "group_id": "kg:physics:a",
                "title_hint": "折射定律",
                "source_chunks": ["physics/a#000"],
                "source_paths": ["a.md"],
                "source_collection": "physics",
                "chunk_range": {"start": 0, "end": 0},
                "core_concepts": ["折射定律", "折射率"],
                "prerequisites": ["光线传播"],
                "methods": ["定律应用"],
                "formula_roles": [{"formula": "n_1 sin theta_1 = n_2 sin theta_2", "role": "law"}],
                "formula_signatures": ["n_1sintheta_1=n_2sintheta_2"],
                "teaching_role": "foundation",
                "completeness": "complete",
                "length_stats": {"token_estimate": 900, "estimated_output_lines": 1200},
            },
            {
                "group_id": "kg:physics:b",
                "title_hint": "斯涅尔定律",
                "source_chunks": ["physics/b#000"],
                "source_paths": ["b.md"],
                "source_collection": "physics",
                "chunk_range": {"start": 0, "end": 0},
                "core_concepts": ["折射定律", "斯涅尔定律"],
                "prerequisites": [],
                "methods": ["定律应用"],
                "formula_roles": [{"formula": "n_1 sin theta_1 = n_2 sin theta_2", "role": "law"}],
                "formula_signatures": ["n_1sintheta_1=n_2sintheta_2"],
                "teaching_role": "foundation",
                "completeness": "complete",
                "length_stats": {"token_estimate": 850, "estimated_output_lines": 1150},
            },
        ],
        "collections": [],
    }

    class MergeBuilder:
        async def build_candidate_nodes(self, inventory_summary: dict, completed_collections: list[str]) -> dict:
            assert inventory_summary["group_pair_metrics"][0]["overall_similarity"] > 0
            return {
                "chapter_candidates": [
                    {
                        "candidate_id": "candidate:snell-law",
                        "merged_group_ids": ["kg:physics:a", "kg:physics:b"],
                        "canonical_title": "折射定律",
                        "primary_source_collection": "physics",
                        "source_collections": ["physics"],
                        "core_concepts": ["折射定律", "斯涅尔定律", "折射率"],
                        "prerequisite_concepts": [],
                        "formula_roles": [{"formula": "n_1 sin theta_1 = n_2 sin theta_2", "role": "law"}],
                        "representative_chunks": ["physics/a#000", "physics/b#000"],
                        "coverage_evidence": ["两个文件讲同一条定律"],
                        "teaching_role": "foundation",
                        "completeness": "complete",
                    }
                ]
            }

    nodes = asyncio.run(rebuild_candidate_nodes_with_ai(tmp_path, inventory, MergeBuilder()))

    assert nodes["group_pair_metrics"]
    assert nodes["merge_review_components"][0]["group_ids"] == ["kg:physics:a", "kg:physics:b"]
    assert nodes["merge_decisions"][0]["decision"] == "merged"
    candidate = nodes["chapter_candidates"][0]
    assert candidate["candidate_id"] == "candidate:snell-law"
    assert candidate["merged_group_ids"] == ["kg:physics:a", "kg:physics:b"]
    assert "光线传播" in candidate["prerequisite_concepts"]
    assert candidate["estimated_output_lines"] > 1000
    assert {chunk["chunk_ref"] for chunk in candidate["representative_chunks"]} == {
        "physics/a#000",
        "physics/b#000",
    }


def test_candidate_merge_review_is_batched_and_validated(tmp_path: Path) -> None:
    inventory = _paired_merge_inventory(7)
    batches: list[list[str]] = []

    class BatchReviewBuilder:
        async def review_candidate_merge_components(self, payload: dict) -> dict:
            component_ids = [item["component_id"] for item in payload["merge_review_components"]]
            batches.append(component_ids)
            return {
                "merge_decisions": [
                    {
                        "component_id": item["component_id"],
                        "group_ids": item["group_ids"],
                        "decision": "rejected",
                        "reason": "测试保留分开。",
                    }
                    for item in payload["merge_review_components"]
                ]
            }

        async def build_candidate_nodes(self, inventory_summary: dict, completed_collections: list[str]) -> dict:
            assert len(inventory_summary["merge_decisions"]) == 7
            return {"chapter_candidates": []}

    nodes = asyncio.run(
        rebuild_candidate_nodes_with_ai(
            tmp_path,
            inventory,
            BatchReviewBuilder(),
            candidate_merge_batch_size=3,
        )
    )

    assert [len(batch) for batch in batches] == [3, 3, 1]
    assert len(nodes["merge_decisions"]) == 7
    assert {decision["decision_source"] for decision in nodes["merge_decisions"]} == {"ai"}
    assert all(decision["attempt_count"] == 1 for decision in nodes["merge_decisions"])


def test_candidate_merge_review_repairs_only_missing_components(tmp_path: Path) -> None:
    inventory = _paired_merge_inventory(2)
    payload_sizes: list[int] = []

    class MissingThenRepairBuilder:
        async def review_candidate_merge_components(self, payload: dict) -> dict:
            payload_sizes.append(len(payload["merge_review_components"]))
            components = payload["merge_review_components"]
            if len(payload_sizes) == 1:
                components = components[:1]
            return {
                "merge_decisions": [
                    {
                        "component_id": item["component_id"],
                        "group_ids": item["group_ids"],
                        "decision": "rejected",
                        "reason": "测试判定。",
                    }
                    for item in components
                ]
            }

        async def build_candidate_nodes(self, inventory_summary: dict, completed_collections: list[str]) -> dict:
            return {"chapter_candidates": []}

    nodes = asyncio.run(
        rebuild_candidate_nodes_with_ai(
            tmp_path,
            inventory,
            MissingThenRepairBuilder(),
            candidate_merge_batch_size=3,
            candidate_merge_repair_attempts=2,
        )
    )

    assert payload_sizes == [2, 1]
    assert [decision["decision_source"] for decision in nodes["merge_decisions"]] == ["ai", "repair"]
    assert [decision["attempt_count"] for decision in nodes["merge_decisions"]] == [1, 2]
    assert nodes["merge_review_observability"]["repair_attempts"] == 1


def test_candidate_merge_review_failure_auto_merges_strong_component_without_fragment_leak(tmp_path: Path) -> None:
    inventory = _paired_merge_inventory(1)

    class FailingReviewBuilder:
        async def review_candidate_merge_components(self, payload: dict) -> dict:
            raise TimeoutError("merge review timed out")

        async def build_candidate_nodes(self, inventory_summary: dict, completed_collections: list[str]) -> dict:
            return {"chapter_candidates": []}

    nodes = asyncio.run(
        rebuild_candidate_nodes_with_ai(
            tmp_path,
            inventory,
            FailingReviewBuilder(),
            candidate_merge_batch_size=3,
            candidate_merge_repair_attempts=2,
        )
    )

    assert len(nodes["chapter_candidates"]) == 1
    candidate = nodes["chapter_candidates"][0]
    assert candidate["canonicalization_status"] == "auto_merged"
    assert candidate["merge_decision_source"] == "deterministic"
    assert candidate["merged_group_ids"] == ["kg:0:a", "kg:0:b"]
    assert nodes["merge_decisions"][0]["decision_source"] == "deterministic"
    assert nodes["merge_decisions"][0]["error_type"] == "TimeoutError"


def test_candidate_merge_review_reject_overrides_later_merged_candidate(tmp_path: Path) -> None:
    inventory = _paired_merge_inventory(1)

    class RejectThenMergeBuilder:
        async def review_candidate_merge_components(self, payload: dict) -> dict:
            component = payload["merge_review_components"][0]
            return {
                "merge_decisions": [
                    {
                        "component_id": component["component_id"],
                        "group_ids": component["group_ids"],
                        "decision": "rejected",
                        "reason": "不是同一教学节点。",
                    }
                ]
            }

        async def build_candidate_nodes(self, inventory_summary: dict, completed_collections: list[str]) -> dict:
            return {
                "chapter_candidates": [
                    {
                        "candidate_id": "candidate:wrong-merge",
                        "merged_group_ids": ["kg:0:a", "kg:0:b"],
                        "canonical_title": "错误合并",
                        "primary_source_collection": "physics",
                        "source_collections": ["physics"],
                        "core_concepts": ["主题0"],
                        "prerequisite_concepts": [],
                        "representative_chunks": ["physics/kg:0:a#000", "physics/kg:0:b#000"],
                    }
                ]
            }

    nodes = asyncio.run(rebuild_candidate_nodes_with_ai(tmp_path, inventory, RejectThenMergeBuilder()))

    assert len(nodes["chapter_candidates"]) == 2
    assert "candidate:wrong-merge" not in {item["candidate_id"] for item in nodes["chapter_candidates"]}
    assert all(len(item["merged_group_ids"]) == 1 for item in nodes["chapter_candidates"])


def test_candidate_strict_merge_review_records_pending_when_ai_omits_strong_component(tmp_path: Path) -> None:
    inventory = {
        "version": 2,
        "knowledge_groups": [
            {
                "group_id": "kg:forced:intro",
                "title_hint": "§10-3 受迫振动 共振",
                "source_chunks": ["physics/a#000"],
                "source_paths": ["chapter.md"],
                "source_collection": "physics",
                "chunk_range": {"start": 4, "end": 4},
                "core_concepts": ["受迫振动", "共振"],
                "formula_signatures": [],
                "teaching_role": "concept",
                "length_stats": {"token_estimate": 200, "estimated_output_lines": 320},
            },
            {
                "group_id": "kg:forced:detail",
                "title_hint": "受迫振动与共振",
                "source_chunks": ["physics/b#000"],
                "source_paths": ["chapter.md"],
                "source_collection": "physics",
                "chunk_range": {"start": 5, "end": 5},
                "core_concepts": ["受迫振动", "共振", "稳态"],
                "formula_signatures": [],
                "teaching_role": "concept",
                "length_stats": {"token_estimate": 260, "estimated_output_lines": 360},
            },
        ],
    }

    class OmitMergeBuilder:
        async def build_candidate_nodes(self, inventory_summary: dict, completed_collections: list[str]) -> dict:
            assert inventory_summary["merge_review_components"][0]["group_ids"] == [
                "kg:forced:intro",
                "kg:forced:detail",
            ]
            return {"chapter_candidates": []}

    nodes = asyncio.run(rebuild_candidate_nodes_with_ai(tmp_path, inventory, OmitMergeBuilder()))

    assert not [item for item in nodes["diagnostics"] if item["kind"] == "canonical_merge_pending"]
    assert len(nodes["chapter_candidates"]) == 1
    candidate = nodes["chapter_candidates"][0]
    assert candidate["canonicalization_status"] == "auto_merged"
    assert candidate["merge_decision_source"] == "deterministic"
    assert candidate["merged_group_ids"] == ["kg:forced:intro", "kg:forced:detail"]


def test_candidate_uncertain_merge_component_becomes_single_blocked_pending_node(tmp_path: Path) -> None:
    inventory = {
        "version": 2,
        "knowledge_groups": [
            {
                "group_id": "kg:uncertain:a",
                "title_hint": "同名主题",
                "source_chunks": ["physics/a#000"],
                "source_paths": ["a.md"],
                "source_collection": "physics",
                "chunk_range": {"start": 1, "end": 1},
                "core_concepts": ["主题概念"],
                "prerequisites": ["前置概念"],
                "formula_signatures": [],
                "teaching_role": "concept",
                "length_stats": {"token_estimate": 220, "estimated_output_lines": 320},
            },
            {
                "group_id": "kg:uncertain:b",
                "title_hint": "同名主题",
                "source_chunks": ["physics/b#000"],
                "source_paths": ["b.md"],
                "source_collection": "physics",
                "chunk_range": {"start": 9, "end": 9},
                "core_concepts": ["主题概念", "另一个侧面"],
                "formula_signatures": [],
                "teaching_role": "concept",
                "length_stats": {"token_estimate": 260, "estimated_output_lines": 360},
            },
        ],
    }

    class UncertainBuilder:
        async def build_candidate_nodes(self, inventory_summary: dict, completed_collections: list[str]) -> dict:
            component = inventory_summary["merge_review_components"][0]
            return {
                "merge_decisions": [
                    {
                        "component_id": component["component_id"],
                        "group_ids": component["group_ids"],
                        "decision": "uncertain",
                        "reason": "语义边界仍不确定。",
                    }
                ],
                "chapter_candidates": [],
            }

    nodes = asyncio.run(rebuild_candidate_nodes_with_ai(tmp_path, inventory, UncertainBuilder()))

    assert len(nodes["chapter_candidates"]) == 1
    candidate = nodes["chapter_candidates"][0]
    assert candidate["canonicalization_status"] == "blocked_pending"
    assert candidate["merge_decision_source"] == "fallback_blocked"
    assert candidate["schedulable"] is False
    assert candidate["blocked_reason"] == "canonical_merge_pending"
    assert candidate["merged_group_ids"] == ["kg:uncertain:a", "kg:uncertain:b"]
    assert "前置概念" in candidate["prerequisite_concepts"]


def test_auxiliary_only_inventory_group_does_not_create_schedulable_candidate(tmp_path: Path) -> None:
    inventory = {
        "version": 2,
        "knowledge_groups": [
            {
                "group_id": "kg:noise",
                "title_hint": "§",
                "source_chunks": ["physics/a#000"],
                "source_paths": ["a.md"],
                "source_collection": "physics",
                "chunk_range": {"start": 0, "end": 0},
                "core_concepts": [],
                "teaching_role": "review",
                "fragment_role": "header",
                "auxiliary_only": True,
                "length_stats": {"token_estimate": 30, "estimated_output_lines": 150},
            }
        ],
    }

    nodes = rebuild_knowledge_nodes(tmp_path, inventory)

    assert nodes["chapter_candidates"][0]["canonicalization_status"] == "auxiliary_only"
    assert nodes["chapter_candidates"][0]["schedulable"] is False


def test_candidate_clean_title_match_builds_merge_review_component(tmp_path: Path) -> None:
    inventory = {
        "version": 2,
        "knowledge_groups": [
            {
                "group_id": "kg:brewster:a",
                "title_hint": "布儒斯特定律",
                "source_chunks": ["optics/a#000"],
                "source_paths": ["a.md"],
                "source_collection": "optics",
                "chunk_range": {"start": 1, "end": 1},
                "core_concepts": ["布儒斯特定律", "布儒斯特角"],
                "formula_signatures": ["tani_b=n_2/n_1"],
                "length_stats": {"token_estimate": 200, "estimated_output_lines": 300},
            },
            {
                "group_id": "kg:brewster:b",
                "title_hint": "布儒斯特定律",
                "source_chunks": ["optics/b#000"],
                "source_paths": ["b.md"],
                "source_collection": "optics",
                "chunk_range": {"start": 8, "end": 8},
                "core_concepts": ["布儒斯特定律", "完全偏振光"],
                "formula_signatures": ["tani_b=n_2/n_1"],
                "length_stats": {"token_estimate": 220, "estimated_output_lines": 310},
            },
        ],
    }

    nodes = rebuild_knowledge_nodes(tmp_path, inventory)

    assert nodes["merge_review_components"][0]["group_ids"] == ["kg:brewster:a", "kg:brewster:b"]
    assert nodes["merge_review_components"][0]["reason"] == "clean_title_match"


def test_knowledge_nodes_api_reads_legacy_candidate_nodes_schema(tmp_path: Path) -> None:
    legacy_path = tmp_path / ".tree/runtime/candidate-nodes.json"
    legacy_path.parent.mkdir(parents=True)
    legacy_path.write_text(
        """
        {
          "version": 1,
          "kind": "candidate_nodes",
          "chapter_candidates": [
            {
              "candidate_id": "candidate:legacy",
              "title_hint": "旧节点",
              "core_concepts": ["旧节点"]
            }
          ]
        }
        """,
        encoding="utf-8",
    )

    nodes = load_knowledge_nodes(tmp_path)

    assert nodes["kind"] == "knowledge_nodes"
    assert nodes["knowledge_nodes"][0]["node_id"] == "candidate:legacy"
    assert nodes["chapter_candidates"][0]["candidate_id"] == "candidate:legacy"


def test_graph_builder_accepts_knowledge_nodes_schema(tmp_path: Path) -> None:
    nodes = {
        "version": 1,
        "kind": "knowledge_nodes",
        "knowledge_nodes": [
            {
                "node_id": "candidate:root",
                "title": "根节点",
                "core_concepts": ["根节点"],
                "source_collections": ["lesson"],
                "representative_chunks": [{"chunk_ref": "lesson#000", "core_concepts": ["根节点"]}],
                "estimated_output_lines": 320,
            }
        ],
    }

    graph = rebuild_knowledge_graph(
        tmp_path,
        nodes,
        {"version": 1, "records": []},
    )

    assert graph["nodes"][0]["node_id"] == "candidate:root"
    assert graph["nodes"][0]["status"] == "planned"


def test_knowledge_node_group_pair_metrics_explain_unavailable_embedding_similarity(tmp_path: Path) -> None:
    inventory = {
        "knowledge_groups": [
            {
                "group_id": "kg:a",
                "source_collection": "book",
                "source_chunks": ["book/a#000"],
                "source_paths": ["book.md"],
                "chunk_range": {"start": 0, "end": 0},
                "heading_path": ["第一章", "定义"],
                "section_ids": ["第一章/定义"],
                "core_concepts": ["定义"],
                "formula_signatures": [],
                "length_stats": {"token_estimate": 100},
            },
            {
                "group_id": "kg:b",
                "source_collection": "book",
                "source_chunks": ["book/a#001"],
                "source_paths": ["book.md"],
                "chunk_range": {"start": 1, "end": 1},
                "heading_path": ["第一章", "定义证明"],
                "section_ids": ["第一章/定义证明"],
                "core_concepts": ["定义"],
                "formula_signatures": [],
                "length_stats": {"token_estimate": 120},
            },
        ]
    }

    nodes = rebuild_knowledge_nodes(tmp_path, inventory)
    metric = nodes["group_pair_metrics"][0]

    assert metric["embedding_similarity"]["status"] == "unavailable"
    assert metric["heading_section_continuity"] > 0


def test_root_selector_sends_top5_to_ai_and_uses_selected_root(tmp_path: Path) -> None:
    candidate_nodes = {
        "version": 1,
        "chapter_candidates": [
            {
                "candidate_id": f"candidate:{index}",
                "status": "pending",
                "title_hint": f"节点{index}",
                "primary_source_collection": "lesson",
                "source_collections": ["lesson"],
                "core_concepts": [f"概念{index}"],
                "prerequisite_concepts": [],
                "prerequisite_candidates": [],
                "representative_chunks": [{"chunk_ref": f"lesson#{index:03d}", "core_concepts": [f"概念{index}"]}],
                "selection_priority": 1.0 - index * 0.05,
                "estimated_output_lines": 320,
            }
            for index in range(6)
        ],
    }

    class RootSelector:
        async def select_root_candidate(self, payload: dict) -> dict:
            assert len(payload["root_candidates"]) == 5
            assert payload["root_candidates"][0]["node_id"] == "candidate:0"
            return {
                "selected_root_group_id": "candidate:3",
                "reason": "AI judges candidate 3 as the cleaner conceptual beginning.",
                "uncertainty": "low",
                "teaching_order_suggestion": ["candidate:3", "candidate:0"],
            }

    graph = asyncio.run(
        rebuild_knowledge_graph_with_ai(
            tmp_path,
            candidate_nodes,
            {"version": 1, "records": []},
            RootSelector(),
        )
    )

    selected = next(node for node in graph["nodes"] if node["planner_selected"])
    assert selected["node_id"] == "candidate:3"
    assert graph["planner"]["root_selection_mode"] == "ai_top5"
    assert graph["planner"]["root_ai_selection"]["selected_root_group_id"] == "candidate:3"
