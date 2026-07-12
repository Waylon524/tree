"""Tests for the NodeRunner Examiner -> Student -> Writer loop."""

from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace

import pytest

from tree.engine.node_run import NodeRunner, ledger_covered_node_ids, reconcile_ledger_generation
from tree.io import paths
from tree.observability.limiter import IterationLimitExceeded
from tree.planner.store import envelope, write_json_atomic
from tree.state.manager import StateManager
from tree.state.models import (
    AuditResult,
    AuditExamDefectKind,
    CoverageSnapshot,
    ExamReconciliationAction,
    ExamReconciliationResult,
    ExamSections,
    NodeExecutionRecord,
    NodeRunRecord,
    PipelineState,
    Route,
    WriterResult,
)


class _FakeExaminer:
    def __init__(self, fail_then_pass=1):
        self.audit_calls = 0
        self.fail_count = fail_then_pass
        self.compose_kwargs = []

    async def compose(self, **kw):
        self.compose_kwargs.append(kw)
        return ExamSections(
            knowledge_point="01. 化学平衡",
            covered_node_ids=["n1", "n2"],
            blind_exam="Q",
            answer_key="A",
            writer_instructions="Scope: x",
        )

    async def audit(self, **kw):
        self.audit_calls += 1
        route = Route.FAIL_KNOWLEDGE_GAP if self.audit_calls <= self.fail_count else Route.PASS
        return AuditResult(route=route, exam_id="化学平衡", bottleneck_report="# Bottleneck Report\n缺公式")


class _ReconcilingExaminer(_FakeExaminer):
    def __init__(self, *, action=ExamReconciliationAction.REVISE_EXAM):
        super().__init__(fail_then_pass=0)
        self.action = action
        self.reconcile_calls = 0

    async def reconcile_exam(self, **kw):
        self.reconcile_calls += 1
        if self.action is ExamReconciliationAction.KEEP_FAIL:
            return ExamReconciliationResult(
                action=ExamReconciliationAction.KEEP_FAIL,
                reason="draft is still missing a method",
            )
        return ExamReconciliationResult(
            action=ExamReconciliationAction.REVISE_EXAM,
            reason="answer key contradicted the draft formula",
            exam_sections=ExamSections(
                knowledge_point="化学平衡",
                covered_node_ids=["n1"],
                blind_exam="Revised Q",
                answer_key="Revised A",
                writer_instructions="Scope: revised",
            ),
        )


class _AuditDefectExaminer(_ReconcilingExaminer):
    def __init__(self, defect_kind: AuditExamDefectKind):
        super().__init__(action=ExamReconciliationAction.REVISE_EXAM)
        self.defect_kind = defect_kind

    async def audit(self, **kw):
        self.audit_calls += 1
        if self.audit_calls == 1:
            return AuditResult(
                route=Route.FAIL_KNOWLEDGE_GAP,
                exam_id="化学平衡",
                bottleneck_report="# Bottleneck Report\nStandard answer is defective.",
                exam_defect_kind=self.defect_kind,
            )
        return AuditResult(route=Route.PASS, exam_id="化学平衡", bottleneck_report="# Bottleneck Report\nok")


class _FakeStudent:
    def __init__(self):
        self.calls = []

    async def answer(self, **kw):
        self.calls.append(kw)
        return "学生作答"


class _FakeWriter:
    def __init__(self):
        self.calls = 0

    async def draft(self, **kw):
        self.calls += 1
        return WriterResult(
            draft_content=(
                "# 01. 化学平衡\n\n"
                "## 学习目标与先修前置\n\n"
                "**先修前置：** AI 自己乱写的先修。\n\n"
                "**学习目标：** 学会平衡常数。\n\n"
                "## 背景与应用场景\n\n"
                "平衡常数 K 的表达式与计算。"
            )
        )


class _FakeRetriever:
    def __init__(self):
        self.indexed = []
        self.source_queries = []
        self.finished_queries = []

    def source_hits(self, query, *, collections, node_ids, top_k):
        self.source_queries.append({"collections": collections, "node_ids": node_ids, "top_k": top_k})
        return []

    def finished_hits(self, query, *, allowed_paths, top_k):
        self.finished_queries.append({"allowed_paths": set(allowed_paths), "top_k": top_k})
        return [{"text": "prior hit", "metadata": {"path": next(iter(allowed_paths))}}] if allowed_paths else []

    def index_finished(self, node_id, path):
        self.indexed.append((node_id, path))
        return 1


def _seed(root):
    paths.ensure_workspace_dirs(root)
    write_json_atomic(
        paths.knowledge_dag_path(root),
        envelope(
            schema="tree.knowledge-dag",
            data={
                "nodes": [
                    {"node_id": "n0", "title": "前置", "defines": ["前置"], "collections": ["课件"], "source_order_index": 0},
                    {"node_id": "n1", "title": "化学平衡", "defines": ["平衡"], "collections": ["课件"], "source_order_index": 1},
                ],
                "edges": [
                    {
                        "from_node_id": "n0",
                        "to_node_id": "n1",
                        "relation": "prerequisite",
                        "required_defines": ["前置定义"],
                    }
                ],
                "roots": ["n0"],
            },
        ),
    )
    write_json_atomic(
        paths.knowledge_nodes_path(root),
        envelope(
            schema="tree.knowledge-nodes",
            data={"knowledge_nodes": [
                {"node_id": "n0", "title": "前置", "defines": ["前置"], "collections": ["课件"], "source_order_index": 0},
                {"node_id": "n1", "title": "化学平衡", "defines": ["平衡"], "collections": ["课件"], "source_order_index": 1},
            ]},
        ),
    )
    prior = paths.outputs_root(root) / "001.前置.md"
    prior.write_text("# 前置\n已学内容", encoding="utf-8")
    write_json_atomic(
        paths.knowledge_ledger_path(root),
        {"records": [{"node_id": "n0", "node_ids": ["n0"], "output_path": "outputs/001.前置.md", "title": "前置", "file_seq": "001"}]},
    )
    StateManager(paths.pipeline_state_path(root)).save(
        PipelineState(
            node_executions=[
                NodeExecutionRecord(
                    node_id="n1",
                    status="in_progress",
                    node_run_id="n1::run",
                    source_collections=["课件"],
                )
            ],
            node_runs=[
                NodeRunRecord(
                    node_id="n1",
                    run_id="n1::run",
                    status="running",
                    coverage_snapshot=CoverageSnapshot(snapshot_visible_ancestor_node_ids=["n0"]),
                )
            ],
        )
    )


async def test_node_run_fail_then_pass_records_single_node_output(tmp_path):
    _seed(tmp_path)
    examiner = _FakeExaminer(fail_then_pass=1)
    student = _FakeStudent()
    writer = _FakeWriter()
    retriever = _FakeRetriever()
    runner = NodeRunner(
        root=tmp_path,
        settings=SimpleNamespace(max_iterations=5),
        examiner=examiner,
        student=student,
        writer=writer,
        retriever=retriever,
        state_mgr=StateManager(paths.pipeline_state_path(tmp_path)),
    )

    result = await runner.run_one("n1")

    assert result == "node_complete"
    assert examiner.audit_calls == 2
    assert writer.calls == 1

    output = paths.outputs_root(tmp_path) / "002.化学平衡.md"
    assert output.exists()
    text = output.read_text(encoding="utf-8")
    assert text.startswith("# 002. 化学平衡\n\n## 先修前置\n")
    assert "- [前置](001.前置.md)：相关先修 defines：前置定义。" in text
    assert "## 学习目标\n\n**学习目标：** 学会平衡常数。" in text
    assert "AI 自己乱写的先修" not in text
    assert retriever.indexed == [("n1", output)]
    assert retriever.source_queries and retriever.source_queries[0]["node_ids"] == ["n1"]
    assert student.calls
    assert "prior_contents" not in student.calls[0]
    assert student.calls[0]["learned_hits"] == [{"text": "prior hit", "metadata": {"path": "outputs/001.前置.md"}}]

    assert ledger_covered_node_ids(tmp_path) == {"n0", "n1"}
    state = StateManager(paths.pipeline_state_path(tmp_path)).load()
    execution = state.node_executions[0]
    assert execution.status == "completed"
    assert execution.outputs_completed == ["002.化学平衡.md"]
    assert state.node_runs[0].status == "complete"


async def test_node_run_recovers_output_transaction_after_index_failure(tmp_path):
    _seed(tmp_path)

    class FailingOnceRetriever(_FakeRetriever):
        def index_finished(self, node_id, path):
            if not self.indexed:
                self.indexed.append(("failed", path))
                raise RuntimeError("simulated index outage")
            return super().index_finished(node_id, path)

    retriever = FailingOnceRetriever()
    state_mgr = StateManager(paths.pipeline_state_path(tmp_path))
    runner = NodeRunner(
        root=tmp_path,
        settings=SimpleNamespace(max_iterations=5),
        examiner=_FakeExaminer(fail_then_pass=1),
        student=_FakeStudent(),
        writer=_FakeWriter(),
        retriever=retriever,
        state_mgr=state_mgr,
    )

    with pytest.raises(RuntimeError, match="simulated index outage"):
        await runner.run_one("n1")

    output = paths.outputs_root(tmp_path) / "002.化学平衡.md"
    assert output.exists()
    assert list(paths.output_transactions_root(tmp_path).glob("*.json"))

    # Recovery republishes the existing file and never invokes the LLM loop.
    examiner = _FakeExaminer()
    writer = _FakeWriter()
    recovered = NodeRunner(
        root=tmp_path,
        settings=SimpleNamespace(max_iterations=5),
        examiner=examiner,
        student=_FakeStudent(),
        writer=writer,
        retriever=retriever,
        state_mgr=state_mgr,
    )
    assert await recovered.run_one("n1") == "node_complete"
    assert examiner.compose_kwargs == []
    assert writer.calls == 0
    assert ledger_covered_node_ids(tmp_path) == {"n0", "n1"}
    assert not list(paths.output_transactions_root(tmp_path).glob("*.json"))


async def test_node_run_includes_every_member_mtu_evidence_and_source_trace(tmp_path):
    _seed(tmp_path)
    dag_envelope = json.loads(paths.knowledge_dag_path(tmp_path).read_text(encoding="utf-8"))
    target = next(node for node in dag_envelope["data"]["nodes"] if node["node_id"] == "n1")
    target["member_mtu_ids"] = ["mtu:a", "mtu:b"]
    write_json_atomic(paths.knowledge_dag_path(tmp_path), dag_envelope)
    write_json_atomic(
        paths.mtus_path(tmp_path),
        envelope(
            schema="tree.mtus",
            data={
                "mtus": [
                    {
                        "mtu_id": "mtu:a",
                        "source_id": "课件/week1.md",
                        "line_range": [1, 31],
                    },
                    {
                        "mtu_id": "mtu:b",
                        "source_id": "课件/week2.md",
                        "line_range": [32, 62],
                    },
                ]
            },
        ),
    )

    class EvidenceRetriever(_FakeRetriever):
        def __init__(self):
            super().__init__()
            self.required_mtu_calls = []

        def source_evidence(self, mtu_ids):
            self.required_mtu_calls.append(list(mtu_ids))
            return [
                {"text": f"evidence {mtu_id}", "metadata": {"mtu_id": mtu_id}}
                for mtu_id in mtu_ids
            ]

    retriever = EvidenceRetriever()
    runner = NodeRunner(
        root=tmp_path,
        settings=SimpleNamespace(max_iterations=5),
        examiner=_FakeExaminer(fail_then_pass=1),
        student=_FakeStudent(),
        writer=_FakeWriter(),
        retriever=retriever,
        state_mgr=StateManager(paths.pipeline_state_path(tmp_path)),
    )

    assert await runner.run_one("n1") == "node_complete"
    assert retriever.required_mtu_calls
    assert all(call == ["mtu:a", "mtu:b"] for call in retriever.required_mtu_calls)
    output = (paths.outputs_root(tmp_path) / "002.化学平衡.md").read_text(encoding="utf-8")
    assert "## 来源追溯" in output
    assert "`课件/week1.md`，第 1–31 行（`mtu:a`）" in output
    assert "`课件/week2.md`，第 32–62 行（`mtu:b`）" in output


def test_ledger_rejects_missing_or_modified_output(tmp_path):
    _seed(tmp_path)
    output = paths.outputs_root(tmp_path) / "001.前置.md"
    original = output.read_bytes()
    digest = hashlib.sha256(original).hexdigest()
    write_json_atomic(
        paths.knowledge_ledger_path(tmp_path),
        {
            "records": [
                {
                    "node_id": "n0",
                    "node_ids": ["n0"],
                    "output_path": "outputs/001.前置.md",
                    "output_sha256": digest,
                }
            ]
        },
    )
    assert ledger_covered_node_ids(tmp_path) == {"n0"}

    output.write_text("modified", encoding="utf-8")
    assert ledger_covered_node_ids(tmp_path) == set()

    output.unlink()
    assert ledger_covered_node_ids(tmp_path) == set()


def test_generation_reconciliation_archives_stale_output(tmp_path):
    _seed(tmp_path)
    write_json_atomic(
        paths.material_manifest_path(tmp_path),
        {"generation_id": "gen:new", "materials": [], "active_materials": [], "inactive_materials": []},
    )
    output = paths.outputs_root(tmp_path) / "001.前置.md"

    assert reconcile_ledger_generation(tmp_path) == 1
    assert not output.exists()
    assert (paths.output_archive_root(tmp_path) / "legacy" / "001.前置.md").exists()
    assert ledger_covered_node_ids(tmp_path) == set()

async def test_node_run_skips_when_already_covered(tmp_path):
    _seed(tmp_path)
    write_json_atomic(
        paths.knowledge_ledger_path(tmp_path),
        {"records": [{"node_id": "n1", "node_ids": ["n1"], "output_path": "outputs/002.x.md", "title": "x", "file_seq": "002"}]},
    )
    runner = NodeRunner(
        root=tmp_path,
        settings=SimpleNamespace(max_iterations=5),
        examiner=_FakeExaminer(),
        student=_FakeStudent(),
        writer=_FakeWriter(),
        retriever=_FakeRetriever(),
        state_mgr=StateManager(paths.pipeline_state_path(tmp_path)),
    )

    result = await runner.run_one("n1")

    assert result == "node_complete"
    state = StateManager(paths.pipeline_state_path(tmp_path)).load()
    assert state.node_executions[0].status == "completed"


async def test_node_run_pass_without_draft_writes_draft_before_accepting(tmp_path):
    _seed(tmp_path)
    examiner = _FakeExaminer(fail_then_pass=0)
    writer = _FakeWriter()
    runner = NodeRunner(
        root=tmp_path,
        settings=SimpleNamespace(max_iterations=5),
        examiner=examiner,
        student=_FakeStudent(),
        writer=writer,
        retriever=_FakeRetriever(),
        state_mgr=StateManager(paths.pipeline_state_path(tmp_path)),
    )

    result = await runner.run_one("n1")

    assert result == "node_complete"
    assert examiner.audit_calls == 2
    assert writer.calls == 1
    assert (paths.outputs_root(tmp_path) / "002.化学平衡.md").exists()


async def test_node_run_resumes_saved_exam_and_draft_without_recompose(tmp_path):
    _seed(tmp_path)
    draft = paths.drafts_root(tmp_path) / "n1" / "002.化学平衡.md"
    draft.parent.mkdir(parents=True)
    draft.write_text(
        "# 002. 化学平衡\n\n## 学习目标\n\n**学习目标：** 学会平衡常数。\n",
        encoding="utf-8",
    )
    state_mgr = StateManager(paths.pipeline_state_path(tmp_path))
    state = state_mgr.load()
    state = state_mgr.update_node_run(
        state,
        "n1::run",
        exam_sections=ExamSections(
            knowledge_point="化学平衡",
            covered_node_ids=["n1"],
            blind_exam="Q",
            answer_key="A",
            writer_instructions="Scope: x",
        ),
        draft_path=draft,
        current_iteration=1,
        previous_bottleneck="# Bottleneck Report\nold",
    )
    state_mgr.save(state)
    examiner = _FakeExaminer(fail_then_pass=0)
    writer = _FakeWriter()
    runner = NodeRunner(
        root=tmp_path,
        settings=SimpleNamespace(max_iterations=5),
        examiner=examiner,
        student=_FakeStudent(),
        writer=writer,
        retriever=_FakeRetriever(),
        state_mgr=state_mgr,
    )

    result = await runner.run_one("n1")

    assert result == "node_complete"
    assert examiner.compose_kwargs == []
    assert examiner.audit_calls == 1
    assert writer.calls == 0
    assert (paths.outputs_root(tmp_path) / "002.化学平衡.md").exists()


async def test_node_run_answer_key_defect_reconciles_without_rewriting_question(tmp_path):
    _seed(tmp_path)
    draft = paths.drafts_root(tmp_path) / "n1" / "002.化学平衡.md"
    draft.parent.mkdir(parents=True)
    draft.write_text("# 002. 化学平衡\n\n已有正确草稿。\n", encoding="utf-8")
    state_mgr = StateManager(paths.pipeline_state_path(tmp_path))
    state = state_mgr.load()
    state = state_mgr.update_node_run(
        state,
        "n1::run",
        exam_sections=ExamSections(
            knowledge_point="Original title",
            covered_node_ids=["n1"],
            blind_exam="Original Q",
            answer_key="Original A",
            writer_instructions="Scope: original",
        ),
        draft_path=draft,
        current_iteration=1,
    )
    state_mgr.save(state)
    examiner = _AuditDefectExaminer(AuditExamDefectKind.ANSWER_KEY_DEFECT)
    writer = _FakeWriter()
    runner = NodeRunner(
        root=tmp_path,
        settings=SimpleNamespace(max_iterations=5),
        examiner=examiner,
        student=_FakeStudent(),
        writer=writer,
        retriever=_FakeRetriever(),
        state_mgr=state_mgr,
    )

    result = await runner.run_one("n1")

    assert result == "node_complete"
    assert examiner.reconcile_calls == 1
    assert examiner.audit_calls == 2
    assert writer.calls == 0
    state = state_mgr.load()
    exam = state.node_runs[0].exam_sections
    assert exam is not None
    assert exam.blind_exam == "Original Q"
    assert exam.answer_key == "Revised A"
    assert exam.writer_instructions == "Scope: original"
    assert state.node_runs[0].exam_repair_count == 1


async def test_node_run_exam_defect_reconciles_full_exam(tmp_path):
    _seed(tmp_path)
    draft = paths.drafts_root(tmp_path) / "n1" / "002.化学平衡.md"
    draft.parent.mkdir(parents=True)
    draft.write_text("# 002. 化学平衡\n\n已有正确草稿。\n", encoding="utf-8")
    state_mgr = StateManager(paths.pipeline_state_path(tmp_path))
    state = state_mgr.load()
    state = state_mgr.update_node_run(
        state,
        "n1::run",
        exam_sections=ExamSections(
            knowledge_point="Original title",
            covered_node_ids=["n1"],
            blind_exam="Original Q",
            answer_key="Original A",
            writer_instructions="Scope: original",
        ),
        draft_path=draft,
        current_iteration=1,
    )
    state_mgr.save(state)
    examiner = _AuditDefectExaminer(AuditExamDefectKind.EXAM_DEFECT)
    student = _FakeStudent()
    writer = _FakeWriter()
    runner = NodeRunner(
        root=tmp_path,
        settings=SimpleNamespace(max_iterations=5),
        examiner=examiner,
        student=student,
        writer=writer,
        retriever=_FakeRetriever(),
        state_mgr=state_mgr,
    )

    result = await runner.run_one("n1")

    assert result == "node_complete"
    assert examiner.reconcile_calls == 1
    assert writer.calls == 0
    state = state_mgr.load()
    exam = state.node_runs[0].exam_sections
    assert exam is not None
    assert exam.blind_exam == "Revised Q"
    assert exam.answer_key == "Revised A"
    assert exam.writer_instructions == "Scope: revised"
    assert state.node_runs[0].exam_repair_count == 1
    assert student.calls[1]["blind_exam"] == "Revised Q"


async def test_node_run_exam_defect_after_repair_does_not_call_writer(tmp_path):
    _seed(tmp_path)
    draft = paths.drafts_root(tmp_path) / "n1" / "002.化学平衡.md"
    draft.parent.mkdir(parents=True)
    draft.write_text("# 002. 化学平衡\n\n已有草稿。\n", encoding="utf-8")
    state_mgr = StateManager(paths.pipeline_state_path(tmp_path))
    state = state_mgr.load()
    state = state_mgr.update_node_run(
        state,
        "n1::run",
        exam_sections=ExamSections(
            knowledge_point="Original title",
            covered_node_ids=["n1"],
            blind_exam="Original Q",
            answer_key="Original A",
            writer_instructions="Scope: original",
        ),
        draft_path=draft,
        current_iteration=1,
        exam_repair_count=1,
    )
    state_mgr.save(state)
    examiner = _AuditDefectExaminer(AuditExamDefectKind.EXAM_DEFECT)
    writer = _FakeWriter()
    runner = NodeRunner(
        root=tmp_path,
        settings=SimpleNamespace(max_iterations=5),
        examiner=examiner,
        student=_FakeStudent(),
        writer=writer,
        retriever=_FakeRetriever(),
        state_mgr=state_mgr,
    )

    with pytest.raises(RuntimeError, match="Exam repair already used"):
        await runner.run_one("n1")

    assert examiner.reconcile_calls == 0
    assert writer.calls == 0
    state = state_mgr.load()
    assert state.node_runs[0].status == "failed"
    assert "Exam repair already used" in (state.node_runs[0].last_error or "")


async def test_node_run_reconciles_bad_answer_key_at_iteration_limit(tmp_path):
    _seed(tmp_path)
    draft = paths.drafts_root(tmp_path) / "n1" / "002.化学平衡.md"
    draft.parent.mkdir(parents=True)
    draft.write_text(
        "# 002. 化学平衡\n\n"
        "相同活化能降低量时，速率提升倍数相同。\n",
        encoding="utf-8",
    )
    state_mgr = StateManager(paths.pipeline_state_path(tmp_path))
    state = state_mgr.load()
    state = state_mgr.update_node_run(
        state,
        "n1::run",
        exam_sections=ExamSections(
            knowledge_point="化学平衡",
            covered_node_ids=["n1"],
            blind_exam="Bad Q",
            answer_key="Bad A",
            writer_instructions="Scope: x",
        ),
        draft_path=draft,
        current_iteration=5,
        previous_bottleneck="# Bottleneck Report\nAnswer key contradicts draft formula.",
    )
    state_mgr.save(state)
    examiner = _ReconcilingExaminer()
    runner = NodeRunner(
        root=tmp_path,
        settings=SimpleNamespace(max_iterations=5),
        examiner=examiner,
        student=_FakeStudent(),
        writer=_FakeWriter(),
        retriever=_FakeRetriever(),
        state_mgr=state_mgr,
    )

    result = await runner.run_one("n1")

    assert result == "node_complete"
    assert examiner.reconcile_calls == 1
    assert examiner.compose_kwargs == []
    assert examiner.audit_calls == 1
    state = state_mgr.load()
    assert state.node_runs[0].exam_repair_count == 1
    assert state.node_runs[0].exam_sections is not None
    assert state.node_runs[0].exam_sections.answer_key == "Revised A"
    assert state.node_runs[0].status == "complete"
    assert (paths.outputs_root(tmp_path) / "002.化学平衡.md").exists()


async def test_node_run_keep_fail_reconciliation_still_raises_iteration_limit(tmp_path):
    _seed(tmp_path)
    draft = paths.drafts_root(tmp_path) / "n1" / "002.化学平衡.md"
    draft.parent.mkdir(parents=True)
    draft.write_text("# 002. 化学平衡\n\n缺方法。\n", encoding="utf-8")
    state_mgr = StateManager(paths.pipeline_state_path(tmp_path))
    state = state_mgr.load()
    state = state_mgr.update_node_run(
        state,
        "n1::run",
        exam_sections=ExamSections(
            knowledge_point="化学平衡",
            covered_node_ids=["n1"],
            blind_exam="Q",
            answer_key="A",
            writer_instructions="Scope: x",
        ),
        draft_path=draft,
        current_iteration=5,
        previous_bottleneck="# Bottleneck Report\nStill missing a method.",
    )
    state_mgr.save(state)
    examiner = _ReconcilingExaminer(action=ExamReconciliationAction.KEEP_FAIL)
    runner = NodeRunner(
        root=tmp_path,
        settings=SimpleNamespace(max_iterations=5),
        examiner=examiner,
        student=_FakeStudent(),
        writer=_FakeWriter(),
        retriever=_FakeRetriever(),
        state_mgr=state_mgr,
    )

    with pytest.raises(IterationLimitExceeded, match="exam_repair_count=1"):
        await runner.run_one("n1")

    state = state_mgr.load()
    assert examiner.reconcile_calls == 1
    assert state.node_runs[0].exam_repair_count == 1
