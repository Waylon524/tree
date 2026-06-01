"""DaggerAgent: one LLM call that merges MTUs into canonical nodes + edges.

Stage ③ (new agent). The agent only performs the raw LLM call and JSON parse;
canonicalization, coverage validation, and cycle breaking live in
``tree.planner.dag``. See docs/REBUILD-DESIGN.md §4 ③.
"""

from __future__ import annotations

import json
from typing import Any

from tree.agents.base import Agent
from tree.agents.parsers import extract_json_object


class DaggerAgent(Agent):
    role = "dagger"

    async def build(self, payload: list[dict[str, Any]], *, timeout_sec: float | None = None) -> dict[str, Any]:
        """Send MTU metadata, return parsed ``{"nodes": [...], "edges": [...]}``."""
        user_prompt = json.dumps(payload, ensure_ascii=False, indent=2)
        raw = await self.complete(user_prompt, timeout_sec=timeout_sec)
        result = extract_json_object(raw)
        if not isinstance(result.get("nodes"), list):
            raise ValueError("Dagger response missing `nodes` list")
        result.setdefault("edges", [])
        return result
