# Writeable Chunk Cluster Planner Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upgrade TREE planning so the next node is a writeable chunk cluster with enough coherent source coverage for a 300-500 line output, while already-covered clusters are absorbed into the finished trunk.

**Architecture:** Candidate generation first clusters source chunks into writeable nodes and annotates each node with estimated size and cohesion. Knowledge graph build then compares planned candidates against the whole finished subtree, absorbs candidates that are already solvable, and leaves only nodes with meaningful novelty for planner selection.

**Tech Stack:** Python 3.12+, pytest, deterministic curriculum graph code under `tree_engine/tree/curriculum/`.

---

### Task 1: Writeable Candidate Cluster Metadata And Thin Merge

**Files:**
- Modify: `tree_engine/tree/curriculum/candidate_nodes.py`
- Test: `tests/test_candidate_nodes.py`

- [ ] **Step 1: Write failing tests**

Add tests proving that same-section adjacent thin chunks merge into one candidate and that generated candidates expose `estimated_output_lines`, `size_band`, `cluster_cohesion`, and `chunk_count`.

- [ ] **Step 2: Run the focused tests**

Run: `.venv/bin/pytest tests/test_candidate_nodes.py -q`

Expected: FAIL before implementation because metadata and thin-cluster merge are missing.

- [ ] **Step 3: Implement minimal candidate-cluster scoring**

Add deterministic helpers to estimate output lines from chunk count, concepts, methods, formulas, and examples. Merge under-sized adjacent clusters only when same path/section evidence says they are locally coherent.

- [ ] **Step 4: Verify tests**

Run: `.venv/bin/pytest tests/test_candidate_nodes.py -q`

Expected: PASS.

### Task 2: Finished Subtree Absorption

**Files:**
- Modify: `tree_engine/tree/curriculum/graph.py`
- Test: `tests/test_knowledge_graph_planner.py`

- [ ] **Step 1: Write failing tests**

Add tests proving that a candidate covered by multiple finished outputs is marked `covered`, records `finished_solvability`, and is not planner-selected.

- [ ] **Step 2: Run the focused planner tests**

Run: `.venv/bin/pytest tests/test_knowledge_graph_planner.py -q`

Expected: FAIL before implementation because only single-node duplicate coverage is handled.

- [ ] **Step 3: Implement subtree solvability**

Compute candidate coverage against the union of finished concepts, chunks, and source collections. If solvability is high and novelty is low, mark the candidate `covered` with `absorbed_by_finished_trunk`.

- [ ] **Step 4: Verify tests**

Run: `.venv/bin/pytest tests/test_knowledge_graph_planner.py -q`

Expected: PASS.

### Task 3: Planner Selection Uses Writeability

**Files:**
- Modify: `tree_engine/tree/curriculum/graph.py`
- Test: `tests/test_knowledge_graph_planner.py`

- [ ] **Step 1: Write failing tests**

Add tests proving that planner ranking prefers candidates with better `size_fit` when prerequisite readiness and novelty are comparable.

- [ ] **Step 2: Implement score weighting**

Blend `size_fit`, `evidence_strength`, `prereq_readiness`, and novelty into root and branch sorting without allowing size alone to override prerequisites.

- [ ] **Step 3: Verify focused and full tests**

Run: `.venv/bin/pytest tests/test_candidate_nodes.py tests/test_knowledge_graph_planner.py -q`
Run: `.venv/bin/pytest -q`

