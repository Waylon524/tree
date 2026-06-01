"""Smoke tests for the foundation layer (skeleton stage)."""

from __future__ import annotations

from tree.planner.branches import build_branches
from tree.planner.ids import normalize_concepts, normalize_text_key, prefixed_id
from tree.planner.store import artifact_hash, envelope, read_json, write_json_atomic


def test_prefixed_id_is_deterministic():
    a = prefixed_id("mtu", ["课件", "f.md", 1, 28])
    b = prefixed_id("mtu", ["课件", "f.md", 1, 28])
    assert a == b
    assert a.startswith("mtu:")
    assert a != prefixed_id("mtu", ["课件", "f.md", 1, 29])


def test_normalize_text_key_and_concepts():
    assert normalize_text_key("  Chemical  Equilibrium ") == "chemicalequilibrium"
    assert normalize_concepts(["平衡", "平衡", " 速率 "]) == ["平衡", "速率"]


def test_envelope_and_hash_roundtrip(tmp_path):
    env = envelope(schema="tree.test", data={"x": [1, 2, 3]})
    assert env["schema"] == "tree.test"
    assert artifact_hash(env) == artifact_hash(envelope(schema="tree.test", data={"x": [1, 2, 3]}))
    path = tmp_path / "a.json"
    write_json_atomic(path, env)
    assert read_json(path)["data"]["x"] == [1, 2, 3]


def test_build_branches_linear_chain():
    # n1 -> n2 -> n3  (single branch)
    dag = {
        "nodes": [
            {"node_id": "n1", "source_order_index": 0},
            {"node_id": "n2", "source_order_index": 1},
            {"node_id": "n3", "source_order_index": 2},
        ],
        "edges": [
            {"from_node_id": "n1", "to_node_id": "n2", "relation": "prerequisite"},
            {"from_node_id": "n2", "to_node_id": "n3", "relation": "prerequisite"},
        ],
    }
    result = build_branches(dag)
    assert len(result["branches"]) == 1
    assert result["branches"][0]["node_ids"] == ["n1", "n2", "n3"]


def test_build_branches_forks_into_two():
    # n1 -> n2 ; n1 -> n3  (branch point at n1 -> two child branches)
    dag = {
        "nodes": [
            {"node_id": "n1", "source_order_index": 0},
            {"node_id": "n2", "source_order_index": 1},
            {"node_id": "n3", "source_order_index": 2},
        ],
        "edges": [
            {"from_node_id": "n1", "to_node_id": "n2", "relation": "prerequisite"},
            {"from_node_id": "n1", "to_node_id": "n3", "relation": "prerequisite"},
        ],
    }
    branches = build_branches(dag)["branches"]
    starts = {b["start_node_id"] for b in branches}
    assert starts == {"n1", "n2", "n3"}
