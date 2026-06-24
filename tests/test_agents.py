"""Tests for examiner/student/writer agents with a fake LLM client (step 7)."""

from __future__ import annotations

import pytest

from tree.agents.archivist import ArchivistAgent
from tree.agents.examiner import ExaminerAgent
from tree.agents.prompts import ARCHIVIST_MTU_PROMPT
from tree.agents.student import StudentAgent
from tree.agents.writer import WriterAgent, sanitize_writer_context
from tree.planner.mtu import MtuCoverageError
from tree.state.models import ExamReconciliationAction, Route

_EXAM_OUTPUT = """## [Next_Knowledge_Point]
01. 化学平衡

## [Covered_Node_IDs]
n1

## [Blind_Exam]
Q1 写出平衡常数表达式

## [Answer_Key]
K = [C]/[A][B]

## [Writer_Instructions]
Scope: 教平衡常数
"""

_AUDIT_OUTPUT = """# Bottleneck Report

## Knowledge Defects
- MISSING_FORMULA: 平衡常数表达式

ROUTE: FAIL_KNOWLEDGE_GAP
EXAM_ID: 化学平衡
"""


class _FakeClient:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    async def call(self, role, system, user, *, timeout_sec=None):
        self.calls.append((role, user))
        r = self.responses[role]
        return r(user) if callable(r) else r


def _examiner_response(user):
    return _EXAM_OUTPUT if "Exam Assembly" in user else _AUDIT_OUTPUT


def _markdown_lines(count: int) -> str:
    return "\n".join(f"line {index}" for index in range(1, count + 1))


async def test_examiner_compose_parses_sections():
    agent = ExaminerAgent(_FakeClient({"examiner": _examiner_response}))
    exam = await agent.compose(next_seq="01", prior_paths=[], prior_contents=[], branch_context="ctx")
    assert exam.knowledge_point == "01. 化学平衡"
    assert exam.covered_node_ids == ["n1"]


async def test_examiner_audit_parses_route():
    agent = ExaminerAgent(_FakeClient({"examiner": _examiner_response}))
    audit = await agent.audit(
        exam_paper="Q", answer_key="A", student_answer="ans", draft_text=None,
        prior_paths=[], prior_contents=[],
    )
    assert audit.route is Route.FAIL_KNOWLEDGE_GAP
    assert audit.exam_id == "化学平衡"
    assert "MISSING_FORMULA" in audit.bottleneck_report


async def test_examiner_reconcile_exam_parses_revised_exam():
    response = """ACTION: REVISE_EXAM
REASON: answer key contradicted the draft formula

## [Next_Knowledge_Point]
01. 化学平衡

## [Covered_Node_IDs]
n1

## [Blind_Exam]
Q

## [Answer_Key]
A

## [Writer_Instructions]
Scope: x
"""
    agent = ExaminerAgent(_FakeClient({"examiner": response}))

    result = await agent.reconcile_exam(
        exam_paper="bad Q",
        answer_key="bad A",
        draft_text="draft",
        bottleneck_report="answer key contradiction",
        prior_paths=[],
        prior_contents=[],
    )

    assert result.action is ExamReconciliationAction.REVISE_EXAM
    assert result.exam_sections is not None
    assert result.exam_sections.answer_key == "A"


async def test_student_answer_returns_text():
    agent = StudentAgent(_FakeClient({"student": "学生作答内容"}))
    out = await agent.answer(blind_exam="Q", prior_paths=[], prior_contents=[])
    assert out == "学生作答内容"


async def test_archivist_clean_deletes_only_llm_selected_ranges():
    raw = "# 原始标题\n页脚 12\n## 原始小节\n教学正文"
    client = _FakeClient(
        {
            "archivist": """{
              "deleted_ranges": [
                {"start_line": 2, "end_line": 2, "reason": "page_footer"}
              ]
            }"""
        }
    )
    agent = ArchivistAgent(client)

    cleaned = await agent.clean(raw)

    assert cleaned == "# 原始标题\n## 原始小节\n教学正文"
    user_prompt = client.calls[0][1]
    assert "TOTAL_LINES: 4" in user_prompt
    assert "1\t# 原始标题" in user_prompt
    assert "2\t页脚 12" in user_prompt


async def test_archivist_clean_repairs_only_invalid_deleted_ranges():
    raw = "教学一\n页脚\n广告\n教学二"

    def response(user):
        if "INVALID_DELETED_RANGES" in user:
            assert '"start_line": 2' in user
            assert '"reason": "page_footer"' in user
            assert '"start_line": 8' in user
            assert '"start_line": 2, "end_line": 2' in user
            return """{
              "deleted_ranges": [
                {"start_line": 3, "end_line": 3, "reason": "ad"}
              ]
            }"""
        return """{
          "deleted_ranges": [
            {"start_line": 2, "end_line": 2, "reason": "page_footer"},
            {"start_line": 2, "end_line": 3, "reason": "overlap"},
            {"start_line": 8, "end_line": 9, "reason": "out_of_bounds"}
          ]
        }"""

    client = _FakeClient({"archivist": response})
    agent = ArchivistAgent(client)

    cleaned = await agent.clean(raw)

    assert cleaned == "教学一\n教学二"
    assert len(client.calls) == 2


async def test_archivist_clean_retries_malformed_json():
    raw = "教学一\n页脚\n教学二"
    responses = iter(
        [
            '{"deleted_ranges": [{"start_line": 2 "end_line": 2}]}',
            '{"deleted_ranges": [{"start_line": 2, "end_line": 2, "reason": "footer"}]}',
        ]
    )
    client = _FakeClient({"archivist": lambda user: next(responses)})
    agent = ArchivistAgent(client)

    cleaned = await agent.clean(raw, repair_attempts=1)

    assert cleaned == "教学一\n教学二"
    assert len(client.calls) == 2
    assert "PREVIOUS RESPONSE WAS NOT VALID JSON" in client.calls[1][1]


async def test_archivist_clean_trims_repair_deleted_ranges_to_unlocked_segments():
    initial = """{
      "deleted_ranges": [
        {"start_line": 2, "end_line": 3, "reason": "noise"},
        {"start_line": 3, "end_line": 5, "reason": "overlap"}
      ]
    }"""
    repair = """{
      "deleted_ranges": [
        {"start_line": 3, "end_line": 5, "reason": "boundary_overlap_repair"}
      ]
    }"""
    client = _FakeClient(
        {"archivist": lambda user: repair if "REPAIR_ONLY_INVALID_DELETED_RANGES" in user else initial}
    )
    agent = ArchivistAgent(client)

    cleaned = await agent.clean("line 1\nline 2\nline 3\nline 4\nline 5\nline 6", repair_attempts=1)

    assert cleaned == "line 1\nline 6"


async def test_archivist_cut_mtus_repairs_invalid_metadata_block_only():
    invalid = """{
      "units": [
        {"start_line": 1, "end_line": 31, "title": "短",
         "defines": ["k"], "summary": "说明产生稳定干涉条纹所需满足的相干条件。", "unit_kind": "concept"}
      ]
    }"""
    repair = """{"title": "干涉条件"}"""

    def response(user):
        if "REPAIR_MTU_METADATA" in user:
            assert '"metadata_errors"' in user
            assert '"field": "title"' in user
            assert '"start_line": 1' in user
            assert '"end_line": 31' in user
            assert '"unit"' not in user
            return repair
        assert "PREVIOUS ATTEMPT WAS INVALID" not in user
        return invalid

    client = _FakeClient({"archivist": response})
    agent = ArchivistAgent(client)

    mtus = await agent.cut_mtus(_markdown_lines(31), collection="课件", source_file="ch1.md", repair_attempts=1)

    assert mtus[0].title == "干涉条件"
    assert mtus[0].defines == ["k"]
    assert len(client.calls) == 2


async def test_archivist_cut_mtus_repairs_coverage_before_metadata():
    invalid = """{
      "units": [
        {"start_line": 1, "end_line": 31, "title": "短",
         "defines": ["相干光"], "summary": "说明产生稳定干涉条纹所需满足的相干条件。",
         "unit_kind": "concept"},
        {"start_line": 33, "end_line": 63, "title": "衍射条件",
         "defines": ["衍射"], "summary": "说明衍射条纹形成条件及其基本教学边界。",
         "unit_kind": "concept"}
      ]
    }"""
    metadata_repair = """{"title": "干涉条件"}"""
    assignment = """{"mtu_title": "短"}"""
    seen = []

    def response(user):
        if "REPAIR_MTU_METADATA" in user:
            seen.append("metadata")
            assert seen == ["coverage", "metadata"]
            return metadata_repair
        if "ASSIGN_MTU_RANGE" in user:
            seen.append("coverage")
            assert '"start_line": 32' in user
            assert '"end_line": 32' in user
            return assignment
        return invalid

    client = _FakeClient({"archivist": response})
    agent = ArchivistAgent(client)

    mtus = await agent.cut_mtus(
        _markdown_lines(63),
        collection="课件",
        source_file="ch1.md",
        repair_attempts=1,
    )

    assert [mtu.line_range for mtu in mtus] == [(1, 32), (33, 63)]
    assert mtus[0].title == "干涉条件"
    assert seen == ["coverage", "metadata"]
    assert len(client.calls) == 3


async def test_archivist_cut_mtus_repairs_legacy_keywords_as_defines():
    invalid = """{
      "units": [
        {"start_line": 1, "end_line": 31, "title": "干涉条件",
         "keywords": ["相干光"], "summary": "说明产生稳定干涉条纹所需满足的相干条件。",
         "unit_kind": "concept"}
      ]
    }"""
    repair = """{"defines": ["相干光"]}"""

    def response(user):
        if "REPAIR_MTU_METADATA" in user:
            assert '"field": "defines"' in user
            assert "keywords is not allowed" in user
            assert '"unit"' not in user
            return repair
        return invalid

    client = _FakeClient({"archivist": response})
    agent = ArchivistAgent(client)

    mtus = await agent.cut_mtus(_markdown_lines(31), collection="课件", source_file="ch1.md", repair_attempts=1)

    assert mtus[0].defines == ["相干光"]
    assert len(client.calls) == 2


async def test_archivist_cut_mtus_repairs_summary_metadata_field_only():
    invalid = """{
      "units": [
        {"start_line": 1, "end_line": 31, "title": "干涉条件",
         "defines": ["相干光"], "summary": "太短", "unit_kind": "concept"}
      ]
    }"""
    repair = """{"summary": "说明产生稳定干涉条纹所需满足的相干条件。"}"""

    def response(user):
        if "REPAIR_MTU_METADATA" in user:
            assert '"field": "summary"' in user
            assert '"unit"' not in user
            return repair
        return invalid

    client = _FakeClient({"archivist": response})
    agent = ArchivistAgent(client)

    mtus = await agent.cut_mtus(_markdown_lines(31), collection="课件", source_file="ch1.md", repair_attempts=1)

    assert mtus[0].summary == "说明产生稳定干涉条纹所需满足的相干条件。"
    assert len(client.calls) == 2


async def test_archivist_cut_mtus_retries_malformed_initial_json():
    malformed = """{
      "units": [
        {"start_line": 1, "end_line": 2, "title": "干涉条件"
      ]
    }"""
    valid = """{
      "units": [
        {"start_line": 1, "end_line": 31, "title": "干涉条件",
         "defines": ["相干光"], "summary": "说明产生稳定干涉条纹所需满足的相干条件。",
         "unit_kind": "concept"}
      ]
    }"""

    def response(user):
        if "PREVIOUS RESPONSE WAS NOT VALID JSON" in user:
            return valid
        return malformed

    client = _FakeClient({"archivist": response})
    agent = ArchivistAgent(client)

    mtus = await agent.cut_mtus(_markdown_lines(31), collection="课件", source_file="ch1.md", repair_attempts=1)

    assert mtus[0].title == "干涉条件"
    assert len(client.calls) == 2


async def test_archivist_cut_mtus_rejects_skipped_ranges_field():
    invalid = """{
      "units": [
        {"start_line": 1, "end_line": 31, "title": "干涉条件",
         "defines": ["相干光"], "summary": "说明产生稳定干涉条纹所需满足的相干条件。",
         "unit_kind": "concept"}
      ],
      "skipped_ranges": []
    }"""
    valid = """{
      "units": [
        {"start_line": 1, "end_line": 31, "title": "衍射条件",
         "defines": ["衍射"], "summary": "说明衍射条纹形成条件及其基本教学边界。",
         "unit_kind": "concept"}
      ]
    }"""

    def response(user):
        if "PREVIOUS RESPONSE WAS NOT VALID JSON" in user:
            assert "skipped_ranges" in user
            return valid
        return invalid

    client = _FakeClient({"archivist": response})
    agent = ArchivistAgent(client)

    mtus = await agent.cut_mtus(_markdown_lines(31), collection="课件", source_file="ch1.md", repair_attempts=1)

    assert [mtu.title for mtu in mtus] == ["衍射条件"]
    assert len(client.calls) == 2


async def test_archivist_cut_mtus_assigns_missing_range_to_previous_mtu():
    incomplete = """{
      "units": [
        {"start_line": 1, "end_line": 31, "title": "干涉条件",
         "defines": ["相干光"], "summary": "说明产生稳定干涉条纹所需满足的相干条件。",
         "unit_kind": "concept"},
        {"start_line": 33, "end_line": 63, "title": "衍射条件",
         "defines": ["衍射"], "summary": "说明衍射条纹形成条件及其基本教学边界。",
         "unit_kind": "concept"}
      ]
    }"""
    decision = """{
      "mtu_title": "干涉条件"
    }"""

    def response(user):
        if "ASSIGN_MTU_RANGE" in user:
            assert '"problem_type": "missing_range"' in user
            assert '"start_line": 32' in user
            assert '"end_line": 32' in user
            assert "previous_mtu_metadata" in user
            assert "next_mtu_metadata" in user
            return decision
        return incomplete

    client = _FakeClient({"archivist": response})
    agent = ArchivistAgent(client)

    mtus = await agent.cut_mtus(_markdown_lines(63), collection="课件", source_file="ch1.md", repair_attempts=1)

    assert [mtu.line_range for mtu in mtus] == [(1, 32), (33, 63)]
    assert len(client.calls) == 2


async def test_archivist_cut_mtus_assigns_missing_range_to_next_mtu():
    incomplete = """{
      "units": [
        {"start_line": 1, "end_line": 31, "title": "干涉条件",
         "defines": ["相干光"], "summary": "说明产生稳定干涉条纹所需满足的相干条件。",
         "unit_kind": "concept"},
        {"start_line": 33, "end_line": 63, "title": "衍射条件",
         "defines": ["衍射"], "summary": "说明衍射条纹形成条件及其基本教学边界。",
         "unit_kind": "concept"}
      ]
    }"""
    decision = """{"mtu_title": "衍射条件"}"""

    def response(user):
        if "ASSIGN_MTU_RANGE" in user:
            assert '"start_line": 32' in user
            return decision
        return incomplete

    client = _FakeClient({"archivist": response})
    agent = ArchivistAgent(client)

    mtus = await agent.cut_mtus(_markdown_lines(63), collection="课件", source_file="ch1.md", repair_attempts=1)

    assert [mtu.line_range for mtu in mtus] == [(1, 31), (32, 63)]
    assert len(client.calls) == 2


async def test_archivist_cut_mtus_assigns_overlap_to_previous_mtu():
    plan = """{
      "units": [
        {"start_line": 1, "end_line": 32, "title": "干涉条件",
         "defines": ["相干光"], "summary": "说明产生稳定干涉条纹所需满足的相干条件。",
         "unit_kind": "concept"},
        {"start_line": 32, "end_line": 63, "title": "衍射条件",
         "defines": ["衍射"], "summary": "说明衍射条纹形成条件及其基本教学边界。",
         "unit_kind": "concept"}
      ]
    }"""

    def response(user):
        if "ASSIGN_MTU_RANGE" in user:
            assert '"problem_type": "overlap"' in user
            assert '"start_line": 32' in user
            assert '"end_line": 32' in user
            return """{"mtu_title": "干涉条件"}"""
        return plan

    client = _FakeClient({"archivist": response})
    agent = ArchivistAgent(client)

    mtus = await agent.cut_mtus(_markdown_lines(63), collection="课件", source_file="ch1.md", repair_attempts=1)

    assert [mtu.line_range for mtu in mtus] == [(1, 32), (33, 63)]
    assert len(client.calls) == 2


async def test_archivist_cut_mtus_assigns_overlap_to_next_mtu():
    plan = """{
      "units": [
        {"start_line": 1, "end_line": 32, "title": "干涉条件",
         "defines": ["相干光"], "summary": "说明产生稳定干涉条纹所需满足的相干条件。",
         "unit_kind": "concept"},
        {"start_line": 32, "end_line": 63, "title": "衍射条件",
         "defines": ["衍射"], "summary": "说明衍射条纹形成条件及其基本教学边界。",
         "unit_kind": "concept"}
      ]
    }"""

    def response(user):
        if "ASSIGN_MTU_RANGE" in user:
            return """{"mtu_title": "衍射条件"}"""
        return plan

    client = _FakeClient({"archivist": response})
    agent = ArchivistAgent(client)

    mtus = await agent.cut_mtus(_markdown_lines(63), collection="课件", source_file="ch1.md", repair_attempts=1)

    assert [mtu.line_range for mtu in mtus] == [(1, 31), (32, 63)]


async def test_archivist_cut_mtus_retries_unknown_assignment_title():
    plan = """{
      "units": [
        {"start_line": 1, "end_line": 31, "title": "干涉条件",
         "defines": ["相干光"], "summary": "说明产生稳定干涉条纹所需满足的相干条件。",
         "unit_kind": "concept"},
        {"start_line": 33, "end_line": 63, "title": "衍射条件",
         "defines": ["衍射"], "summary": "说明衍射条纹形成条件及其基本教学边界。",
         "unit_kind": "concept"}
      ]
    }"""
    calls = {"n": 0}

    def response(user):
        if "ASSIGN_MTU_RANGE" in user:
            calls["n"] += 1
            return """{"mtu_title": "不存在"}""" if calls["n"] == 1 else """{"mtu_title": "衍射条件"}"""
        return plan

    client = _FakeClient({"archivist": response})
    agent = ArchivistAgent(client)

    mtus = await agent.cut_mtus(_markdown_lines(63), collection="课件", source_file="ch1.md", repair_attempts=2)

    assert [mtu.line_range for mtu in mtus] == [(1, 31), (32, 63)]
    assert calls["n"] == 2


async def test_archivist_cut_mtus_sorts_repaired_units_by_source_line():
    incomplete = """{
      "units": [
        {"start_line": 3, "end_line": 33, "title": "衍射条件",
         "defines": ["衍射"], "summary": "说明衍射条纹形成条件及其基本教学边界。",
         "unit_kind": "concept"}
      ]
    }"""
    decision = """{
      "mtu_title": "衍射条件"
    }"""

    client = _FakeClient(
        {"archivist": lambda user: decision if "ASSIGN_MTU_RANGE" in user else incomplete}
    )
    agent = ArchivistAgent(client)

    mtus = await agent.cut_mtus(_markdown_lines(33), collection="课件", source_file="ch1.md", repair_attempts=1)

    assert [mtu.line_range for mtu in mtus] == [(1, 33)]


async def test_archivist_cut_mtus_sorts_units_by_source_line():
    unordered = """{
      "units": [
        {"start_line": 1, "end_line": 31, "title": "干涉条件",
         "defines": ["相干光"], "summary": "说明产生稳定干涉条纹所需满足的相干条件。",
         "unit_kind": "concept"},
        {"start_line": 32, "end_line": 62, "title": "衍射条件",
         "defines": ["衍射"], "summary": "说明衍射条纹形成条件及其基本教学边界。",
         "unit_kind": "concept"}
      ]
    }"""

    client = _FakeClient({"archivist": unordered})
    agent = ArchivistAgent(client)

    mtus = await agent.cut_mtus(_markdown_lines(62), collection="课件", source_file="ch1.md", repair_attempts=1)

    assert [mtu.line_range for mtu in mtus] == [(1, 31), (32, 62)]


async def test_archivist_cut_mtus_repairs_short_concept_with_local_units_window():
    short_plan = """{
      "units": [
        {"start_line": 1, "end_line": 19, "title": "干涉片段",
         "defines": ["相干光"], "summary": "说明相干光条件的局部教学片段。",
         "unit_kind": "concept"},
        {"start_line": 20, "end_line": 60, "title": "衍射条件",
         "defines": ["衍射"], "summary": "说明衍射条纹形成条件及其基本教学边界。",
         "unit_kind": "concept"}
      ]
    }"""
    repair = """{
      "units": [
        {"start_line": 1, "end_line": 60, "title": "衍射条件",
         "defines": ["衍射"], "summary": "合并短教学片段并说明衍射条件的完整边界。",
         "unit_kind": "concept"}
      ]
    }"""

    def response(user):
        if "REPAIR_MTU_UNITS" in user:
            assert '"problem_type": "short_unit"' in user
            assert '"window_range"' in user
            assert '"start_line": 1' in user
            assert '"end_line": 60' in user
            return repair
        return short_plan

    client = _FakeClient({"archivist": response})
    agent = ArchivistAgent(client)

    mtus = await agent.cut_mtus(_markdown_lines(60), collection="课件", source_file="ch1.md", repair_attempts=1)

    assert [mtu.line_range for mtu in mtus] == [(1, 60)]
    assert mtus[0].title == "衍射条件"
    assert len(client.calls) == 2


async def test_archivist_cut_mtus_fallback_merges_final_short_concept_to_previous():
    plan = """{
      "units": [
        {"start_line": 1, "end_line": 40, "title": "沉淀溶解平衡",
         "defines": ["沉淀溶解平衡"], "summary": "说明沉淀溶解平衡的计算方法与应用边界。",
         "unit_kind": "concept"},
        {"start_line": 41, "end_line": 52, "title": "沉淀转化",
         "defines": ["沉淀转化"], "summary": "说明沉淀转化的平衡常数关系。",
         "unit_kind": "concept"}
      ]
    }"""
    client = _FakeClient({"archivist": plan})
    agent = ArchivistAgent(client)

    mtus = await agent.cut_mtus(_markdown_lines(52), collection="课件", source_file="ch1.md", repair_attempts=0)

    assert [mtu.line_range for mtu in mtus] == [(1, 52)]
    assert mtus[0].title == "沉淀溶解平衡"
    assert mtus[0].defines == ["沉淀溶解平衡"]
    assert len(client.calls) == 1


async def test_archivist_cut_mtus_fallback_merges_initial_short_concept_to_next():
    plan = """{
      "units": [
        {"start_line": 1, "end_line": 12, "title": "导入片段",
         "defines": ["课程导入"], "summary": "说明本节的导入背景与问题设置。",
         "unit_kind": "concept"},
        {"start_line": 13, "end_line": 52, "title": "沉淀溶解平衡",
         "defines": ["沉淀溶解平衡"], "summary": "说明沉淀溶解平衡的计算方法与应用边界。",
         "unit_kind": "concept"}
      ]
    }"""
    client = _FakeClient({"archivist": plan})
    agent = ArchivistAgent(client)

    mtus = await agent.cut_mtus(_markdown_lines(52), collection="课件", source_file="ch1.md", repair_attempts=0)

    assert [mtu.line_range for mtu in mtus] == [(1, 52)]
    assert mtus[0].title == "沉淀溶解平衡"
    assert mtus[0].defines == ["沉淀溶解平衡"]
    assert len(client.calls) == 1


async def test_archivist_cut_mtus_fallback_merges_middle_short_concept_without_gap():
    plan = """{
      "units": [
        {"start_line": 1, "end_line": 30, "title": "溶度积",
         "defines": ["溶度积"], "summary": "说明溶度积常数的定义与计算边界。",
         "unit_kind": "concept"},
        {"start_line": 31, "end_line": 42, "title": "沉淀转化",
         "defines": ["沉淀转化"], "summary": "说明沉淀转化的平衡常数关系。",
         "unit_kind": "concept"},
        {"start_line": 43, "end_line": 82, "title": "分步沉淀",
         "defines": ["分步沉淀"], "summary": "说明分步沉淀的判定方法与计算边界。",
         "unit_kind": "concept"}
      ]
    }"""
    client = _FakeClient({"archivist": plan})
    agent = ArchivistAgent(client)

    mtus = await agent.cut_mtus(_markdown_lines(82), collection="课件", source_file="ch1.md", repair_attempts=0)

    assert [mtu.line_range for mtu in mtus] == [(1, 42), (43, 82)]
    assert mtus[0].title == "溶度积"
    assert mtus[1].title == "分步沉淀"
    assert len(client.calls) == 1


async def test_archivist_cut_mtus_fallback_merges_mixed_short_and_example_empty_defines():
    plan = """{
      "units": [
        {"start_line": 1, "end_line": 40, "title": "多元复合函数求导",
         "defines": ["多元复合函数求导法则"], "summary": "说明多元复合函数求导的链式法则与基本计算边界。",
         "unit_kind": "concept"},
        {"start_line": 41, "end_line": 64, "title": "向量值函数求导例题",
         "defines": [], "summary": "通过例题展示向量值函数求导步骤，不单独引入新的定义公式或方法。",
         "unit_kind": "concept"},
        {"start_line": 65, "end_line": 83, "title": "利用一阶微分形式不变性求导",
         "defines": ["一阶微分形式不变性求导"], "summary": "说明一阶微分形式不变性求导的局部应用片段。",
         "unit_kind": "concept"},
        {"start_line": 84, "end_line": 120, "title": "高阶偏导计算",
         "defines": ["高阶偏导计算"], "summary": "说明高阶偏导计算的顺序规则与典型边界。",
         "unit_kind": "concept"}
      ]
    }"""
    client = _FakeClient({"archivist": plan})
    agent = ArchivistAgent(client)

    mtus = await agent.cut_mtus(_markdown_lines(120), collection="课件", source_file="ch1.md", repair_attempts=0)

    assert [mtu.line_range for mtu in mtus] == [(1, 83), (84, 120)]
    assert mtus[0].title == "多元复合函数求导"
    assert mtus[0].defines == ["多元复合函数求导法则"]
    assert mtus[1].title == "高阶偏导计算"
    assert len(client.calls) == 1


async def test_archivist_cut_mtus_fallback_keeps_regular_empty_defines_invalid():
    plan = """{
      "units": [
        {"start_line": 1, "end_line": 30, "title": "连续函数定义",
         "defines": ["连续函数"], "summary": "说明连续函数的定义与局部性质。",
         "unit_kind": "concept"},
        {"start_line": 31, "end_line": 60, "title": "偏导数定义",
         "defines": [], "summary": "说明偏导数定义的基本内容，但缺少有效定义元数据。",
         "unit_kind": "concept"},
        {"start_line": 61, "end_line": 90, "title": "全微分定义",
         "defines": ["全微分"], "summary": "说明全微分的定义与计算边界。",
         "unit_kind": "concept"}
      ]
    }"""
    client = _FakeClient({"archivist": plan})
    agent = ArchivistAgent(client)

    with pytest.raises(MtuCoverageError, match="empty_defines"):
        await agent.cut_mtus(_markdown_lines(90), collection="课件", source_file="ch1.md", repair_attempts=0)

    assert len(client.calls) == 1


async def test_archivist_cut_mtus_repairs_short_units_before_metadata():
    plan = """{
      "units": [
        {"start_line": 1, "end_line": 19, "title": "短片段",
         "defines": ["相干光"], "summary": "说明相干光条件的局部教学片段。",
         "unit_kind": "concept"},
        {"start_line": 20, "end_line": 60, "title": "短",
         "defines": ["衍射"], "summary": "说明衍射条纹形成条件及其基本教学边界。",
         "unit_kind": "concept"}
      ]
    }"""
    repair = """{
      "units": [
        {"start_line": 1, "end_line": 60, "title": "衍射条件",
         "defines": ["衍射"], "summary": "合并短教学片段并说明衍射条件的完整边界。",
         "unit_kind": "concept"}
      ]
    }"""
    seen = []

    def response(user):
        if "REPAIR_MTU_METADATA" in user:
            raise AssertionError("metadata repair must not run before short-unit repair")
        if "REPAIR_MTU_UNITS" in user:
            seen.append("short")
            return repair
        return plan

    client = _FakeClient({"archivist": response})
    agent = ArchivistAgent(client)

    mtus = await agent.cut_mtus(_markdown_lines(60), collection="课件", source_file="ch1.md", repair_attempts=1)

    assert [mtu.line_range for mtu in mtus] == [(1, 60)]
    assert seen == ["short"]
    assert len(client.calls) == 2


async def test_archivist_cut_mtus_repairs_empty_defines_with_local_units_window():
    empty_defines_plan = """{
      "units": [
        {"start_line": 1, "end_line": 31, "title": "干涉条件",
         "defines": [], "summary": "说明产生稳定干涉条纹所需满足的相干条件。",
         "unit_kind": "concept"}
      ]
    }"""
    repair = """{
      "units": [
        {"start_line": 1, "end_line": 31, "title": "干涉条件",
         "defines": ["相干光"], "summary": "说明产生稳定干涉条纹所需满足的相干条件。",
         "unit_kind": "concept"}
      ]
    }"""

    def response(user):
        if "REPAIR_MTU_UNITS" in user:
            assert '"problem_type": "empty_defines"' in user
            assert '"window_range"' in user
            return repair
        return empty_defines_plan

    client = _FakeClient({"archivist": response})
    agent = ArchivistAgent(client)

    mtus = await agent.cut_mtus(_markdown_lines(31), collection="课件", source_file="ch1.md", repair_attempts=1)

    assert mtus[0].defines == ["相干光"]
    assert len(client.calls) == 2


async def test_archivist_cut_mtus_repairs_adjacent_short_units_across_iterations():
    short_plan = """{
      "units": [
        {"start_line": 1, "end_line": 40, "title": "阻尼振动",
         "defines": ["阻尼振动"], "summary": "说明阻尼振动的模型与基本衰减特征。",
         "unit_kind": "concept"},
        {"start_line": 41, "end_line": 59, "title": "李萨如图形",
         "defines": ["李萨如图形"], "summary": "说明李萨如图形表示合振动的方法。",
         "unit_kind": "concept"},
        {"start_line": 60, "end_line": 78, "title": "振动频谱",
         "defines": ["振动频谱"], "summary": "说明振动分解与频谱表达的教学边界。",
         "unit_kind": "concept"},
        {"start_line": 79, "end_line": 130, "title": "受迫振动",
         "defines": ["受迫振动"], "summary": "说明受迫振动的响应模型与共振条件。",
         "unit_kind": "concept"}
      ]
    }"""
    first_repair = """{
      "units": [
        {"start_line": 1, "end_line": 59, "title": "阻尼振动",
         "defines": ["阻尼振动", "李萨如图形"], "summary": "合并李萨如图形内容并说明阻尼振动的完整边界。",
         "unit_kind": "concept"},
        {"start_line": 60, "end_line": 78, "title": "振动频谱",
         "defines": ["振动频谱"], "summary": "说明振动分解与频谱表达的教学边界。",
         "unit_kind": "concept"}
      ]
    }"""
    second_repair = """{
      "units": [
        {"start_line": 1, "end_line": 59, "title": "阻尼振动",
         "defines": ["阻尼振动", "李萨如图形"], "summary": "合并李萨如图形内容并说明阻尼振动的完整边界。",
         "unit_kind": "concept"},
        {"start_line": 60, "end_line": 130, "title": "受迫振动",
         "defines": ["受迫振动", "振动频谱"], "summary": "合并振动频谱内容并说明受迫振动的完整边界。",
         "unit_kind": "concept"}
      ]
    }"""
    repairs = [first_repair, second_repair]

    def response(user):
        if "REPAIR_MTU_UNITS" in user:
            return repairs.pop(0)
        return short_plan

    client = _FakeClient({"archivist": response})
    agent = ArchivistAgent(client)

    mtus = await agent.cut_mtus(_markdown_lines(130), collection="课件", source_file="ch1.md", repair_attempts=2)

    assert [mtu.line_range for mtu in mtus] == [(1, 59), (60, 130)]
    assert len(client.calls) == 3


async def test_archivist_cut_mtus_repairs_duplicate_defines_with_same_schema():
    duplicate_defines_plan = """{
      "units": [
        {"start_line": 1, "end_line": 31, "title": "相干光条件",
         "defines": ["相干光"], "summary": "说明产生稳定干涉条纹所需满足的相干条件。",
         "unit_kind": "concept"},
        {"start_line": 32, "end_line": 62, "title": "光程差分析",
         "defines": ["相 干 光"], "summary": "说明光程差分析方法及其适用的干涉条件。",
         "unit_kind": "concept"}
      ]
    }"""
    repair = """{
      "units": [
        {"start_line": 1, "end_line": 31, "defines": ["相干光"]},
        {"start_line": 32, "end_line": 62, "defines": ["光程差"]}
      ]
    }"""

    def response(user):
        if "REPAIR_MTU_DUPLICATE_DEFINES" in user:
            assert '"problem_type": "duplicate_defines"' in user
            assert '"duplicate_units_metadata"' in user
            assert '"duplicate_unit_excerpts"' in user
            assert "only `start_line`, `end_line`, and `defines`" in user
            return repair
        return duplicate_defines_plan

    client = _FakeClient({"archivist": response})
    agent = ArchivistAgent(client)

    mtus = await agent.cut_mtus(_markdown_lines(62), collection="课件", source_file="ch1.md", repair_attempts=1)

    assert [mtu.defines for mtu in mtus] == [["相干光"], ["光程差"]]
    assert len(client.calls) == 2


async def test_archivist_cut_mtus_rejects_duplicate_define_repair_that_changes_ranges():
    duplicate_defines_plan = """{
      "units": [
        {"start_line": 1, "end_line": 31, "title": "相干光条件",
         "defines": ["相干光"], "summary": "说明产生稳定干涉条纹所需满足的相干条件。",
         "unit_kind": "concept"},
        {"start_line": 32, "end_line": 62, "title": "光程差分析",
         "defines": ["相干光"], "summary": "说明光程差分析方法及其适用的干涉条件。",
         "unit_kind": "concept"}
      ]
    }"""
    bad_repair = """{
      "units": [
        {"start_line": 1, "end_line": 62, "defines": ["相干光"]}
      ]
    }"""

    def response(user):
        if "REPAIR_MTU_DUPLICATE_DEFINES" in user:
            return bad_repair
        return duplicate_defines_plan

    agent = ArchivistAgent(_FakeClient({"archivist": response}))

    import pytest
    from tree.planner.mtu import MtuCoverageError

    with pytest.raises(MtuCoverageError, match="duplicate_defines"):
        await agent.cut_mtus(_markdown_lines(62), collection="课件", source_file="ch1.md", repair_attempts=1)


async def test_archivist_cut_mtus_rejects_duplicate_define_repair_with_extra_metadata():
    duplicate_defines_plan = """{
      "units": [
        {"start_line": 1, "end_line": 31, "title": "相干光条件",
         "defines": ["相干光"], "summary": "说明产生稳定干涉条纹所需满足的相干条件。",
         "unit_kind": "concept"},
        {"start_line": 32, "end_line": 62, "title": "光程差分析",
         "defines": ["相干光"], "summary": "说明光程差分析方法及其适用的干涉条件。",
         "unit_kind": "concept"}
      ]
    }"""
    bad_repair = """{
      "units": [
        {"start_line": 1, "end_line": 31, "title": "相干光条件", "defines": ["相干光"]},
        {"start_line": 32, "end_line": 62, "defines": ["光程差"]}
      ]
    }"""

    def response(user):
        if "REPAIR_MTU_DUPLICATE_DEFINES" in user:
            return bad_repair
        return duplicate_defines_plan

    agent = ArchivistAgent(_FakeClient({"archivist": response}))

    import pytest
    from tree.planner.mtu import MtuCoverageError

    with pytest.raises(MtuCoverageError, match="duplicate_defines"):
        await agent.cut_mtus(_markdown_lines(62), collection="课件", source_file="ch1.md", repair_attempts=1)


async def test_archivist_cut_mtus_allows_same_define_across_separate_calls():
    valid = """{
      "units": [
        {"start_line": 1, "end_line": 31, "title": "相干光条件",
         "defines": ["相干光"], "summary": "说明产生稳定干涉条纹所需满足的相干条件。",
         "unit_kind": "concept"}
      ]
    }"""

    def response(user):
        assert "REPAIR_MTU_DUPLICATE_DEFINES" not in user
        return valid

    client = _FakeClient({"archivist": response})
    agent = ArchivistAgent(client)

    first = await agent.cut_mtus(_markdown_lines(31), collection="课件", source_file="ch1.md", repair_attempts=1)
    second = await agent.cut_mtus(_markdown_lines(31), collection="课件", source_file="ch2.md", repair_attempts=1)

    assert first[0].defines == ["相干光"]
    assert second[0].defines == ["相干光"]
    assert len(client.calls) == 2


async def test_archivist_cut_mtus_includes_dynamic_line_count_in_prompt():
    valid = """{
      "units": [
        {"start_line": 1, "end_line": 31, "title": "干涉条件",
         "defines": ["相干光"], "summary": "说明产生稳定干涉条纹所需满足的相干条件。",
         "unit_kind": "concept"}
      ]
    }"""

    client = _FakeClient({"archivist": valid})
    agent = ArchivistAgent(client)

    await agent.cut_mtus(_markdown_lines(31), collection="课件", source_file="ch1.md", repair_attempts=0)

    user_prompt = client.calls[0][1]
    assert "TOTAL_LINES: 31" in user_prompt
    assert "LAST_VALID_LINE: 31" in user_prompt
    assert "Do not output start_line or end_line greater than 31." in user_prompt
    assert "1\tline 1" in user_prompt


def test_archivist_mtu_prompt_emphasizes_strict_metadata_and_coverage():
    assert "Before returning JSON, audit the full line map" in ARCHIVIST_MTU_PROMPT
    assert "No gaps are allowed" in ARCHIVIST_MTU_PROMPT
    assert "No overlaps are allowed" in ARCHIVIST_MTU_PROMPT
    assert "smallest complete teaching module" in ARCHIVIST_MTU_PROMPT
    assert "60-180 source lines" in ARCHIVIST_MTU_PROMPT
    assert "at least 20 lines" in ARCHIVIST_MTU_PROMPT
    assert "must never have empty `defines`" in ARCHIVIST_MTU_PROMPT
    assert "do not put \"例题\"" in ARCHIVIST_MTU_PROMPT
    assert "Defines are graph anchors for later prerequisite edges" in ARCHIVIST_MTU_PROMPT
    assert "example/exercise/application/case fragments" in ARCHIVIST_MTU_PROMPT
    assert "Program code will merge or remove auxiliary units later" in ARCHIVIST_MTU_PROMPT
    assert "The owning concept may appear before or after the example" in ARCHIVIST_MTU_PROMPT
    assert 'broad reusable base terms such as "频率", "偏振", "光程", or "波长"' in ARCHIVIST_MTU_PROMPT
    assert "几何光学的反射定律" in ARCHIVIST_MTU_PROMPT
    assert "机械波固定端的半波损失" in ARCHIVIST_MTU_PROMPT
    assert "宏观经济学的乘数效应" in ARCHIVIST_MTU_PROMPT
    assert "细胞生物学的主动运输" in ARCHIVIST_MTU_PROMPT
    assert "defines length: 1-4 items for `concept` units" in ARCHIVIST_MTU_PROMPT
    assert "no two `concept` units may use the same normalized define" in ARCHIVIST_MTU_PROMPT
    assert "REPAIR_MTU_UNITS" in ARCHIVIST_MTU_PROMPT
    assert "REPAIR_MTU_DUPLICATE_DEFINES" in ARCHIVIST_MTU_PROMPT
    assert '"defines":' in ARCHIVIST_MTU_PROMPT
    assert '"keywords":' not in ARCHIVIST_MTU_PROMPT
    assert "title display width: 4-40" in ARCHIVIST_MTU_PROMPT
    assert "summary display width: 20-150" in ARCHIVIST_MTU_PROMPT
    assert "`review`" in ARCHIVIST_MTU_PROMPT
    assert "`summary`" in ARCHIVIST_MTU_PROMPT
    assert "`intro`" in ARCHIVIST_MTU_PROMPT
    assert "`excercise`" in ARCHIVIST_MTU_PROMPT
    assert "`example`" not in ARCHIVIST_MTU_PROMPT
    assert "`exercise`" not in ARCHIVIST_MTU_PROMPT
    assert "`misconception`" not in ARCHIVIST_MTU_PROMPT
    assert "`procedure`" not in ARCHIVIST_MTU_PROMPT
    assert "skipped_ranges" not in ARCHIVIST_MTU_PROMPT


async def test_archivist_cut_mtus_raises_when_repairs_exhausted():
    invalid = """{
      "units": [
        {"start_line": 1, "end_line": 31, "title": "A",
         "defines": ["k"], "summary": "太短", "unit_kind": "concept"}
      ]
    }"""

    agent = ArchivistAgent(_FakeClient({"archivist": invalid}))

    import pytest
    from tree.planner.mtu import MtuCoverageError

    with pytest.raises(MtuCoverageError, match="unit 1 must be an object|title"):
        await agent.cut_mtus(_markdown_lines(31), collection="课件", source_file="ch1.md", repair_attempts=1)


async def test_writer_draft_returns_content():
    agent = WriterAgent(_FakeClient({"writer": "# 化学平衡\n内容"}))
    result = await agent.draft(
        span_title="化学平衡", file_seq="01", bottleneck_report="# Bottleneck Report\n缺公式",
        prior_paths=[], prior_contents=[],
    )
    assert result.draft_content.startswith("# 化学平衡")


async def test_writer_revise_from_feedback_uses_feedback_as_optimize_context():
    client = _FakeClient({"writer": "# 化学平衡\n修订内容"})
    agent = WriterAgent(client)

    result = await agent.revise_from_feedback(
        span_title="化学平衡",
        file_seq="001",
        current_text="# 化学平衡\n旧内容",
        user_feedback="这里没有解释平衡移动",
        prior_paths=[],
        prior_contents=[],
        node_context="TARGET n1",
    )

    assert result.draft_content.startswith("# 化学平衡")
    role, prompt = client.calls[-1]
    assert role == "writer"
    assert "User feedback for this generated learning node" in prompt
    assert "这里没有解释平衡移动" in prompt
    assert "Current draft (OPTIMIZE this)" in prompt
    assert "TARGET n1" in prompt


def test_sanitize_writer_context_redacts_exam_blocks():
    text = "# 教学内容\n正文\n## Answer_Key\nK=...\n答案细节\n# 下一节\n继续"
    cleaned = sanitize_writer_context(text)
    assert "K=..." not in cleaned
    assert "REDACTED" in cleaned
    assert "继续" in cleaned
