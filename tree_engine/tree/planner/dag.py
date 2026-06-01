"""Dagger call wrapper + deterministic DAG validation.

Stage ③ program side. See docs/REBUILD-DESIGN.md §4 ③.

Responsibilities (LLM does merge+edges; program only validates):
  - call DaggerAgent.build(mtus) -> raw {nodes(title-ref), edges(title-ref)}
  - map titles -> node_id (prefixed_id from sorted member_mtu_ids)
  - assert every MTU covered exactly once; node_id references resolve
  - break cycles (drop lowest-confidence back edge until acyclic)
  - optional transitive reduction of prerequisite edges
  - on validation failure: one AI repair, else deterministic source-order chain

TODO (step 5).
"""

from __future__ import annotations

from typing import Any


def break_cycles(node_ids: set[str], edges: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop the lowest-confidence edge on each detected cycle until acyclic."""
    raise NotImplementedError("dag.break_cycles — implement in step 5")


async def build_dag(agent: Any, mtus: list[Any], *, settings: Any) -> dict[str, Any]:
    raise NotImplementedError("dag.build_dag — implement in step 5")
