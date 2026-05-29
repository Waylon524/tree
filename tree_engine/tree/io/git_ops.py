"""Optional Git operations for TREE-generated files."""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)


def git_add_commit(filepath: Path, message: str, cwd: Path | None = None) -> bool:
    """Stage and commit a file when cwd is a Git worktree.

    TREE can run from any folder. Git history is useful when available, but a
    missing or misconfigured repository must not stop the learning pipeline.
    """
    working_dir = cwd or filepath.parent
    if not _is_git_worktree(working_dir):
        logger.info("Skipping Git commit because %s is not a Git worktree", working_dir)
        return False
    try:
        subprocess.run(
            ["git", "add", "-f", str(filepath)],
            cwd=working_dir,
            check=True,
            capture_output=True,
            text=True,
        )
        subprocess.run(
            ["git", "commit", "-m", message],
            cwd=working_dir,
            check=True,
            capture_output=True,
            text=True,
        )
        return True
    except subprocess.CalledProcessError as exc:
        logger.warning("Skipping Git commit after command failed: %s", _format_git_error(exc))
        return False


def _is_git_worktree(path: Path) -> bool:
    result = subprocess.run(
        ["git", "rev-parse", "--is-inside-work-tree"],
        cwd=path,
        check=False,
        capture_output=True,
        text=True,
    )
    return result.returncode == 0 and result.stdout.strip() == "true"


def _format_git_error(exc: subprocess.CalledProcessError) -> str:
    stderr = (exc.stderr or "").strip()
    stdout = (exc.stdout or "").strip()
    details = stderr or stdout or f"exit status {exc.returncode}"
    return f"{exc.cmd!r}: {details}"
