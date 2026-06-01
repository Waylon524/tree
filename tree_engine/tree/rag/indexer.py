"""Index source MTUs and finished outputs into RAG.

Thin wrapper over RAGClient that knows the document conventions:
  - source MTU -> doc_id = mtu_id, content_kind="source", MTU-aware chunks
  - finished output -> content_kind="finished", generic markdown chunks

See docs/REBUILD-DESIGN.md §4 ⑤.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from tree.rag.chunker import chunk_mtu
from tree.rag.client import RAGClient


class RAGIndexer:
    def __init__(self, rag: RAGClient):
        self.rag = rag

    def index_mtu(self, mtu: Any, text: str, *, node_id: str = "") -> int:
        """Index one Minimal Teachable Unit (its text read by line_range)."""
        chunks = chunk_mtu(mtu, text)
        return self.rag.index_file(
            file_seq=mtu.mtu_id,
            filename=mtu.source_file,
            text=text,
            content_kind="source",
            source_collection=mtu.collection,
            path=f"{mtu.collection}/{mtu.source_file}",
            doc_id=mtu.mtu_id,
            chunks=chunks,
            extra_payload={"node_id": node_id} if node_id else None,
        )

    def is_mtu_indexed(self, mtu_id: str) -> bool:
        return self.rag.document_indexed(mtu_id)

    def index_finished_file(self, root: Path, execution_path: str, path: Path) -> int:
        text = path.read_text(encoding="utf-8")
        return self.rag.index_file(
            file_seq=path.stem.split(".", 1)[0],
            filename=path.name,
            text=text,
            content_kind="finished",
            source_collection=execution_path,
            path=_relative(root, path),
        )


def _relative(root: Path, path: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)
