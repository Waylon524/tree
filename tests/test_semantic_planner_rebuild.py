import asyncio
from pathlib import Path

from rag.chunker import chunk_markdown
from tree.curriculum.candidate_nodes import rebuild_candidate_nodes_with_ai
from tree.curriculum.knowledge_nodes import load_knowledge_nodes, rebuild_knowledge_nodes
from tree.curriculum.graph import rebuild_knowledge_graph, rebuild_knowledge_graph_with_ai
from tree.curriculum.inventory import rebuild_source_inventory_with_ai
from tree.rag.client import RAGClient


class _FakeEmbedder:
    def embed(self, texts: str | list[str]) -> list[list[float]]:
        if isinstance(texts, str):
            texts = [texts]
        return [[0.1, 0.2, 0.3, 0.4] for _ in texts]


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

    candidate = nodes["chapter_candidates"][0]
    assert candidate["candidate_id"] == "candidate:snell-law"
    assert candidate["merged_group_ids"] == ["kg:physics:a", "kg:physics:b"]
    assert "光线传播" in candidate["prerequisite_concepts"]
    assert candidate["estimated_output_lines"] > 1000
    assert {chunk["chunk_ref"] for chunk in candidate["representative_chunks"]} == {
        "physics/a#000",
        "physics/b#000",
    }


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
