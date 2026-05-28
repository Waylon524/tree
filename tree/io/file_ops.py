"""File operations: read/write drafts, move to finished_outputs."""

from __future__ import annotations

import shutil
from pathlib import Path


def read_prior_files(root: Path, chapter: str) -> list[str]:
    """Read all .md files from finished_outputs/{chapter}/ sorted by name."""
    chapter_dir = root / "finished_outputs" / chapter
    if not chapter_dir.exists():
        return []
    contents = []
    for path in sorted(chapter_dir.glob("*.md")):
        contents.append(path.read_text(encoding="utf-8"))
    return contents


def list_prior_paths(root: Path, chapter: str) -> list[Path]:
    """List paths of all prior completed files."""
    chapter_dir = root / "finished_outputs" / chapter
    if not chapter_dir.exists():
        return []
    return sorted(chapter_dir.glob("*.md"))


def read_draft(root: Path, chapter: str, filename: str) -> str | None:
    """Read draft if it exists in drafts/{chapter}/."""
    path = root / "drafts" / chapter / filename
    if not path.exists():
        return None
    return path.read_text(encoding="utf-8")


def write_draft(root: Path, chapter: str, filename: str, content: str) -> Path:
    """Write content to drafts/{chapter}/{filename}."""
    chapter_dir = root / "drafts" / chapter
    chapter_dir.mkdir(parents=True, exist_ok=True)
    path = chapter_dir / filename
    path.write_text(content, encoding="utf-8")
    return path


def move_draft_to_finished(root: Path, chapter: str, filename: str) -> Path:
    """Move draft from drafts/ to finished_outputs/."""
    src = root / "drafts" / chapter / filename
    dst_dir = root / "finished_outputs" / chapter
    dst_dir.mkdir(parents=True, exist_ok=True)
    dst = dst_dir / filename
    shutil.move(str(src), str(dst))
    return dst


def list_exercises(root: Path) -> list[Path]:
    """List all files in exercises/."""
    ex_dir = root / "exercises"
    if not ex_dir.exists():
        return []
    return sorted(ex_dir.glob("*"))


def read_exercise_files(root: Path) -> list[tuple[Path, str]]:
    """Read all regular files in exercises/ with their paths."""
    return [
        (path, path.read_text(encoding="utf-8"))
        for path in list_exercises(root)
        if path.is_file()
    ]


def read_exercise_bank(root: Path, chapter: str) -> str | None:
    """Read exercises/{chapter}-*.md if it exists."""
    ex_dir = root / "exercises"
    if not ex_dir.exists():
        return None
    for path in ex_dir.glob(f"{chapter}-*.md"):
        return path.read_text(encoding="utf-8")
    return None
