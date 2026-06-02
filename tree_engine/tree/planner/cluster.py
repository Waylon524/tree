"""Embedding-based MTU candidate clusters for Dagger node refinement."""

from __future__ import annotations

import math
from typing import Any

from tree.planner.ids import normalize_text_key, prefixed_id


def build_candidate_clusters(
    mtus: list[Any],
    vectors: dict[str, list[float]],
    *,
    similarity_threshold: float = 0.80,
    top_k: int = 5,
    max_size: int = 8,
) -> list[dict[str, Any]]:
    """Build candidate clusters from stored MTU vectors.

    The output is a candidate surface only. Dagger still confirms or splits
    multi-MTU clusters before KnowledgeNodes become canonical.
    """
    if not mtus:
        return []
    ordered = sorted(mtus, key=lambda m: (m.source_order_index, m.mtu_id))
    normalized = {mtu.mtu_id: _normalize(vectors.get(mtu.mtu_id, [])) for mtu in ordered}
    missing = [mtu.mtu_id for mtu in ordered if not normalized.get(mtu.mtu_id)]
    if missing:
        raise RuntimeError(f"Missing MTU vectors for clustering: {missing}")

    adjacency: dict[str, set[str]] = {mtu.mtu_id: set() for mtu in ordered}
    edge_reasons: dict[frozenset[str], set[str]] = {}
    edge_shared_defines: dict[frozenset[str], set[str]] = {}
    for left in ordered:
        scores: list[tuple[float, str]] = []
        left_vector = normalized[left.mtu_id]
        for right in ordered:
            if left.mtu_id == right.mtu_id:
                continue
            score = _cosine(left_vector, normalized[right.mtu_id])
            if score >= similarity_threshold:
                scores.append((score, right.mtu_id))
        scores.sort(key=lambda item: (-item[0], item[1]))
        for _score, right_id in scores[: max(0, top_k)]:
            adjacency[left.mtu_id].add(right_id)
            adjacency[right_id].add(left.mtu_id)
            _record_edge(edge_reasons, left.mtu_id, right_id, "embedding")

    define_index: dict[str, list[tuple[str, str]]] = {}
    for mtu in ordered:
        seen_keys: set[str] = set()
        for define in _mtu_defines(mtu):
            key = normalize_text_key(define)
            if not key or key in seen_keys:
                continue
            seen_keys.add(key)
            define_index.setdefault(key, []).append((mtu.mtu_id, define))

    for define_items in define_index.values():
        if len(define_items) < 2:
            continue
        for left_index, (left_id, left_define) in enumerate(define_items):
            for right_id, right_define in define_items[left_index + 1:]:
                adjacency[left_id].add(right_id)
                adjacency[right_id].add(left_id)
                _record_edge(edge_reasons, left_id, right_id, "shared_define")
                pair = frozenset({left_id, right_id})
                edge_shared_defines.setdefault(pair, set()).update({left_define, right_define})

    by_id = {mtu.mtu_id: mtu for mtu in ordered}
    clusters: list[list[str]] = []
    seen: set[str] = set()
    for mtu in ordered:
        if mtu.mtu_id in seen:
            continue
        component: list[str] = []
        stack = [mtu.mtu_id]
        seen.add(mtu.mtu_id)
        while stack:
            current = stack.pop()
            component.append(current)
            for neighbor in sorted(adjacency[current]):
                if neighbor in seen:
                    continue
                seen.add(neighbor)
                stack.append(neighbor)
        component.sort(key=lambda mid: (by_id[mid].source_order_index, mid))
        clusters.extend(_split_component(component, max_size=max_size))

    return [
        _cluster_payload(
            member_ids,
            by_id,
            edge_reasons=edge_reasons,
            edge_shared_defines=edge_shared_defines,
        )
        for member_ids in clusters
    ]


def cluster_to_raw_node(cluster: dict[str, Any], mtus_by_id: dict[str, Any]) -> dict[str, Any]:
    member_ids = list(cluster.get("member_mtu_ids") or [])
    members = [mtus_by_id[mid] for mid in member_ids if mid in mtus_by_id]
    return {
        "title": members[0].title if members else "",
        "member_mtu_ids": member_ids,
        "defines": [define for member in members for define in _mtu_defines(member)],
    }


def cluster_refinement_payload(cluster: dict[str, Any], mtus_by_id: dict[str, Any]) -> dict[str, Any]:
    mtus = [mtus_by_id[mid] for mid in cluster.get("member_mtu_ids", []) if mid in mtus_by_id]
    return {
        "task": "REFINE_NODE_CLUSTER",
        "source_cluster_id": cluster["source_cluster_id"],
        "cross_collection": cluster["cross_collection"],
        "candidate_member_mtu_ids": list(cluster["member_mtu_ids"]),
        "cluster_reasons": list(cluster.get("cluster_reasons") or []),
        "shared_defines": list(cluster.get("shared_defines") or []),
        "mtus": [
            {
                "mtu_id": mtu.mtu_id,
                "title": mtu.title,
                "defines": _mtu_defines(mtu),
                "summary": getattr(mtu, "summary", ""),
                "unit_kind": getattr(mtu, "unit_kind", "concept"),
                "collection": mtu.collection,
                "source_order_index": mtu.source_order_index,
            }
            for mtu in mtus
        ],
    }


def _cluster_payload(
    member_ids: list[str],
    by_id: dict[str, Any],
    *,
    edge_reasons: dict[frozenset[str], set[str]],
    edge_shared_defines: dict[frozenset[str], set[str]],
) -> dict[str, Any]:
    collections = sorted({by_id[mid].collection for mid in member_ids if by_id[mid].collection})
    member_set = set(member_ids)
    reasons: set[str] = set()
    shared_defines: set[str] = set()
    for pair, pair_reasons in edge_reasons.items():
        if pair <= member_set:
            reasons.update(pair_reasons)
            shared_defines.update(edge_shared_defines.get(pair, set()))
    return {
        "source_cluster_id": prefixed_id("mc", member_ids),
        "member_mtu_ids": member_ids,
        "cross_collection": len(collections) > 1,
        "collections": collections,
        "cluster_reasons": sorted(reasons),
        "shared_defines": sorted(shared_defines),
    }


def _record_edge(edge_reasons: dict[frozenset[str], set[str]], left_id: str, right_id: str, reason: str) -> None:
    edge_reasons.setdefault(frozenset({left_id, right_id}), set()).add(reason)


def _split_component(component: list[str], *, max_size: int) -> list[list[str]]:
    size = max(1, max_size)
    return [component[index : index + size] for index in range(0, len(component), size)]


def _mtu_defines(mtu: Any) -> list[str]:
    return list(getattr(mtu, "defines", None) or getattr(mtu, "keywords", []) or [])


def _normalize(vector: list[float]) -> list[float]:
    if not vector:
        return []
    norm = math.sqrt(sum(float(value) * float(value) for value in vector))
    if norm <= 0:
        return []
    return [float(value) / norm for value in vector]


def _cosine(left: list[float], right: list[float]) -> float:
    dims = min(len(left), len(right))
    if dims <= 0:
        return 0.0
    return sum(left[index] * right[index] for index in range(dims))
