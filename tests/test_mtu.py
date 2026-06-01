"""Tests for MTU helpers (step 4 program side)."""

from __future__ import annotations

import pytest

from tree.planner.mtu import (
    MtuCoverageError,
    build_mtus,
    mtu_text,
    number_lines,
    validate_and_normalize,
    whole_document_fallback,
)


def test_number_lines():
    assert number_lines("a\nb\nc") == "1\ta\n2\tb\n3\tc"


def test_validate_full_coverage_ok():
    plan = {
        "units": [
            {"start_line": 1, "end_line": 3, "title": "概念A", "keywords": ["x"], "unit_kind": "concept"},
            {"start_line": 5, "end_line": 6, "title": "概念B", "keywords": [], "unit_kind": "example"},
        ],
        "skipped_ranges": [{"start_line": 4, "end_line": 4, "reason": "footer"}],
    }
    units, skipped = validate_and_normalize(plan, line_count=6)
    assert len(units) == 2
    assert len(skipped) == 1
    assert units[0]["unit_kind"] == "concept"


def test_validate_detects_gap():
    plan = {"units": [{"start_line": 1, "end_line": 2, "title": "t"}], "skipped_ranges": []}
    with pytest.raises(MtuCoverageError, match="gap"):
        validate_and_normalize(plan, line_count=5)


def test_validate_detects_overlap():
    plan = {
        "units": [
            {"start_line": 1, "end_line": 3, "title": "a"},
            {"start_line": 3, "end_line": 4, "title": "b"},
        ],
        "skipped_ranges": [],
    }
    with pytest.raises(MtuCoverageError, match="overlap"):
        validate_and_normalize(plan, line_count=4)


def test_invalid_unit_kind_falls_back_to_concept():
    plan = {"units": [{"start_line": 1, "end_line": 2, "title": "t", "unit_kind": "weird"}], "skipped_ranges": []}
    units, _ = validate_and_normalize(plan, line_count=2)
    assert units[0]["unit_kind"] == "concept"


def test_build_mtus_ids_and_order():
    units = [
        {"start_line": 1, "end_line": 2, "title": "A", "keywords": ["k"], "summary": "s", "unit_kind": "concept"},
        {"start_line": 3, "end_line": 4, "title": "B", "keywords": [], "summary": "", "unit_kind": "exercise"},
    ]
    mtus = build_mtus(units, collection="课件", source_file="ch1.md", order_offset=10)
    assert [m.source_order_index for m in mtus] == [10, 11]
    assert mtus[0].mtu_id.startswith("mtu:")
    assert mtus[0].line_range == (1, 2)
    # deterministic
    again = build_mtus(units, collection="课件", source_file="ch1.md", order_offset=10)
    assert [m.mtu_id for m in mtus] == [m.mtu_id for m in again]


def test_whole_document_fallback():
    mtus = whole_document_fallback(12, collection="c", source_file="f.md")
    assert len(mtus) == 1
    assert mtus[0].line_range == (1, 12)


def test_mtu_text_slice():
    md = "l1\nl2\nl3\nl4"
    assert mtu_text(md, (2, 3)) == "l2\nl3"
