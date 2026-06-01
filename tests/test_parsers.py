"""Tests for agent output parsers (step 7)."""

from __future__ import annotations

import pytest

from tree.agents.parsers import (
    ParseError,
    extract_bottleneck_report,
    extract_json_object,
    extract_section,
    parse_exam_id,
    parse_exam_sections,
    parse_route,
)
from tree.state.models import Route

_EXAM = """## [Next_Knowledge_Point]
01. 化学平衡

## [Covered_Node_IDs]
n1, n2

## [Blind_Exam]
Q1 ...

## [Answer_Key]
A1 ...

## [Writer_Instructions]
Scope: 教化学平衡
"""

_AUDIT = """# Bottleneck Report

## Correctness Checklist
- Q1: FAIL — 缺少平衡常数表达式

ROUTE: FAIL_KNOWLEDGE_GAP
EXAM_ID: 化学平衡
"""


def test_parse_exam_sections():
    exam = parse_exam_sections(_EXAM)
    assert exam.knowledge_point == "01. 化学平衡"
    assert exam.covered_node_ids == ["n1", "n2"]
    assert exam.blind_exam.startswith("Q1")
    assert exam.writer_instructions.startswith("Scope")


def test_parse_route_and_exam_id():
    assert parse_route(_AUDIT) is Route.FAIL_KNOWLEDGE_GAP
    assert parse_exam_id(_AUDIT) == "化学平衡"


def test_extract_bottleneck_report_stops_before_route():
    report = extract_bottleneck_report(_AUDIT)
    assert report.startswith("# Bottleneck Report")
    assert "ROUTE:" not in report


def test_extract_section_missing_raises():
    with pytest.raises(ParseError):
        extract_section("no sections here", "Blind_Exam")


def test_extract_json_object_handles_code_fence():
    assert extract_json_object('```json\n{"a": 1}\n```') == {"a": 1}
