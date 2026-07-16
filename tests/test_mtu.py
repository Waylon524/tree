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
                "end_line": 4,
                "title": "干涉条件",
                "defines": ["相干光"],
                "summary": "说明产生稳定干涉条纹所需满足的相干条件。",
                "unit_kind": "concept",
            },
            {
                "start_line": 5,
                "end_line": 6,
                "title": "牛顿环",
                "defines": ["等厚干涉"],
                "summary": "说明牛顿环现象及其作为等厚干涉应用的边界。",
                "unit_kind": "application",
            },
        ]
    }
    units, skipped = validate_and_normalize(plan, line_count=6)
    assert len(units) == 2
    assert skipped == []
    assert units[0]["unit_kind"] == "concept"


def test_validate_rejects_skipped_ranges_field():
    plan = {
        "units": [
            {
                "start_line": 1,
                "end_line": 2,
                "title": "干涉条件",
                "defines": ["相干光"],
                "summary": "说明产生稳定干涉条纹所需满足的相干条件。",
                "unit_kind": "concept",
            }
        ],
        "skipped_ranges": [],
    }
    with pytest.raises(MtuCoverageError, match="skipped_ranges"):
        validate_and_normalize(plan, line_count=2)


def test_validate_detects_gap():
    plan = {
        "units": [
            {
                "start_line": 1,
                "end_line": 2,
                "title": "干涉条件",
                "summary": "说明产生稳定干涉条纹所需满足的相干条件。",
            }
        ]
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
        ]
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
        ]
    }
    units, _ = validate_and_normalize(plan, line_count=2)
    assert units[0]["unit_kind"] == "concept"


@pytest.mark.parametrize("unit_kind", ["example", "misconception", "procedure", "weird"])
def test_removed_or_invalid_unit_kinds_fall_back_to_concept(unit_kind):
    plan = {
        "units": [
            {
                "start_line": 1,
                "end_line": 2,
                "title": "干涉条件",
                "summary": "说明产生稳定干涉条纹所需满足的相干条件。",
                "unit_kind": unit_kind,
            }
        ]
    }

    units, _ = validate_and_normalize(plan, line_count=2)

    assert units[0]["unit_kind"] == "concept"


def test_validate_accepts_auxiliary_unit_kinds():
    plan = {
        "units": [
            {
                "start_line": 1,
                "end_line": 1,
                "title": "练习片段",
                "summary": "给出前文干涉条件的练习题和使用场景。",
                "unit_kind": "excercise",
            },
            {
                "start_line": 1,
                "end_line": 1,
                "title": "复习段落",
                "summary": "回顾前文已经学习过的干涉条件和使用边界。",
                "unit_kind": "review",
            },
            {
                "start_line": 2,
                "end_line": 2,
                "title": "内容小结",
                "summary": "总结本节主要公式和概念之间的适用关系。",
                "unit_kind": "summary",
            },
            {
                "start_line": 3,
                "end_line": 3,
                "title": "章节引入",
                "summary": "引出下一节学习目标并建立和前文的连接。",
                "unit_kind": "intro",
            },
            {
                "start_line": 4,
                "end_line": 4,
                "title": "应用片段",
                "summary": "说明该公式在薄膜应用场景中的使用方式。",
                "unit_kind": "application",
            },
        ]
    }

    for index, unit in enumerate(plan["units"], start=1):
        unit["start_line"] = index
        unit["end_line"] = index

    units, _ = validate_and_normalize(plan, line_count=5)

    assert [unit["unit_kind"] for unit in units] == [
        "exercise",
        "review",
        "summary",
        "intro",
        "application",
    ]


def test_validate_enforces_unit_metadata_limits():
    plan = {
        "units": [
            {
                "start_line": 1,
                "end_line": 2,
                "title": "干涉条件",
                "defines": ["k1", "k2", "k3", "k4", "k5"],
                "summary": "说明产生稳定干涉条纹所需满足的相干条件。",
            }
        ]
    }
    with pytest.raises(MtuCoverageError, match="defines"):
        validate_and_normalize(plan, line_count=2)


def test_validate_normalizes_presentation_metadata_without_failing():
    plan = {
        "units": [
            {
                "start_line": 1,
                "end_line": 2,
                "title": "短",
                "defines": ["概念"],
                "summary": "摘" * 100,
            }
        ]
    }

    units, _ = validate_and_normalize(plan, line_count=2)

    assert units[0]["title"] == "短"
    assert units[0]["summary"].endswith("…")
    assert len(units[0]["summary"]) < 100


def test_validate_rejects_legacy_keywords_field():
    plan = {
        "units": [
            {
                "start_line": 1,
                "end_line": 2,
                "title": "干涉条件",
                "keywords": ["相干光"],
                "summary": "说明产生稳定干涉条纹所需满足的相干条件。",
            }
        ]
    }
    with pytest.raises(MtuCoverageError, match="keywords"):
        validate_and_normalize(plan, line_count=2)


@pytest.mark.parametrize(
    ("title", "summary"),
    [
        ("A", "说明产生稳定干涉条纹所需满足的相干条件。"),
        ("这是一个明显超过四十个显示字符限制的标题因为它包含太多汉字", "说明产生稳定干涉条纹所需满足的相干条件。"),
        ("干涉条件", "太短"),
        ("干涉条件", "很长" * 40),
    ],
)
def test_validate_normalizes_title_and_summary_display_lengths(title, summary):
    plan = {
        "units": [
            {
                "start_line": 1,
                "end_line": 2,
                "title": title,
                "defines": ["相干光"],
                "summary": summary,
            }
        ]
    }
    units, _ = validate_and_normalize(plan, line_count=2)
    assert units[0]["title"]
    assert units[0]["summary"]


def test_validate_allows_four_display_character_title():
    plan = {
        "units": [
            {
                "start_line": 1,
                "end_line": 2,
                "title": "短题",
                "defines": ["相干光"],
                "summary": "说明产生稳定干涉条纹所需满足的相干条件。",
            }
        ]
    }
    units, _ = validate_and_normalize(plan, line_count=2)
    assert units[0]["title"] == "短题"


def test_build_mtus_ids_and_order():
    units = [
        {"start_line": 1, "end_line": 31, "title": "A", "defines": ["k"], "summary": "s", "unit_kind": "concept"},
        {"start_line": 32, "end_line": 62, "title": "B", "defines": ["b"], "summary": "s", "unit_kind": "concept"},
    ]
    mtus = build_mtus(units, collection="课件", source_file="ch1.md", order_offset=10)
    assert [m.source_order_index for m in mtus] == [10, 11]
    assert mtus[0].mtu_id.startswith("mtu:")
    assert mtus[0].line_range == (1, 31)
    assert mtus[0].defines == ["k"]
    assert mtus[0].keywords == ["k"]
    # deterministic
    again = build_mtus(units, collection="课件", source_file="ch1.md", order_offset=10)
    assert [m.mtu_id for m in mtus] == [m.mtu_id for m in again]


def test_build_mtus_accepts_single_short_concept_without_data_loss():
    units = [
        {"start_line": 1, "end_line": 20, "title": "A", "defines": ["k"], "summary": "s", "unit_kind": "concept"},
    ]
    mtus = build_mtus(units, collection="课件", source_file="ch1.md")
    assert mtus[0].line_range == (1, 20)

    too_short = [
        {"start_line": 1, "end_line": 19, "title": "A", "defines": ["k"], "summary": "s", "unit_kind": "concept"},
    ]
    mtus = build_mtus(too_short, collection="课件", source_file="ch1.md")
    assert mtus[0].line_range == (1, 19)


def test_build_mtus_merges_or_deletes_auxiliary_units():
    units = [
        {"start_line": 1, "end_line": 1, "title": "引入", "defines": [], "summary": "引出本节核心概念。", "unit_kind": "intro"},
        {"start_line": 2, "end_line": 32, "title": "概念A", "defines": ["A"], "summary": "说明概念A的定义。", "unit_kind": "concept"},
        {"start_line": 33, "end_line": 33, "title": "练习A", "defines": ["A练习"], "summary": "展示概念A的练习。", "unit_kind": "excercise"},
        {"start_line": 34, "end_line": 34, "title": "应用A", "defines": ["A应用"], "summary": "展示概念A的应用。", "unit_kind": "application"},
        {"start_line": 35, "end_line": 35, "title": "复习A", "defines": ["A"], "summary": "复习概念A。", "unit_kind": "review"},
        {"start_line": 36, "end_line": 36, "title": "小结A", "defines": [], "summary": "总结概念A。", "unit_kind": "summary"},
    ]

    mtus = build_mtus(units, collection="课件", source_file="ch1.md", order_offset=3)

    assert [m.title for m in mtus] == ["概念A"]
    assert mtus[0].line_range == (2, 34)
    assert mtus[0].defines == ["A", "A应用"]
    assert mtus[0].unit_kind == "concept"
    assert [m.source_order_index for m in mtus] == [3]


def test_mtu_text_slice():
    md = "l1\nl2\nl3\nl4"
    assert mtu_text(md, (2, 3)) == "l2\nl3"
