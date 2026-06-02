"""Tests for the NodeRunner Examiner -> Student -> Writer loop."""

from __future__ import annotations

from types import SimpleNamespace

from tree.engine.node_run import NodeRunner, ledger_covered_node_ids
from tree.io import paths
from tree.planner.store import envelope, write_json_atomic
from tree.state.manager import StateManager
from tree.state.models import (
    AuditResult,
    CoverageSnapshot,
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
