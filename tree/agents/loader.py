"""Load built-in agent system prompts."""

from __future__ import annotations

from tree.agents.prompts import get_prompt


class AgentLoader:
    def __init__(self) -> None:
        self._cache: dict[str, str] = {}

    def load(self, name: str) -> str:
        if name in self._cache:
            return self._cache[name]
        prompt = get_prompt(name)
        self._cache[name] = prompt
        return prompt

    def clear_cache(self) -> None:
        self._cache.clear()
