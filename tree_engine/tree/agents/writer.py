"""WriterAgent: CREATE or OPTIMIZE a node draft."""

from __future__ import annotations

import re

from tree.agents.base import Agent
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


class WriterAgent(Agent):
    role = "writer"

    async def draft(
        self,
        *,
        span_title: str,
        file_seq: str,
        bottleneck_report: str,
        prior_paths: list[str],
        prior_contents: list[str],
        draft_text: str | None = None,
        previous_bottleneck: str | None = None,
        writer_instructions: str | None = None,
        retrieved: list[dict] | None = None,
        node_context: str | None = None,
        branch_context: str | None = None,
    ) -> WriterResult:
        mode = "OPTIMIZE" if draft_text else "CREATE"
        bottleneck_report = sanitize_writer_context(bottleneck_report)
        previous_bottleneck = sanitize_writer_context(previous_bottleneck) if previous_bottleneck else None
        writer_instructions = sanitize_writer_context(writer_instructions) if writer_instructions else None

        parts = [
            f"## Task: {mode} mode\n",
            f"Declared node title: {span_title}\n",
            f"File sequence: {file_seq}\n",
            f"Bottleneck Report:\n{bottleneck_report}\n",
        ]
        if previous_bottleneck:
            parts.append(f"Previous Bottleneck Report:\n{previous_bottleneck}\n")
        parts.append(
            f"Current draft (OPTIMIZE this):\n{draft_text}\n" if draft_text
            else "Current draft: 尚未创建 (CREATE from scratch)\n"
        )
        if writer_instructions:
            parts.append(f"[Writer_Instructions]:\n{writer_instructions}\n")
        context = node_context or branch_context
        if context:
            parts.append(
                "Planner-bound ActiveNode context. Use it only to enforce the declared single-node "
                f"delta and prerequisite boundaries:\n{context}\n"
            )
        if retrieved:
            parts.append(_format_retrieved(retrieved))
        parts.append("Prior completed file paths:\n" + "\n".join(f"  - {p}" for p in prior_paths) + "\n")
        if prior_contents:
            parts.append("Prior completed file contents:\n")
            for i, content in enumerate(prior_contents):
                parts.append(f"--- File {i + 1} ---\n{content}\n")

        raw = await self.complete("\n".join(parts))
        return WriterResult(draft_content=raw)

    async def revise_from_feedback(
        self,
        *,
        span_title: str,
        file_seq: str,
        current_text: str,
        user_feedback: str,
        prior_paths: list[str],
        prior_contents: list[str],
        retrieved: list[dict] | None = None,
        node_context: str | None = None,
    ) -> WriterResult:
        """Surgically revise a finished learning node from reader feedback."""
        feedback = sanitize_writer_context(user_feedback)
        instructions = (
            "This is a learner feedback revision, not a full NodeRun. "
            "Apply the smallest necessary edits to the current generated Markdown. "
            "Preserve correct sections, the current H1, and the deterministic prerequisite block "
            "if present. Do not rerun or reveal exam logic. Do not expand into future or sibling "
            "KnowledgeNodes. Return the full revised Markdown file, not a patch."
        )
        return await self.draft(
            span_title=span_title,
            file_seq=file_seq,
            bottleneck_report=f"User feedback for this generated learning node:\n{feedback}",
            prior_paths=prior_paths,
            prior_contents=prior_contents,
            draft_text=current_text,
            previous_bottleneck=None,
            writer_instructions=instructions,
            retrieved=retrieved,
            node_context=node_context,
        )


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


def _format_retrieved(retrieved: list[dict]) -> str:
    parts = [
        "Retrieved RAG context:\n"
        "- content_kind=source hits may teach the current declared node.\n"
        "- content_kind=finished hits are already taught; cite briefly as prerequisites, do not duplicate.\n"
        "- content_kind=ledger hits summarize the delta that must remain after removing duplicates.\n"
    ]
    for i, hit in enumerate(retrieved, start=1):
        meta = hit.get("metadata") or {}
        kind = meta.get("content_kind") or "unknown"
        source = meta.get("path") or meta.get("filename") or meta.get("doc_id") or "unknown"
        score = hit.get("score")
        score_text = f", score={score:.4f}" if isinstance(score, float) else ""
        parts.append(f"--- RAG Hit {i}: kind={kind}, {source}{score_text} ---\n{hit.get('text', '')}\n")
    return "\n".join(parts)
