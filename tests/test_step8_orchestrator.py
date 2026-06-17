"""Tests for Step 8 foreground engine orchestration."""

from __future__ import annotations

import asyncio
import json
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


def _seed_dag(root, nodes, edges=None):
    write_json_atomic(
        paths.knowledge_nodes_path(root),
        envelope(schema="tree.knowledge-nodes", data={"knowledge_nodes": nodes}),
    )
    write_json_atomic(
        paths.knowledge_dag_path(root),
        envelope(
            schema="tree.knowledge-dag",
            data={
                "nodes": nodes,
                "edges": edges or [],
                "roots": [node["node_id"] for node in nodes],
            },
        ),
    )


async def test_tree_engine_run_schedules_ready_node_and_finishes(tmp_path, monkeypatch):
    paths.ensure_workspace_dirs(tmp_path)
    _seed_dag(tmp_path, [{"node_id": "n1", "title": "A", "collections": ["课件"]}])

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


async def test_tree_engine_refills_node_pool_after_each_node_completion(tmp_path, monkeypatch):
    paths.ensure_workspace_dirs(tmp_path)
    _seed_dag(
        tmp_path,
        [
            {"node_id": "n1", "title": "A", "collections": ["课件"], "source_order_index": 0},
            {"node_id": "n2", "title": "B", "collections": ["课件"], "source_order_index": 1},
            {"node_id": "n3", "title": "C", "collections": ["课件"], "source_order_index": 2},
        ],
    )

    async def _noop_prepare(engine):
        return {"mtu_count": 0}

    monkeypatch.setattr("tree.engine.orchestrator.prepare_sources", _noop_prepare)

    events: list[tuple[str, str, int, tuple[str, ...]]] = []
    active = 0
    max_seen_active = 0
    unblock = asyncio.Event()

    class _RollingRunner:
        async def run_one(self, node_id: str) -> str:
            nonlocal active, max_seen_active
            active += 1
            max_seen_active = max(max_seen_active, active)
            events.append(("start", node_id, engine.progress.load()["stages"]["noderun"]["done"], tuple()))
            if node_id in {"n2", "n3"}:
                await unblock.wait()
            state_mgr = StateManager(paths.pipeline_state_path(tmp_path))
            write_json_atomic(
                paths.knowledge_ledger_path(tmp_path),
                {
                    "records": [
                        *(
                            []
                            if not paths.knowledge_ledger_path(tmp_path).exists()
                            else json.loads(paths.knowledge_ledger_path(tmp_path).read_text(encoding="utf-8")).get("records", [])
                        ),
                        {
                            "node_id": node_id,
                            "node_ids": [node_id],
                            "output_path": f"outputs/{node_id}.md",
                            "title": node_id,
                            "file_seq": node_id,
                        },
                    ]
                },
            )
            state = state_mgr.load()
            state = state_mgr.complete_node_execution(state, node_id)
            state = state_mgr.update_node_run(state, f"{node_id}::run", status="complete")
            state_mgr.save(state)
            active -= 1
            events.append(("finish", node_id, engine.progress.load()["stages"]["noderun"]["done"], tuple()))
            if node_id == "n1":
                await asyncio.sleep(0)
            return "node_complete"

    engine = TreeEngine(
        SimpleNamespace(project_root=tmp_path, max_active_node_runs=2),
        node_runner=_RollingRunner(),
        agents=SimpleNamespace(dagger=_ExplodingDagger()),
    )

    task = asyncio.create_task(engine.run())
    try:
        await asyncio.wait_for(_wait_until(lambda: any(event[:2] == ("start", "n3") for event in events)), timeout=1)
        noderun = engine.progress.load()["stages"]["noderun"]
        started = [event[1] for event in events if event[0] == "start"]

        assert started == ["n1", "n2", "n3"]
        assert max_seen_active <= 2
        assert noderun["done"] == 1
        assert noderun["status"] == "running"
    finally:
        unblock.set()
        await task
    assert engine.progress.load()["stages"]["noderun"]["done"] == 3


async def test_tree_engine_starts_child_only_after_parent_is_covered(tmp_path, monkeypatch):
    paths.ensure_workspace_dirs(tmp_path)
    _seed_dag(
        tmp_path,
        [
            {"node_id": "parent", "title": "Parent", "collections": ["课件"], "source_order_index": 0},
            {"node_id": "child", "title": "Child", "collections": ["课件"], "source_order_index": 1},
        ],
        edges=[{"from_node_id": "parent", "to_node_id": "child", "relation": "prerequisite"}],
    )

    async def _noop_prepare(engine):
        return {"mtu_count": 0}

    monkeypatch.setattr("tree.engine.orchestrator.prepare_sources", _noop_prepare)

    calls: list[str] = []

    class _PrereqRunner:
        async def run_one(self, node_id: str) -> str:
            calls.append(node_id)
            state_mgr = StateManager(paths.pipeline_state_path(tmp_path))
            previous = (
                []
                if not paths.knowledge_ledger_path(tmp_path).exists()
                else json.loads(paths.knowledge_ledger_path(tmp_path).read_text(encoding="utf-8")).get("records", [])
            )
            write_json_atomic(
                paths.knowledge_ledger_path(tmp_path),
                {
                    "records": [
                        *previous,
                        {
                            "node_id": node_id,
                            "node_ids": [node_id],
                            "output_path": f"outputs/{node_id}.md",
                            "title": node_id,
                            "file_seq": node_id,
                        },
                    ]
                },
            )
            state = state_mgr.load()
            state = state_mgr.complete_node_execution(state, node_id)
            state = state_mgr.update_node_run(state, f"{node_id}::run", status="complete")
            state_mgr.save(state)
            return "node_complete"

    engine = TreeEngine(
        SimpleNamespace(project_root=tmp_path, max_active_node_runs=2),
        node_runner=_PrereqRunner(),
        agents=SimpleNamespace(dagger=_ExplodingDagger()),
    )

    await engine.run()

    assert calls == ["parent", "child"]


async def _wait_until(predicate):
    while not predicate():
        await asyncio.sleep(0.01)


def test_clear_stale_run_logs_removes_only_pipeline_temp_logs(tmp_path):
    from tree.engine.orchestrator import _clear_stale_run_logs

    temp = paths.pipeline_temp_root(tmp_path)
    temp.mkdir(parents=True, exist_ok=True)
    (temp / "examiner-1.log").write_text("old error\n", encoding="utf-8")
    (temp / "keep.json").write_text("{}", encoding="utf-8")

    _clear_stale_run_logs(tmp_path)

    assert not (temp / "examiner-1.log").exists()
    assert (temp / "keep.json").exists()  # only *.log files are cleared
