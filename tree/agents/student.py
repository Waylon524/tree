"""Student agent: blind test with fresh context every call."""

from __future__ import annotations

from tree.agents.loader import AgentLoader
from tree.deepseek.client import LLMClient


class StudentAgent:
    def __init__(self, client: LLMClient, loader: AgentLoader):
        self._client = client
        self._loader = loader

    async def blind_test(
        self,
        blind_exam: str,
        student_instructions: str,
        prior_file_contents: list[str],
        prior_file_paths: list[str],
        draft_text: str | None = None,
    ) -> str:
        system = self._loader.load("student")
        parts = [
            "## Your Reading List (Pre-Read Protocol)\n\n",
            "Prior completed files:\n"
            + "\n".join(f"  - {p}" for p in prior_file_paths)
            + "\n",
        ]
        if prior_file_contents:
            parts.append("Prior completed file contents:\n")
            for i, content in enumerate(prior_file_contents):
                parts.append(f"--- File {i + 1} ---\n{content}\n")
        if draft_text:
            parts.append(f"Current knowledge point draft:\n{draft_text}\n")
        else:
            parts.append("Current knowledge point draft: 尚未创建\n")

        parts.append(f"\n## [Student_Instructions]\n{student_instructions}\n")
        parts.append(f"\n## [Blind_Exam]\n{blind_exam}\n")

        user = "\n".join(parts)
        return await self._client.call("student", system, user)
