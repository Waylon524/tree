"""ExaminerAgent: Phase A node exam assembly, Phase B dual audit.

`Covered_Node_IDs` are Dagger canonical node ids and `node_context` is the
ActiveNode boundary.
"""

from __future__ import annotations

import time
from pathlib import Path

from tree.agents.base import Agent
from tree.agents.parsers import (
    ParseError,
    extract_bottleneck_report,
    parse_exam_id,
    parse_exam_sections,
    parse_route,
)
from tree.io import paths
from tree.model.client import LLMClient
from tree.state.models import AuditResult, ExamSections


class ExaminerAgent(Agent):
    role = "examiner"

    def __init__(
        self,
        client: LLMClient,
        *,
        max_format_retries: int = 2,
        project_root: Path | None = None,
    ):
        super().__init__(client)
        self._max_format_retries = max_format_retries
        self._project_root = project_root

    async def compose(
        self,
        *,
        next_seq: str,
        prior_paths: list[str],
        prior_contents: list[str],
        retrieved: list[dict] | None = None,
        node_context: str | None = None,
        branch_context: str | None = None,
    ) -> ExamSections:
        parts = [
            "## Task: Exam Assembly (Phase A)\n",
            f"Next file sequence number: {next_seq}\n",
            "Prior completed file paths:\n" + "\n".join(f"  - {p}" for p in prior_paths) + "\n",
        ]
        context = node_context or branch_context
        if context:
            parts.append(
                "Planner-bound ActiveNode context. Treat this as the highest-priority scope "
                f"constraint for Phase A:\n{context}\n"
            )
        if retrieved:
            parts.append(_format_retrieved(retrieved))
        if prior_contents:
            parts.append("Prior completed file contents:\n")
            for i, content in enumerate(prior_contents):
                parts.append(f"--- File {i + 1} ---\n{content}\n")

        user = "\n".join(parts)
        raw = await self.complete(user)
        raw = await self._repair_exam_format(user, raw)
        return parse_exam_sections(raw)

    async def audit(
        self,
        *,
        exam_paper: str,
        answer_key: str,
        student_answer: str,
        draft_text: str | None,
        prior_paths: list[str],
        prior_contents: list[str],
        previous_bottleneck: str | None = None,
        retrieved: list[dict] | None = None,
        node_context: str | None = None,
        branch_context: str | None = None,
    ) -> AuditResult:
        parts = [
            "## Task: Dual Audit & Reporting (Phase B)\n",
            f"[Exam Paper]:\n{exam_paper}\n",
            f"[Standard Answers]:\n{answer_key}\n",
            f"[Student's Exam Responses]:\n{student_answer}\n",
            f"[Current Draft]: {draft_text if draft_text else '尚未创建'}\n",
        ]
        if previous_bottleneck:
            parts.append(f"[Previous Bottleneck Report]:\n{previous_bottleneck}\n")
        context = node_context or branch_context
        if context:
            parts.append(
                "Planner-bound ActiveNode context. PASS requires the draft to cover the declared "
                f"Covered_Node_IDs and stay inside this boundary:\n{context}\n"
            )
        parts.append("Prior completed file paths:\n" + "\n".join(f"  - {p}" for p in prior_paths) + "\n")
        if prior_contents:
            parts.append("Prior completed file contents:\n")
            for i, content in enumerate(prior_contents):
                parts.append(f"--- File {i + 1} ---\n{content}\n")
        if retrieved:
            parts.append(_format_retrieved(retrieved))
        parts.append(
            "You must end the response with exactly:\n"
            "ROUTE: PASS or ROUTE: FAIL_KNOWLEDGE_GAP\nEXAM_ID: <node title or output title>\n"
        )

        user = "\n".join(parts)
        raw = await self.complete(user)
        raw = await self._repair_audit_format(user, raw)
        return AuditResult(
            route=parse_route(raw),
            exam_id=parse_exam_id(raw),
            bottleneck_report=extract_bottleneck_report(raw),
        )

    async def _repair_exam_format(self, original_user: str, raw: str) -> str:
        for _ in range(self._max_format_retries):
            try:
                parse_exam_sections(raw)
                return raw
            except ParseError as exc:
                raw = await self.complete(
                    "Repair the examiner exam assembly format. Do not change the substantive "
                    "decision, scope, questions, answers, or instructions. Return a complete "
                    "response with exactly these five sections:\n"
                    "## [Next_Knowledge_Point]\n## [Covered_Node_IDs]\n## [Blind_Exam]\n"
                    "## [Answer_Key]\n## [Writer_Instructions]\n\n"
                    f"Parser error: {exc}\n\nOriginal task:\n{original_user}\n\n"
                    f"Previous unparseable output:\n{raw}\n"
                )
        try:
            parse_exam_sections(raw)
        except ParseError as exc:
            self._write_format_failure("exam assembly", original_user, raw, str(exc))
            raise
        return raw

    async def _repair_audit_format(self, original_user: str, raw: str) -> str:
        for _ in range(self._max_format_retries):
            try:
                parse_route(raw)
                parse_exam_id(raw)
                return raw
            except ParseError:
                raw = await self.complete(
                    "Repair the machine-readable audit format. Do not change the judgment or "
                    "invent analysis. Preserve the Bottleneck Report meaning, but end with exactly:\n"
                    "ROUTE: PASS\nEXAM_ID: <title>\nor:\nROUTE: FAIL_KNOWLEDGE_GAP\nEXAM_ID: <title>\n\n"
                    f"Original task:\n{original_user}\n\nPrevious unparseable output:\n{raw}\n"
                )
        return raw

    def _write_format_failure(self, task: str, original_user: str, raw: str, error: str) -> None:
        if self._project_root is None:
            return
        out_dir = paths.pipeline_temp_root(self._project_root)
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"examiner-format-failure-{time.strftime('%Y%m%d-%H%M%S')}-{task.replace(' ', '-')}.md"
        path.write_text(
            f"# Examiner Format Failure\n\nTask: {task}\nParser error: {error}\n\n"
            f"## Original Prompt\n\n{original_user}\n\n## Final Output\n\n{raw}\n",
            encoding="utf-8",
        )


def _format_retrieved(retrieved: list[dict]) -> str:
    parts = [
        "Retrieved RAG context:\n"
        "- content_kind=source hits are teacher-side source material for possible new teaching.\n"
        "- content_kind=finished hits are already-taught student-visible material; treat as a strict "
        "no-duplicate boundary.\n"
        "- content_kind=ledger hits summarize finished outputs and possible duplicate overlap.\n"
    ]
    for i, hit in enumerate(retrieved, start=1):
        meta = hit.get("metadata") or {}
        kind = meta.get("content_kind") or "unknown"
        source = meta.get("path") or meta.get("filename") or meta.get("doc_id") or "unknown"
        score = hit.get("score")
        score_text = f", score={score:.4f}" if isinstance(score, float) else ""
        parts.append(f"--- RAG Hit {i}: kind={kind}, {source}{score_text} ---\n{hit.get('text', '')}\n")
    return "\n".join(parts)
