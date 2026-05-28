"""Examiner agent: compose exam (Phase A), audit (Phase B), scan chapters (Phase C)."""

from __future__ import annotations

from tree.agents.loader import AgentLoader
from tree.agents.parsers import (
    ParseError,
    detect_chapter_complete,
    detect_pipeline_complete,
    extract_bottleneck_report,
    parse_exam_id,
    parse_exam_output,
    parse_route,
)
from tree.deepseek.client import LLMClient
from tree.state.models import AuditResult, ExamSections, ExamTooBroadContext


class ExaminerAgent:
    def __init__(self, client: LLMClient, loader: AgentLoader):
        self._client = client
        self._loader = loader

    async def compose_exam(
        self,
        next_seq: str,
        prior_file_contents: list[str],
        prior_file_paths: list[str],
        source_material_contents: list[str] | None = None,
        source_material_paths: list[str] | None = None,
        retrieved_context: list[dict] | None = None,
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

        user = "\n".join(parts)
        raw = await self._client.call("examiner", system, user)

        route = parse_route(raw)
        exam_id = parse_exam_id(raw)
        report = extract_bottleneck_report(raw)
        return AuditResult(route=route, exam_id=exam_id, bottleneck_report=report)

    async def scan_next_chapter(
        self,
        pipeline_state_text: str,
        source_payload: dict[str, list[dict[str, str]]],
    ) -> tuple[str | None, bool]:
        system = self._loader.load("examiner")
        user = (
            "## Task: Chapter Continuation Scan (Phase C)\n\n"
            f"pipeline-state.json:\n{pipeline_state_text}\n\n"
            "Structured source material collections:\n"
        )
        for collection, docs in source_payload.items():
            user += f"\n# Collection: {collection}\n"
            for doc in docs:
                user += f"\n--- {doc['path']} ---\n{doc['content']}\n"
        raw = await self._client.call("examiner", system, user)

        if detect_pipeline_complete(raw):
            return None, True

        try:
            sections = parse_exam_output(raw)
            return sections.knowledge_point, False
        except ParseError:
            return raw.strip()[:50], False


def _format_retrieved_context(retrieved_context: list[dict]) -> str:
    parts = ["Retrieved RAG context (supporting excerpts, verify against source paths):\n"]
    for i, hit in enumerate(retrieved_context, start=1):
        metadata = hit.get("metadata") or {}
        source = metadata.get("path") or metadata.get("filename") or metadata.get("doc_id") or "unknown"
        score = hit.get("score")
        score_text = f", score={score:.4f}" if isinstance(score, float) else ""
        parts.append(f"--- RAG Hit {i}: {source}{score_text} ---\n{hit.get('text', '')}\n")
    return "\n".join(parts)
