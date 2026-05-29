"""RAG client: Qdrant (embedded) + local embedding service.

Provides chunk→embed→upsert indexing and semantic query with metadata filters.

Usage:
    from tree.rag.client import RAGClient

    rag = RAGClient()                           # defaults: .tree/runtime/rag-store
    rag.index_file("01", "01.质点与参考系.md", text, chapter="01-力学")
    results = rag.query("质点定义", top_k=5, filters={"chapter": "01-力学"})
"""

from __future__ import annotations

import hashlib
import logging
import re
from pathlib import Path
from typing import Any, Protocol

from qdrant_client import QdrantClient, models
from rag.chunker import chunk_markdown
from rag.embed import EmbeddingClient
from tree.io import paths

logger = logging.getLogger(__name__)

_COLLECTION = "tree-knowledge"
_DEFAULT_DIMENSIONS = 2560  # Qwen3-Embedding-4B-Q8_0 full dimensions


class Embedder(Protocol):
    def embed(self, texts: str | list[str]) -> list[list[float]]:
        """Embed one or more texts."""


class RAGClient:
    def __init__(
        self,
        store_path: str | Path | None = None,
        embed_url: str | None = None,
        embed_model: str | None = None,
        dimensions: int = _DEFAULT_DIMENSIONS,
        embedder: Embedder | None = None,
    ):
        self.dimensions = dimensions
        self.embedder = embedder or EmbeddingClient(base_url=embed_url, model=embed_model)
        store_path = store_path or paths.rag_store_path(Path.cwd())
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
        content_kind: str = "finished",
        source_collection: str = "",
        path: str = "",
        doc_id: str | None = None,
    ) -> int:
        """Chunk, embed, and upsert a file into the vector store.

        Deletes any existing vectors for this file_seq before re-indexing.
        Returns the number of chunks indexed.
        """
        doc_id = doc_id or self.make_doc_id(content_kind, source_collection or chapter, path or filename)

        # Delete old chunks for this document
        self._client.delete(
            collection_name=_COLLECTION,
            points_selector=models.FilterSelector(
                filter=models.Filter(
                    must=[models.FieldCondition(key="doc_id", match=models.MatchValue(value=doc_id))]
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
            point_id = self._make_point_id(doc_id, chunk["chunk_id"])
            points.append(
                models.PointStruct(
                    id=point_id,
                    vector=vector[: self.dimensions],  # truncate if MRL
                    payload={
                        "chunk_id": chunk["chunk_id"],
                        "chunk_index": chunk["chunk_index"],
                        "text": chunk["text"],
                        "chapter": chunk["chapter"],
                        "file_seq": chunk["file_seq"],
                        "section_id": chunk["section_id"],
                        "chunk_type": chunk["chunk_type"],
                        "is_draft": chunk["is_draft"],
                        "content_kind": content_kind,
                        "source_collection": source_collection,
                        "concepts": chunk["concepts"],
                        "formulas": chunk["formulas"],
                        "filename": filename,
                        "path": path,
                        "doc_id": doc_id,
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
        neighbor_window: int = 1,
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
            text, expanded_chunk_ids = self._expanded_text(payload, neighbor_window)
            hits.append({
                "chunk_id": payload.get("chunk_id", ""),
                "text": text,
                "score": point.score,
                "metadata": {
                    k: payload[k]
                    for k in (
                        "chapter",
                        "file_seq",
                        "section_id",
                        "chunk_type",
                        "is_draft",
                        "content_kind",
                        "source_collection",
                        "concepts",
                        "formulas",
                        "filename",
                        "path",
                        "doc_id",
                        "chunk_index",
                    )
                    if k in payload
                } | ({"expanded_chunk_ids": expanded_chunk_ids} if expanded_chunk_ids else {}),
            })
        return hits

    def _expanded_text(self, payload: dict, neighbor_window: int) -> tuple[str, list[str]]:
        text = payload.get("text", "")
        if neighbor_window <= 0:
            return text, []
        doc_id = payload.get("doc_id")
        center_index = _payload_chunk_index(payload)
        if doc_id is None or center_index is None:
            return text, []

        records, _ = self._client.scroll(
            collection_name=_COLLECTION,
            limit=10000,
            scroll_filter=models.Filter(
                must=[models.FieldCondition(key="doc_id", match=models.MatchValue(value=doc_id))]
            ),
            with_payload=True,
            with_vectors=False,
        )
        expanded = []
        lo = center_index - neighbor_window
        hi = center_index + neighbor_window
        for record in records:
            record_payload = record.payload or {}
            index = _payload_chunk_index(record_payload)
            if index is None or index < lo or index > hi:
                continue
            expanded.append((index, record_payload))
        if not expanded:
            return text, []
        expanded.sort(key=lambda item: item[0])
        return (
            "\n\n".join((item[1].get("text", "") or "").strip() for item in expanded if item[1].get("text")),
            [item[1].get("chunk_id", "") for item in expanded if item[1].get("chunk_id")],
        )

    def scroll_chunks(
        self,
        filters: dict[str, Any] | None = None,
        limit: int = 10000,
        include_drafts: bool = True,
    ) -> list[dict]:
        """Read indexed chunk payloads without opening source/finished files."""
        conditions = self._build_filters(filters, include_drafts)
        records, _ = self._client.scroll(
            collection_name=_COLLECTION,
            limit=limit,
            scroll_filter=models.Filter(must=conditions) if conditions else None,
            with_payload=True,
            with_vectors=False,
        )
        return [_point_to_hit(point) for point in records]

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

    def document_indexed(self, doc_id: str) -> bool:
        """Return True when at least one vector exists for a document id."""
        records, _ = self._client.scroll(
            collection_name=_COLLECTION,
            limit=1,
            scroll_filter=models.Filter(
                must=[models.FieldCondition(key="doc_id", match=models.MatchValue(value=doc_id))]
            ),
            with_payload=False,
            with_vectors=False,
        )
        return bool(records)

    def close(self) -> None:
        """Release the embedded Qdrant client before interpreter shutdown."""
        self._client.close()

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
        """Deterministic integer ID from doc identity + chunk_id."""
        h = hashlib.md5(f"{file_seq}:{chunk_id}".encode()).hexdigest()
        return int(h[:16], 16)

    @staticmethod
    def make_doc_id(content_kind: str, collection: str, path: str) -> str:
        """Stable document identifier across source/finished/draft namespaces."""
        return f"{content_kind}:{collection}:{path}"

    @staticmethod
    def _build_filters(
        filters: dict[str, Any] | None,
        include_drafts: bool,
    ) -> list[models.FieldCondition]:
        conditions = []
        if not filters:
            filters = {}
        for key, value in filters.items():
            if key in (
                "chapter",
                "file_seq",
                "chunk_type",
                "section_id",
                "filename",
                "content_kind",
                "source_collection",
                "path",
                "doc_id",
            ):
                if isinstance(value, (list, tuple, set)):
                    values = [item for item in value if item is not None]
                    if not values:
                        continue
                    conditions.append(
                        models.FieldCondition(key=key, match=models.MatchAny(any=values))
                    )
                else:
                    conditions.append(
                        models.FieldCondition(key=key, match=models.MatchValue(value=value))
                    )
        if not include_drafts:
            conditions.append(
                models.FieldCondition(key="is_draft", match=models.MatchValue(value=False))
            )
        return conditions


def _point_to_hit(point: models.Record) -> dict:
    payload = point.payload or {}
    return {
        "chunk_id": payload.get("chunk_id", ""),
        "text": payload.get("text", ""),
        "score": getattr(point, "score", None),
        "metadata": {
            k: payload[k]
            for k in (
                "chapter",
                "file_seq",
                "section_id",
                "chunk_type",
                "is_draft",
                "content_kind",
                "source_collection",
                "concepts",
                "formulas",
                "filename",
                "path",
                "doc_id",
                "chunk_index",
            )
            if k in payload
        },
    }


def _payload_chunk_index(payload: dict) -> int | None:
    value = payload.get("chunk_index")
    if isinstance(value, int):
        return value
    chunk_id = payload.get("chunk_id")
    if not isinstance(chunk_id, str):
        return None
    match = re.search(r"-(\d+)$", chunk_id)
    return int(match.group(1)) if match else None
