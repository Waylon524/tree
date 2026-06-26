"""Shared agent plumbing: bind a role + system prompt to the LLM client."""

from __future__ import annotations

from pathlib import Path

from tree.agents.prompts import get_prompt
from tree.model.client import LLMClient


class Agent:
    """Base class for role agents (examiner/student/writer/archivist/dagger)."""

    role: str = ""

    def __init__(
        self,
        client: LLMClient,
        *,
        prompt_name: str | None = None,
        project_root: Path | None = None,
    ):
        self.client = client
        self.prompt_name = prompt_name or self.role
        self.project_root = project_root
        self.system_prompt = self.prompt_text(self.prompt_name)

    def prompt_text(self, prompt_name: str | None = None) -> str:
        return get_prompt(prompt_name or self.prompt_name, project_root=self.project_root)

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
