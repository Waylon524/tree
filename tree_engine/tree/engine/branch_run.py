"""Single BranchRun executor: Step 0 -> 1 -> 2 -> 3 -> 4.

Migrated loop semantics (see docs/LEGACY-DESIGN.md §7.3, §7.4):
  Step 0  load in-progress branch, next_seq = len(outputs_completed)+1
  Step 1  Examiner.compose over the active branch span
  Step 2  Student.answer (blind)
  Step 3  Examiner.audit -> PASS | FAIL_KNOWLEDGE_GAP
  Step 4  Writer.draft (FAIL) -> back to Step 2
  PASS    move draft -> outputs/, update ledger + finished RAG + coverage

Prior Scope = DAG ancestor closure (over canonical nodes) + earlier files in the
same branch before the span; frozen via CoverageSnapshot.

TODO (step 7).
"""

from __future__ import annotations


async def process_branch_execution(engine: object, execution_path: str) -> None:
    raise NotImplementedError("branch_run.process_branch_execution — implement in step 7")
