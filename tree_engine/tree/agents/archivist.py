"""Archivist agent: structure raw OCR text into clean Markdown."""

from __future__ import annotations

import json
import re
from typing import Any

from tree.agents.loader import AgentLoader
from tree.model.client import LLMClient


class ArchivistAgent:
    def __init__(self, client: LLMClient, loader: AgentLoader):
        self._client = client
        self._loader = loader

    async def structure(self, raw_text: str) -> str:
        system = self._loader.load("archivist")
        user = (
            "## Task: Structure OCR Output\n\n"
            "Transform the following raw OCR output into clean Markdown.\n\n"
            "## Raw OCR Text\n"
            f"{raw_text}"
        )
        return await self._client.call("archivist", system, user)

    async def analyze_source_chunk(self, chunk: dict[str, Any]) -> dict[str, Any]:
        """Analyze one source RAG chunk into curriculum inventory JSON."""
        system = self._loader.load("archivist")
        metadata = chunk.get("metadata") or {}
        text = str(chunk.get("text") or "")
        user = (
            "## Task: Analyze Source Chunk For Curriculum Inventory\n\n"
            "Return strict JSON only. Do not wrap it in Markdown.\n\n"
            "Schema:\n"
            "{\n"
            '  "core_concepts": ["short concept names"],\n'
            '  "methods": ["methods, procedures, skills, or operations"],\n'
            '  "misconceptions": ["common mistakes or contrastive warnings"],\n'
            '  "prerequisites": ["concepts that should be learned before this chunk"],\n'
            '  "source_type": "lecture|exercise|reference|mixed|unknown",\n'
            '  "teaching_role": "foundation|concept|method|example|application|review|assessment",\n'
            '  "summary": "one concise sentence"\n'
            "}\n\n"
            "Rules:\n"
            "- Be domain-neutral. Do not assume this is programming; infer from the chunk.\n"
            "- Prefer real subject concepts over UI words, page labels, headings like 教学目标, or file names.\n"
            "- Keep each list concise: 3-10 items unless the chunk is very dense.\n"
            "- prerequisites should be conceptual dependencies, not upload order.\n\n"
            f"Metadata:\n{json.dumps(metadata, ensure_ascii=False)}\n\n"
            f"Chunk text:\n{text[:6000]}"
        )
        raw = await self._client.call("archivist", system, user)
        return _extract_json_object(raw)

    async def build_curriculum_map(
        self,
        inventory_summary: dict[str, Any],
        completed_collections: list[str],
    ) -> dict[str, Any]:
        """Build domain-neutral curriculum map candidates from source inventory JSON."""
        system = self._loader.load("archivist")
        user = (
            "## Task: Build Curriculum Map Candidates\n\n"
            "Return strict JSON only. Do not wrap it in Markdown.\n\n"
            "Input is a source inventory summary. Create candidate chapter clusters by semantic "
            "teaching units, not upload order. A chapter may use multiple related source collections, "
            "and one collection may still be split later by the examiner.\n\n"
            "Schema:\n"
            "{\n"
            '  "chapter_candidates": [\n'
            "    {\n"
            '      "candidate_id": "candidate:<stable-id>",\n'
            '      "title_hint": "broad textbook-style chapter title",\n'
            '      "primary_source_collection": "collection id",\n'
            '      "source_collections": ["primary", "related"],\n'
            '      "core_concepts": ["main chapter concepts"],\n'
            '      "prerequisite_concepts": ["concepts that should precede this chapter"],\n'
            '      "prerequisite_candidates": ["candidate ids if obvious"],\n'
            '      "representative_chunks": ["chunk refs"],\n'
            '      "reason": "brief rationale"\n'
            "    }\n"
            "  ]\n"
            "}\n\n"
            "Rules:\n"
            "- Do not encode domain-specific fixed ranks. Infer prerequisites from concepts.\n"
            "- If a source collection is mostly application/review, place its prerequisite concepts clearly.\n"
            "- Do not mark a candidate completed; the engine will do that.\n"
            "- Use only collection ids and chunk refs that appear in the input.\n\n"
            f"Completed collections:\n{json.dumps(completed_collections, ensure_ascii=False)}\n\n"
            f"Inventory summary:\n{json.dumps(inventory_summary, ensure_ascii=False)[:18000]}"
        )
        raw = await self._client.call("archivist", system, user)
        return _extract_json_object(raw)


def _extract_json_object(raw: str) -> dict[str, Any]:
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        loaded = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            raise
        loaded = json.loads(match.group(0))
    if not isinstance(loaded, dict):
        raise ValueError("Archivist JSON response must be an object")
    return loaded
