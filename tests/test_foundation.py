"""Smoke tests for the foundation layer (skeleton stage)."""

from __future__ import annotations

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
