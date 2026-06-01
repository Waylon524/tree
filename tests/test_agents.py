"""Tests for examiner/student/writer agents with a fake LLM client (step 7)."""

from __future__ import annotations

from tree.agents.archivist import ArchivistAgent
from tree.agents.examiner import ExaminerAgent
from tree.agents.prompts import ARCHIVIST_MTU_PROMPT
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


async def test_archivist_cut_mtus_retries_invalid_metadata_json():
    invalid = """{
      "units": [
        {"start_line": 1, "end_line": 2, "title": "短",
         "keywords": ["k"], "summary": "太短", "unit_kind": "concept"}
      ],
      "skipped_ranges": []
    }"""
    valid = """{
      "units": [
        {"start_line": 1, "end_line": 2, "title": "干涉条件",
         "keywords": ["相干光", "光程差"], "summary": "说明产生稳定干涉条纹所需满足的相干条件。",
         "unit_kind": "concept"}
      ],
      "skipped_ranges": []
    }"""

    client = _FakeClient({"archivist": lambda user: valid if "PREVIOUS ATTEMPT WAS INVALID" in user else invalid})
    agent = ArchivistAgent(client)

    mtus = await agent.cut_mtus("line 1\nline 2", collection="课件", source_file="ch1.md", repair_attempts=1)

    assert mtus[0].title == "干涉条件"
    assert len(client.calls) == 2


async def test_archivist_cut_mtus_retries_malformed_initial_json():
    malformed = """{
      "units": [
        {"start_line": 1, "end_line": 2, "title": "干涉条件"
      ],
      "skipped_ranges": []
    }"""
    valid = """{
      "units": [
        {"start_line": 1, "end_line": 2, "title": "干涉条件",
         "keywords": ["相干光"], "summary": "说明产生稳定干涉条纹所需满足的相干条件。",
         "unit_kind": "concept"}
      ],
      "skipped_ranges": []
    }"""

    def response(user):
        if "PREVIOUS RESPONSE WAS NOT VALID JSON" in user:
            return valid
        return malformed

    client = _FakeClient({"archivist": response})
    agent = ArchivistAgent(client)

    mtus = await agent.cut_mtus("line 1\nline 2", collection="课件", source_file="ch1.md", repair_attempts=1)

    assert mtus[0].title == "干涉条件"
    assert len(client.calls) == 2


async def test_archivist_cut_mtus_repairs_only_invalid_unit_metadata():
    invalid = """{
      "units": [
        {"start_line": 1, "end_line": 2, "title": "干涉条件",
         "keywords": ["相干光"], "summary": "说明产生稳定干涉条纹所需满足的相干条件。",
         "unit_kind": "concept"},
        {"start_line": 3, "end_line": 4, "title": "短",
         "keywords": ["k"], "summary": "太短", "unit_kind": "concept"}
      ],
      "skipped_ranges": []
    }"""
    repair = """{
      "units": [
        {"start_line": 3, "end_line": 4, "title": "衍射条件",
         "keywords": ["衍射"], "summary": "说明衍射条纹形成条件及其基本教学边界。",
         "unit_kind": "concept"}
      ],
      "skipped_ranges": []
    }"""

    def response(user):
        if "REPAIR_ONLY_INVALID_MTU_BLOCKS" in user:
            assert "VALID_UNITS_LOCKED" in user
            assert '"title": "干涉条件"' in user
            assert "INVALID_UNITS" in user
            assert '"title": "短"' in user
            return repair
        return invalid

    client = _FakeClient({"archivist": response})
    agent = ArchivistAgent(client)

    mtus = await agent.cut_mtus("line 1\nline 2\nline 3\nline 4", collection="课件", source_file="ch1.md", repair_attempts=1)

    assert [mtu.title for mtu in mtus] == ["干涉条件", "衍射条件"]
    assert len(client.calls) == 2


async def test_archivist_cut_mtus_repairs_only_missing_ranges():
    incomplete = """{
      "units": [
        {"start_line": 1, "end_line": 2, "title": "干涉条件",
         "keywords": ["相干光"], "summary": "说明产生稳定干涉条纹所需满足的相干条件。",
         "unit_kind": "concept"}
      ],
      "skipped_ranges": []
    }"""
    repair = """{
      "units": [
        {"start_line": 3, "end_line": 4, "title": "衍射条件",
         "keywords": ["衍射"], "summary": "说明衍射条纹形成条件及其基本教学边界。",
         "unit_kind": "concept"}
      ],
      "skipped_ranges": []
    }"""

    def response(user):
        if "REPAIR_ONLY_INVALID_MTU_BLOCKS" in user:
            assert "MISSING_RANGES" in user
            assert '"start_line": 3' in user
            assert '"end_line": 4' in user
            assert '"title": "干涉条件"' in user
            return repair
        return incomplete

    client = _FakeClient({"archivist": response})
    agent = ArchivistAgent(client)

    mtus = await agent.cut_mtus("line 1\nline 2\nline 3\nline 4", collection="课件", source_file="ch1.md", repair_attempts=1)

    assert [mtu.line_range for mtu in mtus] == [(1, 2), (3, 4)]
    assert len(client.calls) == 2


async def test_archivist_cut_mtus_ignores_redundant_overlapping_skipped_ranges():
    plan = """{
      "units": [
        {"start_line": 1, "end_line": 2, "title": "干涉条件",
         "keywords": ["相干光"], "summary": "说明产生稳定干涉条纹所需满足的相干条件。",
         "unit_kind": "concept"}
      ],
      "skipped_ranges": [
        {"start_line": 2, "end_line": 2, "reason": "redundant_noise"}
      ]
    }"""

    client = _FakeClient({"archivist": plan})
    agent = ArchivistAgent(client)

    mtus = await agent.cut_mtus("line 1\nline 2", collection="课件", source_file="ch1.md", repair_attempts=0)

    assert [mtu.line_range for mtu in mtus] == [(1, 2)]
    assert len(client.calls) == 1


async def test_archivist_cut_mtus_trims_overlapping_repair_units_to_missing_segments():
    incomplete = """{
      "units": [
        {"start_line": 3, "end_line": 3, "title": "中心条件",
         "keywords": ["条件"], "summary": "说明中心条件在本段中的作用边界。",
         "unit_kind": "concept"}
      ],
      "skipped_ranges": []
    }"""
    broad_repair = """{
      "units": [
        {"start_line": 1, "end_line": 5, "title": "干涉条件",
         "keywords": ["相干光"], "summary": "说明产生稳定干涉条纹所需满足的相干条件。",
         "unit_kind": "concept"}
      ],
      "skipped_ranges": []
    }"""

    client = _FakeClient(
        {"archivist": lambda user: broad_repair if "REPAIR_ONLY_INVALID_MTU_BLOCKS" in user else incomplete}
    )
    agent = ArchivistAgent(client)

    mtus = await agent.cut_mtus(
        "line 1\nline 2\nline 3\nline 4\nline 5",
        collection="课件",
        source_file="ch1.md",
        repair_attempts=1,
    )

    assert [mtu.line_range for mtu in mtus] == [(1, 2), (3, 3), (4, 5)]


async def test_archivist_cut_mtus_drops_fully_redundant_overlapping_repair_units():
    incomplete = """{
      "units": [
        {"start_line": 1, "end_line": 2, "title": "干涉条件",
         "keywords": ["相干光"], "summary": "说明产生稳定干涉条纹所需满足的相干条件。",
         "unit_kind": "concept"}
      ],
      "skipped_ranges": []
    }"""
    redundant_repair = """{
      "units": [
        {"start_line": 2, "end_line": 3, "title": "重复条件",
         "keywords": ["重复"], "summary": "重复覆盖已经锁定的教学行，应被忽略。",
         "unit_kind": "concept"},
        {"start_line": 3, "end_line": 3, "title": "补充条件",
         "keywords": ["补充"], "summary": "补齐第三行遗漏的教学内容边界。",
         "unit_kind": "concept"}
      ],
      "skipped_ranges": []
    }"""

    client = _FakeClient(
        {"archivist": lambda user: redundant_repair if "REPAIR_ONLY_INVALID_MTU_BLOCKS" in user else incomplete}
    )
    agent = ArchivistAgent(client)

    mtus = await agent.cut_mtus("line 1\nline 2\nline 3", collection="课件", source_file="ch1.md", repair_attempts=1)

    assert [mtu.line_range for mtu in mtus] == [(1, 2), (3, 3)]


async def test_archivist_cut_mtus_sorts_repaired_units_by_source_line():
    incomplete = """{
      "units": [
        {"start_line": 3, "end_line": 4, "title": "衍射条件",
         "keywords": ["衍射"], "summary": "说明衍射条纹形成条件及其基本教学边界。",
         "unit_kind": "concept"}
      ],
      "skipped_ranges": []
    }"""
    repair = """{
      "units": [
        {"start_line": 1, "end_line": 2, "title": "干涉条件",
         "keywords": ["相干光"], "summary": "说明产生稳定干涉条纹所需满足的相干条件。",
         "unit_kind": "concept"}
      ],
      "skipped_ranges": []
    }"""

    client = _FakeClient(
        {"archivist": lambda user: repair if "REPAIR_ONLY_INVALID_MTU_BLOCKS" in user else incomplete}
    )
    agent = ArchivistAgent(client)

    mtus = await agent.cut_mtus("line 1\nline 2\nline 3\nline 4", collection="课件", source_file="ch1.md", repair_attempts=1)

    assert [mtu.line_range for mtu in mtus] == [(1, 2), (3, 4)]


async def test_archivist_cut_mtus_includes_dynamic_line_count_in_prompt():
    valid = """{
      "units": [
        {"start_line": 1, "end_line": 3, "title": "干涉条件",
         "keywords": ["相干光"], "summary": "说明产生稳定干涉条纹所需满足的相干条件。",
         "unit_kind": "concept"}
      ],
      "skipped_ranges": []
    }"""

    client = _FakeClient({"archivist": valid})
    agent = ArchivistAgent(client)

    await agent.cut_mtus("line 1\nline 2\nline 3", collection="课件", source_file="ch1.md", repair_attempts=0)

    user_prompt = client.calls[0][1]
    assert "TOTAL_LINES: 3" in user_prompt
    assert "LAST_VALID_LINE: 3" in user_prompt
    assert "Do not output start_line or end_line greater than 3." in user_prompt
    assert "1\tline 1" in user_prompt


def test_archivist_mtu_prompt_emphasizes_strict_metadata_and_coverage():
    assert "Before returning JSON, audit the full line map" in ARCHIVIST_MTU_PROMPT
    assert "No gaps are allowed" in ARCHIVIST_MTU_PROMPT
    assert "No overlaps are allowed" in ARCHIVIST_MTU_PROMPT
    assert "keywords length: 1-10 items" in ARCHIVIST_MTU_PROMPT
    assert "title display width: 6-40" in ARCHIVIST_MTU_PROMPT
    assert "summary display width: 20-150" in ARCHIVIST_MTU_PROMPT


async def test_archivist_cut_mtus_raises_when_repairs_exhausted():
    invalid = """{
      "units": [
        {"start_line": 1, "end_line": 2, "title": "短",
         "keywords": ["k"], "summary": "太短", "unit_kind": "concept"}
      ],
      "skipped_ranges": []
    }"""

    agent = ArchivistAgent(_FakeClient({"archivist": invalid}))

    import pytest
    from tree.planner.mtu import MtuCoverageError

    with pytest.raises(MtuCoverageError, match="title"):
        await agent.cut_mtus("line 1\nline 2", collection="课件", source_file="ch1.md", repair_attempts=1)


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
