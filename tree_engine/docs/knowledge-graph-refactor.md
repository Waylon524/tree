# Tree Knowledge Graph Refactor Plan

> Superseded direction note: this document records the first graph-aware design. The current direction is incremental forest growth, documented in `tree_engine/docs/incremental-forest-planner-refactor.md`, where examiner follows the planner-selected root or branch instead of choosing the global direction. The former curriculum-map layer has also been reframed as candidate node generation in `tree_engine/docs/candidate-node-generator-refactor.md`.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans or follow this document task-by-task. Each phase should leave the engine runnable.

**Goal:** Turn TREE from a mostly linear chapter pipeline into a knowledge graph pipeline where each knowledge point file can depend on multiple previous files and can branch into multiple next files.

**Architecture:** Keep source RAG, source inventory, candidate nodes, and finished ledger as the lower layers. Add a knowledge graph layer that normalizes candidates and finished outputs into nodes, computes relation edges, and gives examiner a graph-aware selection context. The existing chapter loop remains as a compatibility shell while the graph becomes the decision surface.

**Tech Stack:** Python 3.12, Pydantic-style JSON dictionaries, local `.tree/runtime/*.json` state files, Qdrant-backed RAG, OpenAI-compatible LLM agents.

---

## 1. Target Model

TREE should treat a generated knowledge file as a graph node, not merely as the next item in a linear chapter.

Each node should contain:

```json
{
  "node_id": "finished:outputs/01/01.topic.md",
  "status": "finished",
  "title": "topic title",
  "path": "outputs/01/01.topic.md",
  "core_concepts": [],
  "prerequisites": [],
  "source_collections": [],
  "hit_chunks": [],
  "required_nodes": [],
  "related_nodes": []
}
```

Planned nodes come from `candidate-nodes.json`; finished nodes come from `knowledge-ledger.json`. A planned node becomes eligible only when its required nodes are already finished, or when it has no required nodes.

## 2. Relation Algorithm

For every pair of nodes A and B, compute:

```text
concept_overlap = A.core_concepts ∩ B.core_concepts
chunk_overlap = A.hit_chunks ∩ B.hit_chunks
source_overlap = A.source_collections ∩ B.source_collections
prerequisite_ab = B.prerequisites ∩ A.core_concepts
prerequisite_ba = A.prerequisites ∩ B.core_concepts
```

Normalize each score by the smaller non-empty set size so small but decisive prerequisite matches are not drowned out by large concept lists.

Relation labels:

- `duplicate`: high concept overlap and high chunk overlap, or extremely high concept overlap.
- `merge_needed`: two planned nodes hit substantially the same chunks but present different titles or fragmented concept lists.
- `prerequisite`: A covers concepts required by B; create a directed edge `A -> B`.
- `adjacent`: low-to-medium concept overlap, source overlap, or prerequisite-adjacent concepts.
- `independent`: almost no overlap.
- `split_needed`: one planned node hits too many weakly connected source collections or chunk clusters.

The rule layer should recall candidate relations. Later, Archivist or Examiner can audit high-risk edges such as `duplicate`, `merge_needed`, and `split_needed`.

## 3. Runtime Files

The graph layer writes:

```text
.tree/runtime/knowledge-graph.json
```

Suggested shape:

```json
{
  "version": 1,
  "nodes": [],
  "edges": [],
  "stats": {
    "finished_count": 0,
    "planned_count": 0,
    "eligible_count": 0,
    "blocked_count": 0
  }
}
```

The file is derived and can be rebuilt. It should not become the only source of truth for finished outputs; finished files and `knowledge-ledger.json` remain authoritative.

## 4. Engine Flow

Current flow:

```text
source RAG -> source inventory -> candidate nodes -> examiner selects next chapter
```

Target flow:

```text
source RAG
  -> source inventory
  -> candidate knowledge nodes
  -> knowledge graph
  -> examiner selects next eligible node
  -> student / examiner / writer loop
  -> finished output
  -> ledger update
  -> graph rebuild
```

The first implementation should not remove chapter state. Instead, it should make chapter selection graph-aware:

1. Rebuild source inventory.
2. Rebuild candidate nodes.
3. Rebuild knowledge graph.
4. Pass graph context to examiner.
5. Examiner chooses an eligible planned node, or explains why it must split/merge/skip.

## 5. Implementation Phases

### Phase 1: Derived Knowledge Graph

**Files:**

- Create: `tree_engine/tree/curriculum/graph.py`
- Modify: `tree_engine/tree/io/paths.py`
- Modify: `tree_engine/tree/cli.py`

Tasks:

1. Add `knowledge_graph_path(root)`.
2. Add graph load/save helpers.
3. Convert finished ledger records to `finished:*` nodes.
4. Convert candidate knowledge nodes to `candidate:*` graph nodes.
5. Compute relation edges using concept, chunk, source, and prerequisite overlap.
6. Add `tre rag graph` to inspect nodes and edges.

### Phase 2: Graph-Aware Examiner Context

**Files:**

- Modify: `tree_engine/tree/engine.py`
- Modify: `tree_engine/tree/agents/prompts.py`
- Modify: `tree_engine/tree/agents/examiner.py`

Tasks:

1. Rebuild graph during `_scan_next_chapter`.
2. Append a compact graph context after inventory and candidate node context.
3. Tell examiner to prefer eligible graph nodes and avoid duplicate/merge-needed nodes.
4. Preserve existing section output format so the rest of the loop still works.

### Phase 3: Node Dependencies in State

**Files:**

- Modify: `tree_engine/tree/state/models.py`
- Modify: `tree_engine/tree/state/manager.py`
- Modify: `tree_engine/tree/engine.py`

Tasks:

1. Store graph metadata on `ChapterRecord`: `graph_node_id`, `required_nodes`.
2. When examiner chooses a graph node, persist its node id and dependency nodes.
3. When writer output passes, update ledger and rebuild graph so downstream nodes become eligible.

### Phase 4: Split and Merge Safeguards

**Files:**

- Modify: `tree_engine/tree/curriculum/graph.py`
- Modify: `tree_engine/tree/agents/prompts.py`
- Modify: `tree_engine/tree/agents/examiner.py`

Tasks:

1. Expose `split_needed` and `merge_needed` warnings in graph context.
2. If examiner selects a split-needed node, require narrower `Next_Knowledge_Point`.
3. If examiner selects duplicate or merge-needed node, require a clear delta or skip.

### Phase 5: Graph-First Output Views

**Files:**

- Create or modify future exporter modules.

Tasks:

1. Add a topological reading path view.
2. Add a branch view from any target node.
3. Add a reverse prerequisite view for review paths.

## 6. Verification

Minimum smoke tests:

1. Build graph from two finished records and two planned candidates.
2. Confirm prerequisite edge direction: if B prerequisites match A concepts, edge is `A -> B`.
3. Confirm duplicate relation when concepts and chunks overlap strongly.
4. Confirm merge-needed relation when chunks overlap strongly but concepts are not identical.
5. Confirm CLI renders graph without requiring the embedding server.
6. Run:

```bash
PYTHONPATH=tree_engine .venv/bin/python -m compileall tree_engine/tree tree_engine/ingest
.venv/bin/ruff check tree_engine/tree tree_engine/ingest
git diff --check
```

## 7. Migration Rule

Do not delete the existing chapter loop yet. The graph should first become a decision layer. Once graph selection is stable, chapters can become export-time groupings rather than the engine's primary unit.
