"""Archivist agent: structure raw OCR text into clean Markdown."""

from __future__ import annotations

from tree.agents.loader import AgentLoader
from tree.deepseek.client import LLMClient


class ArchivistAgent:
    def __init__(self, client: LLMClient, loader: AgentLoader):
        self._client = client
        self._loader = loader

    async def structure(self, raw_text: str) -> str:
        system = self._loader.load("archivist")
        user = (
            "## Task: Structure OCR Output\n\n"
            "Transform the following raw OCR output into clean Markdown.\n\n"
            "## Raw OCR Text\n"
            f"{raw_text}"
        )
        return await self._client.call("archivist", system, user)
