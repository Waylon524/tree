from pathlib import Path
import asyncio

from tree.curriculum.candidate_nodes import rebuild_candidate_nodes, rebuild_candidate_nodes_with_ai


def _chunk(
    chunk_ref: str,
    collection: str,
    concepts: list[str],
    prerequisites: list[str] | None = None,
) -> dict:
    return {
        "chunk_ref": chunk_ref,
        "chunk_id": chunk_ref,
        "chunk_index": int(chunk_ref.rsplit("#", 1)[-1]),
        "source_collection": collection,
        "path": f"{collection}.md",
        "filename": f"{collection}.md",
        "section_id": "",
        "core_concepts": concepts,
        "prerequisites": prerequisites or [],
        "methods": [],
        "misconceptions": [],
        "summary": " ".join(concepts),
    }


def test_candidate_nodes_split_weakly_related_chunks_inside_one_collection(tmp_path: Path) -> None:
    inventory = {
        "version": 1,
        "chunks": [
            _chunk("1/a#001", "1", ["变量"]),
            _chunk("1/a#002", "1", ["网络协议"]),
        ],
        "collections": [
            {
                "source_collection": "1",
                "doc_count": 1,
                "chunk_count": 2,
                "paths": ["1.md"],
                "section_ids": [],
                "core_concepts": ["变量", "网络协议"],
                "representative_chunks": [],
                "related_collections": [],
            }
        ],
    }

    nodes = rebuild_candidate_nodes(tmp_path, inventory)

    candidates = nodes["chapter_candidates"]
    assert len(candidates) == 2
    assert {tuple(item["source_collections"]) for item in candidates} == {("1",)}
    assert sorted(
        tuple(chunk["chunk_ref"] for chunk in item["representative_chunks"])
        for item in candidates
    ) == [("1/a#001",), ("1/a#002",)]


def test_candidate_nodes_merge_strongly_related_chunks_across_collections(tmp_path: Path) -> None:
    inventory = {
        "version": 1,
        "chunks": [
            _chunk("1/a#001", "1", ["循环"], prerequisites=["变量"]),
            _chunk("2/b#001", "2", ["循环", "循环应用"], prerequisites=["变量"]),
            _chunk("3/c#001", "3", ["数据库事务"]),
        ],
        "collections": [
            {
                "source_collection": "1",
                "doc_count": 1,
                "chunk_count": 1,
                "paths": ["1.md"],
                "section_ids": [],
                "core_concepts": ["循环"],
                "representative_chunks": [],
                "related_collections": [],
            },
            {
                "source_collection": "2",
                "doc_count": 1,
                "chunk_count": 1,
                "paths": ["2.md"],
                "section_ids": [],
                "core_concepts": ["循环", "循环应用"],
                "representative_chunks": [],
                "related_collections": [],
            },
            {
                "source_collection": "3",
                "doc_count": 1,
                "chunk_count": 1,
                "paths": ["3.md"],
                "section_ids": [],
                "core_concepts": ["数据库事务"],
                "representative_chunks": [],
                "related_collections": [],
            },
        ],
    }

    nodes = rebuild_candidate_nodes(tmp_path, inventory)

    candidates = nodes["chapter_candidates"]
    loop_candidate = next(
        item for item in candidates if set(item["source_collections"]) == {"1", "2"}
    )
    assert {chunk["chunk_ref"] for chunk in loop_candidate["representative_chunks"]} == {
        "1/a#001",
        "2/b#001",
    }
    assert any(item["source_collections"] == ["3"] for item in candidates)


def test_ai_enrichment_preserves_multiple_clusters_from_one_collection(tmp_path: Path) -> None:
    class Builder:
        async def build_candidate_nodes(self, inventory_summary: dict, completed_collections: list[str]) -> dict:
            assert len(inventory_summary["candidate_nodes"]) == 2
            return {
                "chapter_candidates": [
                    {
                        "primary_source_collection": "1",
                        "title_hint": "变量基础",
                        "source_collections": ["1"],
                        "core_concepts": ["变量"],
                    }
                ]
            }

    inventory = {
        "version": 1,
        "chunks": [
            _chunk("1/a#001", "1", ["变量"]),
            _chunk("1/a#002", "1", ["网络协议"]),
        ],
        "collections": [
            {
                "source_collection": "1",
                "doc_count": 1,
                "chunk_count": 2,
                "paths": ["1.md"],
                "section_ids": [],
                "core_concepts": ["变量", "网络协议"],
                "representative_chunks": [],
                "related_collections": [],
            }
        ],
    }

    nodes = asyncio.run(rebuild_candidate_nodes_with_ai(tmp_path, inventory, Builder()))

    candidates = nodes["chapter_candidates"]
    assert len(candidates) == 2
    assert {chunk["chunk_ref"] for item in candidates for chunk in item["representative_chunks"]} == {
        "1/a#001",
        "1/a#002",
    }
