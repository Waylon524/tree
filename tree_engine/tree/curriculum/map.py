"""Curriculum map candidates built from source inventory."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Protocol

from tree.io import paths

_TITLE_STOPWORDS = {"AI", "Python", "教学目标", "教学内容"}


class CurriculumMapBuilder(Protocol):
    async def build_curriculum_map(
        self,
        inventory_summary: dict[str, Any],
        completed_collections: list[str],
    ) -> dict[str, Any]:
        """Return AI-generated curriculum map JSON."""


def load_curriculum_map(root: Path) -> dict[str, Any]:
    path = paths.curriculum_map_path(root)
    if not path.exists():
        return {"version": 1, "chapter_candidates": []}
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"version": 1, "chapter_candidates": []}
    if not isinstance(loaded, dict):
        return {"version": 1, "chapter_candidates": []}
    loaded.setdefault("version", 1)
    loaded.setdefault("chapter_candidates", [])
    return loaded


def save_curriculum_map(root: Path, curriculum_map: dict[str, Any]) -> None:
    path = paths.curriculum_map_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(
        json.dumps(curriculum_map, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    tmp.replace(path)


def rebuild_curriculum_map(
    root: Path,
    inventory: dict[str, Any],
    completed_collections: set[str] | None = None,
) -> dict[str, Any]:
    """Build candidate chapter clusters from inventory collection summaries."""
    completed_collections = completed_collections or set()
    candidates = []
    collections = [
        item
        for item in inventory.get("collections", [])
        if isinstance(item, dict)
    ]
    for collection in collections:
        source_collection = str(collection.get("source_collection") or "")
        related = _meaningful_related(collection.get("related_collections", []))
        source_collections = _unique([source_collection, *related])
        concepts = [str(item) for item in collection.get("core_concepts", []) if str(item)]
        candidate_id = f"candidate:{source_collection}"
        status = "completed" if source_collection in completed_collections else "pending"
        candidates.append(
            {
                "candidate_id": candidate_id,
                "status": status,
                "title_hint": _title_hint(concepts, source_collection),
                "primary_source_collection": source_collection,
                "source_collections": source_collections,
                "core_concepts": concepts[:24],
                "prerequisite_concepts": [],
                "prerequisite_candidates": [],
                "section_ids": collection.get("section_ids", [])[:16],
                "representative_chunks": collection.get("representative_chunks", [])[:8],
                "related_collections": collection.get("related_collections", [])[:5],
                "selection_priority": _selection_priority(collection, status),
                "reason": _candidate_reason(collection, source_collections, status),
            }
        )
    candidates.sort(
        key=lambda item: (
            item.get("status") != "pending",
            len(item.get("prerequisite_candidates", []) or []),
            len(item.get("prerequisite_concepts", []) or []),
            -float(item.get("selection_priority", 0)),
            _natural_key(str(item.get("primary_source_collection", ""))),
        )
    )
    curriculum_map = {
        "version": 1,
        "chapter_candidates": candidates,
    }
    save_curriculum_map(root, curriculum_map)
    return curriculum_map


async def rebuild_curriculum_map_with_ai(
    root: Path,
    inventory: dict[str, Any],
    builder: CurriculumMapBuilder | None,
    completed_collections: set[str] | None = None,
) -> dict[str, Any]:
    """Build curriculum map with AI when possible, else use deterministic fallback."""
    fallback = rebuild_curriculum_map(root, inventory, completed_collections)
    if builder is None:
        return fallback
    try:
        ai_map = await builder.build_curriculum_map(
            _inventory_summary_for_ai(inventory),
            sorted(completed_collections or set()),
        )
        normalized = _normalize_ai_map(ai_map, fallback, completed_collections or set())
        save_curriculum_map(root, normalized)
        return normalized
    except Exception:
        return fallback


def build_curriculum_map_context(curriculum_map: dict[str, Any], limit: int = 10) -> str:
    """Format candidate chapter clusters for examiner Phase C."""
    candidates = [
        item
        for item in curriculum_map.get("chapter_candidates", [])
        if isinstance(item, dict)
    ]
    lines = [
        "## Curriculum Map Candidates",
        "Choose the next chapter from these candidate knowledge clusters when possible.",
        "Use title_hint as guidance, but you may improve the final Next_Chapter name.",
        "",
    ]
    if not candidates:
        lines.append("(no curriculum map candidates available)")
        return "\n".join(lines)

    for candidate in candidates[:limit]:
        related = ", ".join(
            f"{item.get('source_collection')}:{item.get('score', 0):.2f}"
            for item in candidate.get("related_collections", [])[:4]
            if isinstance(item, dict)
        )
        chunks = _chunk_text(candidate.get("representative_chunks", []))
        lines.extend(
            [
                f"### {candidate.get('candidate_id')}",
                f"- status: {candidate.get('status')}",
                f"- title_hint: {candidate.get('title_hint')}",
                f"- primary_source_collection: {candidate.get('primary_source_collection')}",
                f"- source_collections: {', '.join(candidate.get('source_collections', []))}",
                f"- core_concepts: {', '.join(candidate.get('core_concepts', [])[:18])}",
                f"- prerequisite_concepts: {', '.join(candidate.get('prerequisite_concepts', [])[:12]) or 'n/a'}",
                f"- prerequisite_candidates: {', '.join(candidate.get('prerequisite_candidates', [])[:8]) or 'n/a'}",
                f"- related_collections: {related or 'n/a'}",
                f"- representative_chunks: {chunks or 'n/a'}",
                f"- reason: {candidate.get('reason')}",
                "",
            ]
        )
    if len(candidates) > limit:
        lines.append(f"... {len(candidates) - limit} more candidates omitted")
    return "\n".join(lines).strip()


def _meaningful_related(items: Any, threshold: float = 0.18) -> list[str]:
    if not isinstance(items, list):
        return []
    result = []
    for item in items:
        if not isinstance(item, dict):
            continue
        if float(item.get("score") or 0) < threshold:
            continue
        collection = str(item.get("source_collection") or "")
        if collection:
            result.append(collection)
    return result


def _selection_priority(collection: dict[str, Any], status: str) -> float:
    if status != "pending":
        return 0.0
    concept_count = len(collection.get("core_concepts", []) or [])
    chunk_count = int(collection.get("chunk_count") or 0)
    related_count = len(collection.get("related_collections", []) or [])
    return min(1.0, concept_count / 18 * 0.55 + chunk_count / 24 * 0.35 + related_count / 5 * 0.1)


def _inventory_summary_for_ai(inventory: dict[str, Any]) -> dict[str, Any]:
    return {
        "collections": [
            {
                "source_collection": item.get("source_collection"),
                "doc_count": item.get("doc_count"),
                "chunk_count": item.get("chunk_count"),
                "paths": item.get("paths", [])[:6],
                "section_ids": item.get("section_ids", [])[:12],
                "core_concepts": item.get("core_concepts", [])[:24],
                "representative_chunks": [
                    {
                        "chunk_ref": chunk.get("chunk_ref"),
                        "core_concepts": chunk.get("core_concepts", [])[:8],
                        "summary": chunk.get("summary", ""),
                    }
                    for chunk in item.get("representative_chunks", [])[:6]
                    if isinstance(chunk, dict)
                ],
                "related_collections": item.get("related_collections", [])[:5],
            }
            for item in inventory.get("collections", [])
            if isinstance(item, dict)
        ]
    }


def _normalize_ai_map(
    ai_map: dict[str, Any],
    fallback: dict[str, Any],
    completed_collections: set[str],
) -> dict[str, Any]:
    fallback_by_collection = {
        item.get("primary_source_collection"): item
        for item in fallback.get("chapter_candidates", [])
        if isinstance(item, dict)
    }
    available = set(fallback_by_collection)
    candidates = []
    for index, raw in enumerate(ai_map.get("chapter_candidates", []), start=1):
        if not isinstance(raw, dict):
            continue
        primary = str(raw.get("primary_source_collection") or "")
        if primary not in available:
            continue
        fallback_item = fallback_by_collection[primary]
        collections = [
            item
            for item in _string_list(raw.get("source_collections"))
            if item in available
        ]
        if primary not in collections:
            collections.insert(0, primary)
        candidate_id = str(raw.get("candidate_id") or f"candidate:{primary}")
        if not candidate_id.startswith("candidate:"):
            candidate_id = f"candidate:{index:02d}:{primary}"
        candidates.append(
            {
                **fallback_item,
                "candidate_id": candidate_id,
                "status": "completed" if primary in completed_collections else "pending",
                "title_hint": str(raw.get("title_hint") or fallback_item.get("title_hint") or primary),
                "primary_source_collection": primary,
                "source_collections": collections,
                "core_concepts": _string_list(raw.get("core_concepts")) or fallback_item.get("core_concepts", []),
                "prerequisite_concepts": _string_list(raw.get("prerequisite_concepts")),
                "prerequisite_candidates": _string_list(raw.get("prerequisite_candidates")),
                "representative_chunks": _normalize_representative_chunks(
                    raw.get("representative_chunks"),
                    fallback_item.get("representative_chunks", []),
                ),
                "reason": str(raw.get("reason") or fallback_item.get("reason") or ""),
                "map_mode": "ai",
            }
        )
    if not candidates:
        return fallback
    seen_primary = {item["primary_source_collection"] for item in candidates}
    for primary, item in fallback_by_collection.items():
        if primary not in seen_primary:
            candidates.append(item)
    candidate_ids = {item["candidate_id"] for item in candidates}
    for item in candidates:
        item["prerequisite_candidates"] = [
            candidate
            for candidate in item.get("prerequisite_candidates", [])
            if candidate in candidate_ids
        ]
    candidates = _sort_candidates_by_prerequisites(candidates)
    return {"version": 1, "map_mode": "ai_with_rule_fallback", "chapter_candidates": candidates}


def _sort_candidates_by_prerequisites(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    pending = {item["candidate_id"]: item for item in candidates}
    result = []
    while pending:
        ready = [
            item
            for item in pending.values()
            if all(dep not in pending for dep in item.get("prerequisite_candidates", []))
        ]
        if not ready:
            ready = list(pending.values())
        ready.sort(
            key=lambda item: (
                item.get("status") != "pending",
                len(item.get("prerequisite_concepts", []) or []),
                -float(item.get("selection_priority", 0)),
                _natural_key(str(item.get("primary_source_collection", ""))),
            )
        )
        item = ready[0]
        result.append(item)
        pending.pop(item["candidate_id"], None)
    return result


def _normalize_representative_chunks(value: Any, fallback: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return fallback
    refs = {str(item) for item in value if isinstance(item, str)}
    if not refs:
        return fallback
    matched = [chunk for chunk in fallback if chunk.get("chunk_ref") in refs]
    return matched or fallback


def _string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        value = re.split(r"[,\n，、]+", value)
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _candidate_reason(
    collection: dict[str, Any],
    source_collections: list[str],
    status: str,
) -> str:
    concepts = ", ".join((collection.get("core_concepts", []) or [])[:8])
    sections = ", ".join((collection.get("section_ids", []) or [])[:5])
    if status == "completed":
        prefix = "Already completed source collection."
    else:
        prefix = "Pending source knowledge cluster."
    return (
        f"{prefix} Collections: {', '.join(source_collections)}. "
        f"Core concepts: {concepts or 'n/a'}. Sections: {sections or 'n/a'}."
    )


def _title_hint(concepts: list[str], fallback: str) -> str:
    useful = [
        concept
        for concept in concepts
        if concept not in _TITLE_STOPWORDS and not concept.isascii()
    ]
    if not useful:
        useful = [concept for concept in concepts if concept not in _TITLE_STOPWORDS]
    if not useful:
        return fallback
    if len(useful) == 1:
        return useful[0]
    return "、".join(useful[:3])


def _chunk_text(items: Any) -> str:
    if not isinstance(items, list):
        return ""
    parts = []
    for item in items[:6]:
        if not isinstance(item, dict):
            continue
        concepts = ", ".join(item.get("core_concepts", [])[:4])
        parts.append(f"{item.get('chunk_ref', '')} [{concepts}]")
    return "; ".join(part for part in parts if part.strip())


def _unique(values: Any) -> list[str]:
    seen = set()
    result = []
    for value in values:
        if not value:
            continue
        key = str(value)
        if key in seen:
            continue
        seen.add(key)
        result.append(key)
    return result


def _natural_key(value: str) -> list[Any]:
    return [int(part) if part.isdigit() else part for part in re.split(r"(\d+)", value)]
