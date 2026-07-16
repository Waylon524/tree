"""DaggerAgent: nodes/defines + prerequisite-define selection.

Stage ③. The agent only performs raw LLM calls and JSON parse; node
canonicalization, define validation, prerequisite mapping, and DAG construction
live in ``tree.planner.dag``.
"""

from __future__ import annotations

import json
from typing import Any

from tree.agents.base import Agent
from tree.agents.schemas import (
    DaggerNodesResponse,
    DaggerPrerequisitesResponse,
    parse_agent_json,
)


class DaggerAgent(Agent):
    role = "dagger"

    async def build_nodes(
        self,
        payload: list[dict[str, Any]],
        *,
        timeout_sec: float | None = None,
        operation: str = "dagger.build_nodes",
    ) -> dict[str, Any]:
        """Send MTU metadata, return parsed ``{"nodes": [...]}``."""
        user_prompt = json.dumps(payload, ensure_ascii=False, indent=2)
        raw = await self.complete(
            user_prompt,
            operation=operation,
            system_prompt=self.prompt_text("dagger"),
            timeout_sec=timeout_sec,
        )
        return parse_agent_json(raw, DaggerNodesResponse).model_dump(exclude_none=True)

    async def build_prerequisites(
        self,
        payload: dict[str, Any],
        *,
        timeout_sec: float | None = None,
        operation: str = "dagger.select_prerequisites",
    ) -> dict[str, Any]:
        """Send nodes + define dictionary, return ``{"node_prerequisites": [...]}``."""
        user_prompt = json.dumps(payload, ensure_ascii=False, indent=2)
        raw = await self.complete(
            user_prompt,
            operation=operation,
            system_prompt=self.prompt_text("dagger_prerequisites"),
            timeout_sec=timeout_sec,
        )
        return parse_agent_json(raw, DaggerPrerequisitesResponse).model_dump(exclude_none=True)

    async def repair_defines(
        self, payload: dict[str, Any], *, timeout_sec: float | None = None
    ) -> dict[str, Any]:
        """Repair conflicting node defines by merging nodes or changing defines."""
        repair_prompt = {
            "task": "REPAIR_NODE_DEFINES",
            **payload,
        }
        return await self.build_nodes(
            [repair_prompt],
            timeout_sec=timeout_sec,
            operation="dagger.repair_defines",
        )

    async def repair_prerequisites(
        self, payload: dict[str, Any], *, timeout_sec: float | None = None
    ) -> dict[str, Any]:
        """Repair prerequisite selection for a cycle or invalid required_defines."""
        repair_prompt = {
            "task": "REPAIR_NODE_PREREQUISITES",
            **payload,
        }
        return await self.build_prerequisites(
            repair_prompt,
            timeout_sec=timeout_sec,
            operation="dagger.repair_prerequisites",
        )

    async def build(
        self, payload: list[dict[str, Any]], *, timeout_sec: float | None = None
    ) -> dict[str, Any]:
        """Compatibility shim for older tests/callers."""
        return await self.build_nodes(payload, timeout_sec=timeout_sec)
