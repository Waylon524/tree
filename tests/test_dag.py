"""Tests for Dagger DAG canonicalization + cycle breaking (step 5 program side)."""

from __future__ import annotations

from types import SimpleNamespace

from tree.planner.dag import break_cycles, build_dag
from tree.planner.models import MTU


def _mtu(mtu_id, title, collection, order, keywords=None):
    return MTU(
        mtu_id=mtu_id,
        collection=collection,
        source_file=f"{collection}.md",
        line_range=(1, 2),
        title=title,
        keywords=keywords or [],
        summary="",
        unit_kind="concept",
        source_order_index=order,
    )


class _FakeAgent:
    def __init__(self, response):
        self.response = response
        self.calls = 0

    async def build(self, payload, *, timeout_sec=None):
        self.calls += 1
        return self.response


_SETTINGS = SimpleNamespace(
    dagger_build_timeout_sec=1.0, dagger_repair_attempts=0, dagger_max_nodes_per_call=400
)


async def test_build_dag_merges_duplicates_and_resolves_edges():
    mtus = [
        _mtu("mtu:1", "化学平衡状态", "课件", 0),
        _mtu("mtu:2", "化学平衡常数", "课件", 1),
        _mtu("mtu:3", "平衡常数考点", "作业", 2),
    ]
    response = {
        "nodes": [
            {"title": "化学平衡状态", "member_mtu_ids": ["mtu:1"], "keywords": ["平衡"]},
            {"title": "化学平衡常数", "member_mtu_ids": ["mtu:2", "mtu:3"], "keywords": ["K"]},
        ],
        "edges": [
            {"from_title": "化学平衡状态", "to_title": "化学平衡常数",
             "relation": "prerequisite", "confidence": 0.9}
        ],
    }
    dag = await build_dag(_FakeAgent(response), mtus, settings=_SETTINGS)
    assert len(dag["nodes"]) == 2
    merged = next(n for n in dag["nodes"] if set(n["member_mtu_ids"]) == {"mtu:2", "mtu:3"})
    assert set(merged["collections"]) == {"课件", "作业"}
    assert len(dag["edges"]) == 1
    assert dag["edges"][0]["relation"] == "prerequisite"
    assert len(dag["roots"]) == 1  # only the prerequisite root
    assert not dag["diagnostics"]


async def test_build_dag_keeps_unassigned_mtu_as_singleton():
    mtus = [_mtu("mtu:1", "A", "c", 0), _mtu("mtu:2", "B", "c", 1)]
    response = {"nodes": [{"title": "A", "member_mtu_ids": ["mtu:1"]}], "edges": []}
    dag = await build_dag(_FakeAgent(response), mtus, settings=_SETTINGS)
    assert len(dag["nodes"]) == 2
    assert any(d["reason_code"] == "mtu_unassigned" and d["mtu_id"] == "mtu:2" for d in dag["diagnostics"])


async def test_build_dag_empty():
    dag = await build_dag(_FakeAgent({"nodes": [], "edges": []}), [], settings=_SETTINGS)
    assert dag == {"nodes": [], "edges": [], "roots": [], "diagnostics": []}


def test_break_cycles_drops_weakest_edge():
    node_ids = {"a", "b", "c"}
    edges = [
        {"from_node_id": "a", "to_node_id": "b", "relation": "prerequisite", "confidence": 0.9},
        {"from_node_id": "b", "to_node_id": "c", "relation": "prerequisite", "confidence": 0.8},
        {"from_node_id": "c", "to_node_id": "a", "relation": "prerequisite", "confidence": 0.5},
    ]
    result = break_cycles(node_ids, edges)
    assert len(result) == 2
    assert ("c", "a") not in {(e["from_node_id"], e["to_node_id"]) for e in result}


def test_break_cycles_keeps_acyclic_graph():
    node_ids = {"a", "b"}
    edges = [{"from_node_id": "a", "to_node_id": "b", "relation": "prerequisite", "confidence": 0.9}]
    assert break_cycles(node_ids, edges) == edges


async def test_build_dag_falls_back_when_llm_unusable():
    # Agent raises -> _build_with_repair returns empty -> all MTUs become singletons.
    class _BadAgent:
        async def build(self, payload, *, timeout_sec=None):
            raise ValueError("bad json")

    mtus = [_mtu("mtu:1", "A", "c", 0), _mtu("mtu:2", "B", "c", 1)]
    dag = await build_dag(_BadAgent(), mtus, settings=_SETTINGS)
    assert len(dag["nodes"]) == 2  # singletons
    assert len(dag["diagnostics"]) == 2
