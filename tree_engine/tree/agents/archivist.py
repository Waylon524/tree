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

    async def build_candidate_nodes(
        self,
        inventory_summary: dict[str, Any],
        completed_collections: list[str],
    ) -> dict[str, Any]:
        """Build candidate knowledge nodes from source inventory JSON."""
        system = self._loader.load("archivist")
        user = (
            "## Task: Build Candidate Knowledge Nodes\n\n"
            "Return strict JSON only. Do not wrap it in Markdown.\n\n"
            "Input is a source inventory summary. Create candidate knowledge nodes by semantic "
            "teaching units, not upload order. This step does not choose curriculum order; "
            "the deterministic graph planner will choose roots, branches, and frontier nodes.\n\n"
            "Schema:\n"
            "{\n"
            '  "chapter_candidates": [\n'
            "    {\n"
            '      "candidate_id": "candidate:<stable-id>",\n'
            '      "title_hint": "knowledge node title hint",\n'
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
            "- Do not choose roots, sequence, or next chapter. Only generate candidate nodes.\n"
            "- Do not mark a candidate completed; the engine will do that.\n"
            "- Use only collection ids and chunk refs that appear in the input.\n\n"
            f"Completed collections:\n{json.dumps(completed_collections, ensure_ascii=False)}\n\n"
            f"Inventory summary:\n{json.dumps(inventory_summary, ensure_ascii=False)[:18000]}"
        )
        raw = await self._client.call("archivist", system, user)
        return _extract_json_object(raw)

    async def build_curriculum_map(
        self,
        inventory_summary: dict[str, Any],
        completed_collections: list[str],
    ) -> dict[str, Any]:
        """Compatibility wrapper for older curriculum-map callers."""
        return await self.build_candidate_nodes(inventory_summary, completed_collections)

    async def name_chapter(self, naming_context: dict[str, Any]) -> dict[str, str]:
        """Name a closed chapter/tree from its finished output concepts."""
        system = self._loader.load("archivist")
        user = (
            "## Task: Name Closed TREE Chapter\n\n"
            "Return strict JSON only. Do not wrap it in Markdown.\n\n"
            "The input is a finished knowledge tree. Name the chapter after seeing "
            "all generated outputs, not before the tree grows.\n\n"
            "Schema:\n"
            "{\n"
            '  "chapter_title": "broad textbook-style chapter title",\n'
            '  "short_slug": "short display slug",\n'
            '  "reason": "brief reason based on the concepts"\n'
            "}\n\n"
            "Rules:\n"
            "- Use the actual knowledge points and concepts, not source collection ids.\n"
            "- Prefer a broad title that can contain all listed knowledge points.\n"
            "- Do not mention TREE, files, outputs, candidate nodes, or implementation details.\n"
            "- Keep chapter_title concise, usually 6-16 Chinese characters when the input is Chinese.\n\n"
            f"Closed tree context:\n{json.dumps(naming_context, ensure_ascii=False)[:12000]}"
        )
        raw = await self._client.call("archivist", system, user)
        from tree.curriculum.chapter_naming import parse_chapter_naming_response

        return parse_chapter_naming_response(raw)


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
