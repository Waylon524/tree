"""Candidate knowledge nodes built from source inventory."""

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
) -> dict[str, Any]:
    """Build candidate nodes with AI enrichment when possible, else use deterministic fallback."""
    fallback = rebuild_candidate_nodes(root, inventory, completed_collections)
    if builder is None:
        return fallback
    try:
        ai_map = await builder.build_candidate_nodes(
            _inventory_summary_for_ai(inventory, fallback),
            sorted(completed_collections or set()),
        )
        normalized = _normalize_ai_map(ai_map, fallback, completed_collections or set())
        save_candidate_nodes(root, normalized)
        return normalized
    except Exception:
        return fallback


def build_candidate_nodes_context(candidate_nodes: dict[str, Any], limit: int = 10) -> str:
    """Format candidate knowledge nodes for examiner Phase C."""
    candidates = [
        item
        for item in candidate_nodes.get("chapter_candidates", [])
        if isinstance(item, dict)
    ]
    lines = [
        "## Candidate Knowledge Nodes",
        "These are possible knowledge-point nodes generated from source inventory.",
        "They are not the curriculum order; the deterministic graph planner selects direction.",
        "",
    ]
    if not candidates:
        lines.append("(no candidate knowledge nodes available)")
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
        }
        chunk["signature_terms"] = _knowledge_signature_terms(chunk)
        chunks.append(chunk)
    chunks.sort(key=_chunk_sort_key)
    return chunks


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
        "summary": chunk.get("summary", ""),
    }


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
    return {
        "candidate_nodes": [
            {
                "candidate_id": item.get("candidate_id"),
                "title_hint": item.get("title_hint"),
                "primary_source_collection": item.get("primary_source_collection"),
                "source_collections": item.get("source_collections", []),
                "core_concepts": item.get("core_concepts", [])[:18],
                "prerequisite_concepts": item.get("prerequisite_concepts", [])[:12],
                "representative_chunks": [
                    {
                        "chunk_ref": chunk.get("chunk_ref"),
                        "core_concepts": chunk.get("core_concepts", [])[:8],
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
        raw_id = str(raw.get("candidate_id") or "")
        primary = str(raw.get("primary_source_collection") or "")
        fallback_item = fallback_by_id.get(raw_id)
        if fallback_item is None:
            fallback_item = _next_unused_fallback_for_collection(
                fallback_by_collection.get(primary, []),
                used_ids,
            )
        if fallback_item is None:
            continue
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
        candidates.append(
            {
                **fallback_item,
                "candidate_id": candidate_id,
                "status": "completed" if set(collections).issubset(completed_collections) else "pending",
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
                "candidate_node_mode": "ai",
            }
        )
        used_ids.add(fallback_item.get("candidate_id"))
    if not candidates:
        return fallback
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
    return {
        "version": 1,
        "kind": "candidate_nodes",
        "generator": "ai_with_chunk_cluster_fallback",
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
