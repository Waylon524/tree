"""Parse structured output from LLM agents: ROUTE:, sections, signals."""

from __future__ import annotations

import re

from tree.state.models import ExamSections, Route


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


def detect_chapter_complete(text: str) -> bool:
    return "CHAPTER_COMPLETE" in text.split("\n")[-5:]


def detect_pipeline_complete(text: str) -> bool:
    return "PIPELINE_COMPLETE" in text.split("\n")[-5:]


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
    si = extract_section(text, "Student_Instructions")
    ak = extract_section(text, "Answer_Key")
    ai = extract_section(text, "Architect_Instructions")
    return ExamSections(
        knowledge_point=kp,
        blind_exam=be,
        student_instructions=si,
        answer_key=ak,
        architect_instructions=ai,
    )


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
