"""Chapter closure naming helpers for incremental TREE forests."""

from __future__ import annotations

import json
import re
from collections import Counter
from typing import Any

from tree.state.models import PipelineState


def next_tree_id(state: PipelineState) -> str:
    """Return the next stable internal tree id."""
    used = {
        chapter.tree_id or _tree_id_from_chapter_name(chapter.chapter_name)
        for chapter in state.chapters
    }
    index = len([item for item in used if item]) + 1
    while True:
        candidate = f"tree-{index:03d}"
        if candidate not in used:
            return candidate
        index += 1


def build_chapter_naming_context(ledger: dict[str, Any], chapter_name: str) -> dict[str, Any]:
    """Build compact context from all finished outputs in one internal tree."""
    records = [
        item
        for item in ledger.get("records", [])
        if isinstance(item, dict) and item.get("chapter") == chapter_name
    ]
    concept_counter: Counter[str] = Counter()
    source_counter: Counter[str] = Counter()
    knowledge_points = []
    summaries = []
    for record in records:
        title = str(record.get("knowledge_point") or record.get("filename") or "")
        if title:
            knowledge_points.append(title)
            concept_counter[title] += 2
        for concept in record.get("covered_concepts", []) or []:
            concept = str(concept).strip()
            if concept:
                concept_counter[concept] += 1
        for source in record.get("source_collections", []) or []:
            source = str(source).strip()
            if source:
                source_counter[source] += 1
        summary = str(record.get("summary") or "").strip()
        if summary:
            summaries.append(summary[:240])
    return {
        "tree_id": chapter_name,
        "file_count": len(records),
        "knowledge_points": _unique(knowledge_points)[:24],
        "top_concepts": _counter_keys(concept_counter, limit=30),
        "source_collections": _counter_keys(source_counter, limit=12),
        "summaries": summaries[:10],
    }


def fallback_chapter_title(context: dict[str, Any]) -> dict[str, str]:
    """Return deterministic chapter name if AI naming is unavailable."""
    concepts = [str(item) for item in context.get("top_concepts", []) if str(item).strip()]
    points = [str(item) for item in context.get("knowledge_points", []) if str(item).strip()]
    title_terms = concepts[:3] or points[:3]
    if not title_terms:
        title = str(context.get("tree_id") or "未命名章节")
    elif len(title_terms) == 1:
        title = f"{title_terms[0]}基础"
    else:
        title = "、".join(title_terms)
    return {
        "chapter_title": title,
        "short_slug": title_terms[0] if title_terms else title,
        "reason": "Deterministic fallback from finished output concepts.",
    }


def parse_chapter_naming_response(raw: str) -> dict[str, str]:
    """Parse AI chapter naming JSON."""
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
        raise ValueError("Chapter naming response must be a JSON object")
    title = str(loaded.get("chapter_title") or "").strip()
    if not title:
        raise ValueError("Chapter naming response missing chapter_title")
    return {
        "chapter_title": title,
        "short_slug": str(loaded.get("short_slug") or title).strip(),
        "reason": str(loaded.get("reason") or "").strip(),
    }


def _counter_keys(counter: Counter[str], limit: int) -> list[str]:
    return [
        key
        for key, _ in sorted(counter.items(), key=lambda item: (-item[1], item[0]))
    ][:limit]


def _unique(values: list[str]) -> list[str]:
    seen = set()
    result = []
    for value in values:
        if not value or value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _tree_id_from_chapter_name(chapter_name: str) -> str:
    """Return the stable tree segment from branch-isolated chapter paths."""
    parts = [part for part in str(chapter_name).split("/") if part]
    return parts[0] if parts else str(chapter_name)
