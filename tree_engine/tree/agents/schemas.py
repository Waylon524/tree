"""Strict schemas for JSON returned by Archivist and Dagger."""

from __future__ import annotations

from typing import Annotated, Literal, TypeVar

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
    model_validator,
)

from tree.agents.parsers import extract_json_object


class StrictAgentModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


NonEmptyStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class DeletedRangePayload(StrictAgentModel):
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)
    reason: NonEmptyStr = Field(max_length=300)


class ArchivistDeletePlan(StrictAgentModel):
    deleted_ranges: list[DeletedRangePayload]


class MtuUnitPayload(StrictAgentModel):
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)
    title: NonEmptyStr
    defines: list[NonEmptyStr] | None = Field(default=None, max_length=4)
    # Explicitly typed legacy input so Archivist can repair it to `defines`.
    keywords: list[NonEmptyStr] | None = Field(default=None, max_length=4)
    summary: NonEmptyStr
    unit_kind: Literal["concept", "excercise", "application", "review", "summary", "intro"]


class ArchivistMtuPlan(StrictAgentModel):
    units: list[MtuUnitPayload]


class MtuAssignmentDecision(StrictAgentModel):
    mtu_title: NonEmptyStr


class MtuTitleRepair(StrictAgentModel):
    title: NonEmptyStr


class MtuDefinesRepair(StrictAgentModel):
    defines: list[NonEmptyStr] = Field(min_length=1, max_length=4)


class MtuSummaryRepair(StrictAgentModel):
    summary: NonEmptyStr


class DuplicateDefineUnitRepair(StrictAgentModel):
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)
    defines: list[NonEmptyStr] = Field(min_length=1, max_length=4)


class DuplicateDefineRepairPlan(StrictAgentModel):
    units: list[DuplicateDefineUnitRepair] = Field(min_length=1)


class DaggerNodePayload(StrictAgentModel):
    title: NonEmptyStr
    member_mtu_ids: list[NonEmptyStr] = Field(min_length=1)
    # Empty is a typed semantic signal for deterministic same-collection merge;
    # non-empty entries themselves remain strict and non-blank.
    defines: list[NonEmptyStr] = Field(max_length=8)
    collections: list[NonEmptyStr] = Field(default_factory=list)


class DaggerNodesResponse(StrictAgentModel):
    nodes: list[DaggerNodePayload]


class DaggerPrerequisitePayload(StrictAgentModel):
    node_id: NonEmptyStr | None = None
    node_title: NonEmptyStr | None = None
    title: NonEmptyStr | None = None
    required_defines: list[NonEmptyStr] = Field(max_length=24)
    reason: NonEmptyStr
    external_prerequisites: list[NonEmptyStr] = Field(default_factory=list)
    internal_prerequisite_decision: Literal["selected", "none"]

    @model_validator(mode="after")
    def require_node_identity(self) -> "DaggerPrerequisitePayload":
        if not any((self.node_id, self.node_title, self.title)):
            raise ValueError("one of node_id, node_title, or title is required")
        return self


class DaggerPrerequisitesResponse(StrictAgentModel):
    node_prerequisites: list[DaggerPrerequisitePayload]


AgentModelT = TypeVar("AgentModelT", bound=BaseModel)


def parse_agent_json(raw: str, schema: type[AgentModelT]) -> AgentModelT:
    """Extract one JSON object and validate it against a strict agent schema."""
    return validate_agent_payload(extract_json_object(raw), schema)


def validate_agent_payload(payload: object, schema: type[AgentModelT]) -> AgentModelT:
    """Validate an already decoded provider payload with concise field-path errors."""
    try:
        return schema.model_validate(payload, strict=True)
    except ValidationError as exc:
        problems: list[str] = []
        for item in exc.errors(include_url=False)[:8]:
            location = ".".join(str(part) for part in item.get("loc", ())) or "<root>"
            problems.append(f"{location}: {item.get('msg', 'invalid value')}")
        summary = "; ".join(problems)
        raise ValueError(f"{schema.__name__} schema invalid: {summary}") from exc


def metadata_repair_schema(field: str) -> type[BaseModel]:
    schemas: dict[str, type[BaseModel]] = {
        "title": MtuTitleRepair,
        "defines": MtuDefinesRepair,
        "summary": MtuSummaryRepair,
    }
    try:
        return schemas[field]
    except KeyError as exc:
        raise ValueError(f"Unsupported MTU metadata repair field: {field}") from exc
