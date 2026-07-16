"""Tests for the NodeRunner Examiner -> Student -> Writer loop."""

from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace

import pytest

from tree.engine.node_run import (
    NodeRunner,
    NodeRunPrerequisiteError,
    NodeRunStagnationError,
    ledger_covered_node_ids,
    reconcile_ledger_generation,
)
from tree.io import paths
from tree.observability.limiter import IterationLimitExceeded
from tree.planner.store import envelope, write_json_atomic
from tree.state.manager import StateManager
from tree.state.models import (
    AuditResult,
    AuditExamDefectKind,
    AuditPlannerDefectKind,
    CoverageSnapshot,
    ExamReconciliationAction,
    ExamReconciliationTrigger,
    ExamReconciliationResult,
    ExamSections,
    NodeExecutionRecord,
    NodeRunMode,
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
        self.reconcile_kwargs = []

    async def reconcile_exam(self, **kw):
        self.reconcile_calls += 1
        self.reconcile_kwargs.append(kw)
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
    def __init__(
        self,
        defect_kind: AuditExamDefectKind,
        *,
        action: ExamReconciliationAction = ExamReconciliationAction.REVISE_EXAM,
    ):
        super().__init__(action=action)
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


class _StagnatingReconcilingExaminer(_ReconcilingExaminer):
    async def audit(self, **kw):
        self.audit_calls += 1
        if self.audit_calls <= 2:
            return AuditResult(
                route=Route.FAIL_KNOWLEDGE_GAP,
                exam_id="化学平衡",
                bottleneck_report="# Bottleneck Report\nStill missing the same formula.",
            )
        return AuditResult(route=Route.PASS, exam_id="化学平衡", bottleneck_report="ok")


class _PlannerDefectExaminer(_FakeExaminer):
    async def audit(self, **kw):
        self.audit_calls += 1
        return AuditResult(
            route=Route.FAIL_KNOWLEDGE_GAP,
            exam_id="化学平衡",
            bottleneck_report="# Bottleneck Report\nMissing internal prerequisite outside ActiveNode.",
            planner_defect_kind=AuditPlannerDefectKind.MISSING_PREREQUISITE,
        )


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


class _FastWriter:
    def __init__(self):
        self.fast_calls = []

    async def draft(self, **kw):
        raise AssertionError("standard Writer must not run in fast mode")

    async def fast_draft(self, **kw):
        self.fast_calls.append(kw)
        return WriterResult(
            draft_content=(
                "# 002. 化学平衡\n\n"
                "## 学习目标\n\n掌握平衡常数。\n\n"
                "## 背景与应用场景\n\n说明可逆反应。\n\n"
                "## 核心概念与符号约定\n\n定义平衡常数。\n\n"
                "## 原理与方法\n\n推导表达式。\n\n"
                "## 例题\n\n## 标准答案\n\n保留并整合合法解析。\n\n"
                "## 常见误区与检查点\n\n检查平衡浓度。"
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


async def test_fast_node_run_calls_only_fast_writer_and_publishes_normal_format(tmp_path):
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
                    {"mtu_id": "mtu:a", "source_id": "课件/a.md", "line_range": [1, 20]},
                    {"mtu_id": "mtu:b", "source_id": "课件/b.md", "line_range": [21, 40]},
                ]
            },
        ),
    )

    class _FastEvidenceRetriever(_FakeRetriever):
        def source_evidence(self, mtu_ids):
            return [
                {
                    "text": f"evidence for {mtu_id}",
                    "metadata": {"content_kind": "source", "mtu_id": mtu_id},
                }
                for mtu_id in mtu_ids
            ]

    state_mgr = StateManager(paths.pipeline_state_path(tmp_path))
    state = state_mgr.load()
    state.node_runs[0].mode = NodeRunMode.FAST
    state_mgr.save(state)
    examiner = _FakeExaminer()
    student = _FakeStudent()
    writer = _FastWriter()
    retriever = _FastEvidenceRetriever()
    runner = NodeRunner(
        root=tmp_path,
        settings=SimpleNamespace(max_iterations=5, node_run_mode="standard"),
        examiner=examiner,
        student=student,
        writer=writer,
        retriever=retriever,
        state_mgr=state_mgr,
    )

    assert await runner.run_one("n1") == "node_complete"

    assert examiner.compose_kwargs == []
    assert examiner.audit_calls == 0
    assert student.calls == []
    assert len(writer.fast_calls) == 1
    task = writer.fast_calls[0]["task_spec"]
    assert task["node_id"] == "n1"
    assert task["defines"] == ["平衡"]
    assert task["member_mtu_ids"] == ["mtu:a", "mtu:b"]
    assert task["direct_prerequisites"][0]["node_id"] == "n0"
    assert retriever.finished_queries == [
        {"allowed_paths": {"outputs/001.前置.md"}, "top_k": 8}
    ]
    assert any(hit["text"] == "prior hit" for hit in writer.fast_calls[0]["retrieved"])
    output = paths.outputs_root(tmp_path) / "002.化学平衡.md"
    text = output.read_text(encoding="utf-8")
    assert text.startswith("# 002. 化学平衡\n\n## 先修前置\n")
    assert "## 学习目标" in text
    assert "## 标准答案" in text
    assert "## 来源追溯" in text
    assert "`课件/a.md`，第 1–20 行（`mtu:a`）" in text
    assert "`课件/b.md`，第 21–40 行（`mtu:b`）" in text
    assert state_mgr.load().node_runs[0].mode is NodeRunMode.FAST


async def test_fast_node_run_resumes_saved_draft_without_second_writer_call(tmp_path):
    _seed(tmp_path)
    draft = paths.drafts_root(tmp_path) / "n1" / "002.化学平衡.md"
    draft.parent.mkdir(parents=True)
    draft.write_text(
        "# 002. 化学平衡\n\n## 学习目标\n\n恢复已经保存的快速草稿。\n",
        encoding="utf-8",
    )
    state_mgr = StateManager(paths.pipeline_state_path(tmp_path))
    state = state_mgr.load()
    state.node_runs[0].mode = NodeRunMode.FAST
    state.node_runs[0].draft_path = draft
    state_mgr.save(state)
    writer = _FastWriter()
    runner = NodeRunner(
        root=tmp_path,
        settings=SimpleNamespace(max_iterations=5, node_run_mode="standard"),
        examiner=_FakeExaminer(),
        student=_FakeStudent(),
        writer=writer,
        retriever=_FakeRetriever(),
        state_mgr=state_mgr,
    )

    assert await runner.run_one("n1") == "node_complete"
    assert writer.fast_calls == []
    assert (paths.outputs_root(tmp_path) / "002.化学平衡.md").exists()


async def test_started_standard_node_ignores_later_fast_setting(tmp_path):
    _seed(tmp_path)
    state_mgr = StateManager(paths.pipeline_state_path(tmp_path))
    state = state_mgr.load()
    state.node_runs[0].mode = NodeRunMode.STANDARD
    state_mgr.save(state)
    examiner = _FakeExaminer(fail_then_pass=1)
    writer = _FakeWriter()
    runner = NodeRunner(
        root=tmp_path,
        settings=SimpleNamespace(max_iterations=5, node_run_mode="fast"),
        examiner=examiner,
        student=_FakeStudent(),
        writer=writer,
        retriever=_FakeRetriever(),
        state_mgr=state_mgr,
    )

    assert await runner.run_one("n1") == "node_complete"
    assert examiner.compose_kwargs
    assert writer.calls == 1


async def test_node_run_planner_prerequisite_defect_stops_before_writer(tmp_path):
    _seed(tmp_path)
    state_mgr = StateManager(paths.pipeline_state_path(tmp_path))
    examiner = _PlannerDefectExaminer()
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

    with pytest.raises(NodeRunPrerequisiteError, match="Regrow the knowledge graph"):
        await runner.run_one("n1")

    run = state_mgr.load().node_runs[0]
    assert examiner.audit_calls == 1
    assert writer.calls == 0
    assert run.status == "failed"
    assert "prerequisite planning defect" in (run.last_error or "")


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
    history = state.node_runs[0].exam_reconciliation_history
    assert len(history) == 1
    assert history[0].trigger is ExamReconciliationTrigger.AUDIT_DEFECT
    assert history[0].defect_kind is AuditExamDefectKind.ANSWER_KEY_DEFECT
    assert history[0].action is ExamReconciliationAction.REVISE_EXAM


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
    history = state.node_runs[0].exam_reconciliation_history
    assert len(history) == 1
    assert history[0].trigger is ExamReconciliationTrigger.AUDIT_DEFECT
    assert history[0].defect_kind is AuditExamDefectKind.EXAM_DEFECT
    assert examiner.reconcile_kwargs[0]["iteration"] == 2
    assert student.calls[1]["blind_exam"] == "Revised Q"


async def test_node_run_keep_fail_for_audit_defect_continues_to_writer(tmp_path):
    _seed(tmp_path)
    state_mgr = StateManager(paths.pipeline_state_path(tmp_path))
    examiner = _AuditDefectExaminer(
        AuditExamDefectKind.EXAM_DEFECT,
        action=ExamReconciliationAction.KEEP_FAIL,
    )
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

    assert await runner.run_one("n1") == "node_complete"

    run = state_mgr.load().node_runs[0]
    assert examiner.reconcile_calls == 1
    assert examiner.audit_calls == 2
    assert writer.calls == 1
    assert run.status == "complete"
    assert run.last_error is None
    assert run.exam_repair_count == 1
    assert len(run.exam_reconciliation_history) == 1
    record = run.exam_reconciliation_history[0]
    assert record.trigger is ExamReconciliationTrigger.AUDIT_DEFECT
    assert record.defect_kind is AuditExamDefectKind.EXAM_DEFECT
    assert record.action is ExamReconciliationAction.KEEP_FAIL
    assert record.reason == "draft is still missing a method"


async def test_node_run_exam_defect_after_repair_continues_to_writer(tmp_path):
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

    assert await runner.run_one("n1") == "node_complete"

    assert examiner.reconcile_calls == 0
    assert examiner.audit_calls == 2
    assert writer.calls == 1
    state = state_mgr.load()
    assert state.node_runs[0].status == "complete"
    assert state.node_runs[0].last_error is None


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
    history = state.node_runs[0].exam_reconciliation_history
    assert len(history) == 1
    assert history[0].trigger is ExamReconciliationTrigger.ITERATION_LIMIT
    assert history[0].action is ExamReconciliationAction.REVISE_EXAM
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
    history = state.node_runs[0].exam_reconciliation_history
    assert len(history) == 1
    assert history[0].trigger is ExamReconciliationTrigger.ITERATION_LIMIT
    assert history[0].action is ExamReconciliationAction.KEEP_FAIL
    assert history[0].reason == "draft is still missing a method"


async def test_node_run_reconciles_after_repeated_equivalent_bottleneck(tmp_path):
    _seed(tmp_path)
    state_mgr = StateManager(paths.pipeline_state_path(tmp_path))
    examiner = _StagnatingReconcilingExaminer()
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

    assert await runner.run_one("n1") == "node_complete"

    state = state_mgr.load()
    run = state.node_runs[0]
    assert examiner.reconcile_calls == 1
    assert examiner.audit_calls == 3
    assert writer.calls == 1
    assert run.exam_repair_count == 1
    assert run.exam_reconciliation_history[0].trigger is ExamReconciliationTrigger.STAGNATION
    assert run.bottleneck_repeat_count == 0
    assert len(run.bottleneck_history) == 2


async def test_node_run_stops_early_when_repeated_bottleneck_cannot_be_reconciled(tmp_path):
    _seed(tmp_path)
    state_mgr = StateManager(paths.pipeline_state_path(tmp_path))
    examiner = _FakeExaminer(fail_then_pass=10)
    runner = NodeRunner(
        root=tmp_path,
        settings=SimpleNamespace(max_iterations=5),
        examiner=examiner,
        student=_FakeStudent(),
        writer=_FakeWriter(),
        retriever=_FakeRetriever(),
        state_mgr=state_mgr,
    )

    with pytest.raises(NodeRunStagnationError, match="stagnated"):
        await runner.run_one("n1")

    run = state_mgr.load().node_runs[0]
    assert examiner.audit_calls == 2
    assert run.current_iteration == 2
    assert run.bottleneck_repeat_count == 2
    assert len(run.bottleneck_history) == 2
    assert run.status == "failed"
