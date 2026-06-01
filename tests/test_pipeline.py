"""Tests for the planner rebuild orchestration (step 6)."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from tree.io import paths
from tree.planner.mtu import build_mtus
from tree.planner.pipeline import load_branches, load_dag, load_nodes, rebuild_planner

_SETTINGS = SimpleNamespace(
    dagger_build_timeout_sec=1.0, dagger_repair_attempts=0, dagger_max_nodes_per_call=400
)


class _EchoDagger:
    """Returns one singleton node per MTU, chained by prerequisite edges."""

    def __init__(self):
        self.calls = 0

    async def build(self, payload, *, timeout_sec=None):
        self.calls += 1
        metas = [p for p in payload if "mtu_id" in p]
        nodes = [
            {"title": p["title"], "member_mtu_ids": [p["mtu_id"]], "keywords": p.get("keywords", [])}
            for p in metas
        ]
        edges = [
            {"from_title": a["title"], "to_title": b["title"], "relation": "prerequisite", "confidence": 0.9}
            for a, b in zip(metas, metas[1:])
        ]
        return {"nodes": nodes, "edges": edges}


def _make_producer(counter):
    async def producer(root, material):
        counter["calls"] += 1
        units = [
            {"start_line": 1, "end_line": 2, "title": f"{material['source_file']}-A",
             "keywords": ["k1"], "summary": "", "unit_kind": "concept"},
            {"start_line": 3, "end_line": 4, "title": f"{material['source_file']}-B",
             "keywords": ["k2"], "summary": "", "unit_kind": "concept"},
        ]
        return build_mtus(units, collection=material["collection"], source_file=material["source_file"])

    return producer


def _make_chunked_producer(counter):
    async def producer(root, material):
        counter["calls"] += 1
        units = [
            {"start_line": 1, "end_line": 2, "title": "分块单元A",
             "keywords": ["k1"], "summary": "", "unit_kind": "concept"},
            {"start_line": 1, "end_line": 2, "title": "分块单元B",
             "keywords": ["k2"], "summary": "", "unit_kind": "concept"},
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
    path.write_text("l1\nl2\nl3\nl4", encoding="utf-8")


async def test_rebuild_planner_writes_all_artifacts(tmp_path):
    _seed_material(tmp_path)
    counter = {"calls": 0}
    agents = SimpleNamespace(dagger=_EchoDagger())
    summary = await rebuild_planner(
        tmp_path, settings=_SETTINGS, agents=agents, mtu_producer=_make_producer(counter)
    )

    assert summary["mtu_count"] == 2
    assert summary["node_count"] == 2
    assert summary["hard_edge_count"] == 1
    assert summary["branch_count"] == 1
    assert counter["calls"] == 1

    for p in (
        paths.material_manifest_path(tmp_path),
        paths.mtus_path(tmp_path),
        paths.knowledge_nodes_path(tmp_path),
        paths.knowledge_dag_path(tmp_path),
        paths.knowledge_branches_path(tmp_path),
    ):
        assert p.exists(), p

    assert len(load_nodes(tmp_path)) == 2
    assert len(load_dag(tmp_path)["edges"]) == 1
    assert len(load_branches(tmp_path)) == 1


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
                    "end_line": 2,
                    "title": material["source_file"],
                    "keywords": ["k1"],
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
