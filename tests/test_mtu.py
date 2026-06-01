"""Tests for MTU helpers (step 4 program side)."""

from __future__ import annotations

import pytest

from tree.planner.mtu import (
    MtuCoverageError,
    build_mtus,
    mtu_text,
    number_lines,
    validate_and_normalize,
)


def test_number_lines():
    assert number_lines("a\nb\nc") == "1\ta\n2\tb\n3\tc"


def test_validate_full_coverage_ok():
    plan = {
        "units": [
            {
                "start_line": 1,
                "end_line": 3,
                "title": "干涉条件",
                "keywords": ["相干光"],
                "summary": "说明产生稳定干涉条纹所需满足的相干条件。",
                "unit_kind": "concept",
            },
            {
                "start_line": 5,
                "end_line": 6,
                "title": "牛顿环",
                "keywords": [],
                "summary": "说明牛顿环现象及其作为等厚干涉应用的边界。",
                "unit_kind": "example",
            },
        ],
        "skipped_ranges": [{"start_line": 4, "end_line": 4, "reason": "footer"}],
    }
    units, skipped = validate_and_normalize(plan, line_count=6)
    assert len(units) == 2
    assert len(skipped) == 1
    assert units[0]["unit_kind"] == "concept"


def test_validate_detects_gap():
    plan = {
        "units": [
            {
                "start_line": 1,
                "end_line": 2,
                "title": "干涉条件",
                "summary": "说明产生稳定干涉条纹所需满足的相干条件。",
            }
        ],
        "skipped_ranges": [],
    }
    with pytest.raises(MtuCoverageError, match="gap"):
        validate_and_normalize(plan, line_count=5)


def test_validate_detects_overlap():
    plan = {
        "units": [
            {
                "start_line": 1,
                "end_line": 3,
                "title": "干涉条件",
                "summary": "说明产生稳定干涉条纹所需满足的相干条件。",
            },
            {
                "start_line": 3,
                "end_line": 4,
                "title": "牛顿环",
                "summary": "说明牛顿环现象及其作为等厚干涉应用的边界。",
            },
        ],
        "skipped_ranges": [],
    }
    with pytest.raises(MtuCoverageError, match="overlap"):
        validate_and_normalize(plan, line_count=4)


def test_invalid_unit_kind_falls_back_to_concept():
    plan = {
        "units": [
            {
                "start_line": 1,
                "end_line": 2,
                "title": "干涉条件",
                "summary": "说明产生稳定干涉条纹所需满足的相干条件。",
                "unit_kind": "weird",
            }
        ],
        "skipped_ranges": [],
    }
    units, _ = validate_and_normalize(plan, line_count=2)
    assert units[0]["unit_kind"] == "concept"


def test_validate_enforces_unit_metadata_limits():
    plan = {
        "units": [
            {
                "start_line": 1,
                "end_line": 2,
                "title": "干涉条件",
                "keywords": ["k1", "k2", "k3", "k4", "k5", "k6", "k7", "k8", "k9", "k10", "k11"],
                "summary": "说明产生稳定干涉条纹所需满足的相干条件。",
            }
        ],
        "skipped_ranges": [],
    }
    with pytest.raises(MtuCoverageError, match="keywords"):
        validate_and_normalize(plan, line_count=2)


@pytest.mark.parametrize(
    ("title", "summary", "message"),
    [
        ("短", "说明产生稳定干涉条纹所需满足的相干条件。", "title"),
        ("这是一个明显超过四十个显示字符限制的标题因为它包含太多汉字", "说明产生稳定干涉条纹所需满足的相干条件。", "title"),
        ("干涉条件", "太短", "summary"),
        ("干涉条件", "很长" * 40, "summary"),
    ],
)
def test_validate_enforces_title_and_summary_display_lengths(title, summary, message):
    plan = {
        "units": [
            {
                "start_line": 1,
                "end_line": 2,
                "title": title,
                "keywords": ["相干光"],
                "summary": summary,
            }
        ],
        "skipped_ranges": [],
    }
    with pytest.raises(MtuCoverageError, match=message):
        validate_and_normalize(plan, line_count=2)


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


def test_mtu_text_slice():
    md = "l1\nl2\nl3\nl4"
    assert mtu_text(md, (2, 3)) == "l2\nl3"
