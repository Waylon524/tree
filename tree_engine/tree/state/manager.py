"""StateManager: load/save pipeline-state.json and query in-progress BranchRuns."""

from __future__ import annotations

import json
from pathlib import Path

from tree.planner.store import write_json_atomic
from tree.state.models import BranchExecutionRecord, PipelineState


class StateManager:
    def __init__(self, state_path: Path):
        self.state_path = Path(state_path)

    def load(self) -> PipelineState:
        if not self.state_path.exists():
            return PipelineState()
        raw = json.loads(self.state_path.read_text(encoding="utf-8"))
        return PipelineState.model_validate(raw)

    def save(self, state: PipelineState) -> None:
        write_json_atomic(self.state_path, state.model_dump(mode="json"))

    def find_in_progress_all(self, state: PipelineState) -> list[BranchExecutionRecord]:
        return [c for c in state.branch_executions if c.status == "in_progress"]
