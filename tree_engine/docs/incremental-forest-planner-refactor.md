# Incremental Forest Planner Refactor

**Goal:** Replace the one-shot backbone tree planner with an incremental forest planner. TREE should first select a root, then let finished outputs become the real tree nodes that determine where the next candidate branch grows.

**Architecture:** Candidate nodes remain possible knowledge nodes derived from source inventory. The planner no longer computes a full candidate-only backbone tree at startup. It repeatedly resolves which candidates are already covered by finished outputs, selects either the best attachable branch or a new root, and tells examiner to write for that selected node.

---

## 1. Target Flow

```text
source inventory
  -> candidate node clustering
  -> incremental forest planner
  -> root selection when no finished tree exists
  -> examiner/writer produce finished output
  -> finished output replaces covered candidate nodes
  -> branch selection against the finished tree
  -> optional root reselection when all remaining candidates are far away
```

## 2. Planner Responsibilities

The planner owns:

- selecting the first root from candidate nodes
- marking candidates as covered when finished outputs duplicate or strongly cover them
- computing each remaining candidate's best parent finished output
- choosing the next branch with the highest prerequisite/parent coverage
- deciding whether the next branch attaches to an existing finished output or starts a new root
- exposing selected node, parent output, root status, and selection evidence to examiner

The planner does not:

- ask examiner to choose global direction
- build a full candidate-only backbone tree before the first output exists
- force unrelated source clusters into one tree

## 3. Root Selection

When there are no finished outputs, select one candidate root only.

Root scoring:

```text
root_score =
  low_prerequisite_score * 0.35
  + outgoing_prerequisite_support * 0.30
  + evidence_strength * 0.25
  + selection_priority * 0.10
```

The selected root gets:

```json
{
  "planner_selected": true,
  "is_root": true,
  "is_new_root": true,
  "parent_output": null,
  "tree_depth": 0
}
```

No `backbone` edges are created among candidate nodes in this phase.

## 4. Coverage Resolver

Finished outputs become the real tree nodes. For each remaining candidate, compare it to all finished outputs using the same relation scores:

- concept overlap
- chunk overlap
- source overlap
- prerequisite overlap
- weighted affinity

A candidate is marked covered when a finished output has a duplicate relation with it. Covered candidates remain visible in the graph for traceability but are not eligible frontier nodes.

Each covered candidate records:

```json
{
  "status": "covered",
  "covered_by_output": "finished:outputs/...",
  "coverage_reason": "covered by finished output duplicate relation"
}
```

## 5. Branch Selection

For every uncovered candidate, compute its parent score against every finished output.

Parent score:

```text
parent_score =
  prerequisite_coverage * 0.45
  + relation_affinity * 0.35
  + source_overlap * 0.10
  + chunk_overlap * 0.10
```

Prerequisite coverage means: how much of the candidate's prerequisite concepts are covered by the parent output's core concepts.

The highest-scoring finished output becomes the `parent_output`. This is the single primary parent that decides where the candidate is inserted into the readable tree.

The candidate can also connect to multiple supporting parents. Any finished output that exceeds the multi-parent threshold can become a required node:

```text
MULTI_PARENT_SCORE_THRESHOLD = 0.30
MULTI_PARENT_PREREQ_THRESHOLD = 0.25
MAX_SUPPORTING_PARENTS = 4
```

Additional supporting parents must have real semantic evidence:

- prerequisite coverage
- or concept overlap
- or chunk overlap

Source overlap alone is not enough.

The candidate's overall support score is:

```text
support_score =
  best_parent_score * 0.65
  + combined_prerequisite_coverage * 0.25
  + supporting_parent_count_bonus * 0.10
```

`combined_prerequisite_coverage` measures how much of the candidate's prerequisites are covered by all supporting parent outputs together.

The candidate's distance to the finished tree is explicit:

```text
tree_distance = 1 - support_score
nearest_finished_output = parent_output
```

Distance components are stored for inspection:

```json
{
  "concept_distance": 1 - concept_overlap,
  "chunk_distance": 1 - chunk_overlap,
  "source_distance": 1 - source_overlap,
  "affinity_distance": 1 - relation_affinity,
  "prerequisite_gap": 1 - combined_prerequisite_coverage
}
```

The selected branch is the candidate with the highest support score and attachability score, after duplicate/merge/split penalties.

Selected branch records:

```json
{
  "parent_output": "finished:outputs/...",
  "required_nodes": [
    "finished:outputs/...",
    "finished:outputs/..."
  ],
  "supporting_parents": [
    {
      "node_id": "finished:outputs/...",
      "score": 0.42,
      "prerequisite_coverage": 0.50,
      "affinity": 0.31
    }
  ],
  "is_new_root": false,
  "branch_score": 0.74,
  "support_score": 0.80,
  "tree_distance": 0.20,
  "nearest_finished_output": "finished:outputs/...",
  "distance_components": {
    "concept_distance": 0.50,
    "chunk_distance": 1.00,
    "source_distance": 0.00,
    "affinity_distance": 0.67,
    "prerequisite_gap": 0.00
  },
  "tree_depth": parent.tree_depth + 1,
  "why_selected": "best attachable branch from existing finished tree"
}
```

This makes the final structure a readable tree plus dependency DAG:

```text
primary tree edge:
  parent_output -> selected_candidate

supporting dependency edges:
  supporting_parent_1 -> selected_candidate
  supporting_parent_2 -> selected_candidate
```

## 6. Root Reselection

If every remaining candidate is far from the finished tree, do not force an attachment. Run root selection again over the remaining candidates and start a new root.

New root threshold should be conservative:

```text
NEW_ROOT_PARENT_SCORE_THRESHOLD = 0.18
NEW_ROOT_PREREQUISITE_THRESHOLD = 0.25
```

Start a new root only when:

- the best remaining candidate's parent/support score is below the parent threshold
- and its single-parent and combined prerequisite coverage are below the prerequisite threshold
- and there is no strong source/chunk connection to existing finished outputs

This creates a forest:

```text
root output A
  -> branch A1
  -> branch A2

root output B
  -> branch B1
```

## 7. Context Given to Examiner

The selected node context should explicitly say:

- selected node id
- whether this is a new root
- parent output, if any
- supporting parents and their scores
- required nodes
- branch score and support score
- tree distance and distance components
- tree depth
- why it was selected
- source chunk refs
- warnings for duplicate/merge/split risks

Examiner must write inside the selected node scope. It must not override root/branch selection.

## 8. Chapter Closure Naming

Active chapters use stable internal tree ids:

```text
outputs/tree-001/
outputs/tree-002/
```

The human chapter title is not fixed when root selection starts. At that point the tree has not grown enough to know its real boundary. TREE assigns the display chapter title only when:

- the planner selects `new_root`, before opening the next root
- or the pipeline reaches `PIPELINE_COMPLETE`

When closing a tree, the engine collects all finished outputs in that tree:

- knowledge point titles
- covered concepts
- source collections
- compact summaries

This compact context is sent to Archivist for a JSON chapter name:

```json
{
  "chapter_title": "程序设计基础与控制结构",
  "short_slug": "程序设计基础",
  "reason": "本章从变量、赋值扩展到条件判断和基础控制结构。"
}
```

If AI naming fails, TREE falls back to a deterministic title from the highest-frequency finished concepts. The title is stored on `pipeline-state.json` as `chapter_title`; output directories are not renamed, so ledger and RAG paths remain stable.

If the planner selection mode is `branch`, the engine reopens the existing unnamed tree that owns the selected branch's finished parent. It does not create a new chapter. This keeps one tree growing until a true `new_root` boundary is detected.

## 9. Planner Trace

Every graph rebuild stores a compact `planner.trace` object:

```json
{
  "mode": "incremental_forest_v1",
  "selection_mode": "branch",
  "selected_node": "candidate:...",
  "candidate_count": 4,
  "candidate_ranking": [
    {
      "rank": 1,
      "node_id": "candidate:...",
      "selected": true,
      "reason": "selected",
      "parent_output": "finished:outputs/...",
      "nearest_finished_output": "finished:outputs/...",
      "tree_distance": 0.20,
      "branch_score": 0.74,
      "support_score": 0.80,
      "supporting_parent_count": 2,
      "distance_components": {}
    }
  ]
}
```

The trace is for debugging threshold choices and explaining why one candidate was selected over another. It should remain compact enough to inspect from CLI output or `.tree/runtime/knowledge-graph.json`.

## 10. Implementation Tasks

### Task 1: Tests

Add focused planner tests:

- no finished outputs selects exactly one root and creates no backbone edges
- finished output covers matching candidate and selects a different remaining candidate
- attachable candidate selects the best parent output
- distant remaining candidate starts a new root

### Task 2: Planner Refactor

Modify `tree_engine/tree/curriculum/graph.py`:

- replace `_apply_backbone_planner` call with `_apply_incremental_forest_planner`
- mark covered candidates before frontier selection
- compute parent scores from finished outputs
- select root or branch
- remove candidate-only maximum spanning tree from the active planner path

Keep relation classification helpers because candidate clustering and planner selection both reuse them.

### Task 3: Context and CLI

Update context strings and CLI table labels:

- show `parent_output`
- show `is_new_root`
- show `branch_score`
- stop presenting candidate-only `backbone_parent` as the main concept

### Task 4: Verification

Run:

```bash
PYTHONPATH=tree_engine .venv/bin/python -m pytest tests/test_candidate_nodes.py tests/test_knowledge_graph_planner.py -q
.venv/bin/ruff check tree_engine/tree tree_engine/ingest tests
PYTHONPATH=tree_engine .venv/bin/python -m compileall tree_engine/tree tree_engine/ingest tests
git diff --check
```
