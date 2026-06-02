"""StateManager: load/save pipeline-state.json and mutate NodeRun records."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from tree.planner.store import write_json_atomic
from tree.state.models import NodeExecutionRecord, PipelineState


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

    def find_in_progress_all(self, state: PipelineState) -> list[NodeExecutionRecord]:
        return [c for c in state.node_executions if c.status == "in_progress"]

    def find_execution(self, state: PipelineState, node_id: str) -> NodeExecutionRecord | None:
        return next((c for c in state.node_executions if c.node_id == node_id), None)

    # --- mutators (in place, return state for chaining) ----------------------

    def add_output_completed(
        self, state: PipelineState, node_id: str, filename: str
    ) -> PipelineState:
        be = self.find_execution(state, node_id)
        if be and filename not in be.outputs_completed:
            be.outputs_completed.append(filename)
        return state

    def complete_node_execution(self, state: PipelineState, node_id: str) -> PipelineState:
        be = self.find_execution(state, node_id)
        if be:
            be.status = "completed"
        return state

    def update_node_run(self, state: PipelineState, run_id: str, **fields: Any) -> PipelineState:
        for run in state.node_runs:
            if run.run_id == run_id:
                for key, value in fields.items():
                    setattr(run, key, value)
        return state

    def add_node_run_file_completed(
        self, state: PipelineState, run_id: str, filename: str
    ) -> PipelineState:
        for run in state.node_runs:
            if run.run_id == run_id and filename not in run.outputs_completed:
                run.outputs_completed.append(filename)
        return state
