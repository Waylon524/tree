"""Tests for Step 8 foreground engine orchestration."""

from __future__ import annotations

from types import SimpleNamespace

from tree.engine.orchestrator import TreeEngine
from tree.io import paths
from tree.planner.store import envelope, write_json_atomic
from tree.state.manager import StateManager


class _FakeRunner:
    def __init__(self, root):
        self.root = root
        self.calls = []

    async def run_one(self, execution_path: str) -> str:
        self.calls.append(execution_path)
        write_json_atomic(
            paths.knowledge_ledger_path(self.root),
            {
                "records": [
                    {
                        "execution_path": execution_path,
                        "output_path": f"outputs/{execution_path}/01.A.md",
                        "title": "A",
                        "node_ids": ["n1"],
                        "file_seq": "01",
                    }
                ]
            },
        )
        state_mgr = StateManager(paths.pipeline_state_path(self.root))
        state = state_mgr.load()
        state = state_mgr.complete_branch_execution(state, execution_path)
        state = state_mgr.update_branch_run(state, f"{execution_path}::run", status="complete")
        state_mgr.save(state)
        return "branch_complete"


class _ExplodingDagger:
    async def build(self, payload, *, timeout_sec=None):
        raise AssertionError("run() should not rebuild the planner after prepare_sources()")


async def test_tree_engine_run_schedules_ready_branch_and_finishes(tmp_path, monkeypatch):
    paths.ensure_workspace_dirs(tmp_path)
    write_json_atomic(
        paths.knowledge_nodes_path(tmp_path),
        envelope(
            schema="tree.knowledge-nodes",
            data={"knowledge_nodes": [{"node_id": "n1", "title": "A", "collections": ["课件"]}]},
        ),
    )
    write_json_atomic(
        paths.knowledge_dag_path(tmp_path),
        envelope(
            schema="tree.knowledge-dag",
            data={
                "nodes": [{"node_id": "n1", "title": "A", "collections": ["课件"]}],
                "edges": [],
                "roots": ["n1"],
            },
        ),
    )
    write_json_atomic(
        paths.knowledge_branches_path(tmp_path),
        envelope(
            schema="tree.knowledge-branches",
            data={
                "branches": [
                    {
                        "branch_id": "kb:1",
                        "node_ids": ["n1"],
                        "coverage_node_ids": ["n1"],
                        "start_node_id": "n1",
                        "end_node_id": "n1",
                        "upstream_branch_ids": [],
                        "downstream_branch_ids": [],
                        "display_order": 0,
                    }
                ]
            },
        ),
    )

    async def _noop_prepare(engine):
        return {"mtu_count": 0}

    monkeypatch.setattr("tree.engine.orchestrator.prepare_sources", _noop_prepare)

    runner = _FakeRunner(tmp_path)
    engine = TreeEngine(
        SimpleNamespace(project_root=tmp_path, max_active_branch_runs=1),
        branch_runner=runner,
        agents=SimpleNamespace(dagger=_ExplodingDagger()),
    )

    await engine.run()

    assert runner.calls == ["kb:1"]
    state = StateManager(paths.pipeline_state_path(tmp_path)).load()
    assert state.branch_executions[0].status == "completed"
    assert state.branch_runs[0].status == "complete"
    assert engine.progress.load()["phase"] == "complete"
