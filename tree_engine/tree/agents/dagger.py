"""DaggerAgent: merge MTUs into canonical nodes + build the DAG.

Stage ③ of the pipeline (new agent). See docs/REBUILD-DESIGN.md §4.

TODO (step 5):
  - build(mtus) -> {"nodes": [...], "edges": [...]} (raw, title-referenced)
      * one-shot global call with DAGGER_PROMPT over lightweight MTU metadata
      * if len(mtus) > dagger_max_nodes_per_call: per-collection batching fallback
  - the planner/dag.py module maps titles -> node_ids, validates coverage,
    breaks cycles, and persists the envelope.
"""

from __future__ import annotations

from tree.agents.base import Agent


class DaggerAgent(Agent):
    role = "dagger"

    async def build(self, mtus: list) -> dict:
        raise NotImplementedError("Dagger.build — implement in step 5")
