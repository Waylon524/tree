"""Pydantic models for pipeline state and agent outputs."""

from __future__ import annotations

from enum import Enum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, model_validator


class BranchExecutionRecord(BaseModel):
    """One executable BranchRun path, with legacy chapter-field compatibility."""

    model_config = ConfigDict(validate_assignment=True)

    execution_path: str
    status: str  # "in_progress" | "completed"
    tree_id: str = ""
    outputs_completed: list[str] = []
    display_title: str | None = None
    provisional_display_title: str | None = None
    display_naming_reason: str = ""
    source_collection: str | None = None
    source_collections: list[str] = []
    current_start_node_id: str | None = None
    coverage_node_ids: list[str] = []
    branch_id: str | None = None
    branch_run_id: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _legacy_chapter_fields(cls, data: object) -> object:
        if not isinstance(data, dict):
            return data
        values = dict(data)
        if "execution_path" not in values and "chapter_name" in values:
            values["execution_path"] = values.get("chapter_name")
        if "outputs_completed" not in values and "files_completed" in values:
            values["outputs_completed"] = values.get("files_completed")
        if "display_title" not in values and "chapter_title" in values:
            values["display_title"] = values.get("chapter_title")
        if "provisional_display_title" not in values and "provisional_chapter_title" in values:
            values["provisional_display_title"] = values.get("provisional_chapter_title")
        if "display_naming_reason" not in values and "chapter_naming_reason" in values:
            values["display_naming_reason"] = values.get("chapter_naming_reason")
        if "current_start_node_id" not in values and "graph_node_id" in values:
            values["current_start_node_id"] = values.get("graph_node_id")
        if "coverage_node_ids" not in values and "required_nodes" in values:
            values["coverage_node_ids"] = values.get("required_nodes")
        path = str(values.get("execution_path") or "")
        if "tree_id" not in values or not values.get("tree_id"):
            values["tree_id"] = _tree_id_from_execution_path(path)
        return values

    @property
    def chapter_name(self) -> str:
        return self.execution_path

    @chapter_name.setter
    def chapter_name(self, value: str) -> None:
        self.execution_path = value
        self.tree_id = _tree_id_from_execution_path(value)

    @property
    def files_completed(self) -> list[str]:
        return self.outputs_completed

    @files_completed.setter
    def files_completed(self, value: list[str]) -> None:
        self.outputs_completed = value

    @property
    def chapter_title(self) -> str | None:
        return self.display_title

    @chapter_title.setter
    def chapter_title(self, value: str | None) -> None:
        self.display_title = value

    @property
    def provisional_chapter_title(self) -> str | None:
        return self.provisional_display_title

    @provisional_chapter_title.setter
    def provisional_chapter_title(self, value: str | None) -> None:
        self.provisional_display_title = value

    @property
    def chapter_naming_reason(self) -> str:
        return self.display_naming_reason

    @chapter_naming_reason.setter
    def chapter_naming_reason(self, value: str) -> None:
        self.display_naming_reason = value

    @property
    def graph_node_id(self) -> str | None:
        return self.current_start_node_id

    @graph_node_id.setter
    def graph_node_id(self, value: str | None) -> None:
        self.current_start_node_id = value

    @property
    def required_nodes(self) -> list[str]:
        return self.coverage_node_ids

    @required_nodes.setter
    def required_nodes(self, value: list[str]) -> None:
        self.coverage_node_ids = value


ChapterRecord = BranchExecutionRecord


class CoverageSnapshot(BaseModel):
    started_at: str = ""
    finished_output_ids: list[str] = []
    covered_node_ids: list[str] = []
    completed_branch_ids: list[str] = []
    snapshot_visible_ancestor_node_ids: list[str] = []
    forbidden_future_branch_ids: list[str] = []

    @model_validator(mode="before")
    @classmethod
    def _legacy_snapshot_fields(cls, data: object) -> object:
        if not isinstance(data, dict):
            return data
        values = dict(data)
        if (
            "snapshot_visible_ancestor_node_ids" not in values
            and "available_prerequisite_nodes" in values
        ):
            values["snapshot_visible_ancestor_node_ids"] = values.get("available_prerequisite_nodes")
        return values

    @property
    def available_prerequisite_nodes(self) -> list[str]:
        return self.snapshot_visible_ancestor_node_ids

    @available_prerequisite_nodes.setter
    def available_prerequisite_nodes(self, value: list[str]) -> None:
        self.snapshot_visible_ancestor_node_ids = value


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
    def _legacy_run_fields(cls, data: object) -> object:
        if not isinstance(data, dict):
            return data
        values = dict(data)
        if "execution_path" not in values and "chapter_name" in values:
            values["execution_path"] = values.get("chapter_name")
        if "outputs_completed" not in values and "files_completed" in values:
            values["outputs_completed"] = values.get("files_completed")
        path = str(values.get("execution_path") or "")
        if ("tree_id" not in values or not values.get("tree_id")) and path:
            values["tree_id"] = _tree_id_from_execution_path(path)
        return values

    @property
    def chapter_name(self) -> str | None:
        return self.execution_path

    @chapter_name.setter
    def chapter_name(self, value: str | None) -> None:
        self.execution_path = value
        self.tree_id = _tree_id_from_execution_path(value or "")

    @property
    def files_completed(self) -> list[str]:
        return self.outputs_completed

    @files_completed.setter
    def files_completed(self, value: list[str]) -> None:
        self.outputs_completed = value


class PipelineState(BaseModel):
    chapters: list[BranchExecutionRecord] = Field(default_factory=list)
    branch_runs: list[BranchRunRecord] = Field(default_factory=list)

    @property
    def branch_executions(self) -> list[BranchExecutionRecord]:
        return self.chapters

    @branch_executions.setter
    def branch_executions(self, value: list[BranchExecutionRecord]) -> None:
        self.chapters = value


class Route(str, Enum):
    PASS = "PASS"
    FAIL_KNOWLEDGE_GAP = "FAIL_KNOWLEDGE_GAP"


class ExamSections(BaseModel):
    knowledge_point: str
    covered_node_ids: list[str] = []
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


ArchitectResult = WriterResult


class IterationState(BaseModel):
    chapter: str
    file_seq: str
    knowledge_point: str = ""
    covered_node_ids: list[str] = []
    exam_sections: ExamSections | None = None
    iteration: int = 0
    previous_bottleneck: str | None = None
    draft_path: Path | None = None


def _tree_id_from_execution_path(execution_path: str) -> str:
    parts = [part for part in str(execution_path).split("/") if part]
    return parts[0] if parts else ""
