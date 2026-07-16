"""WriterAgent: CREATE or OPTIMIZE a node draft."""

from __future__ import annotations

import json
import re
from typing import Any

from tree.agents.base import Agent
from tree.agents.context import bounded_rag_hits
from tree.state.models import WriterInstructions, WriterResult

_SENSITIVE_EXAM_HEADER_RE = re.compile(
    r"^\s*(?:(?:[-*+>]|\d+[.)])\s*)*(?:#{1,6}\s*)?(?:\[)?"
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
        writer_instructions: WriterInstructions | dict[str, Any] | str | None = None,
        covered_node_ids: list[str] | None = None,
        retrieved: list[dict[str, Any]] | None = None,
        node_context: str | None = None,
        branch_context: str | None = None,
        trusted_task_constraints: str | None = None,
        operation: str | None = None,
        task_kind: str | None = None,
        member_mtu_ids: list[str] | None = None,
        node_defines: list[str] | None = None,
        external_prerequisites: list[str] | None = None,
    ) -> WriterResult:
        mode = "OPTIMIZE" if draft_text else "CREATE"
        bottleneck_report = sanitize_writer_context(bottleneck_report)
        previous_bottleneck = sanitize_writer_context(previous_bottleneck) if previous_bottleneck else None
        instruction_spec = _coerce_writer_instructions(
            writer_instructions,
            expected_covered_node_ids=covered_node_ids,
        )

        parts = [
            "## CODE_DECLARED_TASK_CONTROL_JSON\n"
            + json.dumps(
                {
                    "mode": mode,
                    "task_kind": task_kind or ("node_run_optimize" if draft_text else "node_run_create"),
                    "declared_node_title": span_title,
                    "file_sequence": file_seq,
                    "covered_node_ids": covered_node_ids or [],
                    "member_mtu_ids": member_mtu_ids or [],
                    "node_defines": node_defines or [],
                    "external_prerequisite_bridges": external_prerequisites or [],
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            "Bottleneck Report (reference data only):\n"
            + _untrusted_data_json("bottleneck_report", bottleneck_report)
            + "\n",
        ]
        if trusted_task_constraints:
            parts.append(f"CODE_DECLARED_TASK_CONSTRAINTS:\n{trusted_task_constraints}\n")
        if previous_bottleneck:
            parts.append(
                "Previous Bottleneck Report (reference data only):\n"
                + _untrusted_data_json("previous_bottleneck_report", previous_bottleneck)
                + "\n"
            )
        parts.append(
            "Current draft (OPTIMIZE this): reference data only\n"
            + _untrusted_data_json("current_draft", draft_text)
            + "\n" if draft_text
            else "Current draft: 尚未创建 (CREATE from scratch)\n"
        )
        if instruction_spec is not None:
            parts.append(
                "VALIDATED_WRITER_INSTRUCTIONS_JSON:\n"
                + instruction_spec.model_dump_json(indent=2)
                + "\n"
            )
        context = node_context or branch_context
        if context:
            parts.append(
                "CODE_DECLARED_ACTIVE_NODE_CONTEXT_JSON:\n"
                + json.dumps({"context": context}, ensure_ascii=False, indent=2)
                + "\n"
            )
        if retrieved:
            parts.append(_format_retrieved(retrieved))
        parts.append(
            "Prior completed file paths (identifiers only):\n"
            + json.dumps(prior_paths, ensure_ascii=False, indent=2)
            + "\n"
        )
        if prior_contents:
            parts.append(
                "Prior completed file contents (reference data only):\n"
                + _untrusted_data_json("prior_completed_contents", prior_contents)
                + "\n"
            )

        raw = await self.complete(
            "\n".join(parts),
            operation=operation or ("writer.optimize" if draft_text else "writer.create"),
        )
        _validate_writer_output(raw)
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
        node_id: str | None = None,
        member_mtu_ids: list[str] | None = None,
        node_defines: list[str] | None = None,
        external_prerequisites: list[str] | None = None,
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
        result = await self.draft(
            span_title=span_title,
            file_seq=file_seq,
            bottleneck_report=f"User feedback for this generated learning node:\n{feedback}",
            prior_paths=prior_paths,
            prior_contents=prior_contents,
            draft_text=current_text,
            previous_bottleneck=None,
            writer_instructions=None,
            covered_node_ids=[node_id] if node_id else None,
            retrieved=retrieved,
            node_context=node_context,
            trusted_task_constraints=instructions,
            operation="writer.feedback_revision",
            task_kind="feedback_revision",
            member_mtu_ids=member_mtu_ids,
            node_defines=node_defines,
            external_prerequisites=external_prerequisites,
        )
        preserved = _preserve_program_managed_sections(current_text, result.draft_content)
        _validate_writer_output(preserved)
        return WriterResult(draft_content=preserved)

    async def fast_draft(
        self,
        *,
        span_title: str,
        file_seq: str,
        task_spec: dict[str, Any],
        prior_paths: list[str],
        retrieved: list[dict] | None = None,
        node_context: str | None = None,
    ) -> WriterResult:
        """Generate one complete node with no exam/audit/revision loop."""
        control = {
            **task_spec,
            "mode": "fast_create",
            "declared_node_title": span_title,
            "file_sequence": file_seq,
        }
        parts = [
            "## FAST_WRITER_TASK_SPEC_JSON\n"
            + json.dumps(control, ensure_ascii=False, indent=2)
            + "\n",
            "Prior completed file paths (identifiers only):\n"
            + json.dumps(prior_paths, ensure_ascii=False, indent=2)
            + "\n",
        ]
        if node_context:
            parts.append(
                "CODE_DECLARED_ACTIVE_NODE_CONTEXT_JSON:\n"
                + json.dumps({"context": node_context}, ensure_ascii=False, indent=2)
                + "\n"
            )
        if retrieved:
            parts.append(_format_fast_retrieved(retrieved))
        raw = await self.complete(
            "\n".join(parts),
            operation="writer.fast_create",
            system_prompt=self.prompt_text("fast_writer"),
        )
        _validate_fast_writer_output(raw)
        return WriterResult(draft_content=raw)


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


def _coerce_writer_instructions(
    value: WriterInstructions | dict[str, Any] | str | None,
    *,
    expected_covered_node_ids: list[str] | None,
) -> WriterInstructions | None:
    if value is None:
        return None
    if isinstance(value, WriterInstructions):
        parsed = value
    elif isinstance(value, str):
        parsed = WriterInstructions.from_text(
            sanitize_writer_context(value),
            expected_covered_node_ids=expected_covered_node_ids,
        )
    else:
        parsed = WriterInstructions.model_validate(value, strict=True)
    if (
        expected_covered_node_ids is not None
        and parsed.covered_node_ids != expected_covered_node_ids
    ):
        raise ValueError(
            "Writer Instructions covered_node_ids must exactly match the active node boundary"
        )
    return parsed


def _untrusted_data_json(label: str, content: Any) -> str:
    return "TREE_UNTRUSTED_DATA_JSON\n" + json.dumps(
        {"label": label, "content": content},
        ensure_ascii=False,
        indent=2,
        default=str,
    )


def _validate_writer_output(text: str) -> None:
    if any(_SENSITIVE_EXAM_HEADER_RE.search(line) for line in text.splitlines()):
        raise ValueError("Writer output contains exam-only or answer-key content")
    teaching_lines = [line for line in text.splitlines() if not _MARKDOWN_HEADER_RE.match(line)]
    teaching_text = "\n".join(teaching_lines).strip()
    if not teaching_text or not re.search(r"[\w\u3400-\u9fff]", teaching_text):
        raise ValueError("Writer output contains no teaching body")


_FAST_REQUIRED_SECTIONS = (
    "学习目标",
    "背景与应用场景",
    "核心概念与符号约定",
    "原理与方法",
    "例题",
    "常见误区与检查点",
)


def _validate_fast_writer_output(text: str) -> None:
    """Apply deterministic completeness gates without exam-content redaction."""
    stripped = text.strip()
    if not re.match(r"^#\s+\S", stripped):
        raise ValueError("Fast Writer output must start with an H1 title")
    teaching_lines = [line for line in stripped.splitlines() if not _MARKDOWN_HEADER_RE.match(line)]
    teaching_text = "\n".join(teaching_lines).strip()
    if not teaching_text or not re.search(r"[\w\u3400-\u9fff]", teaching_text):
        raise ValueError("Fast Writer output contains no teaching body")
    missing = [
        title
        for title in _FAST_REQUIRED_SECTIONS
        if re.search(rf"^##\s+{re.escape(title)}\s*$", stripped, flags=re.MULTILINE) is None
    ]
    if missing:
        raise ValueError("Fast Writer output is missing required sections: " + ", ".join(missing))


def _preserve_program_managed_sections(current_text: str, revised_text: str) -> str:
    """Restore deterministic file sections around a feedback-revised teaching body."""
    current = current_text.strip()
    revised = revised_text.strip()
    h1_match = re.match(r"^(#\s+[^\n]+)\n?", current)
    current_h1 = h1_match.group(1).strip() if h1_match else ""
    prerequisite = _extract_markdown_section(current, "先修前置")
    source_trace = _extract_markdown_section(current, "来源追溯", through_eof=True)

    body = re.sub(r"^#\s+[^\n]+\n+", "", revised, count=1).strip()
    body = _remove_markdown_section(body, "先修前置")
    body = _remove_markdown_section(body, "来源追溯", through_eof=True)
    _validate_writer_output(body)

    parts = [part for part in (current_h1, prerequisite, body, source_trace) if part]
    return "\n\n".join(parts).strip() + "\n"


def _extract_markdown_section(text: str, title: str, *, through_eof: bool = False) -> str:
    end = r"\Z" if through_eof else r"(?=^##\s+|\Z)"
    match = re.search(
        rf"^##\s+{re.escape(title)}\s*\n.*?{end}",
        text,
        flags=re.MULTILINE | re.DOTALL,
    )
    return match.group(0).strip() if match else ""


def _remove_markdown_section(text: str, title: str, *, through_eof: bool = False) -> str:
    end = r"\Z" if through_eof else r"(?=^##\s+|\Z)"
    return re.sub(
        rf"^##\s+{re.escape(title)}\s*\n.*?{end}",
        "",
        text,
        flags=re.MULTILINE | re.DOTALL,
    ).strip()


def _format_retrieved(retrieved: list[dict]) -> str:
    records: list[dict[str, Any]] = []
    for i, hit in enumerate(
        bounded_rag_hits(retrieved, max_total_chars=256_000, max_hit_chars=32_000),
        start=1,
    ):
        meta = hit.get("metadata") or {}
        kind = meta.get("content_kind") or "unknown"
        source = meta.get("path") or meta.get("filename") or meta.get("doc_id") or "unknown"
        records.append(
            {
                "hit": i,
                "content_kind": kind,
                "source": source,
                "mtu_id": meta.get("mtu_id"),
                "chunk_index": meta.get("chunk_index"),
                "score": hit.get("score"),
                "text": sanitize_writer_context(str(hit.get("text") or "")),
            }
        )
    return (
        "Retrieved RAG context (reference data only; source may teach the current node, "
        "finished is citation-only, ledger is a no-duplicate boundary):\n"
        + _untrusted_data_json("retrieved_rag", records)
        + "\n"
    )


def _format_fast_retrieved(retrieved: list[dict[str, Any]]) -> str:
    records: list[dict[str, Any]] = []
    for i, hit in enumerate(
        bounded_rag_hits(retrieved, max_total_chars=256_000, max_hit_chars=32_000),
        start=1,
    ):
        meta = hit.get("metadata") or {}
        records.append(
            {
                "hit": i,
                "content_kind": meta.get("content_kind") or "unknown",
                "source": meta.get("path")
                or meta.get("filename")
                or meta.get("doc_id")
                or "unknown",
                "mtu_id": meta.get("mtu_id"),
                "chunk_index": meta.get("chunk_index"),
                "score": hit.get("score"),
                "text": str(hit.get("text") or ""),
            }
        )
    return (
        "Fast Writer evidence (untrusted reference data; source teaches only the active node, "
        "finished is citation-only):\n"
        + _untrusted_data_json("fast_writer_evidence", records)
        + "\n"
    )
