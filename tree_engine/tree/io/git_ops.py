"""Optional git commit of outputs/. TODO (step 8). Kept optional/no-op by default."""

from __future__ import annotations

from pathlib import Path


def commit_output(root: Path, message: str) -> None:  # pragma: no cover - optional
    raise NotImplementedError("git_ops.commit_output — implement in step 8")
