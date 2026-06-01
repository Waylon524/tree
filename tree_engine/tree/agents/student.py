"""StudentAgent: zero-baseline blind test answering. See REBUILD-DESIGN §6."""

from __future__ import annotations

from tree.agents.base import Agent


class StudentAgent(Agent):
    role = "student"

    async def answer(
        self,
        *,
        blind_exam: str,
        prior_paths: list[str],
        prior_contents: list[str],
        draft_text: str | None = None,
        learned_hits: list[dict] | None = None,
    ) -> str:
        parts = [
            "## Your Reading List (Pre-Read Protocol)\n",
            "Prior completed files:\n" + "\n".join(f"  - {p}" for p in prior_paths) + "\n",
        ]
        if prior_contents:
            parts.append("Prior completed file contents:\n")
            for i, content in enumerate(prior_contents):
                parts.append(f"--- File {i + 1} ---\n{content}\n")
        parts.append(
            f"current branch-span draft:\n{draft_text}\n" if draft_text
            else "current branch-span draft: 尚未创建\n"
        )
        if learned_hits:
            parts.append(_format_learned(learned_hits))
        parts.append(f"\n## [Blind_Exam]\n{blind_exam}\n")
        return await self.complete("\n".join(parts))


def _format_learned(hits: list[dict]) -> str:
    parts = ["Retrieved RAG context from already learned materials:\n"]
    for i, hit in enumerate(hits, start=1):
        meta = hit.get("metadata") or {}
        source = meta.get("path") or meta.get("filename") or meta.get("doc_id") or "unknown"
        score = hit.get("score")
        score_text = f", score={score:.4f}" if isinstance(score, float) else ""
        parts.append(f"--- Learned RAG Hit {i}: {source}{score_text} ---\n{hit.get('text', '')}\n")
    return "\n".join(parts)
