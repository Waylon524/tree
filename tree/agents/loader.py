"""Load agent system prompts from .claude/agents/*.md, stripping YAML frontmatter."""

from __future__ import annotations

from pathlib import Path


class AgentLoader:
    def __init__(self, agents_dir: Path):
        self._dir = agents_dir
        self._cache: dict[str, str] = {}

    def load(self, name: str) -> str:
        if name in self._cache:
            return self._cache[name]
        path = self._dir / f"{name}.md"
        if not path.exists():
            raise FileNotFoundError(f"Agent prompt not found: {path}")
        content = path.read_text(encoding="utf-8")
        prompt = _strip_frontmatter(content)
        self._cache[name] = prompt
        return prompt

    def clear_cache(self) -> None:
        self._cache.clear()


def _strip_frontmatter(content: str) -> str:
    if not content.startswith("---"):
        return content.strip()
    parts = content.split("---", 2)
    if len(parts) < 3:
        return content.strip()
    return parts[2].strip()
