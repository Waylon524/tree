"""Pydantic models for pipeline execution state and agent outputs."""

from __future__ import annotations

from enum import Enum
from pathlib import Path
import re

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


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


class ExamReconciliationAction(str, Enum):
    KEEP_FAIL = "KEEP_FAIL"
    REVISE_EXAM = "REVISE_EXAM"


class AuditExamDefectKind(str, Enum):
    ANSWER_KEY_DEFECT = "ANSWER_KEY_DEFECT"
    EXAM_DEFECT = "EXAM_DEFECT"


class WriterInstructions(BaseModel):
    """Validated Examiner-to-Writer control data, never a free-form authority block."""

    model_config = ConfigDict(extra="forbid", strict=True)

    scope: str
    covered_node_ids: list[str] = Field(min_length=1)
    required_concepts: list[str] = Field(default_factory=list)
    required_formulas: list[str] = Field(default_factory=list)
    required_derivations: list[str] = Field(default_factory=list)
    forbidden_spillover: list[str] = Field(default_factory=list)
    prior_concepts_to_cite: list[str] = Field(default_factory=list)
    expected_sections: list[str] = Field(min_length=1)
    organization_notes: str
    prerequisite_repairs: list[str] = Field(default_factory=list)

    @field_validator("scope", "organization_notes")
    @classmethod
    def _validate_control_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("must not be empty")
        if _looks_like_control_injection(normalized):
            raise ValueError("contains instruction-override language")
        return normalized

    @field_validator(
        "covered_node_ids",
        "required_concepts",
        "required_formulas",
        "required_derivations",
        "forbidden_spillover",
        "prior_concepts_to_cite",
        "expected_sections",
        "prerequisite_repairs",
    )
    @classmethod
    def _validate_items(cls, values: list[str]) -> list[str]:
        cleaned: list[str] = []
        for value in values:
            item = value.strip()
            if not item:
                continue
            if _looks_like_control_injection(item):
                raise ValueError("contains instruction-override language")
            if item not in cleaned:
                cleaned.append(item)
        return cleaned

    @model_validator(mode="after")
    def _validate_prerequisite_repairs(self) -> "WriterInstructions":
        required = {item.casefold() for item in self.required_concepts}
        outside = [item for item in self.prerequisite_repairs if item.casefold() not in required]
        if outside:
            raise ValueError(
                "prerequisite_repairs must be a subset of required_concepts: "
                + ", ".join(outside)
            )
        return self

    @classmethod
    def from_text(
        cls,
        text: str,
        *,
        expected_covered_node_ids: list[str] | None = None,
    ) -> "WriterInstructions":
        parsed = cls.model_validate(_parse_writer_instruction_fields(text), strict=True)
        if (
            expected_covered_node_ids is not None
            and parsed.covered_node_ids != expected_covered_node_ids
        ):
            raise ValueError(
                "Covered node ids must exactly match the exam boundary: "
                + ", ".join(expected_covered_node_ids)
            )
        return parsed


_WRITER_INSTRUCTION_LABELS = {
    "scope": "scope",
    "covered node ids": "covered_node_ids",
    "required concepts": "required_concepts",
    "required formulas": "required_formulas",
    "required derivations": "required_derivations",
    "forbidden spillover": "forbidden_spillover",
    "prior concepts to cite": "prior_concepts_to_cite",
    "expected sections": "expected_sections",
    "organization notes": "organization_notes",
    "prerequisite repairs": "prerequisite_repairs",
}
_WRITER_INSTRUCTION_LIST_FIELDS = {
    "covered_node_ids",
    "required_concepts",
    "required_formulas",
    "required_derivations",
    "forbidden_spillover",
    "prior_concepts_to_cite",
    "expected_sections",
    "prerequisite_repairs",
}
_EMPTY_INSTRUCTION_VALUES = {"none", "null", "n/a", "(none)", "无", "无要求"}
_CONTROL_INJECTION_RE = re.compile(
    r"(?:ignore\s+(?:all\s+)?(?:previous|prior|system|developer)|"
    r"override\s+(?:the\s+)?(?:system|developer|hard|safety)|"
    r"system\s+prompt|developer\s+message|forget\s+(?:all\s+)?instructions|"
    r"忽略.{0,12}(?:指令|提示词|规则)|覆盖.{0,12}(?:系统|硬约束|规则)|系统提示词)",
    re.IGNORECASE,
)


def _parse_writer_instruction_fields(text: str) -> dict[str, object]:
    values: dict[str, object] = {}
    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        match = re.match(r"^([^:：]+)[:：]\s*(.*)$", line)
        if match is None:
            raise ValueError(
                f"Writer Instructions line {line_number} must be one `Field: value` record"
            )
        label = " ".join(match.group(1).strip().lower().split())
        try:
            field = _WRITER_INSTRUCTION_LABELS[label]
        except KeyError as exc:
            raise ValueError(f"Unknown Writer Instructions field: {match.group(1).strip()}") from exc
        if field in values:
            raise ValueError(f"Duplicate Writer Instructions field: {match.group(1).strip()}")
        raw_value = match.group(2).strip()
        if field in _WRITER_INSTRUCTION_LIST_FIELDS:
            values[field] = _split_instruction_list(raw_value)
        else:
            values[field] = raw_value

    missing = [
        label
        for label, field in _WRITER_INSTRUCTION_LABELS.items()
        if field not in values
    ]
    if missing:
        raise ValueError("Missing Writer Instructions fields: " + ", ".join(missing))
    return values


def _split_instruction_list(value: str) -> list[str]:
    if value.strip().casefold() in _EMPTY_INSTRUCTION_VALUES:
        return []
    return [
        item.strip()
        for item in re.split(r"[,，、;；]+", value)
        if item.strip()
    ]


def _looks_like_control_injection(value: str) -> bool:
    return bool(_CONTROL_INJECTION_RE.search(value))


class ExamSections(BaseModel):
    knowledge_point: str
    covered_node_ids: list[str] = Field(default_factory=list)
    blind_exam: str
    answer_key: str
    writer_instructions: str
    writer_instruction_spec: WriterInstructions | None = None


class ExamReconciliationResult(BaseModel):
    action: ExamReconciliationAction
    reason: str = ""
    exam_sections: ExamSections | None = None


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
    bottleneck_repeat_count: int = 0
    bottleneck_history: list[str] = Field(default_factory=list)
    last_error: str | None = None
    exam_repair_count: int = 0


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
    exam_defect_kind: AuditExamDefectKind | None = None


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
