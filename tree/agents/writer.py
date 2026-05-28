"""Writer agent: CREATE or OPTIMIZE draft."""

from __future__ import annotations

from tree.agents.loader import AgentLoader
from tree.agents.parsers import detect_exam_too_broad
from tree.deepseek.client import LLMClient
from tree.state.models import WriterResult


class WriterAgent:
    def __init__(self, client: LLMClient, loader: AgentLoader):
        self._client = client
        self._loader = loader

    async def create_or_optimize(
        self,
        knowledge_point: str,
        file_seq: str,
        bottleneck_report: str,
        prior_file_contents: list[str],
        prior_file_paths: list[str],
        draft_text: str | None = None,
        previous_bottleneck: str | None = None,
        writer_instructions: str | None = None,
        retrieved_context: list[dict] | None = None,
    ) -> WriterResult:
        system = self._loader.load("writer")
        mode = "OPTIMIZE" if draft_text else "CREATE"
        parts = [
            f"## Task: {mode} mode\n",
            f"Knowledge point: {knowledge_point}\n",
            f"File sequence: {file_seq}\n",
            f"Bottleneck Report:\n{bottleneck_report}\n",
        ]
        if previous_bottleneck:
            parts.append(f"Previous Bottleneck Report:\n{previous_bottleneck}\n")
        if draft_text:
            parts.append(f"Current draft (OPTIMIZE this):\n{draft_text}\n")
        else:
            parts.append("Current draft: 尚未创建 (CREATE from scratch)\n")
        if writer_instructions:
            parts.append(f"[Writer_Instructions]:\n{writer_instructions}\n")
        if retrieved_context:
            parts.append(_format_retrieved_context(retrieved_context))
        parts.append(
            "Prior completed file paths:\n"
            + "\n".join(f"  - {p}" for p in prior_file_paths)
            + "\n"
        )
        if prior_file_contents:
            parts.append("Prior completed file contents:\n")
            for i, content in enumerate(prior_file_contents):
                parts.append(f"--- File {i + 1} ---\n{content}\n")

        user = "\n".join(parts)
        raw = await self._client.call("writer", system, user)

        is_broad, bloat = detect_exam_too_broad(raw)
        if is_broad:
            return WriterResult(is_exam_too_broad=True, bloat_description=bloat)

        return WriterResult(is_exam_too_broad=False, draft_content=raw)


def _format_retrieved_context(retrieved_context: list[dict]) -> str:
    parts = ["Retrieved RAG context (use as supporting source excerpts):\n"]
    for i, hit in enumerate(retrieved_context, start=1):
        metadata = hit.get("metadata") or {}
        source = metadata.get("path") or metadata.get("filename") or metadata.get("doc_id") or "unknown"
        score = hit.get("score")
        score_text = f", score={score:.4f}" if isinstance(score, float) else ""
        parts.append(f"--- RAG Hit {i}: {source}{score_text} ---\n{hit.get('text', '')}\n")
    return "\n".join(parts)
