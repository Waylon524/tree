"""Examiner agent: compose exam (Phase A), audit (Phase B), scan chapters (Phase C)."""

from __future__ import annotations

import time
from pathlib import Path

from tree.agents.loader import AgentLoader
from tree.agents.parsers import (
    ParseError,
    detect_chapter_complete,
    detect_pipeline_complete,
    extract_bottleneck_report,
    parse_exam_id,
    parse_chapter_scan_output,
    parse_exam_output,
    parse_route,
)
from tree.io import paths
from tree.model.client import LLMClient
from tree.state.models import AuditResult, ChapterScanResult, ExamSections, ExamTooBroadContext


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
        exam_too_broad_ctx: ExamTooBroadContext | None = None,
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
                "Planner-bound graph context for this active chapter. Treat this as the "
                "highest-priority scope constraint for Phase A:\n"
                f"{graph_context}\n"
            )
        if retrieved_context:
            parts.append(_format_retrieved_context(retrieved_context))
        if exam_too_broad_ctx:
            parts.append(
                f"⚠ EXAM_TOO_BROAD return from writer.\n"
                f"Reuse knowledge point name: {exam_too_broad_ctx.knowledge_point_name}\n"
                f"Bloating defects:\n{exam_too_broad_ctx.bloat_description}\n"
                f"You must reduce exam scope — remove/replace bloating question types.\n"
            )
        if prior_file_contents:
            parts.append("Prior completed file contents:\n")
            for i, content in enumerate(prior_file_contents):
                parts.append(f"--- File {i + 1} ---\n{content}\n")

        user = "\n".join(parts)
        raw = await self._client.call("examiner", system, user)

        if detect_chapter_complete(raw):
            return None, True

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
            "EXAM_ID: <knowledge point name>\n"
        )

        user = "\n".join(parts)
        raw = await self._client.call("examiner", system, user)
        raw = await self._repair_audit_format_if_needed(system, user, raw)

        route = parse_route(raw)
        exam_id = parse_exam_id(raw)
        report = extract_bottleneck_report(raw)
        return AuditResult(route=route, exam_id=exam_id, bottleneck_report=report)

    async def scan_next_chapter(
        self,
        pipeline_state_text: str,
        source_payload: dict[str, list[dict[str, str]]],
        finished_payload: dict[str, list[dict[str, str]]] | None = None,
        source_inventory_context: str | None = None,
    ) -> tuple[ChapterScanResult | None, bool]:
        system = self._loader.load("examiner")
        user = (
            "## Task: Chapter Continuation Scan (Phase C)\n\n"
            f"pipeline-state.json:\n{pipeline_state_text}\n\n"
            "Finished-output coverage already taught by TREE. Treat this as covered curriculum; "
            "do not open duplicate chapters or duplicate knowledge points:\n"
        )
        if finished_payload:
            for chapter, docs in finished_payload.items():
                user += f"\n# Finished Chapter: {chapter}\n"
                for doc in docs:
                    user += f"\n--- {doc['path']} ---\n{doc['content']}\n"
        else:
            user += "(none)\n"

        if source_inventory_context:
            user += (
                "\nPlanner-controlled context. The Selected Node Context is the primary "
                "scope for this exam. Compose for that node only. Use the full graph and "
                "warnings only to narrow, skip, or flag duplicate/merge-needed/over-broad "
                "selections; do not choose a different global direction:\n"
                f"\n{source_inventory_context}\n"
            )

        user += "\nStructured source material collections:\n"
        for collection, docs in source_payload.items():
            user += f"\n# Collection: {collection}\n"
            for doc in docs:
                user += f"\n--- {doc['path']} ---\n{doc['content']}\n"
        raw = await self._client.call("examiner", system, user)

        if detect_pipeline_complete(raw):
            return None, True

        raw = await self._repair_chapter_scan_format_if_needed(
            system,
            user,
            raw,
            source_payload,
            task_name="chapter continuation scan",
            repair_title="Repair the examiner chapter scan format",
        )
        return _parse_chapter_scan_output(raw, source_payload), False

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
                    "EXAM_ID: <knowledge point name>\n"
                    "or:\n"
                    "ROUTE: FAIL_KNOWLEDGE_GAP\n"
                    "EXAM_ID: <knowledge point name>\n\n"
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
                    "these four sections:\n"
                    "## [Next_Knowledge_Point]\n"
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

    async def _repair_chapter_scan_format_if_needed(
        self,
        system_prompt: str,
        original_user_prompt: str,
        raw_output: str,
        source_payload: dict[str, list[dict[str, str]]],
        task_name: str,
        repair_title: str,
    ) -> str:
        for _ in range(self._max_format_retries):
            try:
                _parse_chapter_scan_output(raw_output, source_payload)
                return raw_output
            except ParseError as exc:
                repair_prompt = (
                    f"{repair_title}.\n\n"
                    "Do not change the examiner's substantive decision, scope, questions, "
                    "answers, or instructions unless needed to place existing content under "
                    "the required headers. Return a complete parseable response with exactly "
                    "these ten sections:\n"
                    "## [Next_Chapter]\n"
                    "## [Source_Collection]\n"
                    "## [Source_Collections]\n"
                    "## [Graph_Node]\n"
                    "## [Required_Nodes]\n"
                    "## [Selection_Rationale]\n"
                    "## [Next_Knowledge_Point]\n"
                    "## [Blind_Exam]\n"
                    "## [Answer_Key]\n"
                    "## [Writer_Instructions]\n\n"
                    "Next_Chapter is only a provisional label; the engine assigns stable "
                    "tree ids and names closed chapters later. Source_Collection must be one primary collection id from "
                    "the source collection headings, or none. Source_Collections must be a "
                    "comma-separated list of all related source collections, primary first. "
                    "Graph_Node and Required_Nodes may be none when no graph node fits.\n\n"
                    f"Parser error: {exc}\n\n"
                    f"Original {task_name} task:\n{original_user_prompt}\n\n"
                    "Previous unparseable examiner output:\n"
                    f"{raw_output}\n"
                )
                raw_output = await self._client.call("examiner", system_prompt, repair_prompt)

        try:
            _parse_chapter_scan_output(raw_output, source_payload)
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


def _parse_chapter_scan_output(
    raw_output: str,
    source_payload: dict[str, list[dict[str, str]]],
) -> ChapterScanResult:
    result = parse_chapter_scan_output(raw_output)
    if source_payload and result.source_collection not in source_payload:
        available = ", ".join(sorted(source_payload))
        raise ParseError(
            "Source_Collection must exactly match one provided collection id. "
            f"Got {result.source_collection!r}; available: {available}"
        )
    invalid = [item for item in result.source_collections if item not in source_payload]
    if source_payload and invalid:
        available = ", ".join(sorted(source_payload))
        raise ParseError(
            "Source_Collections must contain only provided collection ids. "
            f"Got invalid {invalid!r}; available: {available}"
        )
    return result
