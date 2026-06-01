"""Tests for examiner/student/writer agents with a fake LLM client (step 7)."""

from __future__ import annotations

from tree.agents.examiner import ExaminerAgent
from tree.agents.student import StudentAgent
from tree.agents.writer import WriterAgent, sanitize_writer_context
from tree.state.models import Route

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


async def test_student_answer_returns_text():
    agent = StudentAgent(_FakeClient({"student": "学生作答内容"}))
    out = await agent.answer(blind_exam="Q", prior_paths=[], prior_contents=[])
    assert out == "学生作答内容"


async def test_writer_draft_returns_content():
    agent = WriterAgent(_FakeClient({"writer": "# 化学平衡\n内容"}))
    result = await agent.draft(
        span_title="化学平衡", file_seq="01", bottleneck_report="# Bottleneck Report\n缺公式",
        prior_paths=[], prior_contents=[],
    )
    assert result.draft_content.startswith("# 化学平衡")


def test_sanitize_writer_context_redacts_exam_blocks():
    text = "# 教学内容\n正文\n## Answer_Key\nK=...\n答案细节\n# 下一节\n继续"
    cleaned = sanitize_writer_context(text)
    assert "K=..." not in cleaned
    assert "REDACTED" in cleaned
    assert "继续" in cleaned
