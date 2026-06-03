"""Dagger orchestration + deterministic DAG construction.

Dagger no longer draws edges directly. It first merges MTUs into canonical
KnowledgeNodes and names what each node defines. Then it selects prerequisite
defines for each node from the global define dictionary. Program code maps those
define requirements back to defining nodes and builds the prerequisite DAG.
"""

from __future__ import annotations

import logging
import inspect
from typing import Any

from tree.planner.cluster import (
    build_candidate_clusters,
    cluster_refinement_payload,
    cluster_to_raw_node,
)
from tree.planner.ids import normalize_concepts, normalize_text_key, prefixed_id

logger = logging.getLogger(__name__)

_VALID_RELATIONS = {"prerequisite", "order"}
_MAX_NODE_DEFINES = 8
_MAX_REQUIRED_DEFINES = 24


async def build_dag(
    agent: Any,
    mtus: list[Any],
    *,
    settings: Any,
    vector_provider: Any | None = None,
    progress: Any | None = None,
) -> dict[str, Any]:
    """Build canonical nodes + prerequisite edges from MTUs."""
    if not mtus:
        _complete_progress_stage(progress, "cluster", "No MTUs to cluster")
        _complete_progress_stage(progress, "link", "No nodes to link")
        return {"nodes": [], "edges": [], "roots": [], "diagnostics": []}

    timeout = getattr(settings, "dagger_build_timeout_sec", 480.0)
    repair = getattr(settings, "dagger_repair_attempts", 1)
    max_nodes = getattr(settings, "dagger_max_nodes_per_call", 400)

    if getattr(settings, "dagger_embed_cluster_enabled", False):
        nodes_raw = await _build_clustered_nodes(
            agent,
            mtus,
            settings=settings,
            vector_provider=vector_provider,
            timeout=timeout,
            repair=repair,
            progress=progress,
        )
    elif len(mtus) <= max_nodes:
        _set_progress_stage(
            progress,
            "cluster",
            total=1,
            done=0,
            status="running",
            message="Building nodes",
        )
        nodes_raw = await _build_nodes_with_repair(
            agent, [_meta(m) for m in mtus], timeout=timeout, repair=repair
        )
        _advance_progress_stage(progress, "cluster", message="Built nodes")
    else:
        logger.info("Dagger batched node build: %d MTUs > %d per call", len(mtus), max_nodes)
        nodes_raw = await _build_nodes_batched(agent, mtus, timeout=timeout, repair=repair, progress=progress)

    nodes, diagnostics = await _canonicalize_nodes_with_define_repair(
        agent, mtus, nodes_raw, timeout=timeout, repair=repair
    )
    _complete_progress_stage(progress, "cluster", "Cluster complete")
    _set_progress_stage(
        progress,
        "link",
        total=len(nodes),
        done=0,
        status="running" if nodes else "complete",
        message="Selecting required defines",
    )
    prerequisites = await _build_prerequisites_with_repair(
        agent, nodes, timeout=timeout, repair=repair, progress=progress
    )
    nodes, edges, diagnostics = await _build_edges_with_cycle_repair(
        agent, nodes, prerequisites, diagnostics=diagnostics, timeout=timeout, repair=repair
    )
    _set_progress_stage(
        progress,
        "link",
        done=len(nodes),
        status="complete",
        message="Link complete",
        active=[],
    )

    prereq_targets = {e["to_node_id"] for e in edges if e["relation"] == "prerequisite"}
    roots = sorted({n["node_id"] for n in nodes} - prereq_targets)
    return {"nodes": nodes, "edges": edges, "roots": roots, "diagnostics": diagnostics}


async def _build_clustered_nodes(
    agent: Any,
    mtus: list[Any],
    *,
    settings: Any,
    vector_provider: Any,
    timeout: float,
    repair: int,
    progress: Any | None,
) -> list[dict[str, Any]]:
    if vector_provider is None:
        raise RuntimeError("Dagger embedding clustering is enabled but no vector_provider was supplied.")
    vectors = await _source_mtu_vectors(vector_provider, [m.mtu_id for m in mtus])
    clusters = build_candidate_clusters(
        mtus,
        vectors,
        similarity_threshold=getattr(settings, "dagger_cluster_similarity_threshold", 0.80),
        top_k=getattr(settings, "dagger_cluster_top_k", 5),
        max_size=getattr(settings, "dagger_cluster_max_size", 8),
    )
    mtus_by_id = {m.mtu_id: m for m in mtus}
    nodes_raw: list[dict[str, Any]] = []
    auto_singleton = getattr(settings, "dagger_cluster_auto_accept_singleton", True)
    _set_progress_stage(
        progress,
        "cluster",
        total=len(clusters),
        done=0,
        status="running" if clusters else "complete",
        message="Refining candidate clusters",
    )

    for cluster in clusters:
        member_ids = list(cluster.get("member_mtu_ids") or [])
        _set_progress_stage(
            progress,
            "cluster",
            status="running",
            active=str(cluster.get("source_cluster_id") or ",".join(member_ids[:2])),
        )
        if len(member_ids) == 1 and auto_singleton:
            nodes_raw.append(cluster_to_raw_node(cluster, mtus_by_id))
            _advance_progress_stage(progress, "cluster", message="Accepted singleton cluster")
            continue
        payload = cluster_refinement_payload(cluster, mtus_by_id)
        nodes_raw.extend(
            await _build_cluster_nodes_with_repair(agent, payload, timeout=timeout, repair=repair)
        )
        _advance_progress_stage(progress, "cluster", message="Refined cluster")
    return nodes_raw


async def _source_mtu_vectors(vector_provider: Any, mtu_ids: list[str]) -> dict[str, list[float]]:
    if hasattr(vector_provider, "source_mtu_vectors"):
        result = vector_provider.source_mtu_vectors(mtu_ids)
    else:
        result = vector_provider(mtu_ids)
    if inspect.isawaitable(result):
        result = await result
    return dict(result or {})


async def _build_cluster_nodes_with_repair(
    agent: Any,
    payload: dict[str, Any],
    *,
    timeout: float,
    repair: int,
) -> list[dict[str, Any]]:
    feedback = ""
    last_error: Exception | None = None
    allowed = set(payload.get("candidate_member_mtu_ids") or [])
    allowed_defines_by_mtu = _payload_defines_by_mtu(payload.get("mtus") or [])
    for attempt in range(repair + 1):
        try:
            request = dict(payload)
            if feedback:
                request["_note"] = feedback
            raw = await _agent_build_nodes(agent, [request], timeout_sec=timeout)
            nodes = list(raw.get("nodes") or [])
            _validate_cluster_nodes(nodes, allowed, allowed_defines_by_mtu)
            return nodes
        except (ValueError, KeyError) as exc:
            last_error = exc
            logger.warning("Dagger cluster node build invalid (attempt %d): %s", attempt + 1, exc)
            feedback = f"Previous output was invalid ({exc}); re-emit only valid nodes for this cluster."
    raise ValueError(f"Dagger cluster node build failed: {last_error}")


def _validate_cluster_nodes(
    nodes: list[dict[str, Any]], allowed: set[str], allowed_defines_by_mtu: dict[str, set[str]]
) -> None:
    _validate_node_replacements(
        nodes,
        allowed,
        label="cluster node",
        require_all_members=False,
        allowed_defines_by_mtu=allowed_defines_by_mtu,
    )


def _validate_node_replacements(
    nodes: list[dict[str, Any]],
    allowed: set[str],
    *,
    label: str,
    require_all_members: bool,
    allowed_defines_by_mtu: dict[str, set[str]] | None = None,
) -> None:
    seen: set[str] = set()
    for index, node in enumerate(nodes, start=1):
        if "keywords" in node:
            raise ValueError(f"{label} {index} must use defines, not keywords")
        title = str(node.get("title") or "").strip()
        if not title:
            raise ValueError(f"{label} {index} title is empty")
        defines = node.get("defines")
        if not isinstance(defines, list):
            raise ValueError(f"{label} {index} defines must be a list")
        if not defines:
            raise ValueError(f"{label} {index} defines must not be empty")
        if len(defines) > _MAX_NODE_DEFINES:
            raise ValueError(f"{label} {index} defines exceeds {_MAX_NODE_DEFINES}")
        member_ids = list(node.get("member_mtu_ids") or [])
        if not member_ids:
            raise ValueError(f"{label} {index} member_mtu_ids is empty")
        outside = sorted(set(member_ids) - allowed)
        if outside:
            raise ValueError(f"{label} {index} returned MTUs outside the candidate set: {outside}")
        duplicate = sorted(set(member_ids) & seen)
        if duplicate:
            raise ValueError(f"{label} {index} repeated member_mtu_ids: {duplicate}")
        if allowed_defines_by_mtu is not None:
            allowed_defines = {
                define
                for member_id in member_ids
                for define in allowed_defines_by_mtu.get(member_id, set())
            }
            invented = [define for define in defines if str(define) not in allowed_defines]
            if invented:
                raise ValueError(
                    f"{label} {index} defines must be selected from its member MTU defines: {invented}"
                )
        seen.update(member_ids)
    if require_all_members:
        missing = sorted(allowed - seen)
        if missing:
            raise ValueError(f"{label} replacement omitted member_mtu_ids: {missing}")


async def _build_nodes_with_repair(
    agent: Any, payload: list[dict], *, timeout: float, repair: int
) -> list[dict[str, Any]]:
    feedback = ""
    for attempt in range(repair + 1):
        try:
            extra = [{"_note": feedback}] if feedback else []
            raw = await _agent_build_nodes(agent, payload + extra, timeout_sec=timeout)
            return list(raw.get("nodes") or [])
        except (ValueError, KeyError) as exc:
            logger.warning("Dagger node build invalid (attempt %d): %s", attempt + 1, exc)
            feedback = f"Previous output was invalid JSON ({exc}); re-emit the strict nodes schema."
    return []


async def _agent_build_nodes(agent: Any, payload: list[dict], *, timeout_sec: float) -> dict:
    if hasattr(agent, "build_nodes"):
        return await agent.build_nodes(payload, timeout_sec=timeout_sec)
    raw = await agent.build(payload, timeout_sec=timeout_sec)
    for node in raw.get("nodes") or []:
        if "defines" not in node:
            node["defines"] = node.get("keywords") or []
    return {"nodes": raw.get("nodes") or []}


async def _build_nodes_batched(
    agent: Any, mtus: list[Any], *, timeout: float, repair: int, progress: Any | None
) -> list[dict[str, Any]]:
    by_collection: dict[str, list[Any]] = {}
    for mtu in mtus:
        by_collection.setdefault(mtu.collection, []).append(mtu)

    nodes_raw: list[dict[str, Any]] = []
    _set_progress_stage(
        progress,
        "cluster",
        total=len(by_collection),
        done=0,
        status="running",
        message="Building nodes by collection",
    )
    for group in by_collection.values():
        _set_progress_stage(progress, "cluster", status="running", active=group[0].collection)
        raw = await _build_nodes_with_repair(
            agent, [_meta(m) for m in group], timeout=timeout, repair=repair
        )
        nodes_raw.extend(raw)
        _advance_progress_stage(progress, "cluster", message=f"Built {group[0].collection}")
    return _merge_raw_nodes_by_title(nodes_raw)


def _merge_raw_nodes_by_title(nodes_raw: list[dict]) -> list[dict]:
    merged: dict[str, dict] = {}
    for node in nodes_raw:
        key = normalize_text_key(str(node.get("title") or ""))
        if not key:
            continue
        defines = list(node.get("defines") or node.get("keywords") or [])
        if key in merged:
            existing = merged[key]
            existing["member_mtu_ids"] = list(existing.get("member_mtu_ids", [])) + list(
                node.get("member_mtu_ids", [])
            )
            existing["defines"] = list(existing.get("defines", [])) + defines
        else:
            clone = dict(node)
            clone["defines"] = defines
            merged[key] = clone
    return list(merged.values())


async def _canonicalize_nodes_with_define_repair(
    agent: Any,
    mtus: list[Any],
    nodes_raw: list[dict[str, Any]],
    *,
    timeout: float,
    repair: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    last_nodes = nodes_raw
    pairwise_budget = max(1, len(mtus) * max(1, repair + 1) * 2)
    for attempt in range(pairwise_budget + 1):
        nodes, diagnostics = _canonicalize_nodes(mtus, last_nodes)
        nodes, merge_diagnostics = _merge_nodes_without_defines(nodes)
        diagnostics.extend(merge_diagnostics)
        conflicts = _define_conflicts(nodes)
        if not conflicts:
            return nodes, diagnostics
        if repair <= 0 or attempt >= pairwise_budget:
            raise ValueError(f"Dagger node defines conflict remains after repair: {conflicts}")
        conflict = _first_define_conflict(conflicts)
        logger.warning(
            "Dagger node defines conflict (pairwise repair %d): %s",
            attempt + 1,
            conflict,
        )
        replacements = await _repair_define_conflict_pair(
            agent,
            mtus,
            nodes,
            conflict,
            timeout=timeout,
            repair=repair,
        )
        last_nodes = _replace_conflict_nodes(nodes, conflict, replacements)
    return _canonicalize_nodes(mtus, last_nodes)


async def _agent_repair_defines(agent: Any, payload: dict[str, Any], *, timeout_sec: float) -> dict:
    if hasattr(agent, "repair_defines"):
        return await agent.repair_defines(payload, timeout_sec=timeout_sec)
    return {"nodes": payload.get("nodes") or []}


async def _repair_define_conflict_pair(
    agent: Any,
    mtus: list[Any],
    nodes: list[dict[str, Any]],
    conflict: dict[str, Any],
    *,
    timeout: float,
    repair: int,
) -> list[dict[str, Any]]:
    node_ids = list(conflict.get("node_ids") or [])
    if len(node_ids) != 2:
        raise ValueError(f"Define conflict repair requires exactly two node_ids: {conflict}")
    by_id = {node["node_id"]: node for node in nodes}
    try:
        pair = [by_id[node_ids[0]], by_id[node_ids[1]]]
    except KeyError as exc:
        raise ValueError(f"Define conflict references unknown node: {exc}") from exc

    allowed = {
        mtu_id
        for node in pair
        for mtu_id in (node.get("member_mtu_ids") or [])
    }
    allowed_defines_by_mtu = {
        mtu.mtu_id: set(_mtu_defines(mtu))
        for mtu in mtus
        if mtu.mtu_id in allowed
    }
    feedback = ""
    last_error: Exception | None = None
    for attempt in range(repair + 1):
        payload: dict[str, Any] = {
            "task": "REPAIR_NODE_DEFINES",
            "define_conflict": conflict,
            "candidate_member_mtu_ids": sorted(allowed),
            "nodes": [_node_repair_meta(node) for node in pair],
        }
        if feedback:
            payload["_note"] = feedback
        try:
            raw = await _agent_repair_defines(agent, payload, timeout_sec=timeout)
            replacements = list(raw.get("nodes") or [])
            _validate_node_replacements(
                replacements,
                allowed,
                label="define repair node",
                require_all_members=True,
                allowed_defines_by_mtu=allowed_defines_by_mtu,
            )
            return replacements
        except (ValueError, KeyError) as exc:
            last_error = exc
            logger.warning(
                "Dagger define pair repair invalid (attempt %d): %s",
                attempt + 1,
                exc,
            )
            feedback = (
                f"Previous output was invalid ({exc}); re-emit only replacement nodes "
                "for the two conflicted nodes using the normal nodes schema."
            )
    raise ValueError(f"Dagger define pair repair failed: {last_error}")


def _payload_defines_by_mtu(mtus: list[dict[str, Any]]) -> dict[str, set[str]]:
    return {
        str(item.get("mtu_id")): {str(define) for define in (item.get("defines") or [])}
        for item in mtus
        if item.get("mtu_id")
    }


def _first_define_conflict(conflicts: list[dict[str, Any]]) -> dict[str, Any]:
    return sorted(
        conflicts,
        key=lambda item: (
            tuple(item.get("node_orders") or []),
            tuple(item.get("node_ids") or []),
            str(item.get("type") or ""),
            str(item.get("define") or item.get("defines") or ""),
        ),
    )[0]


def _replace_conflict_nodes(
    nodes: list[dict[str, Any]],
    conflict: dict[str, Any],
    replacements: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    repaired_ids = set(conflict.get("node_ids") or [])
    return [node for node in nodes if node["node_id"] not in repaired_ids] + replacements


def _node_repair_meta(node: dict[str, Any]) -> dict[str, Any]:
    return {
        "node_id": node.get("node_id"),
        "title": node.get("title", ""),
        "member_mtu_ids": list(node.get("member_mtu_ids") or []),
        "defines": list(node.get("defines") or []),
        "collections": list(node.get("collections") or []),
        "source_order_index": node.get("source_order_index", 0),
    }


def _canonicalize_nodes(
    mtus: list[Any], nodes_raw: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    by_id = {m.mtu_id: m for m in mtus}
    assigned: set[str] = set()
    nodes: list[dict[str, Any]] = []
    diagnostics: list[dict[str, Any]] = []

    for raw in nodes_raw:
        member_ids = [
            mid for mid in (raw.get("member_mtu_ids") or [])
            if mid in by_id and mid not in assigned
        ]
        if not member_ids:
            continue
        assigned.update(member_ids)
        nodes.append(
            _node_from_members(
                member_ids,
                by_id,
                title=raw.get("title"),
                defines=raw.get("defines") or raw.get("keywords"),
            )
        )

    for mtu in mtus:
        if mtu.mtu_id in assigned:
            continue
        nodes.append(_node_from_members([mtu.mtu_id], by_id, defines=_mtu_defines(mtu)))
        diagnostics.append(
            {
                "severity": "warning",
                "stage": "dagger",
                "reason_code": "mtu_unassigned",
                "mtu_id": mtu.mtu_id,
                "message": "MTU not placed by Dagger; kept as a singleton node.",
            }
        )

    nodes.sort(key=lambda n: (n["source_order_index"], n["node_id"]))
    return nodes, diagnostics


def _merge_nodes_without_defines(
    nodes: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    ordered = sorted(nodes, key=lambda n: (n["source_order_index"], n["node_id"]))
    keep: list[dict[str, Any]] = [dict(n) for n in ordered if n.get("defines")]
    diagnostics: list[dict[str, Any]] = []
    if not keep:
        return ordered, diagnostics

    for node in ordered:
        if node.get("defines"):
            continue
        target = _adjacent_node_with_defines(node, keep, prefer_previous=True)
        if target is None:
            target = _adjacent_node_with_defines(node, keep, prefer_previous=False)
        if target is None:
            diagnostics.append(
                {
                    "severity": "warning",
                    "stage": "dagger",
                    "reason_code": "node_without_defines_unmerged",
                    "node_id": node["node_id"],
                    "message": "Node had no defines and no same-collection neighbor to merge into.",
                }
            )
            keep.append(node)
            continue
        target["member_mtu_ids"] = sorted(
            set(target.get("member_mtu_ids", [])) | set(node.get("member_mtu_ids", []))
        )
        target["collections"] = sorted(set(target.get("collections", [])) | set(node.get("collections", [])))
        target["source_order_index"] = min(target.get("source_order_index", 0), node.get("source_order_index", 0))
        target["node_id"] = prefixed_id("kn", target["member_mtu_ids"])
        diagnostics.append(
            {
                "severity": "info",
                "stage": "dagger",
                "reason_code": "node_without_defines_merged",
                "node_id": node["node_id"],
                "merged_into": target["node_id"],
                "message": "Node without defines was merged into the nearest same-collection node.",
            }
        )

    keep.sort(key=lambda n: (n["source_order_index"], n["node_id"]))
    return keep, diagnostics


def _adjacent_node_with_defines(
    node: dict[str, Any], candidates: list[dict[str, Any]], *, prefer_previous: bool
) -> dict[str, Any] | None:
    collections = set(node.get("collections") or [])
    order = node.get("source_order_index", 0)
    same_collection = [
        c for c in candidates
        if collections & set(c.get("collections") or [])
    ]
    if prefer_previous:
        previous = [c for c in same_collection if c.get("source_order_index", 0) <= order]
        return max(previous, key=lambda c: (c.get("source_order_index", 0), c["node_id"]), default=None)
    following = [c for c in same_collection if c.get("source_order_index", 0) > order]
    return min(following, key=lambda c: (c.get("source_order_index", 0), c["node_id"]), default=None)


def _define_conflicts(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    conflicts: list[dict[str, Any]] = []
    seen: dict[str, tuple[str, str, int]] = {}
    pairs: list[tuple[str, str, str, str, int]] = []
    for node in nodes:
        node_order = int(node.get("source_order_index") or 0)
        for define in node.get("defines") or []:
            key = normalize_text_key(define)
            if not key:
                continue
            if key in seen and seen[key][0] != node["node_id"]:
                conflicts.append(
                    {
                        "type": "duplicate_define",
                        "define": define,
                        "node_ids": [seen[key][0], node["node_id"]],
                        "node_orders": [seen[key][2], node_order],
                    }
                )
            else:
                seen[key] = (node["node_id"], define, node_order)
            pairs.append((node["node_id"], define, key, node["title"], node_order))

    for i, (left_id, left, left_key, _left_title, left_order) in enumerate(pairs):
        for right_id, right, right_key, _right_title, right_order in pairs[i + 1:]:
            if left_id == right_id or not left_key or not right_key or left_key == right_key:
                continue
            if _is_contained_define_conflict(left_key, right_key):
                conflicts.append(
                    {
                        "type": "contained_define",
                        "defines": [left, right],
                        "node_ids": [left_id, right_id],
                        "node_orders": [left_order, right_order],
                    }
                )
    return conflicts


def _is_contained_define_conflict(left_key: str, right_key: str) -> bool:
    """Return true for likely duplicate containment, not generic base terms.

    Short foundational terms such as 光程, 偏振, 频率, or 波长 often appear inside
    more specific formulas/methods. Treating every substring as a conflict makes
    repair payloads noisy and prevents large cross-material runs from completing.
    """
    if left_key == right_key or not left_key or not right_key:
        return False
    if left_key in right_key:
        shorter, longer = left_key, right_key
    elif right_key in left_key:
        shorter, longer = right_key, left_key
    else:
        return False
    if _is_specific_derivative_define(shorter, longer):
        return False
    return len(shorter) >= 4 and (len(shorter) / max(1, len(longer))) >= 0.55


def _is_specific_derivative_define(_shorter: str, longer: str) -> bool:
    left, separator, right = longer.partition("的")
    return bool(separator and left and right)


async def _build_prerequisites_with_repair(
    agent: Any,
    nodes: list[dict[str, Any]],
    *,
    timeout: float,
    repair: int,
    progress: Any | None = None,
) -> list[dict[str, Any]]:
    define_dictionary = _define_dictionary(nodes)
    prereqs: list[dict[str, Any]] = []
    node_meta = [_node_prereq_meta(n) for n in nodes]
    for node in nodes:
        _set_progress_stage(
            progress,
            "link",
            status="running",
            active=str(node.get("title") or node["node_id"]),
            message=f"Selecting required defines for {node.get('title') or node['node_id']}",
        )
        prereq = await _build_one_prerequisite_with_repair(
            agent,
            node,
            node_meta,
            nodes,
            define_dictionary,
            timeout=timeout,
            repair=repair,
        )
        prereqs.append(prereq)
        _advance_progress_stage(
            progress,
            "link",
            message=f"Selected required defines for {node.get('title') or node['node_id']}",
        )
    _validate_prerequisites(prereqs, nodes, define_dictionary)
    return prereqs


async def _build_one_prerequisite_with_repair(
    agent: Any,
    node: dict[str, Any],
    node_meta: list[dict[str, Any]],
    nodes: list[dict[str, Any]],
    define_dictionary: dict[str, Any],
    *,
    timeout: float,
    repair: int,
) -> dict[str, Any]:
    feedback = ""
    last_error: Exception | None = None
    target = _node_prereq_meta(node)
    for attempt in range(repair + 1):
        try:
            payload = {
                "nodes": node_meta,
                "defines": define_dictionary,
                "target_node": target,
                "instructions": (
                    "Return exactly one node_prerequisites item for target_node. "
                    + feedback
                ).strip(),
            }
            raw = await _agent_build_prerequisites(agent, payload, timeout_sec=timeout)
            items = list(raw.get("node_prerequisites") or [])
            prereq = (
                _empty_prerequisite(node)
                if not items
                else _target_prerequisite(items, node, nodes)
            )
            _validate_prerequisites([prereq], nodes, define_dictionary)
            return prereq
        except (ValueError, KeyError) as exc:
            last_error = exc
            logger.warning(
                "Dagger prerequisite invalid for %s (attempt %d): %s",
                node["node_id"],
                attempt + 1,
                exc,
            )
            feedback = (
                f"Previous prerequisite output for {node['node_id']} was invalid ({exc}); "
                "repair only this target node."
            )
    raise ValueError(
        f"Dagger prerequisites remain invalid for {node['node_id']} after "
        f"{repair + 1} attempt(s): {last_error}"
    )


def _empty_prerequisite(node: dict[str, Any]) -> dict[str, Any]:
    return {
        "node_id": node["node_id"],
        "required_defines": [],
        "external_prerequisites": [],
        "reason": "No prerequisite defines selected.",
    }


def _target_prerequisite(
    prereqs: list[dict[str, Any]],
    node: dict[str, Any],
    nodes: list[dict[str, Any]],
) -> dict[str, Any]:
    title_to_id = {normalize_text_key(n["title"]): n["node_id"] for n in nodes}
    for item in prereqs:
        if _prereq_node_id(item, title_to_id) == node["node_id"]:
            clone = dict(item)
            clone["node_id"] = node["node_id"]
            return clone
    raise ValueError(f"missing prerequisite block for {node['node_id']}")


async def _agent_build_prerequisites(agent: Any, payload: dict[str, Any], *, timeout_sec: float) -> dict:
    if hasattr(agent, "build_prerequisites"):
        return await agent.build_prerequisites(payload, timeout_sec=timeout_sec)
    return {"node_prerequisites": []}


def _validate_prerequisites(
    prereqs: list[dict[str, Any]],
    nodes: list[dict[str, Any]],
    define_dictionary: dict[str, Any],
) -> None:
    node_ids = {n["node_id"] for n in nodes}
    title_to_id = {normalize_text_key(n["title"]): n["node_id"] for n in nodes}
    define_keys = {normalize_text_key(k) for k in define_dictionary}
    for item in prereqs:
        node_id = _prereq_node_id(item, title_to_id)
        if node_id not in node_ids:
            raise ValueError(f"unknown prerequisite node: {item.get('node_id') or item.get('node_title')}")
        required = normalize_concepts(item.get("required_defines") or [])
        if len(required) > _MAX_REQUIRED_DEFINES:
            raise ValueError(f"{node_id} required_defines exceeds {_MAX_REQUIRED_DEFINES}")
        if not required and not str(item.get("reason") or "").strip():
            raise ValueError(f"{node_id} empty required_defines must include reason")
        unknown = [d for d in required if normalize_text_key(d) not in define_keys]
        if unknown:
            raise ValueError(f"{node_id} required unknown defines: {unknown}")


async def _build_edges_with_cycle_repair(
    agent: Any,
    nodes: list[dict[str, Any]],
    prerequisites: list[dict[str, Any]],
    *,
    diagnostics: list[dict[str, Any]],
    timeout: float,
    repair: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    current = prerequisites
    for attempt in range(repair + 1):
        _attach_external_prerequisites(nodes, current)
        edges = _edges_from_prerequisites(nodes, current)
        edges = _prune_transitive_edges({n["node_id"] for n in nodes}, edges)
        cycle = _find_cycle({n["node_id"] for n in nodes}, edges)
        if not cycle:
            return nodes, edges, diagnostics
        if attempt >= repair:
            raise ValueError(f"Dagger prerequisite cycle remains after repair: {sorted(cycle)}")
        raw = await _agent_repair_prerequisites(
            agent,
            {
                "cycle_edges": sorted(cycle),
                "nodes": [_node_prereq_meta(n) for n in nodes],
                "node_prerequisites": current,
            },
            timeout_sec=timeout,
        )
        current = list(raw.get("node_prerequisites") or [])
        _validate_prerequisites(current, nodes, _define_dictionary(nodes))
    return nodes, [], diagnostics


async def _agent_repair_prerequisites(agent: Any, payload: dict[str, Any], *, timeout_sec: float) -> dict:
    if hasattr(agent, "repair_prerequisites"):
        return await agent.repair_prerequisites(payload, timeout_sec=timeout_sec)
    return {"node_prerequisites": payload.get("node_prerequisites") or []}


def _attach_external_prerequisites(nodes: list[dict[str, Any]], prereqs: list[dict[str, Any]]) -> None:
    by_id = {n["node_id"]: n for n in nodes}
    title_to_id = {normalize_text_key(n["title"]): n["node_id"] for n in nodes}
    for item in prereqs:
        node_id = _prereq_node_id(item, title_to_id)
        if node_id in by_id:
            by_id[node_id]["external_prerequisites"] = normalize_concepts(
                item.get("external_prerequisites") or []
            )


def _edges_from_prerequisites(
    nodes: list[dict[str, Any]], prereqs: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    by_id = {n["node_id"]: n for n in nodes}
    title_to_id = {normalize_text_key(n["title"]): n["node_id"] for n in nodes}
    define_index = _define_index(nodes)
    by_edge: dict[tuple[str, str], dict[str, Any]] = {}

    for item in prereqs:
        to_id = _prereq_node_id(item, title_to_id)
        if to_id not in by_id:
            continue
        for required in normalize_concepts(item.get("required_defines") or []):
            definers = [
                node_id for node_id in define_index.get(normalize_text_key(required), [])
                if node_id != to_id
            ]
            for from_id in definers:
                key = (from_id, to_id)
                edge = by_edge.setdefault(
                    key,
                    {
                        "from_node_id": from_id,
                        "to_node_id": to_id,
                        "relation": "prerequisite",
                        "confidence": 1.0,
                        "required_defines": [],
                    },
                )
                if required not in edge["required_defines"]:
                    edge["required_defines"].append(required)

    return sorted(
        by_edge.values(),
        key=lambda e: (
            by_id[e["from_node_id"]].get("source_order_index", 0),
            by_id[e["to_node_id"]].get("source_order_index", 0),
            e["from_node_id"],
            e["to_node_id"],
        ),
    )


def _prune_transitive_edges(node_ids: set[str], edges: list[dict[str, Any]]) -> list[dict[str, Any]]:
    edge_pairs = {(e["from_node_id"], e["to_node_id"]) for e in edges}
    keep: list[dict[str, Any]] = []
    for edge in edges:
        pair = (edge["from_node_id"], edge["to_node_id"])
        other_pairs = edge_pairs - {pair}
        if _path_exists(edge["from_node_id"], edge["to_node_id"], node_ids, other_pairs):
            continue
        keep.append(edge)
    return keep


def _path_exists(
    start: str, target: str, node_ids: set[str], edge_pairs: set[tuple[str, str]]
) -> bool:
    adj: dict[str, list[str]] = {nid: [] for nid in node_ids}
    for src, dst in edge_pairs:
        adj.setdefault(src, []).append(dst)
    stack = list(adj.get(start, []))
    seen: set[str] = set()
    while stack:
        node = stack.pop()
        if node == target:
            return True
        if node in seen:
            continue
        seen.add(node)
        stack.extend(adj.get(node, []))
    return False


def _define_dictionary(nodes: list[dict[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for node in nodes:
        for define in node.get("defines") or []:
            result.setdefault(define, {"defined_by": []})["defined_by"].append(node["node_id"])
    return result


def _define_index(nodes: list[dict[str, Any]]) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for node in nodes:
        for define in node.get("defines") or []:
            key = normalize_text_key(define)
            if key:
                result.setdefault(key, []).append(node["node_id"])
    return result


def _node_prereq_meta(node: dict[str, Any]) -> dict[str, Any]:
    return {
        "node_id": node["node_id"],
        "title": node["title"],
        "defines": list(node.get("defines") or []),
        "collections": list(node.get("collections") or []),
        "source_order_index": node.get("source_order_index", 0),
    }


def _prereq_node_id(item: dict[str, Any], title_to_id: dict[str, str]) -> str:
    node_id = str(item.get("node_id") or "").strip()
    if node_id:
        return node_id
    return title_to_id.get(normalize_text_key(str(item.get("node_title") or item.get("title") or "")), "")


def break_cycles(node_ids: set[str], edges: list[dict]) -> list[dict]:
    """Drop the lowest-confidence prerequisite edge on each cycle until acyclic.

    Retained for existing tests and utility callers. The main Dagger path now
    repairs cycles through prerequisite selection instead of guessing a deletion.
    """
    prereq = [e for e in edges if e["relation"] == "prerequisite"]
    other = [e for e in edges if e["relation"] != "prerequisite"]

    while True:
        cycle = _find_cycle(node_ids, prereq)
        if not cycle:
            break
        on_cycle = [e for e in prereq if (e["from_node_id"], e["to_node_id"]) in cycle]
        weakest = min(on_cycle, key=lambda e: e.get("confidence", 1.0))
        prereq.remove(weakest)
        logger.info("break_cycles: dropped %s->%s", weakest["from_node_id"], weakest["to_node_id"])

    return prereq + other


def _find_cycle(node_ids: set[str], edges: list[dict]) -> set[tuple[str, str]]:
    """Return the directed-edge set of one cycle, or empty if the graph is acyclic."""
    adj: dict[str, list[str]] = {nid: [] for nid in node_ids}
    for edge in edges:
        adj.setdefault(edge["from_node_id"], []).append(edge["to_node_id"])

    white, gray, black = 0, 1, 2
    color = {nid: white for nid in adj}
    parent: dict[str, str] = {}

    def visit(start: str) -> set[tuple[str, str]] | None:
        stack = [(start, iter(adj[start]))]
        color[start] = gray
        while stack:
            node, it = stack[-1]
            advanced = False
            for nxt in it:
                if color.get(nxt, white) == gray:
                    cycle = {(node, nxt)}
                    cur = node
                    while cur != nxt and cur in parent:
                        cycle.add((parent[cur], cur))
                        cur = parent[cur]
                    return cycle
                if color.get(nxt, white) == white:
                    parent[nxt] = node
                    color[nxt] = gray
                    stack.append((nxt, iter(adj[nxt])))
                    advanced = True
                    break
            if not advanced:
                color[node] = black
                stack.pop()
        return None

    for nid in adj:
        if color[nid] == white:
            found = visit(nid)
            if found:
                return found
    return set()


def _node_from_members(
    member_ids: list[str],
    by_id: dict[str, Any],
    *,
    title: str | None = None,
    defines: Any = None,
) -> dict[str, Any]:
    members = [by_id[mid] for mid in member_ids]
    ordered_ids = sorted(member_ids)
    source_defines = list(defines) if defines is not None else [
        define for m in members for define in _mtu_defines(m)
    ]
    merged_defines = normalize_concepts(source_defines)
    return {
        "node_id": prefixed_id("kn", ordered_ids),
        "title": (title or members[0].title).strip(),
        "member_mtu_ids": ordered_ids,
        "defines": merged_defines,
        # Compatibility for older context surfaces while the public vocabulary
        # moves from keywords to defines.
        "keywords": merged_defines,
        "summary": "",
        "collections": sorted({m.collection for m in members if m.collection}),
        "source_order_index": min(m.source_order_index for m in members),
        "external_prerequisites": [],
    }


def _meta(mtu: Any) -> dict[str, Any]:
    return {
        "mtu_id": mtu.mtu_id,
        "title": mtu.title,
        "defines": _mtu_defines(mtu),
        "summary": getattr(mtu, "summary", ""),
        "unit_kind": getattr(mtu, "unit_kind", "concept"),
        "collection": mtu.collection,
        "source_order_index": mtu.source_order_index,
    }


def _mtu_defines(mtu: Any) -> list[str]:
    return list(getattr(mtu, "defines", None) or getattr(mtu, "keywords", []) or [])


def _set_progress_stage(progress: Any | None, stage: str, **kwargs: Any) -> None:
    if progress is not None and hasattr(progress, "set_stage"):
        try:
            progress.set_stage(stage, **kwargs)
        except Exception:
            return


def _advance_progress_stage(progress: Any | None, stage: str, **kwargs: Any) -> None:
    if progress is not None and hasattr(progress, "advance_stage"):
        try:
            progress.advance_stage(stage, **kwargs)
        except Exception:
            return


def _complete_progress_stage(progress: Any | None, stage: str, message: str) -> None:
    if progress is not None and hasattr(progress, "complete_stage"):
        try:
            progress.complete_stage(stage, message)
        except Exception:
            return
