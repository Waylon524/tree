"""KnowledgeNode API backed by the legacy candidate-nodes JSON schema."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from tree.curriculum import candidate_nodes as legacy


def load_knowledge_nodes(root: Path) -> dict[str, Any]:
    """Load canonical KnowledgeNodes while preserving legacy schema keys."""
    return _as_knowledge_nodes(legacy.load_candidate_nodes(root))


def save_knowledge_nodes(root: Path, knowledge_nodes: dict[str, Any]) -> None:
    """Persist KnowledgeNodes using the compatibility candidate-nodes file."""
    legacy.save_candidate_nodes(root, _as_legacy_candidate_nodes(knowledge_nodes))


def rebuild_knowledge_nodes(
    root: Path,
    inventory: dict[str, Any],
    completed_collections: set[str] | None = None,
) -> dict[str, Any]:
    """Build KnowledgeNodes from inventory via the compatibility generator."""
    return _as_knowledge_nodes(
        legacy.rebuild_candidate_nodes(
            root,
            inventory,
            completed_collections=completed_collections,
        )
    )


async def rebuild_knowledge_nodes_with_ai(
    root: Path,
    inventory: dict[str, Any],
    builder: Any,
    completed_collections: set[str] | None = None,
) -> dict[str, Any]:
    """Build KnowledgeNodes with AI while keeping disk compatibility."""
    return _as_knowledge_nodes(
        await legacy.rebuild_candidate_nodes_with_ai(
            root,
            inventory,
            builder,
            completed_collections=completed_collections,
        )
    )


def _as_knowledge_nodes(raw: dict[str, Any]) -> dict[str, Any]:
    candidates = [item for item in raw.get("chapter_candidates", []) if isinstance(item, dict)]
    nodes = [_knowledge_node(item) for item in candidates]
    result = dict(raw)
    result["kind"] = "knowledge_nodes"
    result["knowledge_nodes"] = nodes
    result["chapter_candidates"] = candidates
    if "group_pair_metrics" in result:
        result["group_pair_metrics"] = [
            _normalize_group_pair_metric(item)
            for item in result.get("group_pair_metrics", [])
            if isinstance(item, dict)
        ]
    return result


def _as_legacy_candidate_nodes(raw: dict[str, Any]) -> dict[str, Any]:
    if raw.get("chapter_candidates"):
        result = dict(raw)
        result["kind"] = "candidate_nodes"
        return result
    candidates = [
        _legacy_candidate(item)
        for item in raw.get("knowledge_nodes", [])
        if isinstance(item, dict)
    ]
    return {
        "version": raw.get("version", 1),
        "kind": "candidate_nodes",
        "chapter_candidates": candidates,
        "group_pair_metrics": raw.get("group_pair_metrics", []),
    }


def _knowledge_node(item: dict[str, Any]) -> dict[str, Any]:
    node_id = str(item.get("candidate_id") or item.get("node_id") or "")
    return {
        **item,
        "node_id": node_id,
        "title": str(item.get("canonical_title") or item.get("title_hint") or node_id),
        "kind": "knowledge_node",
        "compat_candidate_id": node_id,
    }


def _legacy_candidate(item: dict[str, Any]) -> dict[str, Any]:
    candidate_id = str(item.get("compat_candidate_id") or item.get("candidate_id") or item.get("node_id") or "")
    return {
        **item,
        "candidate_id": candidate_id,
        "title_hint": item.get("title_hint") or item.get("title") or candidate_id,
    }


def _normalize_group_pair_metric(item: dict[str, Any]) -> dict[str, Any]:
    metric = dict(item)
    if metric.get("embedding_similarity") is None:
        metric["embedding_similarity"] = {
            "status": "unavailable",
            "reason": "group embedding centroids are not persisted yet",
        }
    return metric
