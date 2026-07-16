"""Tests for Dagger DAG canonicalization + cycle breaking (step 5 program side)."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from tree.agents.prompts import DAGGER_PREREQUISITES_PROMPT, DAGGER_PROMPT
from tree.model.budget import PromptBudgetExceededError
from tree.observability.retry import LLMOutputTruncatedError
from tree.planner.cluster import build_candidate_clusters
from tree.planner.dag import (
    _build_nodes_batched,
    _build_nodes_with_repair,
    _find_cycle,
    _validate_node_replacements,
    _validate_prerequisites,
    break_cycles,
    build_dag,
)
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


async def test_dagger_coverage_payload_splits_when_token_budget_is_exceeded():
    class BudgetedDagger:
        def __init__(self):
            self.batch_sizes = []

        async def build_nodes(self, payload, *, timeout_sec=None):
            metas = [item for item in payload if "mtu_id" in item]
            self.batch_sizes.append(len(metas))
            if len(metas) > 2:
                raise PromptBudgetExceededError(
                    role="dagger",
                    estimated_input_tokens=2_000,
                    input_budget_tokens=1_000,
                    context_window=2_000,
                    reserved_output_tokens=800,
                    safety_tokens=200,
                )
            return {
                "nodes": [
                    {
                        "title": item["title"],
                        "member_mtu_ids": [item["mtu_id"]],
                        "defines": item["defines"],
                    }
                    for item in metas
                ]
            }

    dagger = BudgetedDagger()
    payload = [
        {"mtu_id": f"m{index}", "title": f"Node {index}", "defines": [f"d{index}"]}
        for index in range(5)
    ]

    nodes = await _build_nodes_with_repair(dagger, payload, timeout=1.0, repair=0)

    assert [node["member_mtu_ids"][0] for node in nodes] == ["m0", "m1", "m2", "m3", "m4"]
    assert dagger.batch_sizes == [5, 2, 3, 1, 2]


async def test_dagger_coverage_payload_splits_when_output_is_truncated():
    class TruncatedDagger:
        def __init__(self):
            self.batch_sizes = []

        async def build_nodes(self, payload, *, timeout_sec=None):
            metas = [item for item in payload if "mtu_id" in item]
            self.batch_sizes.append(len(metas))
            if len(metas) > 2:
                raise LLMOutputTruncatedError("finish_reason=length")
            return {
                "nodes": [
                    {
                        "title": item["title"],
                        "member_mtu_ids": [item["mtu_id"]],
                        "defines": item["defines"],
                    }
                    for item in metas
                ]
            }

    dagger = TruncatedDagger()
    payload = [
        {"mtu_id": f"m{index}", "title": f"Node {index}", "defines": [f"d{index}"]}
        for index in range(5)
    ]

    nodes = await _build_nodes_with_repair(dagger, payload, timeout=1.0, repair=3)

    assert [node["member_mtu_ids"][0] for node in nodes] == ["m0", "m1", "m2", "m3", "m4"]
    assert dagger.batch_sizes == [5, 2, 3, 1, 2]


async def test_dagger_collection_batches_respect_configured_node_limit():
    class RecordingDagger:
        def __init__(self):
            self.batch_sizes = []

        async def build_nodes(self, payload, *, timeout_sec=None):
            metas = [item for item in payload if "mtu_id" in item]
            self.batch_sizes.append(len(metas))
            return {
                "nodes": [
                    {
                        "title": item["title"],
                        "member_mtu_ids": [item["mtu_id"]],
                        "defines": item["defines"],
                    }
                    for item in metas
                ]
            }

    dagger = RecordingDagger()
    mtus = [_mtu(f"mtu:{index}", f"N{index}", "same", index, [f"D{index}"]) for index in range(5)]

    nodes = await _build_nodes_batched(
        dagger,
        mtus,
        timeout=1.0,
        repair=0,
        progress=None,
        max_nodes=2,
    )

    assert dagger.batch_sizes == [2, 2, 1]
    assert sorted(member for node in nodes for member in node["member_mtu_ids"]) == [
        "mtu:0",
        "mtu:1",
        "mtu:2",
        "mtu:3",
        "mtu:4",
    ]


class _FakeAgent:
    def __init__(self, response):
        self.response = response
        self.calls = 0

    async def build(self, payload, *, timeout_sec=None):
        self.calls += 1
        return self.response


class _DefinesAgent:
    def __init__(self, *, nodes, prerequisites=None, repairs=None):
        self.nodes = nodes
        self.prerequisites = prerequisites or []
        self.repairs = repairs or []
        self.node_calls = 0
        self.prerequisite_calls = 0
        self.repair_calls = 0
        self.repair_payloads = []

    async def build_nodes(self, payload, *, timeout_sec=None):
        self.node_calls += 1
        return {"nodes": self.nodes}

    async def build_prerequisites(self, payload, *, timeout_sec=None):
        self.prerequisite_calls += 1
        target = payload.get("target_node") or {}
        selected = [
            item
            for item in self.prerequisites
            if item.get("node_id") == target.get("node_id")
            or item.get("node_title") == target.get("title")
            or item.get("title") == target.get("title")
        ]
        return {"node_prerequisites": [_with_decision(item) for item in selected]}

    async def repair_defines(self, payload, *, timeout_sec=None):
        self.repair_calls += 1
        self.repair_payloads.append(payload)
        if not self.repairs:
            return {"nodes": self.nodes}
        return {"nodes": self.repairs.pop(0)}

    async def repair_prerequisites(self, payload, *, timeout_sec=None):
        self.repair_calls += 1
        if not self.repairs:
            return {"node_prerequisites": [_with_decision(item) for item in self.prerequisites]}
        return {"node_prerequisites": [_with_decision(item) for item in self.repairs.pop(0)]}


def _with_decision(item):
    value = dict(item)
    value.setdefault(
        "internal_prerequisite_decision",
        "selected" if value.get("required_defines") else "none",
    )
    return value


_SETTINGS = SimpleNamespace(
    dagger_build_timeout_sec=1.0, dagger_repair_attempts=0, dagger_max_nodes_per_call=400
)


class _ProgressSpy:
    def __init__(self):
        self.stages = {}
        self.link_done_history = []
        self.link_active_history = []

    def set_stage(self, stage, **kwargs):
        data = self.stages.setdefault(stage, {"done": 0, "total": 0, "active": []})
        data.update(kwargs)
        if stage == "link":
            self._record_link(data)

    def advance_stage(self, stage, step=1, **kwargs):
        data = self.stages.setdefault(stage, {"done": 0, "total": 0, "active": []})
        data["done"] = data.get("done", 0) + step
        data.update(kwargs)
        if data.get("total") and data["done"] >= data["total"]:
            data["status"] = "complete"
            data["active"] = []
        if stage == "link":
            self._record_link(data)

    def complete_stage(self, stage, message=None):
        data = self.stages.setdefault(stage, {"done": 0, "total": 0, "active": []})
        if data.get("total"):
            data["done"] = data["total"]
        data["status"] = "complete"
        data["active"] = []
        if message is not None:
            data["message"] = message
        if stage == "link":
            self._record_link(data)

    def _record_link(self, data):
        done = data.get("done", 0)
        if not self.link_done_history or self.link_done_history[-1] != done:
            self.link_done_history.append(done)
        active = tuple(data.get("active") or [])
        if active:
            self.link_active_history.append(active)


async def test_build_dag_merges_duplicates_and_resolves_edges():
    mtus = [
        _mtu("mtu:1", "化学平衡状态", "课件", 0),
        _mtu("mtu:2", "化学平衡常数", "课件", 1),
        _mtu("mtu:3", "平衡常数考点", "作业", 2),
    ]
    agent = _DefinesAgent(
        nodes=[
            {"title": "化学平衡状态", "member_mtu_ids": ["mtu:1"], "defines": ["化学平衡状态"]},
            {"title": "化学平衡常数", "member_mtu_ids": ["mtu:2", "mtu:3"], "defines": ["平衡常数"]},
        ],
        prerequisites=[
            {
                "node_title": "化学平衡状态",
                "required_defines": [],
                "reason": "本章基础起点。",
            },
            {
                "node_title": "化学平衡常数",
                "required_defines": ["化学平衡状态"],
                "reason": "平衡常数依赖平衡状态。",
            },
        ],
    )
    dag = await build_dag(agent, mtus, settings=_SETTINGS)
    assert len(dag["nodes"]) == 2
    merged = next(n for n in dag["nodes"] if set(n["member_mtu_ids"]) == {"mtu:2", "mtu:3"})
    assert set(merged["collections"]) == {"课件", "作业"}
    assert merged["defines"] == ["平衡常数"]
    assert len(dag["edges"]) == 1
    assert dag["edges"][0]["relation"] == "prerequisite"
    assert dag["edges"][0]["required_defines"] == ["化学平衡状态"]
    assert len(dag["roots"]) == 1  # only the prerequisite root
    assert not dag["diagnostics"]


async def test_build_dag_keeps_unassigned_mtu_as_singleton():
    mtus = [_mtu("mtu:1", "A", "c", 0), _mtu("mtu:2", "B", "c", 1, keywords=["B"])]
    agent = _DefinesAgent(
        nodes=[{"title": "A", "member_mtu_ids": ["mtu:1"], "defines": ["A"]}],
        prerequisites=[
            {"node_title": "A", "required_defines": [], "reason": "基础节点。"},
            {"node_title": "B", "required_defines": ["A"], "reason": "B follows A."},
        ],
    )
    dag = await build_dag(agent, mtus, settings=_SETTINGS)
    assert len(dag["nodes"]) == 2
    assert any(d["reason_code"] == "mtu_unassigned" and d["mtu_id"] == "mtu:2" for d in dag["diagnostics"])


async def test_build_dag_empty():
    dag = await build_dag(_DefinesAgent(nodes=[]), [], settings=_SETTINGS)
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


def test_dagger_prompts_use_defines_and_required_defines_not_edges():
    assert "defines" in DAGGER_PROMPT
    assert "REFINE_NODE_CLUSTER" in DAGGER_PROMPT
    assert "exactly one `REFINE_NODE_CLUSTER` candidate cluster" in DAGGER_PROMPT
    assert "Judge only that one cluster" in DAGGER_PROMPT
    assert "embedding similarity, shared MTU defines, or both" in DAGGER_PROMPT
    assert "selected exactly from the original" in DAGGER_PROMPT
    assert "remove the conflicting define" in DAGGER_PROMPT
    assert "delete the generic define" in DAGGER_PROMPT
    assert "Do not output `keywords`" in DAGGER_PROMPT
    assert "at most 8" in DAGGER_PROMPT
    assert "summary" not in DAGGER_PROMPT
    assert "edges" not in DAGGER_PROMPT
    assert "required_defines" in DAGGER_PREREQUISITES_PROMPT
    assert "external_prerequisites" in DAGGER_PREREQUISITES_PROMPT
    assert "at most 24" in DAGGER_PREREQUISITES_PROMPT
    assert "higher-level" in DAGGER_PREREQUISITES_PROMPT
    assert "closest to the current node" in DAGGER_PREREQUISITES_PROMPT
    assert "smallest necessary change" in DAGGER_PREREQUISITES_PROMPT
    assert "does not need to be a linear or total order" in DAGGER_PREREQUISITES_PROMPT


def test_node_defines_are_limited_to_eight():
    _validate_node_replacements(
        [{"title": "A", "member_mtu_ids": ["mtu:1"], "defines": [f"D{i}" for i in range(8)]}],
        {"mtu:1"},
        label="node",
        require_all_members=True,
    )

    with pytest.raises(ValueError, match="defines exceeds 8"):
        _validate_node_replacements(
            [{"title": "A", "member_mtu_ids": ["mtu:1"], "defines": [f"D{i}" for i in range(9)]}],
            {"mtu:1"},
            label="node",
            require_all_members=True,
        )


def test_required_defines_are_limited_to_twenty_four():
    nodes = [{"node_id": "kn:a", "title": "A"}]
    define_dictionary = {f"D{i}": {"node_id": "kn:src"} for i in range(25)}
    _validate_prerequisites(
        [{
            "node_id": "kn:a",
            "internal_prerequisite_decision": "selected",
            "required_defines": [f"D{i}" for i in range(24)],
            "reason": "needs them",
        }],
        nodes,
        define_dictionary,
    )

    with pytest.raises(ValueError, match="required_defines exceeds 24"):
        _validate_prerequisites(
            [{
                "node_id": "kn:a",
                "internal_prerequisite_decision": "selected",
                "required_defines": [f"D{i}" for i in range(25)],
                "reason": "too many",
            }],
            nodes,
            define_dictionary,
        )


def test_prerequisite_decision_must_match_required_defines():
    nodes = [{"node_id": "kn:a", "title": "A"}]
    with pytest.raises(ValueError, match="declared no prerequisites"):
        _validate_prerequisites(
            [{
                "node_id": "kn:a",
                "internal_prerequisite_decision": "none",
                "required_defines": ["D"],
                "reason": "contradictory",
            }],
            nodes,
            {"D": {"defined_by": ["kn:source"]}},
        )


def test_prerequisite_rejects_self_only_define():
    nodes = [{"node_id": "kn:a", "title": "A"}]
    with pytest.raises(ValueError, match="only defined by itself"):
        _validate_prerequisites(
            [{
                "node_id": "kn:a",
                "internal_prerequisite_decision": "selected",
                "required_defines": ["A"],
                "reason": "self dependency",
            }],
            nodes,
            {"A": {"defined_by": ["kn:a"]}},
        )


def test_full_prerequisite_set_requires_unique_coverage_of_every_node():
    nodes = [
        {"node_id": "kn:a", "title": "A"},
        {"node_id": "kn:b", "title": "B"},
    ]
    root = {
        "node_id": "kn:a",
        "internal_prerequisite_decision": "none",
        "required_defines": [],
        "reason": "foundational",
    }

    with pytest.raises(ValueError, match="missing prerequisite blocks"):
        _validate_prerequisites(
            [root],
            nodes,
            {},
            require_all_nodes=True,
        )
    with pytest.raises(ValueError, match="duplicate prerequisite block"):
        _validate_prerequisites(
            [root, root],
            nodes,
            {},
            require_all_nodes=True,
        )


async def test_empty_prerequisite_response_is_repaired_not_treated_as_root():
    mtu = _mtu("mtu:1", "A", "c", 0)

    class _RepairingAgent:
        def __init__(self):
            self.prerequisite_calls = 0

        async def build_nodes(self, payload, *, timeout_sec=None):
            return {"nodes": [{"title": "A", "member_mtu_ids": ["mtu:1"], "defines": ["A"]}]}

        async def build_prerequisites(self, payload, *, timeout_sec=None):
            self.prerequisite_calls += 1
            if self.prerequisite_calls == 1:
                return {"node_prerequisites": []}
            return {
                "node_prerequisites": [{
                    "node_id": payload["target_node"]["node_id"],
                    "internal_prerequisite_decision": "none",
                    "required_defines": [],
                    "reason": "This is the first foundational concept in the material.",
                }]
            }

    agent = _RepairingAgent()
    dag = await build_dag(
        agent,
        [mtu],
        settings=SimpleNamespace(**{**_SETTINGS.__dict__, "dagger_repair_attempts": 1}),
    )

    assert agent.prerequisite_calls == 2
    assert dag["roots"] == [dag["nodes"][0]["node_id"]]


async def test_all_root_graph_gets_one_global_review_without_forcing_edges():
    mtus = [
        _mtu("mtu:1", "A", "c", 0, keywords=["A"]),
        _mtu("mtu:2", "B", "c", 1, keywords=["B"]),
        _mtu("mtu:3", "C", "c", 2, keywords=["C"]),
    ]

    class ReviewingAgent:
        def __init__(self):
            self.prerequisite_calls = 0

        async def build_nodes(self, payload, *, timeout_sec=None):
            return {
                "nodes": [
                    {
                        "title": item["title"],
                        "member_mtu_ids": [item["mtu_id"]],
                        "defines": item["defines"],
                    }
                    for item in payload
                    if "mtu_id" in item
                ]
            }

        async def build_prerequisites(self, payload, *, timeout_sec=None):
            self.prerequisite_calls += 1
            target = payload["target_node"]
            review = "Global graph review" in payload["instructions"]
            required = []
            if review and target["title"] == "B":
                required = ["A"]
            elif review and target["title"] == "C":
                required = ["B"]
            return {
                "node_prerequisites": [
                    {
                        "node_id": target["node_id"],
                        "internal_prerequisite_decision": "selected" if required else "none",
                        "required_defines": required,
                        "reason": "reviewed against all definitions",
                    }
                ]
            }

    agent = ReviewingAgent()

    dag = await build_dag(agent, mtus, settings=_SETTINGS)

    assert agent.prerequisite_calls == 6
    assert len(dag["roots"]) == 1
    review = next(
        item for item in dag["diagnostics"] if item["reason_code"] == "high_root_ratio_reviewed"
    )
    assert review["roots_before"] == 3
    assert review["roots_after"] == 1


async def test_build_dag_fails_closed_when_prerequisite_agent_is_unusable():
    class _BadAgent:
        async def build(self, payload, *, timeout_sec=None):
            raise ValueError("bad json")

    mtus = [_mtu("mtu:1", "A", "c", 0), _mtu("mtu:2", "B", "c", 1)]
    with pytest.raises(ValueError, match="Dagger node build failed"):
        await build_dag(_BadAgent(), mtus, settings=_SETTINGS)


async def test_build_dag_records_external_prerequisites_without_self_dependency():
    mtus = [_mtu("mtu:1", "A", "c", 0), _mtu("mtu:2", "B", "c", 1)]
    agent = _DefinesAgent(
        nodes=[
            {"title": "A", "member_mtu_ids": ["mtu:1"], "defines": ["A"]},
            {"title": "B", "member_mtu_ids": ["mtu:2"], "defines": ["B"]},
        ],
        prerequisites=[
                {
                    "node_title": "A",
                    "required_defines": [],
                    "reason": "A is a material-internal root",
                    "external_prerequisites": ["代数"],
            },
            {"node_title": "B", "required_defines": ["A"], "reason": "B needs A"},
        ],
    )

    dag = await build_dag(agent, mtus, settings=_SETTINGS)

    assert [(e["from_node_id"], e["to_node_id"]) for e in dag["edges"]] == [
        (dag["nodes"][0]["node_id"], dag["nodes"][1]["node_id"])
    ]
    assert dag["nodes"][0]["external_prerequisites"] == ["代数"]


async def test_build_dag_keeps_required_define_evidence_on_single_node_edge():
    mtus = [_mtu("mtu:1", "A", "c", 0), _mtu("mtu:2", "C", "c", 1)]
    agent = _DefinesAgent(
        nodes=[
            {"title": "A", "member_mtu_ids": ["mtu:1"], "defines": ["相干光", "光程差"]},
            {"title": "C", "member_mtu_ids": ["mtu:2"], "defines": ["双缝干涉"]},
        ],
        prerequisites=[
            {"node_title": "A", "required_defines": [], "reason": "基础节点。"},
            {"node_title": "C", "required_defines": ["相干光", "光程差"], "reason": "C needs both."},
        ],
    )

    dag = await build_dag(agent, mtus, settings=_SETTINGS)

    assert len(dag["edges"]) == 1
    assert dag["edges"][0]["required_defines"] == ["相干光", "光程差"]


async def test_build_dag_fails_when_prerequisites_remain_invalid_after_retries():
    mtus = [_mtu("mtu:1", "A", "c", 0), _mtu("mtu:2", "B", "c", 1)]
    agent = _DefinesAgent(
        nodes=[
            {"title": "A", "member_mtu_ids": ["mtu:1"], "defines": ["A"]},
            {"title": "B", "member_mtu_ids": ["mtu:2"], "defines": ["B"]},
        ],
        prerequisites=[
            {"node_title": "A", "required_defines": [], "reason": "基础节点。"},
            {"node_title": "B", "required_defines": ["missing"], "reason": "invalid define"},
        ],
    )
    settings = SimpleNamespace(**{**_SETTINGS.__dict__, "dagger_repair_attempts": 1})

    with pytest.raises(ValueError, match="Dagger prerequisites remain invalid"):
        await build_dag(agent, mtus, settings=settings)


async def test_build_dag_prunes_transitive_ancestor_edges_to_terminal_parent():
    mtus = [_mtu("mtu:1", "A", "c", 0), _mtu("mtu:2", "B", "c", 1), _mtu("mtu:3", "C", "c", 2)]
    agent = _DefinesAgent(
        nodes=[
            {"title": "A", "member_mtu_ids": ["mtu:1"], "defines": ["A"]},
            {"title": "B", "member_mtu_ids": ["mtu:2"], "defines": ["B"]},
            {"title": "C", "member_mtu_ids": ["mtu:3"], "defines": ["C"]},
        ],
        prerequisites=[
            {"node_title": "A", "required_defines": [], "reason": "基础节点。"},
            {"node_title": "B", "required_defines": ["A"], "reason": "B needs A."},
            {"node_title": "C", "required_defines": ["A", "B"], "reason": "C needs the chain."},
        ],
    )

    dag = await build_dag(agent, mtus, settings=_SETTINGS)
    titles_by_id = {n["node_id"]: n["title"] for n in dag["nodes"]}
    edges = {(titles_by_id[e["from_node_id"]], titles_by_id[e["to_node_id"]]) for e in dag["edges"]}

    assert edges == {("A", "B"), ("B", "C")}


async def test_build_dag_fails_duplicate_defines_when_repair_exhausted():
    mtus = [_mtu("mtu:1", "A", "c", 0), _mtu("mtu:2", "B", "c", 1), _mtu("mtu:3", "C", "c", 2)]
    agent = _DefinesAgent(
        nodes=[
            {"title": "A", "member_mtu_ids": ["mtu:1"], "defines": ["模型"]},
            {"title": "B", "member_mtu_ids": ["mtu:2"], "defines": ["模型"]},
            {"title": "C", "member_mtu_ids": ["mtu:3"], "defines": ["C"]},
        ],
        prerequisites=[
            {"node_title": "A", "required_defines": [], "reason": "基础节点。"},
            {"node_title": "B", "required_defines": [], "reason": "基础节点。"},
            {"node_title": "C", "required_defines": ["模型"], "reason": "C compares both models."},
        ],
    )

    with pytest.raises(ValueError, match="defines conflict remains"):
        await build_dag(agent, mtus, settings=_SETTINGS)


async def test_build_dag_allows_short_base_define_inside_specific_formula():
    mtus = [_mtu("mtu:1", "光程", "c", 0), _mtu("mtu:2", "等厚干涉", "c", 1)]
    agent = _DefinesAgent(
        nodes=[
            {"title": "光程", "member_mtu_ids": ["mtu:1"], "defines": ["光程"]},
            {"title": "等厚干涉", "member_mtu_ids": ["mtu:2"], "defines": ["等厚干涉光程差公式"]},
        ],
        prerequisites=[
            {"node_title": "光程", "required_defines": [], "reason": "基础节点。"},
            {"node_title": "等厚干涉", "required_defines": ["光程"], "reason": "公式使用光程。"},
        ],
    )

    dag = await build_dag(agent, mtus, settings=_SETTINGS)

    assert len(dag["nodes"]) == 2


async def test_build_dag_allows_base_concept_inside_specific_attribute_define():
    mtus = [
        _mtu("mtu:1", "简谐振动", "c", 0, keywords=["简谐振动"]),
        _mtu("mtu:2", "简谐振动能量", "c", 1, keywords=["简谐振动的动能"]),
    ]
    agent = _DefinesAgent(
        nodes=[
            {"title": "简谐振动", "member_mtu_ids": ["mtu:1"], "defines": ["简谐振动"]},
            {"title": "简谐振动能量", "member_mtu_ids": ["mtu:2"], "defines": ["简谐振动的动能"]},
        ],
        prerequisites=[
            {"node_title": "简谐振动", "required_defines": [], "reason": "基础节点。"},
            {"node_title": "简谐振动能量", "required_defines": ["简谐振动"], "reason": "能量分析依赖简谐振动定义。"},
        ],
    )

    dag = await build_dag(agent, mtus, settings=SimpleNamespace(**{**_SETTINGS.__dict__, "dagger_repair_attempts": 1}))

    assert len(dag["nodes"]) == 2
    assert agent.repair_calls == 0


async def test_build_dag_allows_bare_define_inside_scoped_de_define():
    mtus = [
        _mtu("mtu:1", "分辨本领", "c", 0, keywords=["分辨本领"]),
        _mtu("mtu:2", "光栅分辨本领", "c", 1, keywords=["光栅的分辨本领"]),
    ]
    agent = _DefinesAgent(
        nodes=[
            {"title": "分辨本领", "member_mtu_ids": ["mtu:1"], "defines": ["分辨本领"]},
            {"title": "光栅分辨本领", "member_mtu_ids": ["mtu:2"], "defines": ["光栅的分辨本领"]},
        ],
        prerequisites=[
            {"node_title": "分辨本领", "required_defines": [], "reason": "基础节点。"},
            {
                "node_title": "光栅分辨本领",
                "required_defines": ["分辨本领"],
                "reason": "语境化概念依赖基础表述。",
            },
        ],
    )

    dag = await build_dag(agent, mtus, settings=SimpleNamespace(**{**_SETTINGS.__dict__, "dagger_repair_attempts": 1}))

    assert len(dag["nodes"]) == 2
    assert agent.repair_calls == 0


async def test_build_dag_still_repairs_exact_duplicate_scoped_de_defines():
    mtus = [
        _mtu("mtu:1", "几何光学反射一", "c", 0, keywords=["几何光学的反射定律"]),
        _mtu("mtu:2", "几何光学反射二", "c", 1, keywords=["几何光学的反射定律"]),
    ]
    agent = _DefinesAgent(
        nodes=[
            {"title": "几何光学反射一", "member_mtu_ids": ["mtu:1"], "defines": ["几何光学的反射定律"]},
            {"title": "几何光学反射二", "member_mtu_ids": ["mtu:2"], "defines": ["几何光学的反射定律"]},
        ],
        repairs=[
            [
                {
                    "title": "几何光学反射定律",
                    "member_mtu_ids": ["mtu:1", "mtu:2"],
                    "defines": ["几何光学的反射定律"],
                },
            ]
        ],
        prerequisites=[
            {"node_title": "几何光学反射定律", "required_defines": [], "reason": "基础节点。"},
        ],
    )

    dag = await build_dag(agent, mtus, settings=SimpleNamespace(**{**_SETTINGS.__dict__, "dagger_repair_attempts": 1}))

    assert len(dag["nodes"]) == 1
    assert agent.repair_calls == 1


async def test_build_dag_repairs_duplicate_defines_by_merging_nodes():
    mtus = [
        _mtu("mtu:1", "A1", "c", 0, keywords=["光程差"]),
        _mtu("mtu:2", "A2", "c", 1, keywords=["光程差"]),
        _mtu("mtu:3", "B", "c", 2, keywords=["干涉"]),
    ]
    agent = _DefinesAgent(
        nodes=[
            {"title": "A1", "member_mtu_ids": ["mtu:1"], "defines": ["光程差"]},
            {"title": "A2", "member_mtu_ids": ["mtu:2"], "defines": ["光程差"]},
            {"title": "B", "member_mtu_ids": ["mtu:3"], "defines": ["干涉"]},
        ],
        repairs=[
            [
                {"title": "光程差", "member_mtu_ids": ["mtu:1", "mtu:2"], "defines": ["光程差"]},
            ]
        ],
        prerequisites=[
            {"node_title": "光程差", "required_defines": [], "reason": "基础节点。"},
            {"node_title": "B", "required_defines": ["光程差"], "reason": "B needs it."},
        ],
    )

    dag = await build_dag(agent, mtus, settings=SimpleNamespace(**{**_SETTINGS.__dict__, "dagger_repair_attempts": 1}))

    assert len(dag["nodes"]) == 2
    assert agent.repair_calls == 1
    assert {n["title"] for n in agent.repair_payloads[0]["nodes"]} == {"A1", "A2"}
    assert set(agent.repair_payloads[0]["candidate_member_mtu_ids"]) == {"mtu:1", "mtu:2"}


async def test_build_dag_repairs_contained_defines_by_remerging_pair():
    mtus = [
        _mtu("mtu:1", "总论", "c", 0, keywords=["分辨本领", "瑞利判据"]),
        _mtu("mtu:2", "专题", "c", 1, keywords=["光栅分辨本领", "光栅角色散分辨能力"]),
    ]
    agent = _DefinesAgent(
        nodes=[
            {"title": "总论", "member_mtu_ids": ["mtu:1"], "defines": ["分辨本领"]},
            {"title": "专题", "member_mtu_ids": ["mtu:2"], "defines": ["光栅分辨本领"]},
        ],
        repairs=[
            [
                {"title": "总论", "member_mtu_ids": ["mtu:1"], "defines": ["瑞利判据"]},
                {"title": "专题", "member_mtu_ids": ["mtu:2"], "defines": ["光栅角色散分辨能力"]},
            ]
        ],
        prerequisites=[
            {"node_title": "总论", "required_defines": [], "reason": "基础节点。"},
            {"node_title": "专题", "required_defines": ["瑞利判据"], "reason": "专题使用判据。"},
        ],
    )

    dag = await build_dag(agent, mtus, settings=SimpleNamespace(**{**_SETTINGS.__dict__, "dagger_repair_attempts": 1}))

    assert len(dag["nodes"]) == 2
    assert agent.repair_calls == 1
    assert {node["title"]: node["defines"] for node in dag["nodes"]} == {
        "总论": ["瑞利判据"],
        "专题": ["光栅角色散分辨能力"],
    }
    assert mtus[0].keywords == ["分辨本领", "瑞利判据"]


async def test_build_dag_retries_empty_define_pairwise_repair():
    mtus = [
        _mtu("mtu:1", "总论", "c", 0, keywords=["分辨本领", "瑞利判据"]),
        _mtu("mtu:2", "专题", "c", 1, keywords=["光栅分辨本领", "光栅角色散分辨能力"]),
    ]
    agent = _DefinesAgent(
        nodes=[
            {"title": "总论", "member_mtu_ids": ["mtu:1"], "defines": ["分辨本领"]},
            {"title": "专题", "member_mtu_ids": ["mtu:2"], "defines": ["光栅分辨本领"]},
        ],
        repairs=[
            [
                {"title": "总论", "member_mtu_ids": ["mtu:1"], "defines": []},
                {"title": "专题", "member_mtu_ids": ["mtu:2"], "defines": ["光栅分辨本领"]},
            ],
            [
                {"title": "总论", "member_mtu_ids": ["mtu:1"], "defines": ["瑞利判据"]},
                {"title": "专题", "member_mtu_ids": ["mtu:2"], "defines": ["光栅角色散分辨能力"]},
            ],
        ],
        prerequisites=[
            {"node_title": "总论", "required_defines": [], "reason": "基础节点。"},
            {"node_title": "专题", "required_defines": ["瑞利判据"], "reason": "专题使用判据。"},
        ],
    )

    dag = await build_dag(agent, mtus, settings=SimpleNamespace(**{**_SETTINGS.__dict__, "dagger_repair_attempts": 1}))

    assert len(dag["nodes"]) == 2
    assert agent.repair_calls == 2


async def test_build_dag_repairs_define_conflicts_pairwise_and_rechecks():
    mtus = [
        _mtu("mtu:1", "A", "c", 0, keywords=["模型"]),
        _mtu("mtu:2", "B", "c", 1, keywords=["模型"]),
        _mtu("mtu:3", "C", "c", 2, keywords=["模型"]),
    ]
    agent = _DefinesAgent(
        nodes=[
            {"title": "A", "member_mtu_ids": ["mtu:1"], "defines": ["模型"]},
            {"title": "B", "member_mtu_ids": ["mtu:2"], "defines": ["模型"]},
            {"title": "C", "member_mtu_ids": ["mtu:3"], "defines": ["模型"]},
        ],
        repairs=[
            [
                {"title": "AB", "member_mtu_ids": ["mtu:1", "mtu:2"], "defines": ["模型"]},
            ],
            [
                {"title": "ABC", "member_mtu_ids": ["mtu:1", "mtu:2", "mtu:3"], "defines": ["模型"]},
            ],
        ],
        prerequisites=[
            {"node_title": "ABC", "required_defines": [], "reason": "基础节点。"},
        ],
    )

    dag = await build_dag(agent, mtus, settings=SimpleNamespace(**{**_SETTINGS.__dict__, "dagger_repair_attempts": 1}))

    assert len(dag["nodes"]) == 1
    assert agent.repair_calls == 2
    assert [set(p["candidate_member_mtu_ids"]) for p in agent.repair_payloads] == [
        {"mtu:1", "mtu:2"},
        {"mtu:1", "mtu:2", "mtu:3"},
    ]


async def test_build_dag_retries_invalid_pairwise_define_repair():
    mtus = [
        _mtu("mtu:1", "A", "c", 0, keywords=["模型"]),
        _mtu("mtu:2", "B", "c", 1, keywords=["模型"]),
        _mtu("mtu:3", "C", "c", 2, keywords=["C"]),
    ]
    agent = _DefinesAgent(
        nodes=[
            {"title": "A", "member_mtu_ids": ["mtu:1"], "defines": ["模型"]},
            {"title": "B", "member_mtu_ids": ["mtu:2"], "defines": ["模型"]},
            {"title": "C", "member_mtu_ids": ["mtu:3"], "defines": ["C"]},
        ],
        repairs=[
            [
                {"title": "AB", "member_mtu_ids": ["mtu:1", "mtu:3"], "defines": ["模型"]},
            ],
            [
                {"title": "AB", "member_mtu_ids": ["mtu:1", "mtu:2"], "defines": ["模型"]},
            ],
        ],
        prerequisites=[
            {"node_title": "AB", "required_defines": [], "reason": "基础节点。"},
            {"node_title": "C", "required_defines": [], "reason": "基础节点。"},
        ],
    )

    dag = await build_dag(agent, mtus, settings=SimpleNamespace(**{**_SETTINGS.__dict__, "dagger_repair_attempts": 1}))

    assert len(dag["nodes"]) == 2
    assert agent.repair_calls == 2


def test_embedding_candidate_clusters_can_cross_collections():
    mtus = [
        _mtu("mtu:1", "A", "课件", 0),
        _mtu("mtu:2", "A practice", "习题", 1),
        _mtu("mtu:3", "B", "课件", 2),
    ]

    clusters = build_candidate_clusters(
        mtus,
        {"mtu:1": [1, 0], "mtu:2": [0.99, 0.01], "mtu:3": [0, 1]},
        similarity_threshold=0.95,
        top_k=2,
        max_size=8,
    )

    merged = next(cluster for cluster in clusters if set(cluster["member_mtu_ids"]) == {"mtu:1", "mtu:2"})
    assert merged["cross_collection"] is True
    assert merged["collections"] == ["习题", "课件"]
    assert "embedding" in merged["cluster_reasons"]


def test_candidate_clusters_link_mtus_with_same_normalized_define():
    mtus = [
        _mtu("mtu:1", "A", "课件", 0, keywords=["光 程-差"]),
        _mtu("mtu:2", "B", "习题", 1, keywords=["光程差"]),
        _mtu("mtu:3", "C", "课件", 2, keywords=["衍射"]),
    ]

    clusters = build_candidate_clusters(
        mtus,
        {"mtu:1": [1, 0], "mtu:2": [0, 1], "mtu:3": [-1, 0]},
        similarity_threshold=0.99,
        top_k=2,
        max_size=8,
    )

    shared = next(cluster for cluster in clusters if set(cluster["member_mtu_ids"]) == {"mtu:1", "mtu:2"})
    assert shared["cross_collection"] is True
    assert shared["cluster_reasons"] == ["shared_define"]
    assert set(shared["shared_defines"]) == {"光 程-差", "光程差"}


def test_candidate_clusters_mix_embedding_and_shared_define_edges():
    mtus = [
        _mtu("mtu:1", "A", "课件", 0, keywords=["A"]),
        _mtu("mtu:2", "B", "课件", 1, keywords=["B", "shared"]),
        _mtu("mtu:3", "C", "习题", 2, keywords=["shared"]),
    ]

    clusters = build_candidate_clusters(
        mtus,
        {"mtu:1": [1, 0], "mtu:2": [0.99, 0.01], "mtu:3": [0, 1]},
        similarity_threshold=0.95,
        top_k=1,
        max_size=8,
    )

    cluster = next(item for item in clusters if set(item["member_mtu_ids"]) == {"mtu:1", "mtu:2", "mtu:3"})
    assert cluster["cross_collection"] is True
    assert cluster["cluster_reasons"] == ["embedding", "shared_define"]
    assert cluster["shared_defines"] == ["shared"]


async def test_build_dag_refines_embedding_candidate_clusters_with_dagger():
    mtus = [
        _mtu("mtu:1", "A", "课件", 0, keywords=["A"]),
        _mtu("mtu:2", "A practice", "习题", 1, keywords=["A应用"]),
        _mtu("mtu:3", "B", "课件", 2, keywords=["B"]),
    ]

    class _ClusterAgent:
        def __init__(self):
            self.node_payloads = []

        async def build_nodes(self, payload, *, timeout_sec=None):
            self.node_payloads.append(payload)
            cluster = payload[0]
            assert cluster["task"] == "REFINE_NODE_CLUSTER"
            assert cluster["cross_collection"] is True
            assert "embedding" in cluster["cluster_reasons"]
            return {
                "nodes": [
                    {
                        "title": "A",
                        "member_mtu_ids": cluster["candidate_member_mtu_ids"],
                        "defines": ["A"],
                    }
                ]
            }

        async def build_prerequisites(self, payload, *, timeout_sec=None):
            return {
                "node_prerequisites": [
                    {
                        "node_id": node["node_id"],
                        "internal_prerequisite_decision": "none",
                        "required_defines": [],
                        "reason": "foundational node",
                    }
                    for node in [payload["target_node"]]
                ]
            }

    settings = SimpleNamespace(
        **{
            **_SETTINGS.__dict__,
            "dagger_embed_cluster_enabled": True,
            "dagger_cluster_similarity_threshold": 0.95,
            "dagger_cluster_top_k": 2,
            "dagger_cluster_max_size": 8,
            "dagger_cluster_auto_accept_singleton": True,
            "dagger_cluster_auto_accept_same_collection": False,
        }
    )
    agent = _ClusterAgent()

    dag = await build_dag(
        agent,
        mtus,
        settings=settings,
        vector_provider=lambda ids: {
            "mtu:1": [1, 0],
            "mtu:2": [0.99, 0.01],
            "mtu:3": [0, 1],
        },
    )

    assert len(agent.node_payloads) == 1
    assert any(set(node["member_mtu_ids"]) == {"mtu:1", "mtu:2"} for node in dag["nodes"])
    assert any(node["member_mtu_ids"] == ["mtu:3"] for node in dag["nodes"])


async def test_build_dag_rejects_cluster_refinement_with_invented_define():
    mtus = [
        _mtu("mtu:1", "A", "课件", 0, keywords=["A"]),
        _mtu("mtu:2", "A practice", "习题", 1, keywords=["A应用"]),
    ]

    class _InventedDefineAgent:
        async def build_nodes(self, payload, *, timeout_sec=None):
            cluster = payload[0]
            return {
                "nodes": [
                    {
                        "title": "A",
                        "member_mtu_ids": cluster["candidate_member_mtu_ids"],
                        "defines": ["自造define"],
                    }
                ]
            }

    settings = SimpleNamespace(
        **{
            **_SETTINGS.__dict__,
            "dagger_embed_cluster_enabled": True,
            "dagger_cluster_similarity_threshold": 0.95,
            "dagger_cluster_top_k": 2,
            "dagger_cluster_max_size": 8,
            "dagger_cluster_auto_accept_singleton": True,
            "dagger_cluster_auto_accept_same_collection": False,
        }
    )

    with pytest.raises(ValueError, match="defines must be selected from its member MTU defines"):
        await build_dag(
            _InventedDefineAgent(),
            mtus,
            settings=settings,
            vector_provider=lambda ids: {"mtu:1": [1, 0], "mtu:2": [0.99, 0.01]},
        )


async def test_build_dag_allows_cluster_split_with_member_mtu_defines_only():
    mtus = [
        _mtu("mtu:1", "A", "课件", 0, keywords=["A"]),
        _mtu("mtu:2", "B", "习题", 1, keywords=["B"]),
    ]

    class _SplitClusterAgent:
        async def build_nodes(self, payload, *, timeout_sec=None):
            return {
                "nodes": [
                    {"title": "A", "member_mtu_ids": ["mtu:1"], "defines": ["A"]},
                    {"title": "B", "member_mtu_ids": ["mtu:2"], "defines": ["B"]},
                ]
            }

        async def build_prerequisites(self, payload, *, timeout_sec=None):
            return {
                "node_prerequisites": [
                    {
                        "node_id": node["node_id"],
                        "internal_prerequisite_decision": "none",
                        "required_defines": [],
                        "reason": "foundational node",
                    }
                    for node in [payload["target_node"]]
                ]
            }

    settings = SimpleNamespace(
        **{
            **_SETTINGS.__dict__,
            "dagger_embed_cluster_enabled": True,
            "dagger_cluster_similarity_threshold": 0.95,
            "dagger_cluster_top_k": 2,
            "dagger_cluster_max_size": 8,
            "dagger_cluster_auto_accept_singleton": True,
            "dagger_cluster_auto_accept_same_collection": False,
        }
    )

    dag = await build_dag(
        _SplitClusterAgent(),
        mtus,
        settings=settings,
        vector_provider=lambda ids: {"mtu:1": [1, 0], "mtu:2": [0.99, 0.01]},
    )

    assert {node["title"] for node in dag["nodes"]} == {"A", "B"}


async def test_build_dag_links_prerequisites_with_bounded_concurrency_and_sequence_active_labels():
    mtus = [_mtu(f"mtu:{index}", f"N{index}", "课件", index, keywords=[f"D{index}"]) for index in range(5)]
    release = asyncio.Event()

    class _ConcurrentPrereqAgent:
        def __init__(self):
            self.active = 0
            self.max_active = 0
            self.calls = []
            self.started = asyncio.Event()

        async def build_nodes(self, payload, *, timeout_sec=None):
            return {
                "nodes": [
                    {"title": item["title"], "member_mtu_ids": [item["mtu_id"]], "defines": item["defines"]}
                    for item in payload
                ]
            }

        async def build_prerequisites(self, payload, *, timeout_sec=None):
            target = payload["target_node"]
            self.calls.append(target["node_id"])
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            if self.max_active >= 3:
                self.started.set()
            await release.wait()
            self.active -= 1
            required = []
            if target["source_order_index"] > 0:
                required = [f"D{target['source_order_index'] - 1}"]
            return {
                "node_prerequisites": [
                    {
                        "node_id": target["node_id"],
                        "internal_prerequisite_decision": "selected" if required else "none",
                        "required_defines": required,
                        "reason": "ordered prerequisite",
                    }
                ]
            }

    agent = _ConcurrentPrereqAgent()
    progress = _ProgressSpy()
    settings = SimpleNamespace(
        **{
            **_SETTINGS.__dict__,
            "dagger_prerequisite_concurrency": 3,
        }
    )

    task = asyncio.create_task(build_dag(agent, mtus, settings=settings, progress=progress))
    await asyncio.wait_for(agent.started.wait(), timeout=1)

    assert agent.max_active == 3
    assert ("001", "002", "003") in progress.link_active_history
    assert not any("N0" in item for active in progress.link_active_history for item in active)

    release.set()
    dag = await task

    assert agent.max_active == 3
    assert progress.link_done_history == [0, 1, 2, 3, 4, 5]
    assert [call for call in agent.calls[:3]]
    assert [(edge["from_node_id"], edge["to_node_id"]) for edge in dag["edges"]] == [
        (dag["nodes"][0]["node_id"], dag["nodes"][1]["node_id"]),
        (dag["nodes"][1]["node_id"], dag["nodes"][2]["node_id"]),
        (dag["nodes"][2]["node_id"], dag["nodes"][3]["node_id"]),
        (dag["nodes"][3]["node_id"], dag["nodes"][4]["node_id"]),
    ]


async def test_build_dag_calls_dagger_once_per_candidate_cluster():
    mtus = [
        _mtu("mtu:1", "A1", "课件", 0, keywords=["A1"]),
        _mtu("mtu:2", "A2", "习题", 1, keywords=["A2"]),
        _mtu("mtu:3", "B1", "课件", 2, keywords=["B1"]),
        _mtu("mtu:4", "B2", "习题", 3, keywords=["B2"]),
    ]

    class _PerClusterAgent:
        def __init__(self):
            self.node_payloads = []

        async def build_nodes(self, payload, *, timeout_sec=None):
            self.node_payloads.append(payload)
            assert len(payload) == 1
            cluster = payload[0]
            assert cluster["task"] == "REFINE_NODE_CLUSTER"
            return {
                "nodes": [
                    {
                        "title": cluster["mtus"][0]["title"],
                        "member_mtu_ids": cluster["candidate_member_mtu_ids"],
                        "defines": [cluster["mtus"][0]["defines"][0]],
                    }
                ]
            }

        async def build_prerequisites(self, payload, *, timeout_sec=None):
            return {
                "node_prerequisites": [
                    {
                        "node_id": node["node_id"],
                        "internal_prerequisite_decision": "none",
                        "required_defines": [],
                        "reason": "foundational node",
                    }
                    for node in [payload["target_node"]]
                ]
            }

    settings = SimpleNamespace(
        **{
            **_SETTINGS.__dict__,
            "dagger_embed_cluster_enabled": True,
            "dagger_cluster_similarity_threshold": 0.95,
            "dagger_cluster_top_k": 2,
            "dagger_cluster_max_size": 8,
            "dagger_cluster_auto_accept_singleton": True,
            "dagger_cluster_auto_accept_same_collection": False,
        }
    )
    agent = _PerClusterAgent()

    await build_dag(
        agent,
        mtus,
        settings=settings,
        vector_provider=lambda ids: {
            "mtu:1": [1, 0],
            "mtu:2": [0.99, 0.01],
            "mtu:3": [0, 1],
            "mtu:4": [0.01, 0.99],
        },
    )

    assert len(agent.node_payloads) == 2
    assert all(len(payload) == 1 for payload in agent.node_payloads)


async def test_build_dag_sends_same_collection_multi_mtu_cluster_to_dagger_even_when_auto_accept_enabled():
    mtus = [
        _mtu("mtu:1", "A1", "课件", 0, keywords=["A1"]),
        _mtu("mtu:2", "A2", "课件", 1, keywords=["A2"]),
    ]

    class _SameCollectionAgent:
        def __init__(self):
            self.node_payloads = []

        async def build_nodes(self, payload, *, timeout_sec=None):
            self.node_payloads.append(payload)
            cluster = payload[0]
            assert cluster["task"] == "REFINE_NODE_CLUSTER"
            return {
                "nodes": [
                    {
                        "title": "A",
                        "member_mtu_ids": cluster["candidate_member_mtu_ids"],
                        "defines": ["A1", "A2"],
                    }
                ]
            }

        async def build_prerequisites(self, payload, *, timeout_sec=None):
            return {
                "node_prerequisites": [
                    {
                        "node_id": node["node_id"],
                        "internal_prerequisite_decision": "none",
                        "required_defines": [],
                        "reason": "foundational node",
                    }
                    for node in [payload["target_node"]]
                ]
            }

    settings = SimpleNamespace(
        **{
            **_SETTINGS.__dict__,
            "dagger_embed_cluster_enabled": True,
            "dagger_cluster_similarity_threshold": 0.95,
            "dagger_cluster_top_k": 2,
            "dagger_cluster_max_size": 8,
            "dagger_cluster_auto_accept_singleton": True,
            "dagger_cluster_auto_accept_same_collection": True,
        }
    )
    agent = _SameCollectionAgent()

    dag = await build_dag(
        agent,
        mtus,
        settings=settings,
        vector_provider=lambda ids: {"mtu:1": [1, 0], "mtu:2": [0.99, 0.01]},
    )

    assert len(agent.node_payloads) == 1
    assert [set(node["member_mtu_ids"]) for node in dag["nodes"]] == [{"mtu:1", "mtu:2"}]


async def test_build_dag_merges_node_without_defines_into_previous_same_collection():
    mtus = [_mtu("mtu:1", "A", "c", 0), _mtu("mtu:2", "Example", "c", 1), _mtu("mtu:3", "B", "c", 2)]
    agent = _DefinesAgent(
        nodes=[
            {"title": "A", "member_mtu_ids": ["mtu:1"], "defines": ["A"]},
            {"title": "Example", "member_mtu_ids": ["mtu:2"], "defines": []},
            {"title": "B", "member_mtu_ids": ["mtu:3"], "defines": ["B"]},
        ],
        prerequisites=[
            {"node_title": "A", "required_defines": [], "reason": "基础节点。"},
            {"node_title": "B", "required_defines": ["A"], "reason": "B follows A."},
        ],
    )

    dag = await build_dag(agent, mtus, settings=_SETTINGS)

    assert len(dag["nodes"]) == 2
    assert any(set(node["member_mtu_ids"]) == {"mtu:1", "mtu:2"} for node in dag["nodes"])
    assert any(d["reason_code"] == "node_without_defines_merged" for d in dag["diagnostics"])


async def test_build_dag_merges_first_node_without_defines_into_next_same_collection():
    mtus = [_mtu("mtu:1", "Example", "c", 0), _mtu("mtu:2", "A", "c", 1)]
    agent = _DefinesAgent(
        nodes=[
            {"title": "Example", "member_mtu_ids": ["mtu:1"], "defines": []},
            {"title": "A", "member_mtu_ids": ["mtu:2"], "defines": ["A"]},
        ],
        prerequisites=[
            {"node_title": "A", "required_defines": [], "reason": "基础节点。"},
        ],
    )

    dag = await build_dag(agent, mtus, settings=_SETTINGS)

    assert len(dag["nodes"]) == 1
    assert set(dag["nodes"][0]["member_mtu_ids"]) == {"mtu:1", "mtu:2"}


async def test_build_dag_repairs_cycle_with_minimal_edge_removal():
    mtus = [_mtu("mtu:1", "A", "c", 0), _mtu("mtu:2", "B", "c", 1)]
    agent = _DefinesAgent(
        nodes=[
            {"title": "A", "member_mtu_ids": ["mtu:1"], "defines": ["A"]},
            {"title": "B", "member_mtu_ids": ["mtu:2"], "defines": ["B"]},
        ],
        prerequisites=[
            {"node_title": "A", "required_defines": ["B"], "reason": "bad cycle"},
            {"node_title": "B", "required_defines": ["A"], "reason": "bad cycle"},
        ],
        repairs=[
            [
                {"node_title": "A", "required_defines": [], "reason": "A is first."},
                {"node_title": "B", "required_defines": ["A"], "reason": "B follows A."},
            ]
        ],
    )

    dag = await build_dag(agent, mtus, settings=SimpleNamespace(**{**_SETTINGS.__dict__, "dagger_repair_attempts": 1}))

    titles_by_id = {n["node_id"]: n["title"] for n in dag["nodes"]}
    assert [(titles_by_id[e["from_node_id"]], titles_by_id[e["to_node_id"]]) for e in dag["edges"]] == [("A", "B")]
    assert agent.repair_calls == 1


async def test_cycle_repair_preserves_unrelated_root_and_parallel_branch():
    mtus = [
        _mtu("mtu:1", "A", "c", 0),
        _mtu("mtu:2", "B", "c", 1),
        _mtu("mtu:3", "C", "c", 2),
        _mtu("mtu:4", "D", "c", 3),
    ]
    agent = _DefinesAgent(
        nodes=[
            {"title": "A", "member_mtu_ids": ["mtu:1"], "defines": ["A"]},
            {"title": "B", "member_mtu_ids": ["mtu:2"], "defines": ["B"]},
            {"title": "C", "member_mtu_ids": ["mtu:3"], "defines": ["C"]},
            {"title": "D", "member_mtu_ids": ["mtu:4"], "defines": ["D"]},
        ],
        prerequisites=[
            {"node_title": "A", "required_defines": ["B"], "reason": "bad cycle"},
            {"node_title": "B", "required_defines": ["A"], "reason": "bad cycle"},
            {"node_title": "C", "required_defines": [], "reason": "independent root"},
            {"node_title": "D", "required_defines": ["C"], "reason": "parallel branch"},
        ],
        repairs=[
            [
                {"node_title": "A", "required_defines": [], "reason": "remove one cycle edge"},
                {"node_title": "B", "required_defines": ["A"], "reason": "keep valid edge"},
                {"node_title": "C", "required_defines": [], "reason": "independent root"},
                {"node_title": "D", "required_defines": ["C"], "reason": "parallel branch"},
            ]
        ],
    )

    dag = await build_dag(
        agent,
        mtus,
        settings=SimpleNamespace(**{**_SETTINGS.__dict__, "dagger_repair_attempts": 1}),
    )

    titles_by_id = {n["node_id"]: n["title"] for n in dag["nodes"]}
    edges = {
        (titles_by_id[edge["from_node_id"]], titles_by_id[edge["to_node_id"]])
        for edge in dag["edges"]
    }
    roots = {titles_by_id[node_id] for node_id in dag["roots"]}
    assert edges == {("A", "B"), ("C", "D")}
    assert roots == {"A", "C"}


async def test_cycle_repair_rejects_changes_to_non_cycle_nodes():
    mtus = [
        _mtu("mtu:1", "A", "c", 0),
        _mtu("mtu:2", "B", "c", 1),
        _mtu("mtu:3", "C", "c", 2),
    ]
    cyclic = [
        {"node_title": "A", "required_defines": ["B"], "reason": "bad cycle"},
        {"node_title": "B", "required_defines": ["A"], "reason": "bad cycle"},
        {"node_title": "C", "required_defines": [], "reason": "independent root"},
    ]
    agent = _DefinesAgent(
        nodes=[
            {"title": "A", "member_mtu_ids": ["mtu:1"], "defines": ["A"]},
            {"title": "B", "member_mtu_ids": ["mtu:2"], "defines": ["B"]},
            {"title": "C", "member_mtu_ids": ["mtu:3"], "defines": ["C"]},
        ],
        prerequisites=cyclic,
        repairs=[
            [
                {"node_title": "A", "required_defines": [], "reason": "remove cycle edge"},
                {"node_title": "B", "required_defines": ["A"], "reason": "keep edge"},
                {"node_title": "C", "required_defines": ["A"], "reason": "invalid new edge"},
            ]
        ],
    )

    dag = await build_dag(
        agent,
        mtus,
        settings=SimpleNamespace(**{**_SETTINGS.__dict__, "dagger_repair_attempts": 1}),
    )

    titles_by_id = {n["node_id"]: n["title"] for n in dag["nodes"]}
    assert all(titles_by_id[edge["to_node_id"]] != "C" for edge in dag["edges"])
    assert "C" in {titles_by_id[node_id] for node_id in dag["roots"]}
    assert any(item["reason_code"] == "cycle_edges_removed" for item in dag["diagnostics"])


async def test_build_dag_drops_cycle_edge_when_llm_repairs_are_exhausted():
    mtus = [_mtu("mtu:1", "A", "c", 0), _mtu("mtu:2", "B", "c", 1)]
    cyclic = [
        {"node_title": "A", "required_defines": ["B"], "reason": "bad cycle"},
        {"node_title": "B", "required_defines": ["A"], "reason": "bad cycle"},
    ]
    agent = _DefinesAgent(
        nodes=[
            {"title": "A", "member_mtu_ids": ["mtu:1"], "defines": ["A"]},
            {"title": "B", "member_mtu_ids": ["mtu:2"], "defines": ["B"]},
        ],
        prerequisites=cyclic,
        repairs=[cyclic],
    )

    dag = await build_dag(
        agent,
        mtus,
        settings=SimpleNamespace(**{**_SETTINGS.__dict__, "dagger_repair_attempts": 1}),
    )

    assert len(dag["edges"]) == 1
    assert not _find_cycle({node["node_id"] for node in dag["nodes"]}, dag["edges"])
    diagnostic = next(d for d in dag["diagnostics"] if d["reason_code"] == "cycle_edges_removed")
    assert len(diagnostic["removed_edges"]) == 1
