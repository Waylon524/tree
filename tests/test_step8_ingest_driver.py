"""Tests for Step 8 source ingest + embedding orchestration."""

from __future__ import annotations

from types import SimpleNamespace

from tree.engine.ingest_driver import prepare_sources
from tree.io import paths
from tree.planner.pipeline import load_dag, load_nodes


class _FakeArchivist:
    async def clean(self, raw_markdown: str, *, timeout_sec=None) -> str:
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
                "end_line": 2,
                "title": "A",
                "keywords": ["ka"],
                "summary": "",
                "unit_kind": "concept",
            },
            {
                "start_line": 3,
                "end_line": 4,
                "title": "B",
                "keywords": ["kb"],
                "summary": "",
                "unit_kind": "concept",
            },
        ]
        return build_mtus(units, collection=collection, source_file=source_file, order_offset=order_offset)


class _EchoDagger:
    async def build(self, payload, *, timeout_sec=None):
        metas = [p for p in payload if "mtu_id" in p]
        return {
            "nodes": [
                {
                    "title": p["title"],
                    "member_mtu_ids": [p["mtu_id"]],
                    "keywords": p.get("keywords", []),
                }
                for p in metas
            ],
            "edges": [
                {
                    "from_title": metas[0]["title"],
                    "to_title": metas[1]["title"],
                    "relation": "prerequisite",
                    "confidence": 0.9,
                }
            ],
        }


class _FakeIndexer:
    def __init__(self):
        self.indexed = []

    def index_mtu(self, mtu, text: str, *, node_id: str = "") -> int:
        self.indexed.append((mtu.mtu_id, text, node_id))
        return 1

    def is_mtu_indexed(self, mtu_id: str) -> bool:
        return False


async def test_prepare_sources_builds_planner_indexes_mtus_and_deletes_markdown(tmp_path, monkeypatch):
    material = tmp_path / "materials" / "课件" / "ch1.md"
    material.parent.mkdir(parents=True)
    material.write_text("raw line 1\nraw line 2\nraw line 3\nraw line 4", encoding="utf-8")
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
    )

    summary = await prepare_sources(engine)

    assert summary["mtu_count"] == 2
    assert len(load_nodes(tmp_path)) == 2
    assert len(load_dag(tmp_path)["edges"]) == 1
    assert len(indexer.indexed) == 2
    assert [text for _, text, _ in indexer.indexed] == ["clean line 1\nclean line 2", "clean line 3\nclean line 4"]
    assert all(node_id for _, _, node_id in indexer.indexed)
    assert not any(paths.source_markdown_root(tmp_path).rglob("*.md"))
