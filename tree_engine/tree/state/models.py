"""Pydantic models for pipeline execution state and agent outputs.

Migrated unchanged from the previous engine (these models were already clean).
See docs/REBUILD-DESIGN.md §3 and docs/LEGACY-DESIGN.md §3.2.
"""

from __future__ import annotations

from enum import Enum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, model_validator


def _tree_id_from_execution_path(execution_path: str) -> str:
    parts = [part for part in str(execution_path).split("/") if part]
    return parts[0] if parts else ""


class BranchExecutionRecord(BaseModel):
    """One executable BranchRun path."""

    model_config = ConfigDict(validate_assignment=True)

    execution_path: str
    status: str  # "in_progress" | "completed"
    tree_id: str = ""
    outputs_completed: list[str] = Field(default_factory=list)
    display_title: str | None = None
    provisional_display_title: str | None = None
    display_naming_reason: str = ""
    source_collection: str | None = None
    source_collections: list[str] = Field(default_factory=list)
    current_start_node_id: str | None = None
    coverage_node_ids: list[str] = Field(default_factory=list)
    branch_id: str | None = None
    branch_run_id: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _derive_tree_id(cls, data: object) -> object:
        if not isinstance(data, dict):
            return data
        values = dict(data)
        path = str(values.get("execution_path") or "")
        if not values.get("tree_id"):
            values["tree_id"] = _tree_id_from_execution_path(path)
        return values


class CoverageSnapshot(BaseModel):
    started_at: str = ""
    finished_output_ids: list[str] = Field(default_factory=list)
    covered_node_ids: list[str] = Field(default_factory=list)
    completed_branch_ids: list[str] = Field(default_factory=list)
    snapshot_visible_ancestor_node_ids: list[str] = Field(default_factory=list)
    forbidden_future_branch_ids: list[str] = Field(default_factory=list)


class BranchRunRecord(BaseModel):
    branch_id: str
    run_id: str
    status: str = "running"
    coverage_snapshot: CoverageSnapshot = Field(default_factory=CoverageSnapshot)
    outputs_completed: list[str] = Field(default_factory=list)
    current_iteration: int = 0
    execution_path: str | None = None
    tree_id: str = ""

    @model_validator(mode="before")
    @classmethod
    def _derive_tree_id(cls, data: object) -> object:
        if not isinstance(data, dict):
            return data
        values = dict(data)
        path = str(values.get("execution_path") or "")
        if not values.get("tree_id") and path:
            values["tree_id"] = _tree_id_from_execution_path(path)
        return values


class PipelineState(BaseModel):
    branch_executions: list[BranchExecutionRecord] = Field(default_factory=list)
    branch_runs: list[BranchRunRecord] = Field(default_factory=list)


class Route(str, Enum):
    PASS = "PASS"
    FAIL_KNOWLEDGE_GAP = "FAIL_KNOWLEDGE_GAP"


class ExamSections(BaseModel):
    knowledge_point: str
    covered_node_ids: list[str] = Field(default_factory=list)
    blind_exam: str
    answer_key: str
    writer_instructions: str


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
