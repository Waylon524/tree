"""RAGClient: Qdrant (embedded) + local embedding service.

Three content_kind namespaces: source / finished / draft (drafts read directly,
not indexed in normal flow). One document per source MTU (doc_id = mtu_id).

★ Embedding/Qdrant interface preserved from the previous engine; source chunking
is now MTU-driven. See docs/REBUILD-DESIGN.md §5, docs/LEGACY-DESIGN.md §5.
"""

from __future__ import annotations

import hashlib
import logging
import re
from pathlib import Path
from typing import Any, Protocol

from qdrant_client import QdrantClient, models

from tree.io import paths
from tree.rag.chunker import chunk_markdown
from tree.rag.embed import EmbeddingClient

logger = logging.getLogger(__name__)

_COLLECTION = "tree-knowledge"
_DEFAULT_DIMENSIONS = 2560  # Qwen3-Embedding-4B-Q8_0 full dimensions

_FILTERABLE_KEYS = (
    "file_seq", "chunk_type", "section_id", "filename",
    "content_kind", "source_collection", "path", "doc_id",
    "mtu_id", "node_id", "unit_kind",
)


class Embedder(Protocol):
    def embed(self, texts: str | list[str]) -> list[list[float]]: ...


class RAGClient:
    def __init__(
        self,
        store_path: str | Path | None = None,
        embed_url: str | None = None,
        embed_model: str | None = None,
        dimensions: int = _DEFAULT_DIMENSIONS,
        embedder: Embedder | None = None,
        collection_name: str = _COLLECTION,
    ):
        self.dimensions = dimensions
        self.embedder = embedder or EmbeddingClient(base_url=embed_url, model=embed_model)
        self.collection_name = collection_name
        store_path = store_path or paths.rag_store_path(Path.cwd())
        self._client = QdrantClient(path=str(store_path))
        self._ensure_collection()

    def _ensure_collection(self) -> None:
        names = [c.name for c in self._client.get_collections().collections]
        if self.collection_name not in names:
            self._client.create_collection(
                collection_name=self.collection_name,
                vectors_config=models.VectorParams(
                    size=self.dimensions, distance=models.Distance.COSINE
                ),
            )
            logger.info("Created Qdrant collection '%s' (dim=%d)", self.collection_name, self.dimensions)

    def index_file(
        self,
        file_seq: str,
        filename: str,
        text: str,
        *,
        is_draft: bool = False,
        content_kind: str = "finished",
        source_collection: str = "",
        path: str = "",
        doc_id: str | None = None,
        extra_payload: dict[str, Any] | None = None,
        chunks: list[dict] | None = None,
    ) -> int:
        """Chunk (unless `chunks` provided), embed, and upsert a document.

        Deletes any existing vectors for this doc_id first. Returns chunk count.
        """
        doc_id = doc_id or self.make_doc_id(content_kind, source_collection, path or filename)
        self._client.delete(
            collection_name=self.collection_name,
            points_selector=models.FilterSelector(
                filter=models.Filter(
                    must=[models.FieldCondition(key="doc_id", match=models.MatchValue(value=doc_id))]
                )
            ),
        )

        if chunks is None:
            chunks = chunk_markdown(
                file_seq, text, source_collection=source_collection, is_draft=is_draft
            )
        if not chunks:
            return 0

        vectors = self.embedder.embed([c["text"] for c in chunks])
        points = []
        for chunk, vector in zip(chunks, vectors):
            payload = {
                **chunk,
                "content_kind": content_kind,
                "source_collection": source_collection,
                "filename": filename,
                "path": path,
                "doc_id": doc_id,
            }
            if extra_payload:
                payload.update(extra_payload)
            points.append(
                models.PointStruct(
                    id=self._make_point_id(doc_id, chunk["chunk_id"]),
                    vector=vector[: self.dimensions],
                    payload=payload,
                )
            )
        self._client.upsert(collection_name=self.collection_name, points=points, wait=True)
        logger.info("Indexed %d chunks for %s (kind=%s)", len(points), doc_id, content_kind)
        return len(points)

    def query(
        self,
        query_text: str,
        top_k: int = 5,
        filters: dict[str, Any] | None = None,
        include_drafts: bool = True,
        neighbor_window: int = 1,
    ) -> list[dict]:
        """Semantic search with optional metadata filters + adjacent-chunk expansion."""
        query_vector = self.embedder.embed([query_text])[0][: self.dimensions]
        must = self._build_filters(filters, include_drafts)
        result = self._client.query_points(
            collection_name=self.collection_name,
            query=query_vector,
            limit=top_k,
            query_filter=models.Filter(must=must) if must else None,
            with_payload=True,
        )
        hits = []
        for point in result.points:
            payload = point.payload or {}
            text, expanded = self._expanded_text(payload, neighbor_window)
            hits.append(_to_hit(payload, text=text, score=point.score, expanded=expanded))
        return hits

    def scroll_chunks(
        self,
        filters: dict[str, Any] | None = None,
        limit: int = 10000,
        include_drafts: bool = True,
    ) -> list[dict]:
        conditions = self._build_filters(filters, include_drafts)
        records = self._scroll_records(
            limit=limit,
            scroll_filter=models.Filter(must=conditions) if conditions else None,
        )
        return [_to_hit(r.payload or {}) for r in records]

    def document_indexed(self, doc_id: str) -> bool:
        records, _ = self._client.scroll(
            collection_name=self.collection_name,
            limit=1,
            scroll_filter=models.Filter(
                must=[models.FieldCondition(key="doc_id", match=models.MatchValue(value=doc_id))]
            ),
            with_payload=False,
            with_vectors=False,
        )
        return bool(records)

    def delete_document(self, doc_id: str) -> None:
        self._client.delete(
            collection_name=self.collection_name,
            points_selector=models.FilterSelector(
                filter=models.Filter(
                    must=[models.FieldCondition(key="doc_id", match=models.MatchValue(value=doc_id))]
                )
            ),
        )

    def close(self) -> None:
        self._client.close()

    def _expanded_text(self, payload: dict, neighbor_window: int) -> tuple[str, list[str]]:
        text = payload.get("text", "")
        if neighbor_window <= 0:
            return text, []
        doc_id = payload.get("doc_id")
        center = _payload_chunk_index(payload)
        if doc_id is None or center is None:
            return text, []
        records = self._scroll_records(
            limit=10000,
            scroll_filter=models.Filter(
                must=[models.FieldCondition(key="doc_id", match=models.MatchValue(value=doc_id))]
            ),
        )
        lo, hi = center - neighbor_window, center + neighbor_window
        expanded = []
        for record in records:
            rp = record.payload or {}
            index = _payload_chunk_index(rp)
            if index is None or index < lo or index > hi:
                continue
            expanded.append((index, rp))
        if not expanded:
            return text, []
        expanded.sort(key=lambda item: item[0])
        joined = "\n\n".join((rp.get("text", "") or "").strip() for _, rp in expanded if rp.get("text"))
        ids = [rp.get("chunk_id", "") for _, rp in expanded if rp.get("chunk_id")]
        return joined, ids

    def _scroll_records(
        self,
        *,
        limit: int,
        scroll_filter: models.Filter | None = None,
    ) -> list[models.Record]:
        records: list[models.Record] = []
        offset = None
        while True:
            page, offset = self._client.scroll(
                collection_name=self.collection_name,
                limit=limit,
                scroll_filter=scroll_filter,
                with_payload=True,
                with_vectors=False,
                offset=offset,
            )
            records.extend(page)
            if offset is None:
                return records

    @staticmethod
    def _make_point_id(doc_id: str, chunk_id: str) -> int:
        h = hashlib.md5(f"{doc_id}:{chunk_id}".encode()).hexdigest()
        return int(h[:16], 16)

    @staticmethod
    def make_doc_id(content_kind: str, collection: str, path: str) -> str:
        return f"{content_kind}:{collection}:{path}"

    @staticmethod
    def _build_filters(
        filters: dict[str, Any] | None, include_drafts: bool
    ) -> list[models.FieldCondition]:
        conditions: list[models.FieldCondition] = []
        for key, value in (filters or {}).items():
            if key not in _FILTERABLE_KEYS:
                continue
            if isinstance(value, (list, tuple, set)):
                values = [v for v in value if v is not None]
                if values:
                    conditions.append(models.FieldCondition(key=key, match=models.MatchAny(any=values)))
            else:
                conditions.append(models.FieldCondition(key=key, match=models.MatchValue(value=value)))
        if not include_drafts:
            conditions.append(models.FieldCondition(key="is_draft", match=models.MatchValue(value=False)))
        return conditions


def _to_hit(payload: dict, *, text: str | None = None, score: Any = None, expanded: list[str] | None = None) -> dict:
    metadata = {k: v for k, v in payload.items() if k not in ("text", "chunk_id")}
    if expanded:
        metadata["expanded_chunk_ids"] = expanded
    return {
        "chunk_id": payload.get("chunk_id", ""),
        "text": text if text is not None else payload.get("text", ""),
        "score": score,
        "metadata": metadata,
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
