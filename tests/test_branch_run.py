"""Tests for the BranchRunner Step 0->4 loop (step 7)."""

from __future__ import annotations

from types import SimpleNamespace

from tree.engine.branch_run import BranchRunner, ledger_covered_node_ids
from tree.io import paths
from tree.planner.store import envelope, write_json_atomic
from tree.state.manager import StateManager
from tree.state.models import (
    AuditResult,
    BranchExecutionRecord,
    BranchRunRecord,
    CoverageSnapshot,
    ExamSections,
    PipelineState,
    Route,
    WriterResult,
)


class _FakeExaminer:
    def __init__(self, fail_then_pass=1):
        self.audit_calls = 0
        self.fail_count = fail_then_pass

    async def compose(self, **kw):
        return ExamSections(
            knowledge_point="化学平衡", covered_node_ids=["n1"],
            blind_exam="Q", answer_key="A", writer_instructions="Scope: x",
        )

    async def audit(self, **kw):
        self.audit_calls += 1
        route = Route.FAIL_KNOWLEDGE_GAP if self.audit_calls <= self.fail_count else Route.PASS
        return AuditResult(route=route, exam_id="化学平衡", bottleneck_report="# Bottleneck Report\n缺公式")


class _FakeStudent:
    async def answer(self, **kw):
        return "学生作答"


class _FakeWriter:
    def __init__(self):
        self.calls = 0

    async def draft(self, **kw):
        self.calls += 1
        return WriterResult(draft_content="# 化学平衡\n平衡常数 K 的表达式与计算。")


class _FakeRetriever:
    def __init__(self):
        self.indexed = []

    def source_hits(self, query, *, collections, top_k):
        return []

    def finished_hits(self, query, *, allowed_paths, top_k):
        return []

    def index_finished(self, execution_path, path):
        self.indexed.append(path)
        return 1


def _seed(root):
    paths.ensure_workspace_dirs(root)
    write_json_atomic(
        paths.knowledge_branches_path(root),
        envelope(schema="tree.knowledge-branches", data={"branches": [
            {"branch_id": "kb:1", "node_ids": ["n1"], "coverage_node_ids": ["n1"],
             "start_node_id": "n1", "end_node_id": "n1", "upstream_branch_ids": [],
             "downstream_branch_ids": [], "display_order": 0}
        ]}),
    )
    write_json_atomic(
        paths.knowledge_nodes_path(root),
        envelope(schema="tree.knowledge-nodes", data={"knowledge_nodes": [
            {"node_id": "n1", "title": "化学平衡", "keywords": ["平衡"], "collections": ["课件"]}
        ]}),
    )
    state = PipelineState(
        branch_executions=[
            BranchExecutionRecord(
                execution_path="kb:1", status="in_progress", branch_id="kb:1",
                branch_run_id="kb:1::run", source_collections=["课件"], coverage_node_ids=["n1"],
                current_start_node_id="n1",
            )
        ],
        branch_runs=[
            BranchRunRecord(
                branch_id="kb:1", run_id="kb:1::run", status="running",
                coverage_snapshot=CoverageSnapshot(), execution_path="kb:1",
            )
        ],
    )
    StateManager(paths.pipeline_state_path(root)).save(state)


async def test_branch_run_fail_then_pass_records_output(tmp_path):
    _seed(tmp_path)
    examiner = _FakeExaminer(fail_then_pass=1)
    writer = _FakeWriter()
    retriever = _FakeRetriever()
    runner = BranchRunner(
        root=tmp_path,
        settings=SimpleNamespace(max_iterations=5),
        examiner=examiner,
        student=_FakeStudent(),
        writer=writer,
        retriever=retriever,
        state_mgr=StateManager(paths.pipeline_state_path(tmp_path)),
    )

    result = await runner.run_one("kb:1")

    assert result == "branch_complete"
    assert examiner.audit_calls == 2  # one FAIL, one PASS
    assert writer.calls == 1

    # Output file landed and was indexed.
    output = paths.outputs_root(tmp_path) / "kb_1" / "01.化学平衡.md"
    assert output.exists()
    assert retriever.indexed == [output]

    # Ledger covers n1.
    assert ledger_covered_node_ids(tmp_path) == {"n1"}

    # State updated.
    state = StateManager(paths.pipeline_state_path(tmp_path)).load()
    be = state.branch_executions[0]
    assert be.status == "completed"
    assert be.outputs_completed == ["01.化学平衡.md"]


async def test_branch_run_skips_when_already_covered(tmp_path):
    _seed(tmp_path)
    # Pre-cover n1 in the ledger.
    write_json_atomic(
        paths.knowledge_ledger_path(tmp_path),
        {"records": [{"execution_path": "kb:1", "output_path": "outputs/kb_1/01.x.md",
                      "title": "x", "node_ids": ["n1"], "file_seq": "01"}]},
    )
    runner = BranchRunner(
        root=tmp_path,
        settings=SimpleNamespace(max_iterations=5),
        examiner=_FakeExaminer(),
        student=_FakeStudent(),
        writer=_FakeWriter(),
        retriever=_FakeRetriever(),
        state_mgr=StateManager(paths.pipeline_state_path(tmp_path)),
    )
    result = await runner.run_one("kb:1")
    assert result == "branch_complete"
    state = StateManager(paths.pipeline_state_path(tmp_path)).load()
    assert state.branch_executions[0].status == "completed"
