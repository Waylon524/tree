# Candidate Node Generator Refactor Plan

**Goal:** Replace the misleading "curriculum map" layer with a clearer candidate node generator. The engine should flow from source inventory to candidate nodes to deterministic graph planner.

**Architecture:** Source inventory remains chunk/collection level. Candidate nodes are the middle layer that groups inventory evidence into possible knowledge-point nodes. The deterministic graph planner consumes candidate nodes and owns ordering, roots, frontier selection, and tree structure.

---

## 1. Target Flow

Current wording:

```text
source inventory -> curriculum map -> knowledge graph planner
```

Target wording:

```text
source inventory -> candidate node generator -> knowledge graph planner
```

The old "curriculum map" name is misleading because this layer does not decide curriculum order anymore. The planner decides direction.

## 2. Candidate Node Responsibilities

Candidate nodes should do only this:

- group related source chunks/collections into possible knowledge nodes
- name a `title_hint`
- record `core_concepts`
- record possible `prerequisite_concepts`
- record `representative_chunks`
- record `source_collections`
- provide `selection_priority` as evidence strength, not sequence authority

Candidate nodes should not:

- choose roots
- choose next chapter
- choose generation order
- decide tree structure
- override graph planner direction

## 3. Runtime File

Use:

```text
.tree/runtime/candidate-nodes.json
```

`curriculum-map.json` is kept only as a temporary compatibility fallback. New code should read/write `candidate-nodes.json`.

## 4. Implementation Steps

### Step 1: Rename Semantics

Files:

- Move implementation from `tree_engine/tree/curriculum/map.py` to `tree_engine/tree/curriculum/candidate_nodes.py`
- Keep `tree_engine/tree/curriculum/map.py` as compatibility re-exports
- Add `paths.candidate_nodes_path(root)`
- Update engine imports and variable names
- Update CLI display text from `rag map` to `rag candidates`

### Step 2: Deterministic Candidate Generation

The deterministic generator is chunk/concept clustering first:

```text
inventory.chunks -> relation similarity -> chunk clusters -> candidate nodes
```

Each chunk is converted to the same lightweight node shape used by relation classification: `core_concepts`, `prerequisites`, `hit_chunks`, and `source_collections`. The generator reuses the knowledge graph relation scoring weights for concept, chunk, source, and prerequisite overlap. Source overlap alone is not enough to merge chunks; clustering requires concept overlap, prerequisite overlap, or enough affinity with semantic evidence.

If an old inventory has no chunk records, the generator falls back to one candidate per source collection.

### Step 3: AI Boundary Role

Archivist can still enrich candidate nodes for now, but its role is candidate grouping/enrichment, not curriculum ordering. In a later version, AI should only review uncertain cluster boundaries.

## 5. Verification

Run:

```bash
.venv/bin/ruff check tree_engine/tree tree_engine/ingest
PYTHONPATH=tree_engine .venv/bin/python -m compileall tree_engine/tree tree_engine/ingest
git diff --check
```

Smoke tests:

- `candidate_nodes` can split weakly related chunks inside one collection.
- `candidate_nodes` can merge strongly related chunks across collections.
- old `tree.curriculum.map` imports still work.
- engine imports candidate nodes directly.
- CLI `rag candidates --help` works.
