# 2026-05-30 chem TREE Run Test Report

## Summary

Test workspace: `/Users/waylon524/Documents/WS/chem`

The chem run successfully completed source ingest and produced four outputs under `outputs/tree-001/`, then stopped during the fifth knowledge point because the examiner returned PASS before any draft had been persisted.

The run exposed several high-priority issues:

- The engine can still accept a first-iteration PASS without a writer-created draft, causing a hard crash.
- Planner prerequisite constraints can include unfinished candidate nodes.
- Writer outputs are often far below the target 300-500 lines.
- LaTeX delimiter rules are not reliably followed.
- Some nodes converge slowly or are too fragmented.
- Progress state keeps stale retry metadata after OCR succeeds.

## Timeline

### Source Ingest

- 17 material files were detected.
- OCR completed for 17/17 files.
- Embedding completed for 31/31 source chunks.
- PaddleOCR submit saw two SSL handshake timeout retries, then recovered.

Evidence:

- `.tree/runtime/progress.json`
- `.tree/runtime/services/tree.log`

### Generated Outputs

| File | Result | Iterations | Lines | Notes |
| --- | --- | ---: | ---: | --- |
| `01.难溶离子固体的沉淀平衡与温度效应.md` | PASS | 2 | 107 | Too short; contains `\(` / `\)` LaTeX delimiters |
| `02.稀土元素分离中的沉淀平衡应用—分步沉淀、氢氧化物分离与沉淀转化.md` | PASS | 2 | 288 | Near lower bound; acceptable but still below 300 |
| `03.沉淀完全的标准与定量、定性分析阈值.md` | PASS | 5 | 74 | Too short; slow convergence |
| `04.晶格能、溶解度与离子半径的关系.md` | PASS | 2 | 105 | Too short; contains `\(` / `\)` LaTeX delimiters |

Evidence:

- `outputs/tree-001/*.md`
- `.tree/runtime/pipeline-temp/trace.jsonl`
- `.tree/runtime/services/tree.log`

## Blocking Failure

### First-Iteration PASS Without Draft

During file 05:

- Knowledge point: `05. 溶解度阈值(solubilitythreshold)与相对溶解度(relativesolubility)`
- The student answered iteration 1.
- Examiner returned PASS.
- No writer draft had been created.
- Engine crashed in `_handle_pass`:

```text
RuntimeError: Cannot PASS without a persisted draft. The writer must create a draft before examiner PASS can be accepted.
```

Observed route:

```text
Step 1: knowledge point = 05...
  Step 2: student answered (iteration 1)
  S3 route: PASS
  crash in _handle_pass because iter_state.draft_path is missing
```

Likely cause:

- The engine has a guard in `_handle_pass`, but the routing still allows examiner PASS when `iter_state.draft_path` does not exist.
- For a new node, first iteration should not be allowed to PASS unless an existing draft is present and persisted.

Recommended fix:

- In the iteration loop, before accepting PASS, check whether `iter_state.draft_path` exists.
- If no draft exists, convert the route to a knowledge-gap failure and send writer an explicit defect such as: "No persisted draft exists for the selected node; create a complete draft before PASS can be accepted."
- Add a regression test that simulates examiner PASS on first iteration with no draft and verifies writer is invoked instead of `_handle_pass`.

## Planner Issues

### Unfinished Candidate Appears in Required Nodes

The selected fifth node had:

```text
selected_id: candidate:8. 沉淀溶解平衡:29
selected_title: 溶解度阈值(solubilitythreshold)、相对溶解度(relativesolubility)
selected_required:
  - candidate:8. 沉淀溶解平衡:30
  - finished:outputs/tree-001/04.晶格能、溶解度与离子半径的关系.md
```

This is risky because student and examiner prompts treat required nodes as prerequisite context. An unfinished candidate cannot be supplied as learned prior material.

Recommended fix:

- Split planner relations into:
  - `required_finished_outputs`: prerequisites that are already available to student/writer.
  - `blocked_by_candidates`: prerequisite candidates that must be completed first.
- A candidate with unfinished prerequisites should not be selected as executable.
- If the planner wants to preserve the relation, keep it in graph metadata but not in the selected node's executable `required_nodes`.
- Add tests for "selected executable node must only require finished outputs."

### Chapter State Inconsistency

At one check, `pipeline-state.json` briefly showed `tree-001` as `completed` after file 04. Later it returned to `in_progress` for file 05.

This may be intentional during scan/continuation, but it makes the watch panel harder to reason about.

Recommended fix:

- Represent tree status with a more explicit state such as `scanning_next_node`, `active`, `completed`, `woods_complete`.
- Avoid marking a tree `completed` if the planner may immediately continue the same tree.

## Output Quality Issues

### Outputs Are Too Short

The user expectation is that final output files should usually be 300-500 lines so one knowledge point is explained completely.

Observed outputs:

- 01: 107 lines
- 02: 288 lines
- 03: 74 lines
- 04: 105 lines

The newest prompt changes in the repo already tighten writer expectations, but the tested installed engine had not yet incorporated those changes.

Recommended fix:

- Keep the 300-500 line target in `WRITER_PROMPT`.
- Add a post-writer validation pass before student testing:
  - If draft is under 300 lines, route back to writer with a "draft too thin" defect unless the selected node is explicitly tiny.
  - If draft is over 500 lines, route to `EXAM_TOO_BROAD` or ask examiner/planner to narrow the node.
- Record line count in progress/trace so watch can surface this immediately.

### LaTeX Delimiter Violations

Files 01 and 04 contain `\(` / `\)` delimiters, which render poorly in the target Markdown pipeline.

Recommended fix:

- Keep the prompt rule requiring `$...$` and `$$...$$`.
- Add deterministic post-processing or validation:
  - Reject drafts containing `\(`, `\)`, `\[`, or `\]`.
  - Either normalize them automatically or route to writer for repair.
- Add tests for markdown math delimiter validation.

### Node Granularity Still Too Fine

Several outputs are short and narrow. The third node passed only after five iterations, suggesting the node boundary may have been too thin or poorly aligned with the exam.

Recommended fix:

- Use chunk/concept clustering to merge tightly related micro-nodes before examiner sees them.
- Penalize candidate nodes whose source coverage is too small or whose concepts are mostly a subset of an adjacent node.
- Add planner stats to trace: hit chunk count, concept count, overlap score, selected parent, and reason for node boundary.

## Progress And Watch Issues

### Stale OCR Retry Fields

`progress.json` still showed:

```json
"retry_action": "submit local OCR job",
"retry_attempt": 1,
"retry_delay_sec": 2.0
```

after OCR was already complete.

Recommended fix:

- Clear retry metadata on successful OCR completion.
- Or store retry metadata under `last_retry` with an explicit `resolved: true` flag.

### Trace Is Too Sparse For Debugging

The trace records S1/S2/S3/S4 events, but not enough planner and quality information.

Recommended fix:

- Add trace events for:
  - selected node id/title
  - selection mode
  - required finished outputs
  - blocked candidate prerequisites
  - draft line count
  - LaTeX validation result
  - reason for PASS/FAIL

## Suggested Fix Order

1. Prevent first-iteration PASS without a persisted draft.
2. Prevent executable selected nodes from depending on unfinished candidates.
3. Add deterministic draft validators for line count and LaTeX delimiters.
4. Improve planner node granularity with concept/chunk clustering thresholds.
5. Clean progress retry metadata after successful OCR.
6. Add richer trace/progress fields for watch diagnostics.

## Regression Tests To Add

- Examiner PASS with no draft should route to writer instead of crashing.
- Selected executable node should not include unfinished `candidate:*` entries in `required_nodes`.
- Drafts containing `\(`, `\)`, `\[`, or `\]` should fail validation.
- Drafts under the configured line target should fail validation or trigger writer expansion.
- OCR retry metadata should be cleared or marked resolved after success.

