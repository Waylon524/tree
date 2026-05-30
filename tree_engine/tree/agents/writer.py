"""Writer agent: CREATE or OPTIMIZE draft."""

from __future__ import annotations

import re

from tree.agents.loader import AgentLoader
from tree.agents.parsers import detect_exam_too_broad
from tree.model.client import LLMClient
from tree.state.models import WriterResult

_SENSITIVE_EXAM_HEADER_RE = re.compile(
    r"^\s*(?:#{1,6}\s*)?(?:\[)?"
    r"(?:Blind_Exam|Answer_Key|Exam Paper|Standard Answers|Student'?s? Exam Responses|"
    r"Student Responses|Student Answer|Student's Answer|试卷原文|盲考试题|考试题目|"
    r"标准答案|学生答卷|学生答案)"
    r"(?:\])?\b",
    re.IGNORECASE,
)
_MARKDOWN_HEADER_RE = re.compile(r"^\s*#{1,6}\s+\S")


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
        graph_context: str | None = None,
    ) -> WriterResult:
        system = self._loader.load("writer")
        mode = "OPTIMIZE" if draft_text else "CREATE"
        bottleneck_report = sanitize_writer_context(bottleneck_report)
        previous_bottleneck = (
            sanitize_writer_context(previous_bottleneck) if previous_bottleneck else None
        )
        writer_instructions = (
            sanitize_writer_context(writer_instructions) if writer_instructions else None
        )
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
        if graph_context:
            parts.append(
                "Planner-bound graph context for the active chapter. Use it only to enforce "
                "the selected node delta and prerequisite boundaries:\n"
                f"{graph_context}\n"
            )
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
    parts = [
        "Retrieved RAG context:\n"
        "- content_kind=source hits may teach the current new knowledge point.\n"
        "- content_kind=finished hits are already taught material. Cite them briefly as prerequisites; "
        "do not duplicate their definitions, examples, or misconception explanations.\n"
        "- content_kind=ledger hits summarize the delta that must remain after removing duplicates.\n"
    ]
    for i, hit in enumerate(retrieved_context, start=1):
        metadata = hit.get("metadata") or {}
        kind = metadata.get("content_kind") or "unknown"
        source = metadata.get("path") or metadata.get("filename") or metadata.get("doc_id") or "unknown"
        score = hit.get("score")
        score_text = f", score={score:.4f}" if isinstance(score, float) else ""
        parts.append(f"--- RAG Hit {i}: kind={kind}, {source}{score_text} ---\n{hit.get('text', '')}\n")
    return "\n".join(parts)


def sanitize_writer_context(text: str) -> str:
    """Remove exam-only blocks before text is sent to the writer."""
    sanitized: list[str] = []
    redacting = False
    for line in text.splitlines():
        if _SENSITIVE_EXAM_HEADER_RE.search(line):
            if not sanitized or sanitized[-1] != "[REDACTED writer-invisible exam content]":
                sanitized.append("[REDACTED writer-invisible exam content]")
            redacting = True
            continue
        if redacting:
            if _MARKDOWN_HEADER_RE.match(line):
                redacting = False
            else:
                continue
        sanitized.append(line)
    return "\n".join(sanitized).strip()
