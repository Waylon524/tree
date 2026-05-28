"""RAG client: Qdrant (embedded) + local embedding service.

Provides chunk→embed→upsert indexing and semantic query with metadata filters.

Usage:
    from tree.rag.client import RAGClient

    rag = RAGClient()                           # defaults: ./rag-store, localhost:8788
    rag.index_file("01", "01.质点与参考系.md", text, chapter="01-力学")
    results = rag.query("质点定义", top_k=5, filters={"chapter": "01-力学"})
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Any

from qdrant_client import QdrantClient, models
from rag.chunker import chunk_markdown
from rag.embed import EmbeddingClient

logger = logging.getLogger(__name__)

_COLLECTION = "tree-knowledge"
_DEFAULT_DIMENSIONS = 2560  # Qwen3-Embedding-4B-Q8_0 full dimensions


class RAGClient:
    def __init__(
        self,
        store_path: str | Path = "./rag-store",
        embed_url: str | None = None,
        embed_model: str | None = None,
        dimensions: int = _DEFAULT_DIMENSIONS,
    ):
        self.dimensions = dimensions
        self.embedder = EmbeddingClient(base_url=embed_url, model=embed_model)
        self._client = QdrantClient(path=str(store_path))
        self._ensure_collection()

    def _ensure_collection(self) -> None:
        collections = self._client.get_collections().collections
        names = [c.name for c in collections]
        if _COLLECTION not in names:
            self._client.create_collection(
                collection_name=_COLLECTION,
                vectors_config=models.VectorParams(
                    size=self.dimensions,
                    distance=models.Distance.COSINE,
                ),
            )
            logger.info("Created Qdrant collection '%s' (dim=%d)", _COLLECTION, self.dimensions)

    def index_file(
        self,
        file_seq: str,
        filename: str,
        text: str,
        chapter: str = "",
        is_draft: bool = False,
    ) -> int:
        """Chunk, embed, and upsert a file into the vector store.

        Deletes any existing vectors for this file_seq before re-indexing.
        Returns the number of chunks indexed.
        """
        # Delete old chunks for this file
        self._client.delete(
            collection_name=_COLLECTION,
            points_selector=models.FilterSelector(
                filter=models.Filter(
                    must=[models.FieldCondition(key="file_seq", match=models.MatchValue(value=file_seq))]
                )
            ),
        )

        # Chunk
        chunks = chunk_markdown(file_seq, text, chapter=chapter, is_draft=is_draft)
        if not chunks:
            return 0

        # Embed
        texts = [c["text"] for c in chunks]
        vectors = self.embedder.embed(texts)

        # Upsert
        points = []
        for chunk, vector in zip(chunks, vectors):
            point_id = self._make_point_id(file_seq, chunk["chunk_id"])
            points.append(
                models.PointStruct(
                    id=point_id,
                    vector=vector[: self.dimensions],  # truncate if MRL
                    payload={
                        "chunk_id": chunk["chunk_id"],
                        "text": chunk["text"],
                        "chapter": chunk["chapter"],
                        "file_seq": chunk["file_seq"],
                        "section_id": chunk["section_id"],
                        "chunk_type": chunk["chunk_type"],
                        "is_draft": chunk["is_draft"],
                        "concepts": chunk["concepts"],
                        "formulas": chunk["formulas"],
                        "filename": filename,
                    },
                )
            )

        self._client.upsert(collection_name=_COLLECTION, points=points, wait=True)
        logger.info("Indexed %d chunks for %s (draft=%s)", len(points), file_seq, is_draft)
        return len(points)

    def query(
        self,
        query_text: str,
        top_k: int = 5,
        filters: dict[str, Any] | None = None,
        include_drafts: bool = True,
    ) -> list[dict]:
        """Semantic search with optional metadata filters.

        Returns list of {chunk_id, text, score, metadata}.
        """
        query_vector = self.embedder.embed([query_text])[0][: self.dimensions]

        must_conditions = self._build_filters(filters, include_drafts)

        result = self._client.query_points(
            collection_name=_COLLECTION,
            query=query_vector,
            limit=top_k,
            query_filter=models.Filter(must=must_conditions) if must_conditions else None,
            with_payload=True,
        )

        hits = []
        for point in result.points:
            payload = point.payload or {}
            hits.append({
                "chunk_id": payload.get("chunk_id", ""),
                "text": payload.get("text", ""),
                "score": point.score,
                "metadata": {
                    k: payload[k]
                    for k in ("chapter", "file_seq", "section_id", "chunk_type", "is_draft", "concepts", "formulas", "filename")
                    if k in payload
                },
            })
        return hits

    def delete_file(self, file_seq: str) -> int:
        """Delete all vectors for a given file_seq. Returns count deleted."""
        self._client.delete(
            collection_name=_COLLECTION,
            points_selector=models.FilterSelector(
                filter=models.Filter(
                    must=[models.FieldCondition(key="file_seq", match=models.MatchValue(value=file_seq))]
                )
            ),
        )
        logger.info("Deleted vectors for file_seq=%s", file_seq)
        return 0  # Qdrant delete doesn't return count directly

    def get_chapter_ledger(self, chapter: str) -> list[dict]:
        """Get a summary of all indexed files in a chapter (for auto-briefing)."""
        records, _ = self._client.scroll(
            collection_name=_COLLECTION,
            limit=10000,
            scroll_filter=models.Filter(
                must=[models.FieldCondition(key="chapter", match=models.MatchValue(value=chapter))]
            ),
            with_payload=True,
        )

        # Group by file_seq
        files: dict[str, dict] = {}
        for point in records:
            p = point.payload or {}
            seq = p.get("file_seq", "")
            if seq not in files:
                files[seq] = {
                    "file_seq": seq,
                    "filename": p.get("filename", ""),
                    "concepts": set(),
                    "formulas": set(),
                    "chunk_count": 0,
                }
            files[seq]["concepts"].update(p.get("concepts", []))
            files[seq]["formulas"].update(p.get("formulas", []))
            files[seq]["chunk_count"] += 1

        return [
            {
                "file_seq": v["file_seq"],
                "filename": v["filename"],
                "concepts": sorted(v["concepts"]),
                "formulas": sorted(v["formulas"]),
                "chunk_count": v["chunk_count"],
            }
            for v in sorted(files.values(), key=lambda x: x["file_seq"])
        ]

    @staticmethod
    def _make_point_id(file_seq: str, chunk_id: str) -> int:
        """Deterministic integer ID from file_seq + chunk_id."""
        h = hashlib.md5(f"{file_seq}:{chunk_id}".encode()).hexdigest()
        return int(h[:16], 16)

    @staticmethod
    def _build_filters(
        filters: dict[str, Any] | None,
        include_drafts: bool,
    ) -> list[models.FieldCondition]:
        conditions = []
        if not filters:
            filters = {}
        for key, value in filters.items():
            if key in ("chapter", "file_seq", "chunk_type", "section_id", "filename"):
                conditions.append(
                    models.FieldCondition(key=key, match=models.MatchValue(value=value))
                )
        if not include_drafts:
            conditions.append(
                models.FieldCondition(key="is_draft", match=models.MatchValue(value=False))
            )
        return conditions
