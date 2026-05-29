# Deterministic Tree Planner Refactor Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans or follow this plan task-by-task. Each phase must leave TREE runnable.

**Goal:** Move direction control from examiner to a deterministic tree planner. TREE should compute the best knowledge growth tree first, use AI only for low-confidence boundary cases, and ask examiner to write exams for the planner-selected frontier node.

**Architecture:** Source inventory and curriculum map still produce candidate knowledge nodes. A deterministic planner computes pairwise distances, builds a maximum-spanning backbone tree or forest, chooses roots, orients edges, adds multi-prerequisite dependency edges, and produces a selected frontier node. Examiner receives this fixed tree decision and should not choose the global direction itself.

**Tech Stack:** Python 3.12, local `.tree/runtime/knowledge-graph.json`, rule-based graph algorithms, optional Archivist boundary reviews in later phases.

---

## 1. Target Control Flow

Current graph-aware flow still lets examiner choose direction:

```text
source inventory -> curriculum map -> knowledge graph context -> examiner chooses next node
```

Target planner-controlled flow:

```text
source inventory
  -> curriculum map candidates
  -> deterministic tree planner
  -> selected frontier node
  -> examiner composes exam only for that node
```

Examiner may report that the selected node is too broad, duplicate, or blocked, but it should not freely choose another direction.

## 2. Planned Graph Shape

`knowledge-graph.json` should include:

```json
{
  "version": 1,
  "nodes": [
    {
      "node_id": "candidate:2",
      "status": "planned",
      "is_root": false,
      "root_score": 0.52,
      "backbone_parent": "candidate:1",
      "backbone_children": ["candidate:3"],
      "tree_depth": 1,
      "eligible": true,
      "planner_selected": true,
      "why_selected": "eligible frontier node at depth 0; evidence_strength=0.41; root_score=0.57; no duplicate/merge/split warning penalty",
      "selection_evidence": {
        "tree_depth": 0,
        "root_score": 0.57,
        "evidence_strength": 0.41,
        "warning_penalty": 0,
        "incoming_prerequisites": []
      },
      "required_nodes": ["finished:outputs/01/01.base.md"]
    }
  ],
  "edges": [
    {
      "from": "candidate:1",
      "to": "candidate:2",
      "relation": "backbone",
      "scores": {"affinity": 0.72},
      "evidence": {
        "matched_concepts": [],
        "matched_chunks": [],
        "matched_sources": [],
        "prerequisite_hits": []
      },
      "confidence": 0.82
    },
    {
      "from": "finished:outputs/01/01.base.md",
      "to": "candidate:2",
      "relation": "prerequisite"
    }
  ],
  "planner": {
    "mode": "deterministic_mst_v1",
    "root_nodes": ["candidate:1"],
    "selected_node": "candidate:2",
    "frontier_nodes": ["candidate:2", "candidate:4"],
    "boundary_edges": []
  }
}
```

The backbone is tree-shaped. Extra `prerequisite` edges create a DAG so one node can require multiple previous nodes.

## 3. Deterministic Tree Algorithm

### 3.1 Pairwise Affinity

For candidate nodes A and B:

```text
concept_similarity = overlap(A.core_concepts, B.core_concepts)
chunk_similarity = overlap(A.hit_chunks, B.hit_chunks)
source_similarity = overlap(A.source_collections, B.source_collections)
prerequisite_signal = max(
  overlap(A.core_concepts, B.prerequisites),
  overlap(B.core_concepts, A.prerequisites)
)

affinity =
  0.42 * concept_similarity
  + 0.28 * chunk_similarity
  + 0.18 * source_similarity
  + 0.12 * prerequisite_signal
```

This is domain-neutral and avoids ranks such as "programming first" or "AI later".

### 3.2 Backbone Tree

Use a maximum spanning tree over candidate nodes:

1. Compute all pair affinities.
2. Sort by affinity descending.
3. Add edges with union-find if they connect separate components.
4. Drop zero or near-zero affinity edges so truly independent materials become a forest.

This gives a stable, explainable first version. Neighbor Joining or UPGMA can be added later as an alternative clustering view.

### 3.3 Root Selection

Root score should be calculated by evidence and dependency shape:

```text
root_score =
  low_prerequisite_count
  + outgoing_prerequisite_support
  + evidence_strength
  + selection_priority
  - duplicate_with_finished_penalty
```

Roots are chosen per connected component. A root should be a node that supports later nodes and requires little prior knowledge.

### 3.4 Edge Orientation

Backbone edges are directed after root selection:

1. BFS from component root gives default parent -> child direction.
2. Strong prerequisite evidence overrides direction when clear.
3. Ambiguous direction becomes a boundary edge for later AI review.

### 3.5 Frontier Selection

Planner chooses the next node before examiner:

```text
frontier = planned nodes where all required_nodes are satisfied
selected = frontier node with:
  lowest tree_depth
  no duplicate/merge/split warning if possible
  highest evidence_strength
  highest root/backbone priority
```

If no frontier exists, graph reports blocked nodes and reasons.

## 4. AI Boundary Review

AI should not compute the whole tree. It should review only low-confidence cases:

- duplicate score near threshold
- merge_needed with different titles
- split_needed with multiple weak chunk clusters
- prerequisite direction ambiguous
- root scores close inside one component

Archivist review output should be strict JSON:

```json
{
  "edge_id": "candidate:a->candidate:b",
  "decision": "duplicate|adjacent|prerequisite|independent|split_needed|merge_needed",
  "direction": "A_to_B|B_to_A|none",
  "confidence": 0.0,
  "reason": ""
}
```

This phase is planned after deterministic planner behavior is stable.

## 5. Implementation Phases

### Phase 1: Planner Metadata

**Files:**

- Modify: `tree_engine/tree/curriculum/graph.py`
- Modify: `tree_engine/tree/cli.py`

Tasks:

1. Add pairwise affinity calculation.
2. Add maximum-spanning forest builder.
3. Add root scoring and component root selection.
4. Add directed backbone metadata to nodes and edges.
5. Add planner stats and selected frontier node.
6. Show root/frontier/selected in `tre rag graph`.

### Phase 2: Engine Uses Selected Frontier

**Files:**

- Modify: `tree_engine/tree/engine.py`
- Modify: `tree_engine/tree/agents/prompts.py`
- Modify: `tree_engine/tree/agents/examiner.py`

Tasks:

1. Pass the planner-selected node explicitly to examiner context.
2. If examiner omits `Graph_Node`, attach the selected node instead of loosely matching by source collection.
3. Tell examiner that direction is fixed by the planner and it should not choose another node.
4. Put a compact `Selected Node Context` before the full graph context so examiner sees the selected node, required nodes, allowed scope, out-of-scope boundaries, and warnings first.

### Phase 3: Boundary Review Hooks

**Files:**

- Modify: `tree_engine/tree/curriculum/graph.py`
- Modify: `tree_engine/tree/agents/archivist.py`

Tasks:

1. Mark low-confidence edges as `boundary_review_required`.
2. Add Archivist strict JSON review method.
3. Store review results under `planner.boundary_edges`.
4. Do not let AI override high-confidence planner edges.

### Phase 4: Graph-First State

**Files:**

- Modify: `tree_engine/tree/state/models.py`
- Modify: `tree_engine/tree/engine.py`

Tasks:

1. Persist selected `graph_node_id` and `required_nodes` for each generated file.
2. Treat chapter names as grouping labels, not sequencing authority.
3. Later, replace chapter scan with graph frontier scan.

## 6. Verification

Minimum smoke tests:

1. Three nodes where A prerequisite-supports B and B supports C produce `A -> B -> C`.
2. A duplicate planned node does not become selected frontier when a non-duplicate eligible node exists.
3. Independent weakly connected nodes become separate roots rather than forced into a fake edge.
4. A candidate whose required nodes are satisfied by a duplicate of a finished node becomes eligible.
5. `tre rag graph --help` and graph rendering still work.
6. Run:

```bash
.venv/bin/ruff check tree_engine/tree tree_engine/ingest
PYTHONPATH=tree_engine .venv/bin/python -m compileall tree_engine/tree tree_engine/ingest
git diff --check
```

## 7. Current Scope

This implementation starts with Phase 1 and Phase 2. Phase 3 AI boundary review is intentionally deferred until the deterministic planner is stable and inspectable.
