"""Parse structured agent output: exam sections, audit route, strict JSON."""

from __future__ import annotations

import json
import re

from tree.state.models import (
    AuditExamDefectKind,
    AuditPlannerDefectKind,
    ExamReconciliationAction,
    ExamReconciliationResult,
    ExamSections,
    Route,
    WriterInstructions,
)


class ParseError(Exception):
    pass


_ROUTE_PATTERN = re.compile(r"^ROUTE:\s*(PASS|FAIL_KNOWLEDGE_GAP)\s*$", re.MULTILINE)
_EXAM_ID_PATTERN = re.compile(r"^EXAM_ID:\s*(.+)$", re.MULTILINE)
_AUDIT_DEFECT_PATTERN = re.compile(r"^EXAM_DEFECT:\s*(\S+)\s*$", re.MULTILINE)
_PLANNER_DEFECT_PATTERN = re.compile(r"^PLANNER_DEFECT:\s*(\S+)\s*$", re.MULTILINE)
_RECONCILIATION_ACTION_PATTERN = re.compile(
    r"^ACTION:\s*(KEEP_FAIL|REVISE_EXAM)\s*$", re.MULTILINE
)
_REASON_PATTERN = re.compile(r"^REASON:\s*(.+)$", re.MULTILINE)


def extract_section(text: str, header: str) -> str:
    """Extract content between ## [header] and the next ## [ or EOF."""
    pattern = re.compile(
        rf"^##\s*\[{re.escape(header)}\]\s*\n(.*?)(?=^##\s*\[|\Z)",
        re.MULTILINE | re.DOTALL,
    )
    match = pattern.search(text)
    if not match:
        raise ParseError(f"Section ## [{header}] not found in output")
    return match.group(1).strip()


def extract_optional_section(text: str, header: str) -> str:
    try:
        return extract_section(text, header)
    except ParseError:
        return ""


def parse_route(text: str) -> Route:
    match = _unique_match(_ROUTE_PATTERN, text, "ROUTE")
    return Route(match.group(1))


def parse_exam_id(text: str) -> str:
    match = _unique_match(_EXAM_ID_PATTERN, text, "EXAM_ID")
    value = match.group(1).strip()
    if not value:
        raise ParseError("EXAM_ID must not be empty")
    return value


def parse_audit_defect_kind(text: str) -> AuditExamDefectKind | None:
    matches = list(_AUDIT_DEFECT_PATTERN.finditer(text))
    if not matches:
        return None
    if len(matches) != 1:
        raise ParseError(f"EXAM_DEFECT must appear at most once; found {len(matches)}")
    match = matches[0]
    value = match.group(1).strip()
    try:
        return AuditExamDefectKind(value)
    except ValueError as exc:
        raise ParseError(f"Invalid EXAM_DEFECT value: {value}") from exc


def parse_planner_defect_kind(text: str) -> AuditPlannerDefectKind | None:
    matches = list(_PLANNER_DEFECT_PATTERN.finditer(text))
    if not matches:
        return None
    if len(matches) != 1:
        raise ParseError(f"PLANNER_DEFECT must appear at most once; found {len(matches)}")
    value = matches[0].group(1).strip()
    try:
        return AuditPlannerDefectKind(value)
    except ValueError as exc:
        raise ParseError(f"Invalid PLANNER_DEFECT value: {value}") from exc


def parse_exam_sections(text: str) -> ExamSections:
    """Parse examiner Phase A output into structured sections."""
    covered_node_ids = _split_required_list(
        _required_section(text, "Covered_Node_IDs"), "Covered_Node_IDs"
    )
    writer_instructions = _required_section(text, "Writer_Instructions")
    try:
        writer_instruction_spec = WriterInstructions.from_text(
            writer_instructions,
            expected_covered_node_ids=covered_node_ids,
        )
    except ValueError as exc:
        raise ParseError(f"Writer_Instructions schema invalid: {exc}") from exc
    return ExamSections(
        knowledge_point=_required_section(text, "Next_Knowledge_Point"),
        covered_node_ids=covered_node_ids,
        blind_exam=_required_section(text, "Blind_Exam"),
        answer_key=_required_section(text, "Answer_Key"),
        writer_instructions=writer_instructions,
        writer_instruction_spec=writer_instruction_spec,
    )


def parse_exam_reconciliation(text: str) -> ExamReconciliationResult:
    """Parse examiner Phase C output into a keep/revise decision."""
    match = _unique_match(_RECONCILIATION_ACTION_PATTERN, text, "ACTION")
    action = ExamReconciliationAction(match.group(1))
    reason_match = _unique_match(_REASON_PATTERN, text, "REASON")
    reason = reason_match.group(1).strip()
    if not reason:
        raise ParseError("REASON must not be empty")
    exam_sections = parse_exam_sections(text) if action is ExamReconciliationAction.REVISE_EXAM else None
    return ExamReconciliationResult(action=action, reason=reason, exam_sections=exam_sections)


def extract_bottleneck_report(text: str) -> str:
    """Extract the Bottleneck Report (between # Bottleneck Report and ROUTE:)."""
    start = re.compile(r"^#\s*Bottleneck\s+Report", re.MULTILINE).search(text)
    if not start:
        return text.strip()
    end = _ROUTE_PATTERN.search(text)
    if end:
        return text[start.start() : end.start()].strip()
    return text[start.start() :].strip()


def extract_json_object(raw: str) -> dict:
    """Decode exactly one top-level JSON object, tolerating only a full code fence."""
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text).strip()
    try:
        value, end = json.JSONDecoder().raw_decode(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"No valid JSON object found in response: {exc.msg}") from exc
    if text[end:].strip():
        raise ValueError("Unexpected content after the JSON object")
    if not isinstance(value, dict):
        raise ValueError("Agent response must be one top-level JSON object")
    return value


def _split_optional_list(value: str) -> list[str]:
    normalized = value.strip()
    if not normalized or normalized.lower() in {"none", "null", "n/a", "unknown", "(none)"}:
        return []
    items = re.split(r"[,\n，、]+", normalized)
    return [item.strip() for item in items if item.strip()]


def _split_required_list(value: str, header: str) -> list[str]:
    items = _split_optional_list(value)
    if not items:
        raise ParseError(f"Section ## [{header}] must contain at least one item")
    return items


def _required_section(text: str, header: str) -> str:
    value = extract_section(text, header)
    if not value:
        raise ParseError(f"Section ## [{header}] must not be empty")
    return value


def _unique_match(pattern: re.Pattern[str], text: str, label: str) -> re.Match[str]:
    matches = list(pattern.finditer(text))
    if not matches:
        raise ParseError(f"No {label}: found in examiner output")
    if len(matches) != 1:
        raise ParseError(f"{label} must appear exactly once; found {len(matches)}")
    return matches[0]
