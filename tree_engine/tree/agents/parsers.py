"""Parse structured agent output: exam sections, audit route, strict JSON."""

from __future__ import annotations

import json
import re

from tree.state.models import (
    AuditExamDefectKind,
    ExamReconciliationAction,
    ExamReconciliationResult,
    ExamSections,
    Route,
)


class ParseError(Exception):
    pass


_ROUTE_PATTERN = re.compile(r"^ROUTE:\s*(PASS|FAIL_KNOWLEDGE_GAP)\s*$", re.MULTILINE)
_EXAM_ID_PATTERN = re.compile(r"^EXAM_ID:\s*(.+)$", re.MULTILINE)
_AUDIT_DEFECT_PATTERN = re.compile(r"^EXAM_DEFECT:\s*(\S+)\s*$", re.MULTILINE)
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
    match = _ROUTE_PATTERN.search(text)
    if not match:
        raise ParseError("No ROUTE: found in examiner output")
    return Route(match.group(1))


def parse_exam_id(text: str) -> str:
    match = _EXAM_ID_PATTERN.search(text)
    if not match:
        raise ParseError("No EXAM_ID: found in examiner output")
    return match.group(1).strip()


def parse_audit_defect_kind(text: str) -> AuditExamDefectKind | None:
    match = _AUDIT_DEFECT_PATTERN.search(text)
    if not match:
        return None
    value = match.group(1).strip()
    try:
        return AuditExamDefectKind(value)
    except ValueError as exc:
        raise ParseError(f"Invalid EXAM_DEFECT value: {value}") from exc


def parse_exam_sections(text: str) -> ExamSections:
    """Parse examiner Phase A output into structured sections."""
    return ExamSections(
        knowledge_point=extract_section(text, "Next_Knowledge_Point"),
        covered_node_ids=_split_required_list(
            extract_section(text, "Covered_Node_IDs"), "Covered_Node_IDs"
        ),
        blind_exam=extract_section(text, "Blind_Exam"),
        answer_key=extract_section(text, "Answer_Key"),
        writer_instructions=extract_section(text, "Writer_Instructions"),
    )


def parse_exam_reconciliation(text: str) -> ExamReconciliationResult:
    """Parse examiner Phase C output into a keep/revise decision."""
    match = _RECONCILIATION_ACTION_PATTERN.search(text)
    if not match:
        raise ParseError("No ACTION: KEEP_FAIL or ACTION: REVISE_EXAM found")
    action = ExamReconciliationAction(match.group(1))
    reason_match = _REASON_PATTERN.search(text)
    reason = reason_match.group(1).strip() if reason_match else ""
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
    """Best-effort extraction of the first top-level JSON object in `raw`."""
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("No JSON object found in response")
    return json.loads(text[start : end + 1])


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
