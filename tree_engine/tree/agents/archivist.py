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

    async def analyze_inventory_chunk(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Analyze one chunk in file order and decide whether it joins the active group."""
        system = self._loader.load("archivist")
        user = (
            "## Task: Sequential Inventory Knowledge Grouping\n\n"
            "Return strict JSON only. Do not wrap it in Markdown.\n\n"
            "You are processing chunks from one source file in forward order. Decide whether the "
            "current chunk belongs to the previous active KnowledgeGroup. Do not reason about future chunks.\n\n"
            "Schema:\n"
            "{\n"
            '  "merge_with_previous": false,\n'
            '  "is_complete_knowledge_point": true,\n'
            '  "title_hint": "KnowledgeGroup title hint",\n'
            '  "core_concepts": ["AI-extracted concepts"],\n'
            '  "methods": ["methods or skills"],\n'
            '  "misconceptions": ["mistakes or contrastive warnings"],\n'
            '  "prerequisites": ["conceptual prerequisites"],\n'
            '  "formula_roles": [{"formula": "raw formula", "role": "definition|law|derivation|example|application"}],\n'
            '  "source_type": "lecture|exercise|reference|mixed|unknown",\n'
            '  "teaching_role": "foundation|concept|method|example|application|review|assessment",\n'
            '  "completeness": "fragment|partial|complete",\n'
            '  "evidence_spans": ["short source evidence"],\n'
            '  "summary": "one concise sentence"\n'
            "}\n\n"
            "Rules:\n"
            "- The program-provided section_id, weak_concepts, and formula signatures are weak signals only.\n"
            "- Use the chunk text as the semantic source of truth.\n"
            "- merge_with_previous may be true only when the current chunk extends the active KnowledgeGroup.\n"
            "- Prefer teaching groups that would become a coherent 300-1000 line output, but this is guidance, not a hard cap.\n"
            "- Preserve prerequisite signals from the text; never clear a prerequisite merely because the current chunk is short.\n\n"
            f"Payload:\n{json.dumps(payload, ensure_ascii=False)[:18000]}"
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
            "## Task: Build KnowledgeNodes\n\n"
            "Return strict JSON only. Do not wrap it in Markdown.\n\n"
            "Input is a source inventory summary. Create canonical KnowledgeNodes by semantic "
            "teaching units, not upload order. Input may include Inventory KnowledgeGroups and "
            "program-computed group-pair metrics. Merge cross-file groups only when they are the "
            "same teachable KnowledgeNode. Keep the compatibility JSON keys "
            "`chapter_candidates` and `candidate_id`, but semantically each item is a final KnowledgeNode. "
            "This step does not choose curriculum order; "
            "the graph planner and root selector will choose roots, branches, and frontier nodes.\n\n"
            "Schema:\n"
            "{\n"
            '  "chapter_candidates": [\n'
            "    {\n"
            '      "candidate_id": "candidate:<stable-id>",\n'
            '      "merged_group_ids": ["kg ids if available"],\n'
            '      "canonical_title": "canonical knowledge title",\n'
            '      "title_hint": "knowledge node title hint",\n'
            '      "primary_source_collection": "collection id",\n'
            '      "source_collections": ["primary", "related"],\n'
            '      "core_concepts": ["main KnowledgeNode concepts"],\n'
            '      "prerequisite_concepts": ["concepts that should precede this KnowledgeNode"],\n'
            '      "prerequisite_candidates": ["candidate ids if obvious"],\n'
            '      "formula_roles": [{"formula": "formula", "role": "role in teaching"}],\n'
            '      "representative_chunks": ["chunk refs"],\n'
            '      "coverage_evidence": ["why these groups are one node"],\n'
            '      "teaching_role": "foundation|concept|method|example|application|review|assessment",\n'
            '      "completeness": "fragment|partial|complete",\n'
            '      "reason": "brief rationale"\n'
            "    }\n"
            "  ]\n"
            "}\n\n"
            "Rules:\n"
            "- Do not encode domain-specific fixed ranks. Infer prerequisites from concepts.\n"
            "- If a source collection is mostly application/review, place its prerequisite concepts clearly.\n"
            "- AI may supplement and order prerequisites, but must not clear existing group prerequisites.\n"
            "- Candidate cross-file merge has no output-length cap; use estimated length only as a risk signal.\n"
            "- Do not choose roots, sequence, or next branch. Only generate canonical KnowledgeNodes.\n"
            "- Do not mark a KnowledgeNode completed; the engine will do that.\n"
            "- Use only collection ids and chunk refs that appear in the input.\n\n"
            f"Completed collections:\n{json.dumps(completed_collections, ensure_ascii=False)}\n\n"
            f"Inventory summary:\n{json.dumps(inventory_summary, ensure_ascii=False)[:18000]}"
        )
        raw = await self._client.call("archivist", system, user)
        return _extract_json_object(raw)

    async def select_root_candidate(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Choose the final root from the program-ranked root candidates."""
        system = self._loader.load("archivist")
        user = (
            "## Task: Select TREE Root Candidate\n\n"
            "Return strict JSON only. Do not wrap it in Markdown.\n\n"
            "The program has ranked possible root KnowledgeNodes. Choose the true tree root "
            "from the provided root_candidates only. Prefer foundational concepts with clean evidence "
            "and low prerequisite burden. Penalize application/review/example nodes and noisy section evidence.\n\n"
            "Schema:\n"
            "{\n"
            '  "selected_root_group_id": "candidate id or ROOT_UNCERTAIN",\n'
            '  "reason": "why this root is better than the others",\n'
            '  "uncertainty": "low|medium|high",\n'
            '  "teaching_order_suggestion": ["candidate ids in suggested order"]\n'
            "}\n\n"
            "Rules:\n"
            "- Choose only from root_candidates unless all are unsuitable.\n"
            "- Return ROOT_UNCERTAIN only if none of the provided candidates can reasonably start the tree.\n"
            "- Do not invent candidate ids.\n\n"
            f"Root selection payload:\n{json.dumps(payload, ensure_ascii=False)[:16000]}"
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
        """Name a closed tree from its finished output concepts."""
        system = self._loader.load("archivist")
        user = (
            "## Task: Name Closed TREE\n\n"
            "Return strict JSON only. Do not wrap it in Markdown.\n\n"
            "The input is a finished knowledge tree. Name the tree after seeing "
            "all generated outputs, not before the tree grows.\n\n"
            "Schema:\n"
            "{\n"
            '  "chapter_title": "broad textbook-style tree title",\n'
            '  "short_slug": "short display slug",\n'
            '  "reason": "brief reason based on the concepts"\n'
            "}\n\n"
            "Rules:\n"
            "- Use the actual branch-span outputs and concepts, not source collection ids.\n"
            "- Prefer a broad title that can contain all listed branch-span outputs.\n"
            "- Do not mention TREE, files, outputs, KnowledgeNode compatibility fields, or implementation details.\n"
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
