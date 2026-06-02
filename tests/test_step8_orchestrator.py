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

    async def run_one(self, node_id: str) -> str:
        self.calls.append(node_id)
        write_json_atomic(
            paths.knowledge_ledger_path(self.root),
            {
                "records": [
                    {
                        "node_id": node_id,
                        "output_path": f"outputs/{node_id}.A.md",
                        "title": "A",
                        "node_ids": ["n1"],
                        "file_seq": "01",
                    }
                ]
            },
        )
        state_mgr = StateManager(paths.pipeline_state_path(self.root))
        state = state_mgr.load()
        state = state_mgr.complete_node_execution(state, node_id)
        state = state_mgr.update_node_run(state, f"{node_id}::run", status="complete")
        state_mgr.save(state)
        return "node_complete"


class _ExplodingDagger:
    async def build(self, payload, *, timeout_sec=None):
        raise AssertionError("run() should not rebuild the planner after prepare_sources()")


async def test_tree_engine_run_schedules_ready_node_and_finishes(tmp_path, monkeypatch):
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

    async def _noop_prepare(engine):
        return {"mtu_count": 0}

    monkeypatch.setattr("tree.engine.orchestrator.prepare_sources", _noop_prepare)

    runner = _FakeRunner(tmp_path)
    engine = TreeEngine(
        SimpleNamespace(project_root=tmp_path, max_active_node_runs=1),
        node_runner=runner,
        agents=SimpleNamespace(dagger=_ExplodingDagger()),
    )

    await engine.run()

    assert runner.calls == ["n1"]
    state = StateManager(paths.pipeline_state_path(tmp_path)).load()
    assert state.node_executions[0].status == "completed"
    assert state.node_runs[0].status == "complete"
    assert engine.progress.load()["phase"] == "complete"
    noderun = engine.progress.load()["stages"]["noderun"]
    assert noderun["done"] == 1
    assert noderun["total"] == 1
    assert noderun["status"] == "complete"
