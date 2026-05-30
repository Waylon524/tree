"""Legacy KnowledgeNode compatibility schema backed by candidate-nodes.json."""

from __future__ import annotations

import json
import re
import hashlib
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Protocol

from tree.curriculum.graph import relation_affinity, relation_pair_scores
from tree.io import paths

_TITLE_STOPWORDS = {"AI", "Python", "教学目标", "教学内容"}
_MIN_CLUSTER_AFFINITY = 0.30
_MIN_CLUSTER_CONCEPT = 0.34
_MIN_CLUSTER_PREREQUISITE = 0.34
_MIN_CLUSTER_METHOD = 0.50
_MIN_CLUSTER_SIGNATURE = 0.46
_MIN_ADJACENT_SIGNATURE = 0.12
_MIN_STRONG_MERGE_CONCEPT = 0.55
_MIN_STRONG_MERGE_FORMULA = 0.50
_MIN_STRONG_MERGE_OVERALL = 0.32
_CANDIDATE_MERGE_BATCH_SIZE = 3
_CANDIDATE_MERGE_REPAIR_ATTEMPTS = 2
_CANDIDATE_MERGE_TIMEOUT_SEC = 240.0
_VALID_MERGE_DECISIONS = {"merged", "rejected", "blocked_pending"}
_GENERIC_TITLES = {
    "§",
    "光学",
    "机械波、电磁波",
    "机械波和电磁波",
    "温故知新",
}
_AUXILIARY_FRAGMENT_ROLES = {
    "fragment",
    "partial",
    "review",
    "header",
    "title",
    "transition",
    "proof",
    "example",
    "exercise",
    "formula_derivation",
    "derivation",
}
_TERM_RE = re.compile(r"[\u4e00-\u9fffA-Za-z][\u4e00-\u9fffA-Za-z0-9_`()+\-]*")
_SPLIT_RE = re.compile(r"[，,、/；;：:（）()\[\]【】\s的与和及或]+")
_SIGNATURE_STOPWORDS = {
    "an",
    "and",
    "based",
    "for",
    "from",
    "in",
    "known",
    "of",
    "on",
    "requiring",
    "set",
    "students",
    "their",
    "to",
    "using",
    "various",
    "write",
    "写出下列各",
    "确定下列各",
}


class CandidateNodeBuilder(Protocol):
    async def build_candidate_nodes(
        self,
        inventory_summary: dict[str, Any],
        completed_collections: list[str],
    ) -> dict[str, Any]:
        """Return AI-generated candidate node JSON."""

    async def review_candidate_merge_components(
        self,
        payload: dict[str, Any],
        *,
        timeout_sec: float | None = None,
    ) -> dict[str, Any]:
        """Return mandatory merge decisions for a small component batch."""


def load_candidate_nodes(root: Path) -> dict[str, Any]:
    path = paths.candidate_nodes_path(root)
    if not path.exists() and paths.curriculum_map_path(root).exists():
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


def save_candidate_nodes(root: Path, candidate_nodes: dict[str, Any]) -> None:
    path = paths.candidate_nodes_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(
        json.dumps(candidate_nodes, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    tmp.replace(path)


def rebuild_candidate_nodes(
    root: Path,
    inventory: dict[str, Any],
    completed_collections: set[str] | None = None,
) -> dict[str, Any]:
    """Build deterministic candidate knowledge nodes from inventory chunks."""
    completed_collections = completed_collections or set()
    groups = _inventory_groups(inventory)
    if groups:
        candidate_nodes = _rebuild_inventory_group_nodes(groups, completed_collections)
        save_candidate_nodes(root, candidate_nodes)
        return candidate_nodes

    chunks = _inventory_chunks(inventory)
    if chunks:
        candidate_nodes = _rebuild_chunk_cluster_nodes(inventory, chunks, completed_collections)
        save_candidate_nodes(root, candidate_nodes)
        return candidate_nodes

    candidate_nodes = _rebuild_collection_level_nodes(inventory, completed_collections)
    save_candidate_nodes(root, candidate_nodes)
    return candidate_nodes


def _rebuild_collection_level_nodes(
    inventory: dict[str, Any],
    completed_collections: set[str],
) -> dict[str, Any]:
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
    candidate_nodes = {
        "version": 1,
        "kind": "candidate_nodes",
        "generator": "collection_level_v1",
        "chapter_candidates": candidates,
    }
    return candidate_nodes


def _rebuild_chunk_cluster_nodes(
    inventory: dict[str, Any],
    chunks: list[dict[str, Any]],
    completed_collections: set[str],
) -> dict[str, Any]:
    union = _UnionFind([chunk["_cluster_id"] for chunk in chunks])
    nodes = [_chunk_cluster_node(chunk) for chunk in chunks]
    for index, left in enumerate(nodes):
        for right in nodes[index + 1 :]:
            scores = _candidate_pair_scores(left, right)
            if _should_cluster(scores):
                union.union(left["node_id"], right["node_id"])

    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_id = {chunk["_cluster_id"]: chunk for chunk in chunks}
    for chunk_id, chunk in by_id.items():
        grouped[union.find(chunk_id)].append(chunk)

    collection_lookup = {
        str(item.get("source_collection") or ""): item
        for item in inventory.get("collections", [])
        if isinstance(item, dict)
    }
    candidates = []
    for index, cluster in enumerate(_sorted_clusters(grouped.values()), start=1):
        candidates.append(
            _candidate_from_cluster(
                cluster,
                index,
                collection_lookup,
                completed_collections,
            )
        )

    candidates.sort(
        key=lambda item: (
            item.get("status") != "pending",
            len(item.get("prerequisite_candidates", []) or []),
            len(item.get("prerequisite_concepts", []) or []),
            -float(item.get("selection_priority", 0)),
            _natural_key(str(item.get("primary_source_collection", ""))),
            str(item.get("candidate_id", "")),
        )
    )
    return {
        "version": 1,
        "kind": "candidate_nodes",
        "generator": "chunk_concept_cluster_v1",
        "cluster_similarity": {
            "algorithm": "knowledge_graph_relation_v1",
            "min_affinity": _MIN_CLUSTER_AFFINITY,
            "min_concept": _MIN_CLUSTER_CONCEPT,
            "min_prerequisite": _MIN_CLUSTER_PREREQUISITE,
            "min_method": _MIN_CLUSTER_METHOD,
            "min_signature": _MIN_CLUSTER_SIGNATURE,
            "min_adjacent_signature": _MIN_ADJACENT_SIGNATURE,
        },
        "chapter_candidates": candidates,
    }


async def rebuild_candidate_nodes_with_ai(
    root: Path,
    inventory: dict[str, Any],
    builder: CandidateNodeBuilder | None,
    completed_collections: set[str] | None = None,
    *,
    candidate_merge_batch_size: int = _CANDIDATE_MERGE_BATCH_SIZE,
    candidate_merge_repair_attempts: int = _CANDIDATE_MERGE_REPAIR_ATTEMPTS,
    candidate_merge_timeout_sec: float = _CANDIDATE_MERGE_TIMEOUT_SEC,
    progress: Any | None = None,
) -> dict[str, Any]:
    """Build candidate nodes with AI enrichment when possible, else use deterministic fallback."""
    fallback = rebuild_candidate_nodes(root, inventory, completed_collections)
    if builder is None:
        normalized = _with_merge_review_metadata({}, fallback, fallback.get("chapter_candidates", []))
        save_candidate_nodes(root, normalized)
        return normalized
    merge_review = await _review_merge_components_with_ai(
        builder,
        inventory,
        fallback,
        sorted(completed_collections or set()),
        batch_size=candidate_merge_batch_size,
        repair_attempts=candidate_merge_repair_attempts,
        timeout_sec=candidate_merge_timeout_sec,
        progress=progress,
    )
    try:
        inventory_summary = _inventory_summary_for_ai(inventory, fallback)
        inventory_summary["merge_decisions"] = merge_review["merge_decisions"]
        inventory_summary["merge_review_observability"] = merge_review["observability"]
        ai_map = await builder.build_candidate_nodes(
            inventory_summary,
            sorted(completed_collections or set()),
        )
        ai_map["merge_decisions"] = _merge_review_decisions(
            merge_review["merge_decisions"],
            ai_map.get("merge_decisions", []),
        )
        ai_map["merge_review_observability"] = merge_review["observability"]
        normalized = _normalize_ai_map(ai_map, fallback, completed_collections or set())
        normalized["merge_review_observability"] = merge_review["observability"]
        save_candidate_nodes(root, normalized)
        return normalized
    except Exception:
        normalized = _with_merge_review_metadata(
            {"merge_decisions": merge_review["merge_decisions"]},
            fallback,
            fallback.get("chapter_candidates", []),
        )
        normalized["merge_review_observability"] = merge_review["observability"]
        save_candidate_nodes(root, normalized)
        return normalized


def build_candidate_nodes_context(candidate_nodes: dict[str, Any], limit: int = 10) -> str:
    """Format the legacy KnowledgeNode compatibility schema for debug contexts."""
    candidates = [
        item
        for item in candidate_nodes.get("chapter_candidates", [])
        if isinstance(item, dict)
    ]
    lines = [
        "## KnowledgeNodes",
        "These are canonical teaching nodes generated from KnowledgeGroup inventory.",
        "They are not the curriculum order; the deterministic graph planner selects direction.",
        "",
    ]
    if not candidates:
        lines.append("(no KnowledgeNodes available)")
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


async def _review_merge_components_with_ai(
    builder: CandidateNodeBuilder,
    inventory: dict[str, Any],
    fallback: dict[str, Any],
    completed_collections: list[str],
    *,
    batch_size: int,
    repair_attempts: int,
    timeout_sec: float,
    progress: Any | None,
) -> dict[str, Any]:
    components = [item for item in fallback.get("merge_review_components", []) if isinstance(item, dict)]
    review_method = getattr(builder, "review_candidate_merge_components", None)
    if not components or not callable(review_method):
        decisions: list[dict[str, Any]] = []
        return {"merge_decisions": decisions, "observability": _merge_review_observability(components, decisions)}
    decisions: list[dict[str, Any]] = []
    batches = _component_batches(components, max(1, batch_size))
    for batch_index, batch in enumerate(batches, start=1):
        batch_id = f"merge-batch-{batch_index:03d}"
        pending = list(batch)
        last_error = ""
        for attempt in range(1, repair_attempts + 2):
            _mark_merge_review_progress(progress, components, decisions, batch_id, attempt)
            try:
                payload = _merge_review_payload(
                    inventory,
                    fallback,
                    pending,
                    completed_collections,
                    batch_id=batch_id,
                    attempt=attempt,
                    repair=attempt > 1,
                )
                raw = await _call_merge_review_method(review_method, payload, timeout_sec)
                valid, missing, invalid = _validated_batch_decisions(
                    raw,
                    pending,
                    batch_id=batch_id,
                    attempt=attempt,
                    source="repair" if attempt > 1 else "ai",
                )
                decisions.extend(valid)
                if not missing and not invalid:
                    pending = []
                    break
                pending = [component for component in pending if component.get("component_id") in set(missing + invalid)]
                last_error = "invalid_or_missing_decisions"
            except Exception as exc:
                last_error = type(exc).__name__
            if not pending:
                break
        if pending:
            decisions.extend(_fallback_merge_decisions(pending, error_type=last_error or "missing_after_repair"))
    observability = _merge_review_observability(components, decisions)
    return {"merge_decisions": decisions, "observability": observability}


def _component_batches(components: list[dict[str, Any]], batch_size: int) -> list[list[dict[str, Any]]]:
    return [components[index : index + batch_size] for index in range(0, len(components), batch_size)]


async def _call_merge_review_method(review_method: Any, payload: dict[str, Any], timeout_sec: float) -> dict[str, Any]:
    try:
        return await review_method(payload, timeout_sec=timeout_sec)
    except TypeError as exc:
        if "timeout_sec" not in str(exc):
            raise
        return await review_method(payload)


def _merge_review_payload(
    inventory: dict[str, Any],
    fallback: dict[str, Any],
    components: list[dict[str, Any]],
    completed_collections: list[str],
    *,
    batch_id: str,
    attempt: int,
    repair: bool,
) -> dict[str, Any]:
    group_ids = {
        group_id
        for component in components
        for group_id in _string_list(component.get("group_ids"))
    }
    candidate_group_ids = set(group_ids)
    return {
        "batch_id": batch_id,
        "attempt": attempt,
        "repair": repair,
        "completed_collections": completed_collections,
        "merge_review_components": components,
        "knowledge_groups": [
            item
            for item in _inventory_summary_for_ai(inventory, fallback).get("knowledge_groups", [])
            if item.get("group_id") in group_ids
        ],
        "group_pair_metrics": [
            item
            for item in fallback.get("group_pair_metrics", [])[:80]
            if item.get("left_group_id") in group_ids or item.get("right_group_id") in group_ids
        ],
        "candidate_nodes": [
            item
            for item in fallback.get("chapter_candidates", [])
            if set(_string_list(item.get("merged_group_ids"))) & candidate_group_ids
        ],
    }


def _validated_batch_decisions(
    raw: dict[str, Any],
    components: list[dict[str, Any]],
    *,
    batch_id: str,
    attempt: int,
    source: str,
) -> tuple[list[dict[str, Any]], list[str], list[str]]:
    component_by_id = {str(component.get("component_id") or ""): component for component in components}
    seen: set[str] = set()
    valid: list[dict[str, Any]] = []
    invalid: list[str] = []
    for item in raw.get("merge_decisions", []) if isinstance(raw, dict) else []:
        if not isinstance(item, dict):
            continue
        component_id = str(item.get("component_id") or "")
        component = component_by_id.get(component_id)
        if component is None:
            invalid.append(component_id)
            continue
        expected_groups = set(_string_list(component.get("group_ids")))
        actual_groups = set(_string_list(item.get("group_ids")))
        decision = _normalize_merge_decision(item.get("decision"))
        if not decision or expected_groups != actual_groups:
            invalid.append(component_id)
            continue
        seen.add(component_id)
        valid.append(
            {
                **item,
                "component_id": component_id,
                "group_ids": _string_list(component.get("group_ids")),
                "decision": decision,
                "decision_source": source,
                "attempt_count": attempt,
                "batch_id": batch_id,
            }
        )
    missing = [component_id for component_id in component_by_id if component_id not in seen]
    return valid, missing, invalid


def _normalize_merge_decision(value: Any) -> str:
    decision = str(value or "").strip()
    if decision == "uncertain":
        return "blocked_pending"
    if decision in _VALID_MERGE_DECISIONS:
        return decision
    return ""


def _fallback_merge_decisions(components: list[dict[str, Any]], *, error_type: str) -> list[dict[str, Any]]:
    decisions = []
    for component in components:
        deterministic = _component_allows_conservative_merge(component)
        decisions.append(
            {
                "component_id": component.get("component_id"),
                "group_ids": _string_list(component.get("group_ids")),
                "decision": "merged" if deterministic else "blocked_pending",
                "decision_source": "deterministic" if deterministic else "fallback_blocked",
                "attempt_count": 0,
                "batch_id": "",
                "error_type": error_type,
                "reason": (
                    "Conservative deterministic merge after AI review did not return a valid decision."
                    if deterministic
                    else "AI merge review did not return a valid decision."
                ),
            }
        )
    return decisions


def _merge_review_decisions(reviewed: list[dict[str, Any]], inline: Any) -> list[dict[str, Any]]:
    by_component = {str(item.get("component_id") or ""): item for item in reviewed if isinstance(item, dict)}
    for item in inline or []:
        if not isinstance(item, dict):
            continue
        component_id = str(item.get("component_id") or "")
        if component_id and component_id not in by_component:
            by_component[component_id] = item
    return list(by_component.values())


def _merge_review_observability(components: list[dict[str, Any]], decisions: list[dict[str, Any]]) -> dict[str, Any]:
    decided = len({str(item.get("component_id") or "") for item in decisions if item.get("component_id")})
    return {
        "components_total": len(components),
        "components_decided": decided,
        "repair_attempts": sum(1 for item in decisions if item.get("decision_source") == "repair"),
        "blocked_pending": sum(1 for item in decisions if item.get("decision") == "blocked_pending"),
        "auto_merged": sum(1 for item in decisions if item.get("decision_source") == "deterministic"),
        "missing_component_ids": [
            str(component.get("component_id") or "")
            for component in components
            if str(component.get("component_id") or "")
            not in {str(item.get("component_id") or "") for item in decisions}
        ],
    }


def _mark_merge_review_progress(
    progress: Any | None,
    components: list[dict[str, Any]],
    decisions: list[dict[str, Any]],
    batch_id: str,
    attempt: int,
) -> None:
    planner_stage = getattr(progress, "planner_stage", None)
    if not callable(planner_stage):
        return
    planner_stage(
        stage="merge_review",
        stage_label="Reviewing merge components",
        stage_index=3,
        details={
            **_merge_review_observability(components, decisions),
            "current_batch_id": batch_id,
            "attempt": attempt,
        },
    )


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


def _inventory_chunks(inventory: dict[str, Any]) -> list[dict[str, Any]]:
    chunks = []
    for index, raw in enumerate(inventory.get("chunks", []), start=1):
        if not isinstance(raw, dict):
            continue
        chunk_ref = str(raw.get("chunk_ref") or raw.get("chunk_id") or "")
        source_collection = str(raw.get("source_collection") or "")
        if not chunk_ref or not source_collection:
            continue
        chunk = {
            **raw,
            "_cluster_id": chunk_ref,
            "chunk_ref": chunk_ref,
            "source_collection": source_collection,
            "path": str(raw.get("path") or ""),
            "section_id": str(raw.get("section_id") or ""),
            "chunk_index": _int(raw.get("chunk_index"), index),
            "core_concepts": _string_list(raw.get("core_concepts")),
            "prerequisites": _string_list(raw.get("prerequisites")),
            "methods": _string_list(raw.get("methods")),
            "formulas": _string_list(raw.get("formulas")),
            "summary": str(raw.get("summary") or ""),
            "source_type": str(raw.get("source_type") or ""),
            "teaching_role": str(raw.get("teaching_role") or ""),
            "low_confidence_section_terms": _string_list(raw.get("low_confidence_section_terms")),
        }
        chunk["signature_terms"] = _knowledge_signature_terms(chunk)
        chunks.append(chunk)
    chunks.sort(key=_chunk_sort_key)
    return chunks


def _inventory_groups(inventory: dict[str, Any]) -> list[dict[str, Any]]:
    groups = []
    for index, raw in enumerate(inventory.get("knowledge_groups", []), start=1):
        if not isinstance(raw, dict):
            continue
        group_id = str(raw.get("group_id") or f"kg:{index:04d}")
        source_chunks = _string_list(raw.get("source_chunks"))
        raw_collections = raw.get("source_collections")
        fallback_collection = raw_collections[0] if isinstance(raw_collections, list) and raw_collections else ""
        source_collection = str(raw.get("source_collection") or fallback_collection)
        if not source_chunks or not source_collection:
            continue
        group = {
            **raw,
            "_cluster_id": group_id,
            "group_id": group_id,
            "source_collection": source_collection,
            "source_collections": _string_list(raw.get("source_collections")) or [source_collection],
            "source_chunks": source_chunks,
            "source_paths": _string_list(raw.get("source_paths")),
            "section_ids": _string_list(raw.get("section_ids")),
            "heading_path": _string_list(raw.get("heading_path")),
            "core_concepts": _string_list(raw.get("core_concepts")),
            "weak_concepts": _string_list(raw.get("weak_concepts")),
            "prerequisites": _string_list(raw.get("prerequisites")),
            "methods": _string_list(raw.get("methods")),
            "raw_formulas": _string_list(raw.get("raw_formulas") or raw.get("formulas")),
            "formula_signatures": _string_list(raw.get("formula_signatures")),
            "formula_roles": raw.get("formula_roles", []) if isinstance(raw.get("formula_roles"), list) else [],
            "low_confidence_section_terms": _string_list(raw.get("low_confidence_section_terms")),
            "title_hint": str(raw.get("title_hint") or ""),
            "summary": str(raw.get("summary") or ""),
            "teaching_role": str(raw.get("teaching_role") or ""),
            "source_type": str(raw.get("source_type") or ""),
            "completeness": str(raw.get("completeness") or ""),
            "fragment_role": str(raw.get("fragment_role") or ""),
            "auxiliary_only": bool(raw.get("auxiliary_only")),
            "auxiliary_group_ids": _string_list(raw.get("auxiliary_group_ids")),
            "representative_chunks": _group_representative_chunks(raw),
            "length_stats": raw.get("length_stats") if isinstance(raw.get("length_stats"), dict) else {},
        }
        group["signature_terms"] = _knowledge_signature_terms(group)
        groups.append(group)
    groups.sort(key=_group_sort_key)
    return groups


def _rebuild_inventory_group_nodes(
    groups: list[dict[str, Any]],
    completed_collections: set[str],
) -> dict[str, Any]:
    candidates = [_candidate_from_group(group, completed_collections) for group in groups]
    candidates.sort(
        key=lambda item: (
            item.get("status") != "pending",
            len(item.get("prerequisite_candidates", []) or []),
            len(item.get("prerequisite_concepts", []) or []),
            -float(item.get("selection_priority", 0)),
            _natural_key(str(item.get("primary_source_collection", ""))),
            str(item.get("candidate_id", "")),
        )
    )
    group_pair_metrics = _ranked_group_pair_metrics(groups)
    merge_components = _merge_review_components(groups, group_pair_metrics)
    diagnostics = _pending_merge_diagnostics(merge_components, candidates)
    _mark_pending_merge_candidates(candidates, diagnostics)
    return {
        "version": 1,
        "kind": "candidate_nodes",
        "generator": "inventory_group_v1",
        "group_pair_metrics": group_pair_metrics,
        "merge_review_components": merge_components,
        "diagnostics": diagnostics,
        "chapter_candidates": candidates,
    }


def _candidate_from_group(group: dict[str, Any], completed_collections: set[str]) -> dict[str, Any]:
    primary = str(group.get("source_collection") or "unknown")
    source_collections = _string_list(group.get("source_collections")) or [primary]
    status = "completed" if source_collections and set(source_collections).issubset(completed_collections) else "pending"
    concepts = _string_list(group.get("core_concepts"))
    weak_concepts = _string_list(group.get("weak_concepts"))
    methods = _string_list(group.get("methods"))
    formulas = _string_list(group.get("raw_formulas") or group.get("formulas"))
    length_stats = group.get("length_stats") if isinstance(group.get("length_stats"), dict) else {}
    estimated_lines = _int(length_stats.get("estimated_output_lines"), 0) or _estimated_output_lines(
        [{"core_concepts": concepts, "methods": methods, "formulas": formulas}],
        concepts,
        methods,
        formulas,
    )
    title = str(group.get("title_hint") or "") or _title_hint(concepts or weak_concepts, primary)
    low_confidence_terms = _string_list(group.get("low_confidence_section_terms"))
    title = _clean_title_hint(title, low_confidence_terms, primary)
    auxiliary_only = bool(group.get("auxiliary_only")) or _is_auxiliary_group(group)
    candidate = {
        "candidate_id": f"candidate:{primary}:{_stable_group_suffix(group)}",
        "status": status,
        "title_hint": title,
        "canonical_title": title,
        "primary_source_collection": primary,
        "source_collections": source_collections,
        "merged_group_ids": [group.get("group_id")],
        "core_concepts": concepts,
        "weak_concepts": weak_concepts,
        "prerequisite_concepts": _string_list(group.get("prerequisites")),
        "prerequisite_candidates": [],
        "methods": methods,
        "formulas": formulas,
        "formula_signatures": _string_list(group.get("formula_signatures")),
        "formula_roles": group.get("formula_roles", [])[:12],
        "section_ids": _string_list(group.get("section_ids"))[:16],
        "low_confidence_terms": low_confidence_terms[:16],
        "source_types": _string_list(group.get("source_type")),
        "teaching_roles": _string_list(group.get("teaching_role")),
        "representative_chunks": group.get("representative_chunks", [])[:8],
        "chunk_count": len(group.get("source_chunks", [])),
        "estimated_output_lines": estimated_lines,
        "size_band": _size_band(estimated_lines),
        "cluster_cohesion": 1.0,
        "selection_priority": _group_selection_priority(group, concepts, status),
        "coverage_evidence": _string_list(group.get("evidence_spans")),
        "root_features": {},
        "canonicalization_status": "auxiliary_only" if auxiliary_only else "canonical",
        "auxiliary_group_ids": _string_list(group.get("auxiliary_group_ids")),
        "merge_decision_source": "deterministic",
        "reason": _group_reason(group, concepts, status),
    }
    if auxiliary_only:
        candidate["schedulable"] = False
        candidate["blocked_reason"] = "auxiliary_only"
    return candidate


def _ranked_group_pair_metrics(groups: list[dict[str, Any]], limit: int = 60) -> list[dict[str, Any]]:
    metrics = []
    for index, left in enumerate(groups):
        for right in groups[index + 1 :]:
            item = _group_pair_metrics(left, right)
            if item["overall_similarity"] <= 0:
                continue
            metrics.append(item)
    metrics.sort(
        key=lambda item: (
            -float(item.get("overall_similarity") or 0),
            item.get("left_group_id", ""),
            item.get("right_group_id", ""),
        )
    )
    return metrics[:limit]


def _group_pair_metrics(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    heading_section = max(
        _overlap_score(
            set(left.get("section_ids", [])),
            set(right.get("section_ids", [])),
        ),
        _overlap_score(
            _term_set(left.get("heading_path", [])),
            _term_set(right.get("heading_path", [])),
        ),
    )
    concept = max(
        _overlap_score(_term_set(left.get("core_concepts", [])), _term_set(right.get("core_concepts", []))),
        _overlap_score(_term_set(left.get("weak_concepts", [])), _term_set(right.get("weak_concepts", []))) * 0.55,
    )
    formula = _overlap_score(set(left.get("formula_signatures", [])), set(right.get("formula_signatures", [])))
    source = _overlap_score(set(left.get("source_paths", [])), set(right.get("source_paths", [])))
    if set(left.get("source_collections", [])) & set(right.get("source_collections", [])):
        source = max(source, 0.5)
    left_tokens = max(1, _int(left.get("length_stats", {}).get("token_estimate"), 1))
    right_tokens = max(1, _int(right.get("length_stats", {}).get("token_estimate"), 1))
    token_ratio = min(left_tokens, right_tokens) / max(left_tokens, right_tokens)
    chunk_distance = _group_chunk_distance(left, right)
    adjacency = 1.0 if chunk_distance is not None and chunk_distance <= 1 else 0.0
    clean_title_match = _clean_merge_title(left.get("title_hint")) != "" and (
        _clean_merge_title(left.get("title_hint")) == _clean_merge_title(right.get("title_hint"))
    )
    overall = (
        heading_section * 0.16
        + concept * 0.32
        + formula * 0.24
        + source * 0.10
        + token_ratio * 0.06
        + adjacency * 0.12
    )
    return {
        "left_group_id": left.get("group_id"),
        "right_group_id": right.get("group_id"),
        "heading_section_continuity": round(heading_section, 4),
        "embedding_similarity": None,
        "concept_overlap": round(concept, 4),
        "formula_overlap": round(formula, 4),
        "source_path_continuity": round(source, 4),
        "token_length_ratio": round(token_ratio, 4),
        "chunk_index_distance": chunk_distance,
        "clean_title_match": clean_title_match,
        "overall_similarity": round(min(1.0, overall), 4),
    }


def _merge_review_components(groups: list[dict[str, Any]], metrics: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_id = {str(group.get("group_id") or ""): group for group in groups}
    strong_pairs = []
    for metric in metrics:
        reason = _strong_merge_reason(metric)
        if not reason:
            continue
        left = str(metric.get("left_group_id") or "")
        right = str(metric.get("right_group_id") or "")
        if left in by_id and right in by_id:
            strong_pairs.append((left, right, reason, metric))
    if not strong_pairs:
        return []
    union = _UnionFind(list(by_id))
    for left, right, _reason, _metric in strong_pairs:
        union.union(left, right)
    grouped: dict[str, list[str]] = defaultdict(list)
    for group_id in by_id:
        grouped[union.find(group_id)].append(group_id)
    components = []
    for group_ids in grouped.values():
        if len(group_ids) < 2:
            continue
        component_pairs = [
            {
                "left_group_id": left,
                "right_group_id": right,
                "reason": reason,
                "overall_similarity": metric.get("overall_similarity"),
                "concept_overlap": metric.get("concept_overlap"),
                "formula_overlap": metric.get("formula_overlap"),
                "heading_section_continuity": metric.get("heading_section_continuity"),
                "chunk_index_distance": metric.get("chunk_index_distance"),
            }
            for left, right, reason, metric in strong_pairs
            if left in group_ids and right in group_ids
        ]
        reasons = _unique(pair["reason"] for pair in component_pairs)
        ordered = sorted(group_ids, key=lambda group_id: groups.index(by_id[group_id]))
        components.append(
            {
                "component_id": f"merge:{hashlib.sha1('|'.join(ordered).encode('utf-8')).hexdigest()[:10]}",
                "group_ids": ordered,
                "reason": reasons[0] if reasons else "strong_similarity",
                "reasons": reasons,
                "pairs": component_pairs,
                "groups": [
                    {
                        "group_id": group_id,
                        "title_hint": by_id[group_id].get("title_hint"),
                        "source_chunks": by_id[group_id].get("source_chunks", [])[:8],
                        "core_concepts": by_id[group_id].get("core_concepts", [])[:12],
                        "formula_signatures": by_id[group_id].get("formula_signatures", [])[:8],
                        "teaching_role": by_id[group_id].get("teaching_role"),
                    }
                    for group_id in ordered
                ],
            }
        )
    components.sort(key=lambda item: (item["group_ids"][0], item["component_id"]))
    return components


def _strong_merge_reason(metric: dict[str, Any]) -> str:
    concept = float(metric.get("concept_overlap") or 0)
    formula = float(metric.get("formula_overlap") or 0)
    heading = float(metric.get("heading_section_continuity") or 0)
    chunk_distance = metric.get("chunk_index_distance")
    overall = float(metric.get("overall_similarity") or 0)
    if metric.get("clean_title_match"):
        return "clean_title_match"
    if concept >= _MIN_STRONG_MERGE_CONCEPT:
        return "concept_overlap"
    if formula >= _MIN_STRONG_MERGE_FORMULA and (concept > 0 or heading > 0):
        return "formula_overlap"
    if chunk_distance is not None and int(chunk_distance) <= 1 and (concept > 0 or formula > 0):
        return "adjacent_strong_signal"
    if overall >= _MIN_STRONG_MERGE_OVERALL:
        return "overall_similarity"
    return ""


def _pending_merge_diagnostics(
    components: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
    *,
    decisions: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    decisions = decisions or []
    diagnostics = []
    candidate_by_group: dict[str, list[str]] = defaultdict(list)
    for candidate in candidates:
        for group_id in _string_list(candidate.get("merged_group_ids")):
            candidate_by_group[group_id].append(str(candidate.get("candidate_id") or ""))
    for component in components:
        group_ids = _string_list(component.get("group_ids"))
        decision = _component_decision(component, decisions, candidates)
        if decision.get("decision") in {"merged", "rejected"}:
            continue
        node_ids = _unique(
            candidate_id
            for group_id in group_ids
            for candidate_id in candidate_by_group.get(group_id, [])
        )
        diagnostics.append(
            {
                "kind": "canonical_merge_pending",
                "component_id": component.get("component_id"),
                "group_ids": group_ids,
                "nodes": node_ids,
                "reason": "Strongly similar groups require explicit AI merge or reject decision.",
            }
        )
    return diagnostics


def _component_decision(
    component: dict[str, Any],
    decisions: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
) -> dict[str, Any]:
    group_ids = set(_string_list(component.get("group_ids")))
    for decision in decisions:
        decision_groups = set(_string_list(decision.get("group_ids")))
        if group_ids and group_ids.issubset(decision_groups):
            return decision
    for candidate in candidates:
        merged = set(_string_list(candidate.get("merged_group_ids")))
        if group_ids and group_ids.issubset(merged):
            return {
                "component_id": component.get("component_id"),
                "group_ids": sorted(group_ids),
                "decision": "merged",
                "candidate_id": candidate.get("candidate_id"),
                "reason": "AI candidate merged all groups in this component.",
            }
    return {
        "component_id": component.get("component_id"),
        "group_ids": sorted(group_ids),
        "decision": "uncertain",
        "reason": "AI response did not explicitly cover this strong merge component.",
    }


def _merge_decisions(
    ai_map: dict[str, Any],
    components: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    explicit = [
        {**item, "decision": _normalize_merge_decision(item.get("decision")) or str(item.get("decision") or "")}
        for item in ai_map.get("merge_decisions", [])
        if isinstance(item, dict) and _string_list(item.get("group_ids"))
    ]
    decisions = []
    for component in components:
        decision = _component_decision(component, explicit, candidates)
        if not any(
            set(_string_list(component.get("group_ids"))).issubset(set(_string_list(item.get("group_ids"))))
            for item in explicit
        ) and decision.get("decision") == "uncertain":
            decision["decision_source"] = "omitted"
        else:
            decision.setdefault("decision_source", "ai")
        decision.setdefault("attempt_count", 1)
        decisions.append(decision)
    return decisions


def _mark_pending_merge_candidates(candidates: list[dict[str, Any]], diagnostics: list[dict[str, Any]]) -> None:
    by_group = {
        group_id: candidate
        for candidate in candidates
        for group_id in _string_list(candidate.get("merged_group_ids"))
    }
    for diagnostic in diagnostics:
        for group_id in _string_list(diagnostic.get("group_ids")):
            candidate = by_group.get(group_id)
            if not candidate:
                continue
            pending = candidate.setdefault("pending_merge_group_ids", [])
            for pending_id in _string_list(diagnostic.get("group_ids")):
                if pending_id not in pending:
                    pending.append(pending_id)
            candidate["schedulable"] = False
            candidate["blocked_reason"] = "canonical_merge_pending"


def _canonicalize_merge_components(
    fallback: dict[str, Any],
    candidates: list[dict[str, Any]],
    merge_decisions: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    components = [item for item in fallback.get("merge_review_components", []) if isinstance(item, dict)]
    if not components:
        return candidates
    by_group = _candidate_lookup_by_group(candidates)
    fallback_by_group = _candidate_lookup_by_group(fallback.get("chapter_candidates", []))
    result = list(candidates)
    used_groups: set[str] = set()
    for component in components:
        group_ids = _string_list(component.get("group_ids"))
        if not group_ids or used_groups & set(group_ids):
            continue
        decision = _decision_for_component(component, merge_decisions)
        if decision.get("decision") == "rejected":
            if _component_already_merged(result, group_ids):
                result = [
                    item
                    for item in result
                    if not (set(_string_list(item.get("merged_group_ids"))) & set(group_ids))
                ]
                result.extend(_unique_candidates_for_groups(fallback_by_group, group_ids))
            continue
        component_items = _unique_candidates_for_groups(by_group, group_ids)
        if len(component_items) < 2:
            component_items = _unique_candidates_for_groups(fallback_by_group, group_ids)
        if len(component_items) < 2:
            continue
        if decision.get("decision") == "merged" and _component_already_merged(result, group_ids):
            continue
        result = [
            item
            for item in result
            if not (set(_string_list(item.get("merged_group_ids"))) & set(group_ids))
        ]
        if decision.get("decision") == "merged":
            merged = _canonical_component_candidate(component, component_items)
            source = str(decision.get("decision_source") or "ai")
            merged["canonicalization_status"] = "auto_merged" if source == "deterministic" else "canonical"
            merged["merge_decision_source"] = source
            result.append(merged)
            decision["candidate_id"] = merged.get("candidate_id")
        elif decision.get("decision_source") == "omitted" and _component_allows_conservative_merge(component):
            merged = _canonical_component_candidate(component, component_items)
            merged["canonicalization_status"] = "auto_merged"
            merged["merge_decision_source"] = "deterministic"
            result.append(merged)
            decision["decision"] = "merged"
            decision["decision_source"] = "deterministic"
            decision["candidate_id"] = merged.get("candidate_id")
            decision["reason"] = "Conservative deterministic merge for an omitted strong component."
        else:
            blocked = _canonical_component_candidate(component, component_items)
            blocked["canonicalization_status"] = "blocked_pending"
            blocked["merge_decision_source"] = "fallback_blocked"
            blocked["schedulable"] = False
            blocked["blocked_reason"] = "canonical_merge_pending"
            blocked["pending_merge_group_ids"] = group_ids
            result.append(blocked)
            decision["decision"] = "blocked_pending"
            decision["decision_source"] = "fallback_blocked"
            decision["candidate_id"] = blocked.get("candidate_id")
        used_groups.update(group_ids)
    return _sort_candidates_by_prerequisites(_dedupe_candidates(result))


def _component_already_merged(candidates: list[dict[str, Any]], group_ids: list[str]) -> bool:
    expected = set(group_ids)
    return any(expected.issubset(set(_string_list(candidate.get("merged_group_ids")))) for candidate in candidates)


def _candidate_lookup_by_group(candidates: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    by_group: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for candidate in candidates:
        for group_id in _string_list(candidate.get("merged_group_ids")):
            by_group[group_id].append(candidate)
    return by_group


def _unique_candidates_for_groups(
    by_group: dict[str, list[dict[str, Any]]],
    group_ids: list[str],
) -> list[dict[str, Any]]:
    seen = set()
    items = []
    for group_id in group_ids:
        for candidate in by_group.get(group_id, []):
            candidate_id = str(candidate.get("candidate_id") or "")
            if not candidate_id or candidate_id in seen:
                continue
            seen.add(candidate_id)
            items.append(candidate)
    return items


def _dedupe_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen = set()
    result = []
    for candidate in candidates:
        candidate_id = str(candidate.get("candidate_id") or "")
        if not candidate_id or candidate_id in seen:
            continue
        seen.add(candidate_id)
        result.append(candidate)
    return result


def _decision_for_component(
    component: dict[str, Any],
    decisions: list[dict[str, Any]],
) -> dict[str, Any]:
    group_ids = set(_string_list(component.get("group_ids")))
    for decision in decisions:
        decision_groups = set(_string_list(decision.get("group_ids")))
        if group_ids and group_ids.issubset(decision_groups):
            return decision
    return {"decision": "uncertain", "decision_source": "omitted", "group_ids": sorted(group_ids)}


def _component_allows_conservative_merge(component: dict[str, Any]) -> bool:
    reasons = set(_string_list(component.get("reasons")) or _string_list(component.get("reason")))
    if reasons & {"clean_title_match", "adjacent_strong_signal", "concept_overlap"}:
        return True
    for pair in component.get("pairs", []) or []:
        if not isinstance(pair, dict):
            continue
        if str(pair.get("reason") or "") in {"clean_title_match", "adjacent_strong_signal"}:
            return True
        if float(pair.get("concept_overlap") or 0) >= _MIN_STRONG_MERGE_CONCEPT:
            return True
    return False


def _canonical_component_candidate(
    component: dict[str, Any],
    items: list[dict[str, Any]],
) -> dict[str, Any]:
    combined = _combine_fallback_items(items)
    group_ids = _string_list(component.get("group_ids")) or _unique(
        group_id for item in items for group_id in _string_list(item.get("merged_group_ids"))
    )
    suffix = hashlib.sha1("|".join(group_ids).encode("utf-8")).hexdigest()[:10]
    combined["candidate_id"] = f"candidate:canonical:{suffix}"
    combined["merged_group_ids"] = group_ids
    combined["canonical_title"] = combined.get("title_hint", "")
    combined.pop("pending_merge_group_ids", None)
    if combined.get("blocked_reason") == "canonical_merge_pending":
        combined.pop("blocked_reason", None)
    combined.pop("schedulable", None)
    combined["coverage_evidence"] = _unique(
        [
            *[
                evidence
                for item in items
                for evidence in _string_list(item.get("coverage_evidence"))
            ],
            f"Canonical merge component {component.get('component_id')}.",
        ]
    )[:16]
    combined["auxiliary_group_ids"] = _unique(
        group_id for item in items for group_id in _string_list(item.get("auxiliary_group_ids"))
    )
    return combined


def _clean_merge_title(value: Any) -> str:
    title = str(value or "").strip()
    title = re.sub(r"^§\s*[\d\-–—.]*\s*", "", title).strip()
    title = re.sub(r"^第[一二三四五六七八九十百\d]+章[、.．\s]*", "", title).strip()
    title = title.strip(" -—:：。；;，,")
    if not title or title in _GENERIC_TITLES:
        return ""
    if re.fullmatch(r"\$?\^\{?\*+\}?\$?[一二三四五六七八九十\d]*", title):
        return ""
    return title


def _is_auxiliary_group(group: dict[str, Any]) -> bool:
    if group.get("auxiliary_only"):
        return True
    title = str(group.get("title_hint") or "").strip()
    role = str(group.get("fragment_role") or group.get("teaching_role") or group.get("completeness") or "").lower()
    if _clean_merge_title(title) == "" and (not _string_list(group.get("core_concepts")) or title in _GENERIC_TITLES):
        return True
    if role in _AUXILIARY_FRAGMENT_ROLES and not _string_list(group.get("core_concepts")):
        return True
    return False


def _group_chunk_distance(left: dict[str, Any], right: dict[str, Any]) -> int | None:
    if not (set(left.get("source_paths", [])) & set(right.get("source_paths", []))):
        return None
    left_range = left.get("chunk_range", {}) if isinstance(left.get("chunk_range"), dict) else {}
    right_range = right.get("chunk_range", {}) if isinstance(right.get("chunk_range"), dict) else {}
    return max(
        0,
        max(_int(left_range.get("start")), _int(right_range.get("start")))
        - min(_int(left_range.get("end")), _int(right_range.get("end"))),
    )


def _chunk_cluster_node(chunk: dict[str, Any]) -> dict[str, Any]:
    return {
        "node_id": chunk["_cluster_id"],
        "core_concepts": chunk.get("core_concepts", []),
        "prerequisites": chunk.get("prerequisites", []),
        "methods": chunk.get("methods", []),
        "formulas": chunk.get("formulas", []),
        "section_id": chunk.get("section_id", ""),
        "signature_terms": chunk.get("signature_terms", []),
        "hit_chunks": [chunk.get("chunk_ref", "")],
        "source_collections": [chunk.get("source_collection", "")],
        "path": chunk.get("path", ""),
        "chunk_index": chunk.get("chunk_index", 0),
    }


def _candidate_pair_scores(left: dict[str, Any], right: dict[str, Any]) -> dict[str, float]:
    scores = relation_pair_scores(left, right)
    scores["method"] = _overlap_score(
        _term_set(left.get("methods", [])),
        _term_set(right.get("methods", [])),
    )
    scores["signature"] = _overlap_score(
        set(left.get("signature_terms", [])),
        set(right.get("signature_terms", [])),
    )
    scores["adjacent"] = 1.0 if _same_path_adjacent(left, right) else 0.0
    scores["section"] = 1.0 if _same_section_adjacent(left, right) else 0.0
    return scores


def _should_cluster(scores: dict[str, float]) -> bool:
    prerequisite = max(scores["prerequisite_ab"], scores["prerequisite_ba"])
    if scores["concept"] >= _MIN_CLUSTER_CONCEPT:
        return True
    if prerequisite >= _MIN_CLUSTER_PREREQUISITE:
        return True
    if scores.get("method", 0.0) >= _MIN_CLUSTER_METHOD:
        return True
    if scores.get("signature", 0.0) >= _MIN_CLUSTER_SIGNATURE and (
        scores["concept"] > 0 or prerequisite > 0 or scores.get("method", 0.0) > 0
    ):
        return True
    if scores.get("adjacent", 0.0) and (
        scores.get("method", 0.0) > 0
        or scores.get("signature", 0.0) >= _MIN_ADJACENT_SIGNATURE
    ):
        return True
    if scores.get("section", 0.0) and scores.get("adjacent", 0.0):
        return True
    return relation_affinity(scores) >= _MIN_CLUSTER_AFFINITY and (
        scores["concept"] > 0 or prerequisite > 0
    )


def _same_path_adjacent(left: dict[str, Any], right: dict[str, Any]) -> bool:
    if left.get("source_collections") != right.get("source_collections"):
        return False
    if str(left.get("path") or "") != str(right.get("path") or ""):
        return False
    return abs(_int(left.get("chunk_index")) - _int(right.get("chunk_index"))) <= 1


def _same_section_adjacent(left: dict[str, Any], right: dict[str, Any]) -> bool:
    left_section = str(left.get("section_id") or "")
    right_section = str(right.get("section_id") or "")
    if not left_section or left_section != right_section:
        return False
    return _same_path_adjacent(left, right)


def _knowledge_signature_terms(chunk: dict[str, Any]) -> list[str]:
    values: list[str] = []
    for key in ("core_concepts", "prerequisites", "methods"):
        values.extend(_string_list(chunk.get(key)))
    summary = str(chunk.get("summary") or "")
    if summary:
        values.append(summary)
    return sorted(_term_set(values))


def _term_set(values: Any) -> set[str]:
    terms = set()
    if isinstance(values, str):
        values = [values]
    if not isinstance(values, list):
        return terms
    for value in values:
        text = str(value)
        for raw in _TERM_RE.findall(text):
            token = _clean_signature_term(raw)
            if len(token) >= 2 and token.lower() not in _SIGNATURE_STOPWORDS:
                terms.add(token.lower())
            for piece in _SPLIT_RE.split(token):
                piece = _clean_signature_term(piece)
                if len(piece) >= 2 and piece.lower() not in _SIGNATURE_STOPWORDS:
                    terms.add(piece.lower())
    return terms


def _overlap_score(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / max(1, min(len(left), len(right)))


def _clean_signature_term(value: str) -> str:
    value = re.sub(r"`([^`]+)`", r"\1", value)
    value = re.sub(r"\s+", "", value)
    return value.strip(" -—:：。；;，,")


def _candidate_from_cluster(
    cluster: list[dict[str, Any]],
    index: int,
    collection_lookup: dict[str, dict[str, Any]],
    completed_collections: set[str],
) -> dict[str, Any]:
    cluster = sorted(cluster, key=_chunk_sort_key)
    source_collections = _unique(chunk["source_collection"] for chunk in cluster)
    primary = source_collections[0] if source_collections else "unknown"
    status = (
        "completed"
        if source_collections and set(source_collections).issubset(completed_collections)
        else "pending"
    )
    concepts = _ranked_terms((chunk.get("core_concepts", []) for chunk in cluster), limit=24)
    prerequisites = _ranked_terms((chunk.get("prerequisites", []) for chunk in cluster), limit=16)
    methods = _ranked_terms((chunk.get("methods", []) for chunk in cluster), limit=16)
    formulas = _ranked_terms((chunk.get("formulas", []) for chunk in cluster), limit=12)
    section_ids = _unique(chunk.get("section_id", "") for chunk in cluster)[:16]
    low_confidence_terms = _ranked_terms(
        (chunk.get("low_confidence_section_terms", []) for chunk in cluster),
        limit=16,
    )
    representative_chunks = [_representative_chunk(chunk) for chunk in cluster[:8]]
    related_collections = _cluster_related_collections(
        source_collections,
        collection_lookup,
    )
    candidate_id = f"candidate:{primary}:{_stable_cluster_suffix(cluster, concepts)}"
    return {
        "candidate_id": candidate_id,
        "status": status,
        "title_hint": _title_hint(concepts, primary),
        "primary_source_collection": primary,
        "source_collections": source_collections,
        "core_concepts": concepts,
        "prerequisite_concepts": prerequisites,
        "methods": methods,
        "formulas": formulas,
        "prerequisite_candidates": [],
        "section_ids": section_ids,
        "low_confidence_terms": low_confidence_terms,
        "source_types": _unique(chunk.get("source_type", "") for chunk in cluster)[:8],
        "teaching_roles": _unique(chunk.get("teaching_role", "") for chunk in cluster)[:8],
        "representative_chunks": representative_chunks,
        "chunk_count": len(cluster),
        "estimated_output_lines": _estimated_output_lines(cluster, concepts, methods, formulas),
        "size_band": _size_band(_estimated_output_lines(cluster, concepts, methods, formulas)),
        "cluster_cohesion": _cluster_cohesion(cluster),
        "related_collections": related_collections,
        "selection_priority": _cluster_selection_priority(cluster, concepts, related_collections, status),
        "reason": _cluster_reason(cluster, source_collections, concepts, prerequisites, status),
    }


def _representative_chunk(chunk: dict[str, Any]) -> dict[str, Any]:
    return {
        "chunk_ref": chunk.get("chunk_ref", ""),
        "core_concepts": chunk.get("core_concepts", [])[:8],
        "prerequisites": chunk.get("prerequisites", [])[:8],
        "section_id": chunk.get("section_id", ""),
        "source_type": chunk.get("source_type", ""),
        "teaching_role": chunk.get("teaching_role", ""),
        "low_confidence_section_terms": chunk.get("low_confidence_section_terms", [])[:8],
        "summary": chunk.get("summary", ""),
    }


def _group_representative_chunks(group: dict[str, Any]) -> list[dict[str, Any]]:
    existing = group.get("representative_chunks")
    if isinstance(existing, list) and existing:
        return [
            item
            for item in existing
            if isinstance(item, dict) and str(item.get("chunk_ref") or "").strip()
        ][:8]
    source_chunks = _string_list(group.get("source_chunks"))
    concepts = _string_list(group.get("core_concepts"))
    prereqs = _string_list(group.get("prerequisites"))
    formulas = _string_list(group.get("formula_signatures"))
    sections = _string_list(group.get("section_ids"))
    return [
        {
            "chunk_ref": chunk_ref,
            "core_concepts": concepts[:8],
            "weak_concepts": _string_list(group.get("weak_concepts"))[:8],
            "prerequisites": prereqs[:8],
            "formula_signatures": formulas[:8],
            "section_id": sections[0] if sections else "",
            "source_type": str(group.get("source_type") or ""),
            "teaching_role": str(group.get("teaching_role") or ""),
            "summary": str(group.get("summary") or ""),
        }
        for chunk_ref in source_chunks[:8]
    ]


def _stable_group_suffix(group: dict[str, Any]) -> str:
    basis = str(group.get("group_id") or "") or "\n".join(_string_list(group.get("source_chunks")))
    return hashlib.sha1(basis.encode("utf-8")).hexdigest()[:10]


def _group_selection_priority(group: dict[str, Any], concepts: list[str], status: str) -> float:
    if status != "pending":
        return 0.0
    chunk_count = len(group.get("source_chunks", []) or [])
    completeness_bonus = 0.12 if str(group.get("completeness") or "").lower() in {"complete", "完整"} else 0.0
    return min(1.0, len(concepts) / 18 * 0.50 + chunk_count / 8 * 0.38 + completeness_bonus)


def _group_reason(group: dict[str, Any], concepts: list[str], status: str) -> str:
    prefix = "Already completed inventory knowledge group." if status == "completed" else "Pending inventory knowledge group."
    return (
        f"{prefix} Group: {group.get('group_id')}. "
        f"Chunks: {', '.join(group.get('source_chunks', [])[:6]) or 'n/a'}. "
        f"Core concepts: {', '.join(concepts[:8]) or 'n/a'}."
    )


def _group_sort_key(group: dict[str, Any]) -> tuple[Any, ...]:
    source_paths = group.get("source_paths", []) or [""]
    chunk_range = group.get("chunk_range", {}) if isinstance(group.get("chunk_range"), dict) else {}
    return (
        _natural_key(str(group.get("source_collection", ""))),
        str(source_paths[0]),
        _int(chunk_range.get("start")),
        str(group.get("group_id", "")),
    )


def _stable_cluster_suffix(cluster: list[dict[str, Any]], concepts: list[str]) -> str:
    chunk_refs = sorted(str(chunk.get("chunk_ref") or "") for chunk in cluster if chunk.get("chunk_ref"))
    basis = "\n".join(chunk_refs or sorted(str(concept) for concept in concepts))
    digest = hashlib.sha1(basis.encode("utf-8")).hexdigest()[:10]
    return digest


def _cluster_related_collections(
    source_collections: list[str],
    collection_lookup: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    related: dict[str, float] = {}
    current = set(source_collections)
    for collection_id in source_collections:
        collection = collection_lookup.get(collection_id, {})
        for item in collection.get("related_collections", []) or []:
            if not isinstance(item, dict):
                continue
            related_collection = str(item.get("source_collection") or "")
            if not related_collection or related_collection in current:
                continue
            related[related_collection] = max(
                related.get(related_collection, 0.0),
                float(item.get("score") or 0),
            )
    return [
        {"source_collection": key, "score": round(value, 4)}
        for key, value in sorted(related.items(), key=lambda item: (-item[1], _natural_key(item[0])))[:5]
    ]


def _cluster_selection_priority(
    cluster: list[dict[str, Any]],
    concepts: list[str],
    related_collections: list[dict[str, Any]],
    status: str,
) -> float:
    if status != "pending":
        return 0.0
    return min(
        1.0,
        len(concepts) / 18 * 0.45
        + len(cluster) / 8 * 0.40
        + len(related_collections) / 5 * 0.15,
    )


def _estimated_output_lines(
    cluster: list[dict[str, Any]],
    concepts: list[str],
    methods: list[str],
    formulas: list[str],
) -> int:
    return int(
        130
        + len(cluster) * 45
        + min(len(concepts), 12) * 20
        + min(len(methods), 8) * 15
        + min(len(formulas), 8) * 10
    )


def _size_band(estimated_lines: int) -> str:
    if estimated_lines < 260:
        return "thin"
    if estimated_lines > 560:
        return "broad"
    return "fit"


def _cluster_cohesion(cluster: list[dict[str, Any]]) -> float:
    if len(cluster) <= 1:
        return 1.0
    scores = []
    nodes = [_chunk_cluster_node(chunk) for chunk in cluster]
    for index, left in enumerate(nodes):
        for right in nodes[index + 1 :]:
            pair_scores = _candidate_pair_scores(left, right)
            scores.append(
                max(
                    relation_affinity(pair_scores),
                    pair_scores.get("method", 0.0),
                    pair_scores.get("signature", 0.0),
                    pair_scores.get("section", 0.0) * 0.85,
                )
            )
    if not scores:
        return 1.0
    return round(sum(scores) / len(scores), 4)


def _cluster_reason(
    cluster: list[dict[str, Any]],
    source_collections: list[str],
    concepts: list[str],
    prerequisites: list[str],
    status: str,
) -> str:
    prefix = "Already completed chunk/concept cluster." if status == "completed" else "Pending chunk/concept cluster."
    chunk_refs = ", ".join(chunk.get("chunk_ref", "") for chunk in cluster[:6])
    return (
        f"{prefix} Collections: {', '.join(source_collections) or 'n/a'}. "
        f"Chunks: {chunk_refs or 'n/a'}. "
        f"Core concepts: {', '.join(concepts[:8]) or 'n/a'}. "
        f"Prerequisites: {', '.join(prerequisites[:6]) or 'n/a'}."
    )


def _ranked_terms(groups: Any, limit: int) -> list[str]:
    counter: Counter[str] = Counter()
    first_seen: dict[str, int] = {}
    position = 0
    for group in groups:
        for raw in group:
            value = str(raw).strip()
            if not value:
                continue
            if value not in first_seen:
                first_seen[value] = position
                position += 1
            counter[value] += 1
    ranked = sorted(counter, key=lambda value: (-counter[value], first_seen[value], value))
    return ranked[:limit]


def _sorted_clusters(clusters: Any) -> list[list[dict[str, Any]]]:
    return sorted(
        [sorted(cluster, key=_chunk_sort_key) for cluster in clusters],
        key=lambda cluster: _chunk_sort_key(cluster[0]),
    )


def _chunk_sort_key(chunk: dict[str, Any]) -> tuple[Any, ...]:
    return (
        _natural_key(str(chunk.get("source_collection", ""))),
        str(chunk.get("path", "")),
        int(chunk.get("chunk_index") or 0),
        str(chunk.get("chunk_ref", "")),
    )


def _int(value: Any, fallback: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


class _UnionFind:
    def __init__(self, values: list[str]):
        self.parent = {value: value for value in values}

    def find(self, value: str) -> str:
        parent = self.parent[value]
        if parent != value:
            self.parent[value] = self.find(parent)
        return self.parent[value]

    def union(self, left: str, right: str) -> None:
        left_root = self.find(left)
        right_root = self.find(right)
        if left_root != right_root:
            self.parent[right_root] = left_root


def _selection_priority(collection: dict[str, Any], status: str) -> float:
    if status != "pending":
        return 0.0
    concept_count = len(collection.get("core_concepts", []) or [])
    chunk_count = int(collection.get("chunk_count") or 0)
    related_count = len(collection.get("related_collections", []) or [])
    return min(1.0, concept_count / 18 * 0.55 + chunk_count / 24 * 0.35 + related_count / 5 * 0.1)


def _inventory_summary_for_ai(inventory: dict[str, Any], fallback: dict[str, Any]) -> dict[str, Any]:
    summary = {
        "knowledge_groups": [
            {
                "group_id": item.get("group_id"),
                "title_hint": item.get("title_hint"),
                "source_chunks": item.get("source_chunks", [])[:12],
                "source_paths": item.get("source_paths", [])[:6],
                "source_collection": item.get("source_collection"),
                "chunk_range": item.get("chunk_range", {}),
                "core_concepts": item.get("core_concepts", [])[:18],
                "weak_concepts": item.get("weak_concepts", [])[:12],
                "prerequisites": item.get("prerequisites", [])[:12],
                "formula_roles": item.get("formula_roles", [])[:8],
                "formula_signatures": item.get("formula_signatures", [])[:8],
                "teaching_role": item.get("teaching_role"),
                "completeness": item.get("completeness"),
                "length_stats": item.get("length_stats", {}),
            }
            for item in inventory.get("knowledge_groups", [])
            if isinstance(item, dict)
        ],
        "group_pair_metrics": fallback.get("group_pair_metrics", [])[:60],
        "candidate_nodes": [
            {
                "candidate_id": item.get("candidate_id"),
                "merged_group_ids": item.get("merged_group_ids", []),
                "title_hint": item.get("title_hint"),
                "primary_source_collection": item.get("primary_source_collection"),
                "source_collections": item.get("source_collections", []),
                "core_concepts": item.get("core_concepts", [])[:18],
                "prerequisite_concepts": item.get("prerequisite_concepts", [])[:12],
                "formula_roles": item.get("formula_roles", [])[:8],
                "estimated_output_lines": item.get("estimated_output_lines"),
                "representative_chunks": [
                    {
                        "chunk_ref": chunk.get("chunk_ref"),
                        "core_concepts": chunk.get("core_concepts", [])[:8],
                        "prerequisites": chunk.get("prerequisites", [])[:8],
                        "formula_signatures": chunk.get("formula_signatures", [])[:8],
                        "summary": chunk.get("summary", ""),
                    }
                    for chunk in item.get("representative_chunks", [])[:6]
                    if isinstance(chunk, dict)
                ],
            }
            for item in fallback.get("chapter_candidates", [])
            if isinstance(item, dict)
        ],
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
    summary["merge_review_components"] = fallback.get("merge_review_components", [])[:40]
    summary["diagnostics"] = fallback.get("diagnostics", [])[:40]
    return summary


def _normalize_ai_map(
    ai_map: dict[str, Any],
    fallback: dict[str, Any],
    completed_collections: set[str],
) -> dict[str, Any]:
    fallback_items = [
        item
        for item in fallback.get("chapter_candidates", [])
        if isinstance(item, dict)
    ]
    fallback_by_id = {item.get("candidate_id"): item for item in fallback_items}
    fallback_by_collection: dict[str, list[dict[str, Any]]] = defaultdict(list)
    valid_collections = set()
    for item in fallback_items:
        primary = str(item.get("primary_source_collection") or "")
        if primary:
            fallback_by_collection[primary].append(item)
        valid_collections.update(_string_list(item.get("source_collections")))
    candidates = []
    used_ids = set()
    for index, raw in enumerate(ai_map.get("chapter_candidates", []), start=1):
        if not isinstance(raw, dict):
            continue
        primary = str(raw.get("primary_source_collection") or "")
        fallback_items_for_raw = _fallback_items_for_ai_raw(
            raw,
            fallback_items,
            fallback_by_id,
            fallback_by_collection,
            used_ids,
            primary,
        )
        if not fallback_items_for_raw:
            continue
        fallback_item = _combine_fallback_items(fallback_items_for_raw)
        primary = str(fallback_item.get("primary_source_collection") or primary)
        collections = [
            item
            for item in _string_list(raw.get("source_collections"))
            if item in valid_collections
        ]
        if primary not in collections:
            collections.insert(0, primary)
        candidate_id = str(raw.get("candidate_id") or fallback_item.get("candidate_id") or f"candidate:{primary}:{index:02d}")
        if not candidate_id.startswith("candidate:"):
            candidate_id = f"candidate:{index:02d}:{primary}"
        representative_chunks = _normalize_representative_chunks(
            raw.get("representative_chunks"),
            fallback_item.get("representative_chunks", []),
        )
        low_confidence_terms = _combined_low_confidence_terms(fallback_item, representative_chunks)
        core_concepts = _filter_low_confidence_terms(
            _string_list(raw.get("core_concepts")) or fallback_item.get("core_concepts", []),
            low_confidence_terms,
        )
        prerequisite_concepts = _combined_prerequisite_concepts(raw, fallback_item, representative_chunks)
        title_hint = _clean_title_hint(
            str(raw.get("canonical_title") or raw.get("title_hint") or fallback_item.get("title_hint") or primary),
            low_confidence_terms,
            fallback_item.get("title_hint") or primary,
        )
        candidates.append(
            {
                **fallback_item,
                "candidate_id": candidate_id,
                "status": "completed" if set(collections).issubset(completed_collections) else "pending",
                "title_hint": title_hint,
                "canonical_title": title_hint,
                "primary_source_collection": primary,
                "source_collections": collections,
                "merged_group_ids": _string_list(raw.get("merged_group_ids")) or fallback_item.get("merged_group_ids", []),
                "core_concepts": core_concepts,
                "prerequisite_concepts": prerequisite_concepts,
                "prerequisite_candidates": _string_list(raw.get("prerequisite_candidates")),
                "representative_chunks": representative_chunks,
                "low_confidence_terms": low_confidence_terms,
                "formula_roles": raw.get("formula_roles", fallback_item.get("formula_roles", []))
                if isinstance(raw.get("formula_roles", fallback_item.get("formula_roles", [])), list)
                else fallback_item.get("formula_roles", []),
                "coverage_evidence": _string_list(raw.get("coverage_evidence")) or fallback_item.get("coverage_evidence", []),
                "teaching_roles": _string_list(raw.get("teaching_role")) or fallback_item.get("teaching_roles", []),
                "completeness": str(raw.get("completeness") or fallback_item.get("completeness") or ""),
                "root_features": raw.get("root_features", fallback_item.get("root_features", {}))
                if isinstance(raw.get("root_features", fallback_item.get("root_features", {})), dict)
                else {},
                "reason": str(raw.get("reason") or fallback_item.get("reason") or ""),
                "candidate_node_mode": "ai",
            }
        )
        used_ids.update(item.get("candidate_id") for item in fallback_items_for_raw)
    if not candidates:
        return _with_merge_review_metadata(ai_map, fallback, fallback_items)
    for item in fallback_items:
        if item.get("candidate_id") not in used_ids:
            candidates.append(item)
    candidate_ids = {item["candidate_id"] for item in candidates}
    for item in candidates:
        item["prerequisite_candidates"] = [
            candidate
            for candidate in item.get("prerequisite_candidates", [])
            if candidate in candidate_ids
        ]
    candidates = _sort_candidates_by_prerequisites(candidates)
    return _with_merge_review_metadata(ai_map, fallback, candidates, generator="ai_with_chunk_cluster_fallback")


def _with_merge_review_metadata(
    ai_map: dict[str, Any],
    fallback: dict[str, Any],
    candidates: list[dict[str, Any]],
    *,
    generator: str | None = None,
) -> dict[str, Any]:
    merge_components = fallback.get("merge_review_components", [])
    merge_decisions = _merge_decisions(ai_map, merge_components, candidates)
    candidates = _canonicalize_merge_components(fallback, candidates, merge_decisions)
    diagnostics = [
        *[
            item
            for item in fallback.get("diagnostics", [])
            if isinstance(item, dict) and item.get("kind") != "canonical_merge_pending"
        ],
        *_pending_merge_diagnostics(merge_components, candidates, decisions=merge_decisions),
    ]
    _mark_pending_merge_candidates(candidates, diagnostics)
    return {
        "version": 1,
        "kind": "candidate_nodes",
        "generator": generator or fallback.get("generator", "candidate_nodes"),
        "group_pair_metrics": fallback.get("group_pair_metrics", []),
        "merge_review_components": merge_components,
        "merge_decisions": merge_decisions,
        "diagnostics": diagnostics,
        "chapter_candidates": candidates,
    }


def _next_unused_fallback_for_collection(
    items: list[dict[str, Any]],
    used_ids: set[str],
) -> dict[str, Any] | None:
    for item in items:
        candidate_id = str(item.get("candidate_id") or "")
        if candidate_id not in used_ids:
            return item
    return None


def _fallback_items_for_ai_raw(
    raw: dict[str, Any],
    fallback_items: list[dict[str, Any]],
    fallback_by_id: dict[Any, dict[str, Any]],
    fallback_by_collection: dict[str, list[dict[str, Any]]],
    used_ids: set[str],
    primary: str,
) -> list[dict[str, Any]]:
    result = []
    raw_id = str(raw.get("candidate_id") or "")
    if raw_id in fallback_by_id:
        result.append(fallback_by_id[raw_id])
    merged_group_ids = set(_string_list(raw.get("merged_group_ids")))
    if merged_group_ids:
        for item in fallback_items:
            if merged_group_ids & set(_string_list(item.get("merged_group_ids"))):
                result.append(item)
    refs = set(_string_list(raw.get("representative_chunks")))
    if refs:
        for item in fallback_items:
            item_refs = {chunk.get("chunk_ref") for chunk in item.get("representative_chunks", []) if isinstance(chunk, dict)}
            if refs & item_refs:
                result.append(item)
    if not result:
        fallback = _next_unused_fallback_for_collection(fallback_by_collection.get(primary, []), used_ids)
        if fallback is not None:
            result.append(fallback)
    unique = []
    seen = set()
    for item in result:
        candidate_id = str(item.get("candidate_id") or "")
        if not candidate_id or candidate_id in seen:
            continue
        seen.add(candidate_id)
        unique.append(item)
    return unique


def _combine_fallback_items(items: list[dict[str, Any]]) -> dict[str, Any]:
    if len(items) == 1:
        return dict(items[0])
    first = items[0]
    representative_chunks = [
        chunk
        for item in items
        for chunk in item.get("representative_chunks", []) or []
        if isinstance(chunk, dict)
    ]
    concepts = _ranked_terms((item.get("core_concepts", []) for item in items), 24)
    methods = _ranked_terms((item.get("methods", []) for item in items), 16)
    formulas = _ranked_terms((item.get("formulas", []) for item in items), 16)
    estimated_lines = sum(_int(item.get("estimated_output_lines"), 0) for item in items)
    return {
        **first,
        "candidate_id": str(first.get("candidate_id") or ""),
        "title_hint": _title_hint(concepts, str(first.get("title_hint") or "")),
        "source_collections": _unique(
            collection
            for item in items
            for collection in _string_list(item.get("source_collections"))
        ),
        "merged_group_ids": _unique(
            group_id
            for item in items
            for group_id in _string_list(item.get("merged_group_ids"))
        ),
        "core_concepts": concepts,
        "prerequisite_concepts": _ranked_terms((item.get("prerequisite_concepts", []) for item in items), 16),
        "methods": methods,
        "formulas": formulas,
        "formula_signatures": _ranked_terms((item.get("formula_signatures", []) for item in items), 16),
        "formula_roles": [
            role
            for item in items
            for role in item.get("formula_roles", []) or []
            if isinstance(role, dict)
        ][:16],
        "representative_chunks": representative_chunks[:12],
        "chunk_count": sum(int(item.get("chunk_count") or 0) for item in items),
        "estimated_output_lines": estimated_lines,
        "size_band": _size_band(estimated_lines),
        "cluster_cohesion": round(
            sum(float(item.get("cluster_cohesion") or 0) for item in items) / max(1, len(items)),
            4,
        ),
    }


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


def _combined_prerequisite_concepts(
    raw: dict[str, Any],
    fallback: dict[str, Any],
    representative_chunks: list[dict[str, Any]],
) -> list[str]:
    return _unique(
        [
            *_string_list(raw.get("prerequisite_concepts")),
            *_string_list(fallback.get("prerequisite_concepts")),
            *[
                prereq
                for chunk in representative_chunks
                for prereq in _string_list(chunk.get("prerequisites"))
            ],
        ]
    )[:16]


def _combined_low_confidence_terms(
    fallback: dict[str, Any],
    representative_chunks: list[dict[str, Any]],
) -> list[str]:
    return _unique(
        [
            *_string_list(fallback.get("low_confidence_terms")),
            *[
                term
                for chunk in representative_chunks
                for term in _string_list(chunk.get("low_confidence_section_terms"))
            ],
        ]
    )[:16]


def _filter_low_confidence_terms(terms: list[str], low_confidence_terms: list[str]) -> list[str]:
    low_confidence = set(low_confidence_terms)
    return [term for term in terms if term not in low_confidence]


def _clean_title_hint(title: str, low_confidence_terms: list[str], fallback: Any) -> str:
    cleaned = title
    for term in low_confidence_terms:
        cleaned = cleaned.replace(term, "")
    parts = [part for part in _SPLIT_RE.split(cleaned) if part and part not in _TITLE_STOPWORDS]
    if parts:
        return _title_hint(parts, str(fallback or title))
    fallback_text = str(fallback or "").strip()
    if fallback_text and fallback_text != title:
        return _clean_title_hint(fallback_text, low_confidence_terms, "")
    return title


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
