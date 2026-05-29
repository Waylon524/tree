"""Source material operations for structured Markdown collections."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from tree.io.paths import source_root as _source_root


@dataclass(frozen=True)
class SourceDocument:
    path: Path
    content: str


def source_root(root: Path) -> Path:
    return _source_root(root)


def list_collections(root: Path) -> list[str]:
    base = source_root(root)
    if not base.exists():
        return []
    return sorted(path.name for path in base.iterdir() if path.is_dir())


def read_collection(root: Path, collection: str) -> list[SourceDocument]:
    collection_dir = source_root(root) / collection
    if not collection_dir.exists():
        return []
    return [
        SourceDocument(path=path, content=path.read_text(encoding="utf-8"))
        for path in sorted(collection_dir.glob("*.md"))
    ]


def read_all_collections(root: Path) -> dict[str, list[SourceDocument]]:
    return {name: read_collection(root, name) for name in list_collections(root)}
