"""Tests for the planner rebuild orchestration (step 6)."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from tree.io import paths
from tree.observability.progress import ProgressTracker
from tree.planner.mtu import build_mtus
from tree.planner.pipeline import load_dag, load_nodes, rebuild_planner

_SETTINGS = SimpleNamespace(
    dagger_build_timeout_sec=1.0, dagger_repair_attempts=0, dagger_max_nodes_per_call=400
)


class _EchoDagger:
    """Returns one singleton node per MTU, chained by prerequisite defines."""

    def __init__(self):
        self.calls = 0

    async def build_nodes(self, payload, *, timeout_sec=None):
        self.calls += 1
        metas = [p for p in payload if "mtu_id" in p]
        return {
            "nodes": [
                {
                    "title": p["title"],
                    "member_mtu_ids": [p["mtu_id"]],
                    "defines": [p["mtu_id"]],
                }
                for p in metas
            ]
        }

    async def build_prerequisites(self, payload, *, timeout_sec=None):
        nodes = list(payload.get("nodes") or [])
        prerequisites = []
        for index, node in enumerate(nodes):
            required = []
            if index > 0:
                required = [nodes[index - 1]["defines"][0]]
            prerequisites.append(
                {
                    "node_id": node["node_id"],
                    "required_defines": required,
                    "reason": "first node" if not required else "continues the previous node",
                }
            )
        return {"node_prerequisites": prerequisites}


class _RecordingProgress:
    def __init__(self, inner):
        self.inner = inner
        self.link_done_history = []

    def reset(self):
        return self.inner.reset()

    def load(self):
        return self.inner.load()

    def _record_link_done(self):
        done = self.inner.load()["stages"]["link"]["done"]
        if not self.link_done_history or self.link_done_history[-1] != done:
            self.link_done_history.append(done)

    def set_stage(self, stage, **kwargs):
        result = self.inner.set_stage(stage, **kwargs)
        if stage == "link":
            self._record_link_done()
        return result

    def add_stage_total(self, stage, amount, **kwargs):
        return self.inner.add_stage_total(stage, amount, **kwargs)

    def advance_stage(self, stage, **kwargs):
        result = self.inner.advance_stage(stage, **kwargs)
        if stage == "link":
            self._record_link_done()
        return result

    def complete_stage(self, stage, message=None):
        result = self.inner.complete_stage(stage, message)
        if stage == "link":
            self._record_link_done()
        return result


def _make_producer(counter):
    async def producer(root, material):
        counter["calls"] += 1
        units = [
            {"start_line": 1, "end_line": 31, "title": f"{material['source_file']}-A",
             "defines": ["k1"], "summary": "", "unit_kind": "concept"},
            {"start_line": 32, "end_line": 62, "title": f"{material['source_file']}-B",
             "defines": ["k2"], "summary": "", "unit_kind": "concept"},
        ]
        return build_mtus(units, collection=material["collection"], source_file=material["source_file"])

    return producer


def _make_chunked_producer(counter):
    async def producer(root, material):
        counter["calls"] += 1
        units = [
            {"start_line": 1, "end_line": 31, "title": "分块单元A",
             "defines": ["k1"], "summary": "", "unit_kind": "concept"},
            {"start_line": 1, "end_line": 31, "title": "分块单元B",
             "defines": ["k2"], "summary": "", "unit_kind": "concept"},
        ]
        return [
            *build_mtus(
                [units[0]],
                collection=material["collection"],
                source_file=f"{material['source_file']}.part-001",
            ),
            *build_mtus(
                [units[1]],
                collection=material["collection"],
                source_file=f"{material['source_file']}.part-002",
            ),
        ]

    return producer


def _seed_material(root):
    path = root / "materials" / "课件" / "ch1.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(f"l{i}" for i in range(1, 63)), encoding="utf-8")


async def test_rebuild_planner_writes_all_artifacts(tmp_path):
    _seed_material(tmp_path)
    counter = {"calls": 0}
    agents = SimpleNamespace(dagger=_EchoDagger())
    progress = _RecordingProgress(ProgressTracker(tmp_path))
    progress.reset()
    summary = await rebuild_planner(
        tmp_path, settings=_SETTINGS, agents=agents, mtu_producer=_make_producer(counter), progress=progress
    )

    assert summary["mtu_count"] == 2
    assert summary["node_count"] == 2
    assert summary["hard_edge_count"] == 1
    assert "branch_count" not in summary
    assert summary["dag_svg_path"].endswith("knowledge-dag.svg")
    assert counter["calls"] == 1

    for p in (
        paths.material_manifest_path(tmp_path),
        paths.mtus_path(tmp_path),
        paths.knowledge_nodes_path(tmp_path),
        paths.knowledge_dag_path(tmp_path),
        paths.knowledge_dag_svg_path(tmp_path),
        paths.outputs_root(tmp_path) / "knowledge-dag.svg",
    ):
        assert p.exists(), p

    assert len(load_nodes(tmp_path)) == 2
    assert len(load_dag(tmp_path)["edges"]) == 1
    assert "001." in paths.knowledge_dag_svg_path(tmp_path).read_text(encoding="utf-8")
    assert "001." in (paths.outputs_root(tmp_path) / "knowledge-dag.svg").read_text(encoding="utf-8")
    assert not (paths.planner_root(tmp_path) / "knowledge-branches.json").exists()
    stages = progress.load()["stages"]
    assert stages["ocr"]["done"] == 0
    assert stages["ocr"]["total"] == 1
    assert stages["clean"]["status"] == "complete"
    assert stages["cut"]["status"] == "complete"
    assert stages["cluster"]["status"] == "complete"
    assert stages["link"]["done"] == 2
    assert stages["link"]["status"] == "complete"
    assert progress.link_done_history == [0, 1, 2]


async def test_rebuild_planner_reuses_cache_when_unchanged(tmp_path):
    _seed_material(tmp_path)
    counter = {"calls": 0}
    agents = SimpleNamespace(dagger=_EchoDagger())
    await rebuild_planner(tmp_path, settings=_SETTINGS, agents=agents, mtu_producer=_make_producer(counter))
    assert counter["calls"] == 1

    # Second rebuild: nothing changed -> no producer needed, MTUs reused from cache.
    summary = await rebuild_planner(tmp_path, settings=_SETTINGS, agents=agents, mtu_producer=None)
    assert counter["calls"] == 1  # producer not called again
    assert summary["mtu_count"] == 2
    assert summary["node_count"] == 2


async def test_rebuild_planner_reuses_chunked_mtu_cache_when_unchanged(tmp_path):
    _seed_material(tmp_path)
    counter = {"calls": 0}
    agents = SimpleNamespace(dagger=_EchoDagger())
    await rebuild_planner(tmp_path, settings=_SETTINGS, agents=agents, mtu_producer=_make_chunked_producer(counter))
    assert counter["calls"] == 1

    summary = await rebuild_planner(tmp_path, settings=_SETTINGS, agents=agents, mtu_producer=None)

    assert summary["mtu_count"] == 2
    assert summary["node_count"] == 2


async def test_rebuild_planner_processes_changed_materials_with_configured_concurrency(tmp_path):
    for index in range(6):
        path = tmp_path / "materials" / "课件" / f"ch{index}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("l1\nl2", encoding="utf-8")

    active = 0
    max_active = 0
    started_five = asyncio.Event()
    release = asyncio.Event()

    async def producer(root, material):
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        if active == 5:
            started_five.set()
        try:
            await release.wait()
            units = [
                {
                    "start_line": 1,
                    "end_line": 31,
                    "title": material["source_file"],
                    "defines": ["k1"],
                    "summary": "",
                    "unit_kind": "concept",
                }
            ]
            return build_mtus(units, collection=material["collection"], source_file=material["source_file"])
        finally:
            active -= 1

    settings = SimpleNamespace(
        source_ingest_concurrency=5,
        dagger_build_timeout_sec=1.0,
        dagger_repair_attempts=0,
        dagger_max_nodes_per_call=400,
    )
    task = asyncio.create_task(
        rebuild_planner(tmp_path, settings=settings, agents=SimpleNamespace(dagger=_EchoDagger()), mtu_producer=producer)
    )
    try:
        await asyncio.wait_for(started_five.wait(), timeout=1)
        assert active == 5
        release.set()
        await task
    finally:
        release.set()
        if not task.done():
            task.cancel()

    assert max_active == 5
