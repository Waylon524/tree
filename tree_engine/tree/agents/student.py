"""StudentAgent: zero-baseline blind test answering."""

from __future__ import annotations

from tree.agents.base import Agent
from tree.agents.context import bounded_rag_hits


class StudentAgent(Agent):
    role = "student"

    async def answer(
        self,
        *,
        blind_exam: str,
        prior_paths: list[str],
        prior_contents: list[str] | None = None,
        draft_text: str | None = None,
        learned_hits: list[dict] | None = None,
    ) -> str:
        parts = [
            "## Your Reading List (Pre-Read Protocol)\n",
            "Prior completed files:\n" + "\n".join(f"  - {p}" for p in prior_paths) + "\n",
        ]
        parts.append(
            f"current active-node draft:\n{draft_text}\n" if draft_text
            else "current active-node draft: 尚未创建\n"
        )
        if learned_hits:
            parts.append(_format_learned(learned_hits))
        parts.append(f"\n## [Blind_Exam]\n{blind_exam}\n")
        return await self.complete("\n".join(parts), operation="student.answer")


def _format_learned(hits: list[dict]) -> str:
    parts = ["Retrieved RAG context from already learned materials:\n"]
    for i, hit in enumerate(bounded_rag_hits(hits), start=1):
        meta = hit.get("metadata") or {}
        source = meta.get("path") or meta.get("filename") or meta.get("doc_id") or "unknown"
        score = hit.get("score")
        score_text = f", score={score:.4f}" if isinstance(score, float) else ""
        parts.append(f"--- Learned RAG Hit {i}: {source}{score_text} ---\n{hit.get('text', '')}\n")
    return "\n".join(parts)
