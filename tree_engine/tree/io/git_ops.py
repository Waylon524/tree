"""Git operations: add and commit."""

from __future__ import annotations

import subprocess
from pathlib import Path


def git_add_commit(filepath: Path, message: str, cwd: Path | None = None) -> None:
    """Stage and commit a file."""
    working_dir = cwd or filepath.parent
    subprocess.run(["git", "add", "-f", str(filepath)], cwd=working_dir, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", message], cwd=working_dir, check=True, capture_output=True)
