"""Index source, draft, and finished TREE documents into RAG."""

from __future__ import annotations

from pathlib import Path

from tree.io import source_ops
from tree.rag.client import RAGClient


class RAGIndexer:
    def __init__(self, rag: RAGClient):
        self.rag = rag

    def index_source_collection(self, root: Path, collection: str) -> int:
        """Index all Markdown files in source_materials/{collection}."""
        total = 0
        for doc in source_ops.read_collection(root, collection):
            total += self.index_source_file(root, collection, doc.path)
        return total

    def index_source_file(self, root: Path, collection: str, path: Path) -> int:
        """Index one structured source Markdown file."""
        text = path.read_text(encoding="utf-8")
        rel_path = _relative_path(root, path)
        return self.rag.index_file(
            file_seq=path.stem,
            filename=path.name,
            text=text,
            chapter=collection,
            is_draft=False,
            content_kind="source",
            source_collection=collection,
            path=rel_path,
        )

    def index_finished_file(self, root: Path, chapter: str, path: Path) -> int:
        """Index one finished output Markdown file."""
        text = path.read_text(encoding="utf-8")
        rel_path = _relative_path(root, path)
        return self.rag.index_file(
            file_seq=path.stem.split(".", 1)[0],
            filename=path.name,
            text=text,
            chapter=chapter,
            is_draft=False,
            content_kind="finished",
            source_collection=chapter,
            path=rel_path,
        )

    def index_draft_file(self, root: Path, chapter: str, path: Path) -> int:
        """Index one draft Markdown file."""
        text = path.read_text(encoding="utf-8")
        rel_path = _relative_path(root, path)
        return self.rag.index_file(
            file_seq=path.stem.split(".", 1)[0],
            filename=path.name,
            text=text,
            chapter=chapter,
            is_draft=True,
            content_kind="draft",
            source_collection=chapter,
            path=rel_path,
        )


def _relative_path(root: Path, path: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)
