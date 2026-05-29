"""File operations: read/write drafts, move to outputs."""

from __future__ import annotations

import shutil
from pathlib import Path

from tree.io import paths


def read_prior_files(root: Path, chapter: str) -> list[str]:
    """Read all .md files from outputs/{chapter}/ sorted by name."""
    chapter_dir = paths.outputs_root(root) / chapter
    if not chapter_dir.exists():
        return []
    contents = []
    for path in sorted(chapter_dir.glob("*.md")):
        contents.append(path.read_text(encoding="utf-8"))
    return contents


def list_prior_paths(root: Path, chapter: str) -> list[Path]:
    """List paths of all prior completed files."""
    chapter_dir = paths.outputs_root(root) / chapter
    if not chapter_dir.exists():
        return []
    return sorted(chapter_dir.glob("*.md"))


def read_draft(root: Path, chapter: str, filename: str) -> str | None:
    """Read draft if it exists in drafts/{chapter}/."""
    path = paths.drafts_root(root) / chapter / filename
    if not path.exists():
        return None
    return path.read_text(encoding="utf-8")


def write_draft(root: Path, chapter: str, filename: str, content: str) -> Path:
    """Write content to drafts/{chapter}/{filename}."""
    chapter_dir = paths.drafts_root(root) / chapter
    chapter_dir.mkdir(parents=True, exist_ok=True)
    path = chapter_dir / filename
    path.write_text(content, encoding="utf-8")
    return path


def move_draft_to_finished(root: Path, chapter: str, filename: str) -> Path:
    """Move draft from drafts/ to outputs/."""
    src = paths.drafts_root(root) / chapter / filename
    dst_dir = paths.outputs_root(root) / chapter
    dst_dir.mkdir(parents=True, exist_ok=True)
    dst = dst_dir / filename
    shutil.move(str(src), str(dst))
    return dst
