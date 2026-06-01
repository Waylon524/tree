"""Ready-branch scheduling: pick branches whose upstream is covered and write
BranchExecution records into pipeline-state.json.

Bounded by settings.max_active_branch_runs. See docs/REBUILD-DESIGN.md §4/§6.

TODO (step 6):
  - start_ready_branch_runs(state, branches, ledger) -> PipelineState
  - a branch is ready when all upstream_branch_ids are completed/covered
  - freeze a CoverageSnapshot (visible ancestors, forbidden future branches)
"""

from __future__ import annotations

from typing import Any


def start_ready_branch_runs(state: Any, branches: dict[str, Any], ledger: dict[str, Any]) -> Any:
    raise NotImplementedError("schedule.start_ready_branch_runs — implement in step 6")
