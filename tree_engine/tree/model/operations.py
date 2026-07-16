"""Stable operation-level LLM request specifications.

Role configuration remains the user-facing ceiling. These specs select a smaller
budget or cheaper reasoning mode when an operation does not need the role's full
allowance.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


ThinkingMode = Literal["enabled", "disabled"]


@dataclass(frozen=True)
class LLMOperationSpec:
    role: str
    max_output_tokens: int
    thinking: ThinkingMode
    json_mode: bool = False
    reasoning_effort: str | None = None
    timeout_sec: float | None = None
    max_retries: int | None = None


_ROLE_DEFAULTS = {
    "archivist": LLMOperationSpec("archivist", 131_072, "disabled", json_mode=True),
    "dagger": LLMOperationSpec(
        "dagger", 131_072, "enabled", json_mode=True, reasoning_effort="high"
    ),
    "examiner": LLMOperationSpec("examiner", 131_072, "enabled"),
    "student": LLMOperationSpec("student", 131_072, "disabled"),
    "writer": LLMOperationSpec("writer", 131_072, "enabled"),
}


OPERATION_SPECS: dict[str, LLMOperationSpec] = {
    # Archivist
    "archivist.clean": LLMOperationSpec("archivist", 65_536, "disabled", json_mode=True),
    "archivist.clean_range_repair": LLMOperationSpec(
        "archivist", 32_768, "disabled", json_mode=True, timeout_sec=240, max_retries=1
    ),
    "archivist.mtu_segment": LLMOperationSpec(
        "archivist", 131_072, "disabled", json_mode=True
    ),
    "archivist.mtu_assignment": LLMOperationSpec(
        "archivist", 8_192, "disabled", json_mode=True, timeout_sec=180, max_retries=1
    ),
    "archivist.mtu_metadata_repair": LLMOperationSpec(
        "archivist", 8_192, "disabled", json_mode=True, timeout_sec=180, max_retries=1
    ),
    "archivist.mtu_units_repair": LLMOperationSpec(
        "archivist", 65_536, "disabled", json_mode=True, timeout_sec=240, max_retries=1
    ),
    "archivist.mtu_duplicate_define_repair": LLMOperationSpec(
        "archivist", 32_768, "disabled", json_mode=True, timeout_sec=240, max_retries=1
    ),
    # Dagger
    "dagger.build_nodes": LLMOperationSpec(
        "dagger", 131_072, "enabled", json_mode=True, reasoning_effort="high"
    ),
    "dagger.select_prerequisites": LLMOperationSpec(
        "dagger", 32_768, "enabled", json_mode=True, timeout_sec=240
    ),
    "dagger.repair_defines": LLMOperationSpec(
        "dagger",
        65_536,
        "enabled",
        json_mode=True,
        reasoning_effort="high",
        timeout_sec=300,
        max_retries=1,
    ),
    "dagger.repair_prerequisites": LLMOperationSpec(
        "dagger",
        131_072,
        "enabled",
        json_mode=True,
        reasoning_effort="high",
        max_retries=1,
    ),
    # Examiner
    "examiner.compose": LLMOperationSpec("examiner", 131_072, "enabled"),
    "examiner.audit": LLMOperationSpec("examiner", 131_072, "enabled"),
    "examiner.reconcile": LLMOperationSpec(
        "examiner", 131_072, "enabled", reasoning_effort="high"
    ),
    "examiner.compose_format_repair": LLMOperationSpec(
        "examiner", 131_072, "disabled", timeout_sec=300, max_retries=1
    ),
    "examiner.audit_format_repair": LLMOperationSpec(
        "examiner", 65_536, "disabled", timeout_sec=240, max_retries=1
    ),
    "examiner.reconcile_format_repair": LLMOperationSpec(
        "examiner", 131_072, "disabled", timeout_sec=300, max_retries=1
    ),
    # Student / Writer natural-language operations
    "student.answer": LLMOperationSpec("student", 131_072, "disabled"),
    "writer.create": LLMOperationSpec("writer", 131_072, "enabled"),
    "writer.optimize": LLMOperationSpec("writer", 131_072, "enabled"),
    "writer.feedback_revision": LLMOperationSpec("writer", 131_072, "enabled"),
}


def resolve_operation_spec(role: str, operation: str | None) -> tuple[str, LLMOperationSpec]:
    """Return a validated stable id and spec, preserving direct-client compatibility."""
    if operation is None:
        try:
            return f"{role}.default", _ROLE_DEFAULTS[role]
        except KeyError as exc:
            raise ValueError(f"Unknown LLM role: {role}") from exc
    try:
        spec = OPERATION_SPECS[operation]
    except KeyError as exc:
        raise ValueError(f"Unknown LLM operation: {operation}") from exc
    if spec.role != role:
        raise ValueError(
            f"LLM operation {operation} belongs to role {spec.role}, not requested role {role}"
        )
    return operation, spec
