"""ProgressTracker: writes progress.json consumed by the dashboard.

Minimal foundation. The dashboard (cli/dashboard) reads the three sections:
source_ingest / planner / learning_loop. Specialized helper methods are added
incrementally as the engine is implemented (see docs/REBUILD-DESIGN.md §9 step 9).
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from tree.io import paths
from tree.planner.store import write_json_atomic


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())


def _empty_state() -> dict[str, Any]:
    return {
        "phase": "idle",
        "message": "",
        "updated_at": _now(),
        "source_ingest": {},
        "planner": {},
        "learning_loop": {},
    }


class ProgressTracker:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.path = paths.progress_path(self.root)

    def reset(self) -> None:
        write_json_atomic(self.path, _empty_state())

    def load(self) -> dict[str, Any]:
        if not self.path.exists():
            return _empty_state()
        import json

        return json.loads(self.path.read_text(encoding="utf-8"))

    def update(self, patch: dict[str, Any]) -> None:
        state = self.load()
        _deep_update(state, patch)
        state["updated_at"] = _now()
        write_json_atomic(self.path, state)

    def complete(self, message: str) -> None:
        self.update({"phase": "complete", "message": message})


def load_progress(root: Path) -> dict[str, Any]:
    return ProgressTracker(root).load()


def _deep_update(target: dict[str, Any], patch: dict[str, Any]) -> None:
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            _deep_update(target[key], value)
        else:
            target[key] = value
