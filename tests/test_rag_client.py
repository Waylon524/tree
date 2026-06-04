from __future__ import annotations

import pytest

from tree.rag.client import RAGClient


def test_default_dimensions_match_qwen3_embedding_0_6b(tmp_path):
    rag = RAGClient(store_path=tmp_path / "rag", embedder=_FakeEmbedder())

    try:
        assert rag.dimensions == 1024
    finally:
        rag.close()


class _FakeEmbedder:
    def embed(self, texts: str | list[str]) -> list[list[float]]:
        if isinstance(texts, str):
            texts = [texts]
        lookup = {"left": [1.0, 0.0], "right": [0.0, 1.0]}
        return [lookup.get(text, [1.0, 0.0]) for text in texts]


def test_source_mtu_vectors_average_chunks_and_node_id_backfill(tmp_path):
    rag = RAGClient(store_path=tmp_path / "rag", dimensions=2, embedder=_FakeEmbedder())
    try:
        rag.index_file(
            "mtu:1",
            "source.md",
            "left\n\nright",
            content_kind="source",
            source_collection="课件",
            path="课件/source.md",
            doc_id="mtu:1",
            chunks=[
                {
                    "chunk_id": "mtu:1-0",
                    "chunk_index": 0,
                    "text": "left",
                    "mtu_id": "mtu:1",
                    "is_draft": False,
                },
                {
                    "chunk_id": "mtu:1-1",
                    "chunk_index": 1,
                    "text": "right",
                    "mtu_id": "mtu:1",
                    "is_draft": False,
                },
            ],
        )

        vector = rag.source_mtu_vectors(["mtu:1"])["mtu:1"]
        assert vector == pytest.approx([0.707106, 0.707106], abs=1e-5)

        rag.update_source_mtu_node_ids({"mtu:1": "kn:1"})
        hits = rag.scroll_chunks(filters={"mtu_id": "mtu:1"})
        assert {hit["metadata"].get("node_id") for hit in hits} == {"kn:1"}
    finally:
        rag.close()
