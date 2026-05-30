"""Parse structured output from LLM agents: ROUTE:, sections, signals."""

from __future__ import annotations

import re

from tree.state.models import ChapterScanResult, ExamSections, Route


class ParseError(Exception):
    pass


_SECTION_PATTERN = re.compile(r"^##\s*\[([^\]]+)\]\s*$", re.MULTILINE)
_ROUTE_PATTERN = re.compile(r"^ROUTE:\s*(PASS|FAIL_KNOWLEDGE_GAP)\s*$", re.MULTILINE)
_EXAM_ID_PATTERN = re.compile(r"^EXAM_ID:\s*(.+)$", re.MULTILINE)


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


def detect_exam_too_broad(text: str) -> tuple[bool, str]:
    if "EXAM_TOO_BROAD" not in text:
        return False, ""
    idx = text.index("EXAM_TOO_BROAD")
    bloat = text[idx:].strip()
    return True, bloat


def parse_exam_output(text: str) -> ExamSections:
    """Parse examiner Phase A output into structured sections."""
    kp = extract_section(text, "Next_Knowledge_Point")
    be = extract_section(text, "Blind_Exam")
    ak = extract_section(text, "Answer_Key")
    wi = extract_section(text, "Writer_Instructions")
    return ExamSections(
        knowledge_point=kp,
        blind_exam=be,
        answer_key=ak,
        writer_instructions=wi,
    )


def parse_chapter_scan_output(text: str) -> ChapterScanResult:
    """Parse examiner Phase C output into chapter metadata plus first exam sections."""
    chapter_name = extract_section(text, "Next_Chapter").strip()
    if not chapter_name:
        raise ParseError("Section ## [Next_Chapter] is empty")
    source_collection = _normalize_optional_section(extract_section(text, "Source_Collection"))
    source_collections = _split_optional_list(extract_optional_section(text, "Source_Collections"))
    if source_collection and source_collection not in source_collections:
        source_collections.insert(0, source_collection)
    graph_node_id = _normalize_optional_section(extract_optional_section(text, "Graph_Node"))
    required_nodes = _split_optional_list(extract_optional_section(text, "Required_Nodes"))
    return ChapterScanResult(
        chapter_name=chapter_name,
        source_collection=source_collection,
        source_collections=source_collections,
        graph_node_id=graph_node_id,
        required_nodes=required_nodes,
        exam_sections=parse_exam_output(text),
        selection_rationale=extract_optional_section(text, "Selection_Rationale"),
    )


def _normalize_optional_section(value: str) -> str | None:
    normalized = value.strip()
    if normalized.lower() in {"", "none", "null", "n/a", "unknown", "(none)"}:
        return None
    return normalized


def _split_optional_list(value: str) -> list[str]:
    normalized = value.strip()
    if not normalized:
        return []
    if normalized.lower() in {"none", "null", "n/a", "unknown", "(none)"}:
        return []
    items = re.split(r"[,\n，、]+", normalized)
    return [item.strip() for item in items if item.strip()]


def extract_bottleneck_report(text: str) -> str:
    """Extract Bottleneck Report text (between # Bottleneck Report and ROUTE: line)."""
    start_pat = re.compile(r"^#\s*Bottleneck\s+Report", re.MULTILINE)
    start = start_pat.search(text)
    if not start:
        return text
    end = _ROUTE_PATTERN.search(text)
    if end:
        return text[start.start() : end.start()].strip()
    return text[start.start() :].strip()
