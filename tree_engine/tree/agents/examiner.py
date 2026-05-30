"""Examiner agent: branch-span exam assembly and audit."""

from __future__ import annotations

import time
from pathlib import Path

from tree.agents.loader import AgentLoader
from tree.agents.parsers import (
    ParseError,
    extract_bottleneck_report,
    parse_exam_id,
    parse_exam_output,
    parse_route,
)
from tree.io import paths
from tree.model.client import LLMClient
from tree.state.models import AuditResult, ExamSections


class ExaminerAgent:
    def __init__(
        self,
        client: LLMClient,
        loader: AgentLoader,
        max_format_retries: int = 2,
        project_root: Path | None = None,
    ):
        self._client = client
        self._loader = loader
        self._max_format_retries = max_format_retries
        self._project_root = project_root

    async def compose_exam(
        self,
        next_seq: str,
        prior_file_contents: list[str],
        prior_file_paths: list[str],
        source_material_contents: list[str] | None = None,
        source_material_paths: list[str] | None = None,
        retrieved_context: list[dict] | None = None,
        graph_context: str | None = None,
    ) -> tuple[ExamSections | None, bool]:
        system = self._loader.load("examiner")
        parts = [
            "## Task: Exam Assembly (Phase A)\n",
            f"Next file sequence number: {next_seq}\n",
            "Prior completed file paths:\n"
            + "\n".join(f"  - {p}" for p in prior_file_paths)
            + "\n",
        ]
        if source_material_paths:
            parts.append(
                "Source material paths:\n"
                + "\n".join(f"  - {p}" for p in source_material_paths)
                + "\n"
            )
        if source_material_contents:
            parts.append("Source material contents:\n")
            for i, content in enumerate(source_material_contents):
                parts.append(f"--- Source {i + 1} ---\n{content}\n")
        if graph_context:
            parts.append(
                "Planner-bound graph context for this active BranchRun / branch span. Treat this as the "
                "highest-priority scope constraint for Phase A:\n"
                f"{graph_context}\n"
            )
        if retrieved_context:
            parts.append(_format_retrieved_context(retrieved_context))
        if prior_file_contents:
            parts.append("Prior completed file contents:\n")
            for i, content in enumerate(prior_file_contents):
                parts.append(f"--- File {i + 1} ---\n{content}\n")

        user = "\n".join(parts)
        raw = await self._client.call("examiner", system, user)

        raw = await self._repair_exam_format_if_needed(
            system,
            user,
            raw,
            task_name="exam assembly",
            repair_title="Repair the examiner exam assembly format",
        )
        sections = parse_exam_output(raw)
        return sections, False

    async def audit(
        self,
        exam_paper: str,
        answer_key: str,
        student_answer: str,
        draft_text: str | None,
        prior_file_contents: list[str],
        prior_file_paths: list[str],
        previous_bottleneck: str | None = None,
        retrieved_context: list[dict] | None = None,
        graph_context: str | None = None,
    ) -> AuditResult:
        system = self._loader.load("examiner")
        parts = [
            "## Task: Dual Audit & Reporting (Phase B)\n",
            f"[Exam Paper]:\n{exam_paper}\n",
            f"[Standard Answers]:\n{answer_key}\n",
            f"[Student's Exam Responses]:\n{student_answer}\n",
            f"[Current Draft]: {draft_text if draft_text else '尚未创建'}\n",
        ]
        if previous_bottleneck:
            parts.append(f"[Previous Bottleneck Report]:\n{previous_bottleneck}\n")
        if graph_context:
            parts.append(
                "Planner-bound graph context for this audit. PASS requires the draft to cover "
                "the declared Covered_Node_IDs and stay inside this ActiveBranch boundary:\n"
                f"{graph_context}\n"
            )
        parts.append(
            "Prior completed file paths:\n"
            + "\n".join(f"  - {p}" for p in prior_file_paths)
            + "\n"
        )
        if prior_file_contents:
            parts.append("Prior completed file contents:\n")
            for i, content in enumerate(prior_file_contents):
                parts.append(f"--- File {i + 1} ---\n{content}\n")
        if retrieved_context:
            parts.append(_format_retrieved_context(retrieved_context))
        parts.append(
            "You must end the response with exactly these two machine-readable lines:\n"
            "ROUTE: PASS or ROUTE: FAIL_KNOWLEDGE_GAP\n"
            "EXAM_ID: <branch span or output title>\n"
        )

        user = "\n".join(parts)
        raw = await self._client.call("examiner", system, user)
        raw = await self._repair_audit_format_if_needed(system, user, raw)

        route = parse_route(raw)
        exam_id = parse_exam_id(raw)
        report = extract_bottleneck_report(raw)
        return AuditResult(route=route, exam_id=exam_id, bottleneck_report=report)

    async def _repair_audit_format_if_needed(
        self,
        system_prompt: str,
        original_user_prompt: str,
        raw_output: str,
    ) -> str:
        for _ in range(self._max_format_retries):
            try:
                parse_route(raw_output)
                parse_exam_id(raw_output)
                return raw_output
            except ParseError:
                repair_prompt = (
                    "Repair the machine-readable audit format for the previous examiner output.\n\n"
                    "Do not change the audit judgment or invent new analysis. Preserve the Bottleneck "
                    "Report meaning, but return a complete parseable response that ends with exactly:\n"
                    "ROUTE: PASS\n"
                    "EXAM_ID: <branch span or output title>\n"
                    "or:\n"
                    "ROUTE: FAIL_KNOWLEDGE_GAP\n"
                    "EXAM_ID: <branch span or output title>\n\n"
                    "Original audit task:\n"
                    f"{original_user_prompt}\n\n"
                    "Previous unparseable examiner output:\n"
                    f"{raw_output}\n"
                )
                raw_output = await self._client.call("examiner", system_prompt, repair_prompt)
        return raw_output

    async def _repair_exam_format_if_needed(
        self,
        system_prompt: str,
        original_user_prompt: str,
        raw_output: str,
        task_name: str,
        repair_title: str,
    ) -> str:
        for _ in range(self._max_format_retries):
            try:
                parse_exam_output(raw_output)
                return raw_output
            except ParseError as exc:
                repair_prompt = (
                    f"{repair_title}.\n\n"
                    "Do not change the examiner's substantive decision, scope, questions, "
                    "answers, or instructions unless needed to place existing content under "
                    "the required headers. Return a complete parseable response with exactly "
                    "these five sections:\n"
                    "## [Next_Knowledge_Point]\n"
                    "## [Covered_Node_IDs]\n"
                    "## [Blind_Exam]\n"
                    "## [Answer_Key]\n"
                    "## [Writer_Instructions]\n\n"
                    f"Parser error: {exc}\n\n"
                    f"Original {task_name} task:\n{original_user_prompt}\n\n"
                    "Previous unparseable examiner output:\n"
                    f"{raw_output}\n"
                )
                raw_output = await self._client.call("examiner", system_prompt, repair_prompt)

        try:
            parse_exam_output(raw_output)
            return raw_output
        except ParseError as exc:
            self._write_format_failure(task_name, original_user_prompt, raw_output, str(exc))
            raise

    def _write_format_failure(
        self,
        task_name: str,
        original_user_prompt: str,
        raw_output: str,
        error: str,
    ) -> None:
        if self._project_root is None:
            return
        out_dir = paths.pipeline_temp_root(self._project_root)
        out_dir.mkdir(parents=True, exist_ok=True)
        timestamp = time.strftime("%Y%m%d-%H%M%S")
        safe_task = task_name.replace(" ", "-")
        path = out_dir / f"examiner-format-failure-{timestamp}-{safe_task}.md"
        path.write_text(
            "# Examiner Format Failure\n\n"
            f"Task: {task_name}\n\n"
            f"Parser error: {error}\n\n"
            "## Original Prompt\n\n"
            f"{original_user_prompt}\n\n"
            "## Final Unparseable Output\n\n"
            f"{raw_output}\n",
            encoding="utf-8",
        )


def _format_retrieved_context(retrieved_context: list[dict]) -> str:
    parts = [
        "Retrieved RAG context:\n"
        "- content_kind=source hits are teacher-side source material for possible new teaching.\n"
        "- content_kind=finished hits are already taught student-visible material. "
        "Use them as a strict no-duplicate boundary; do not reteach their core content.\n"
        "- content_kind=ledger hits summarize finished outputs and possible duplicate overlap.\n"
    ]
    for i, hit in enumerate(retrieved_context, start=1):
        metadata = hit.get("metadata") or {}
        kind = metadata.get("content_kind") or "unknown"
        source = metadata.get("path") or metadata.get("filename") or metadata.get("doc_id") or "unknown"
        score = hit.get("score")
        score_text = f", score={score:.4f}" if isinstance(score, float) else ""
        parts.append(f"--- RAG Hit {i}: kind={kind}, {source}{score_text} ---\n{hit.get('text', '')}\n")
    return "\n".join(parts)
