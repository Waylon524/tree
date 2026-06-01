"""Planner orchestration: scan -> MTUs -> DAG -> branches, all envelope-persisted.

Single entry point shared by the runtime engine and `tre planner rebuild`
(no dual planner paths). See docs/REBUILD-DESIGN.md §5.

    def rebuild_planner(root, *, settings, agents) -> Summary:
        manifest = scan_materials(root)
        mtus     = collect_mtus(root, manifest, agents.archivist)   # stage ②
        dag      = build_dag(agents.dagger, mtus, settings=settings) # stage ③
        branches = build_branches(dag)                               # stage ④
        # each step wrapped in envelope() + write_json_atomic, hash-gated

TODO (step 6).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


async def rebuild_planner(root: Path, *, settings: Any, agents: Any) -> dict[str, Any]:
    raise NotImplementedError("planner.rebuild_planner — implement in step 6")
