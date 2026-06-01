"""Shared agent plumbing: bind a role + system prompt to the LLM client."""

from __future__ import annotations

from tree.agents.prompts import get_prompt
from tree.model.client import LLMClient


class Agent:
    """Base class for role agents (examiner/student/writer/archivist/dagger)."""

    role: str = ""

    def __init__(self, client: LLMClient, *, prompt_name: str | None = None):
        self.client = client
        self.prompt_name = prompt_name or self.role
        self.system_prompt = get_prompt(self.prompt_name)

    async def complete(
        self,
        user_prompt: str,
        *,
        system_prompt: str | None = None,
        timeout_sec: float | None = None,
    ) -> str:
        return await self.client.call(
            self.role,
            system_prompt or self.system_prompt,
            user_prompt,
            timeout_sec=timeout_sec,
        )
