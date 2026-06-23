"""Pydantic models for pipeline execution state and agent outputs."""

from __future__ import annotations

from enum import Enum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, model_validator


class NodeExecutionRecord(BaseModel):
    """One executable NodeRun target."""

    model_config = ConfigDict(validate_assignment=True)

    node_id: str
    status: str  # "in_progress" | "completed"
    outputs_completed: list[str] = Field(default_factory=list)
    source_collection: str | None = None
    source_collections: list[str] = Field(default_factory=list)
    node_run_id: str | None = None


class CoverageSnapshot(BaseModel):
    started_at: str = ""
    finished_output_ids: list[str] = Field(default_factory=list)
    covered_node_ids: list[str] = Field(default_factory=list)
    snapshot_visible_ancestor_node_ids: list[str] = Field(default_factory=list)
    forbidden_future_node_ids: list[str] = Field(default_factory=list)


class Route(str, Enum):
    PASS = "PASS"
    FAIL_KNOWLEDGE_GAP = "FAIL_KNOWLEDGE_GAP"


class ExamSections(BaseModel):
    knowledge_point: str
    covered_node_ids: list[str] = Field(default_factory=list)
    blind_exam: str
    answer_key: str
    writer_instructions: str


class NodeRunRecord(BaseModel):
    node_id: str
    run_id: str
    status: str = "running"
    coverage_snapshot: CoverageSnapshot = Field(default_factory=CoverageSnapshot)
    outputs_completed: list[str] = Field(default_factory=list)
    current_iteration: int = 0
    exam_sections: ExamSections | None = None
    draft_path: Path | None = None
    previous_bottleneck: str | None = None
    last_error: str | None = None


class PipelineState(BaseModel):
    node_executions: list[NodeExecutionRecord] = Field(default_factory=list)
    node_runs: list[NodeRunRecord] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _drop_legacy_branch_state(cls, data: object) -> object:
        if not isinstance(data, dict):
            return data
        values = dict(data)
        values.pop("branch_executions", None)
        values.pop("branch_runs", None)
        return values


class AuditResult(BaseModel):
    route: Route
    exam_id: str
    bottleneck_report: str


class WriterResult(BaseModel):
    is_exam_too_broad: bool = False
    bloat_description: str = ""
    draft_content: str = ""
    draft_path: Path | None = None


class IterationState(BaseModel):
    execution_path: str
    file_seq: str
    knowledge_point: str = ""
    covered_node_ids: list[str] = Field(default_factory=list)
    exam_sections: ExamSections | None = None
    iteration: int = 0
    previous_bottleneck: str | None = None
    draft_path: Path | None = None
