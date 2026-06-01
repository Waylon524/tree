"""Top-level run loop (thin).

Responsibilities (see docs/REBUILD-DESIGN.md §6, docs/LEGACY-DESIGN.md §7.3):
  reconcile finished -> ingest_driver.prepare_sources() -> loop:
    load state -> activate ready BranchRuns
    if none in progress: rebuild_planner -> schedule -> recheck
      else report blocked / WOODS_COMPLETE
    gather process_branch_execution(...) for in_progress[:max_active_branch_runs]

TODO (step 8).
"""

from __future__ import annotations

from tree.config import Settings


class TreeEngine:
    def __init__(self, settings: Settings):
        self.settings = settings
        # TODO (step 8): wire client, agents, state_mgr, rag, progress, tracer,
        # ingest_driver, branch_run.

    async def run(self) -> None:
        raise NotImplementedError("TreeEngine.run — implement in step 8")
