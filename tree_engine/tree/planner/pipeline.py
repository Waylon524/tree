"""Planner orchestration: scan -> MTUs -> DAG -> branches, envelope-persisted.

Single entry point shared by the runtime engine and `tre planner rebuild`
(no dual planner paths). Incremental: unchanged materials reuse cached MTUs.

See docs/REBUILD-DESIGN.md §5.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Awaitable, Callable, Protocol

from tree.io import paths
from tree.planner.branches import build_branches
from tree.planner.dag import build_dag
from tree.planner.manifest import scan_materials
from tree.planner.models import MTU
from tree.planner.store import (
    artifact_hash,
    envelope,
    read_envelope_data,
    read_json,
    write_json_atomic,
)

# A producer turns one material into its MTUs (runs OCR -> clean -> cut + writes
# cleaned Markdown to runtime/source). Supplied by the ingest driver (step 8);
# tests inject a fake. None means "no rebuild needed" must hold.
MtuProducer = Callable[[Path, dict[str, Any]], Awaitable[list[MTU]]]


class PlannerError(RuntimeError):
    pass


class HasDagger(Protocol):
    dagger: Any


async def rebuild_planner(
    root: Path,
    *,
    settings: Any,
    agents: HasDagger,
    mtu_producer: MtuProducer | None = None,
) -> dict[str, Any]:
    paths.ensure_workspace_dirs(root)

    manifest_path = paths.material_manifest_path(root)
    previous = read_json(manifest_path) if manifest_path.exists() else None
    manifest = scan_materials(root, previous=previous)
    write_json_atomic(manifest_path, manifest)

    needs_build = any(m["status"] != "unchanged" for m in manifest["materials"])
    if needs_build and mtu_producer is None:
        raise PlannerError("Materials are new/changed but no mtu_producer was supplied.")

    mtus = await _collect_mtus(root, manifest, producer=mtu_producer)
    mtus_env = envelope(
        schema="tree.mtus",
        data={"mtus": [m.model_dump(mode="json") for m in mtus]},
        inputs=[{"path": str(manifest_path), "hash": artifact_hash(manifest)}],
        algorithm_versions={"archivist": "v1"},
    )
    write_json_atomic(paths.mtus_path(root), mtus_env)

    dag = await build_dag(agents.dagger, mtus, settings=settings)
    nodes_env = envelope(
        schema="tree.knowledge-nodes",
        data={"knowledge_nodes": dag["nodes"]},
        inputs=[{"path": str(paths.mtus_path(root)), "hash": artifact_hash(mtus_env)}],
        diagnostics=dag.get("diagnostics", []),
        algorithm_versions={"dagger": "v1"},
    )
    write_json_atomic(paths.knowledge_nodes_path(root), nodes_env)

    dag_env = envelope(
        schema="tree.knowledge-dag",
        data={"nodes": dag["nodes"], "edges": dag["edges"], "roots": dag["roots"]},
        inputs=[{"path": str(paths.knowledge_nodes_path(root)), "hash": artifact_hash(nodes_env)}],
        diagnostics=dag.get("diagnostics", []),
        algorithm_versions={"dagger": "v1"},
    )
    write_json_atomic(paths.knowledge_dag_path(root), dag_env)

    branches = build_branches({"nodes": dag["nodes"], "edges": dag["edges"]})
    branches_env = envelope(
        schema="tree.knowledge-branches",
        data=branches,
        inputs=[{"path": str(paths.knowledge_dag_path(root)), "hash": artifact_hash(dag_env)}],
        diagnostics=branches.get("diagnostics", []),
        algorithm_versions={"branch_build": "v1"},
    )
    write_json_atomic(paths.knowledge_branches_path(root), branches_env)

    edges = dag["edges"]
    return {
        "materials": manifest,
        "mtu_count": len(mtus),
        "node_count": len(dag["nodes"]),
        "hard_edge_count": sum(1 for e in edges if e["relation"] == "prerequisite"),
        "soft_order_edge_count": sum(1 for e in edges if e["relation"] == "order"),
        "branch_count": len(branches["branches"]),
    }


async def _collect_mtus(
    root: Path, manifest: dict[str, Any], *, producer: MtuProducer | None
) -> list[MTU]:
    cache = _load_mtu_cache(root)
    collected: list[MTU] = []
    for material in manifest["materials"]:
        key = (material["collection"], material["source_file"])
        if material["status"] == "unchanged" and key in cache:
            collected.extend(cache[key])
        elif producer is not None:
            collected.extend(await producer(root, material))

    # Deterministic global ordering used for source-order edges.
    collected.sort(key=lambda m: (m.collection, m.source_file, m.line_range[0]))
    for index, mtu in enumerate(collected):
        mtu.source_order_index = index
    return collected


def _load_mtu_cache(root: Path) -> dict[tuple[str, str], list[MTU]]:
    data = read_envelope_data(paths.mtus_path(root))
    cache: dict[tuple[str, str], list[MTU]] = {}
    for raw in data.get("mtus", []):
        mtu = MTU.model_validate(raw)
        cache.setdefault((mtu.collection, mtu.source_file), []).append(mtu)
    return cache


# --- artifact loaders (used by engine / cli) --------------------------------

def load_dag(root: Path) -> dict[str, Any]:
    data = read_envelope_data(paths.knowledge_dag_path(root))
    return {"nodes": data.get("nodes", []), "edges": data.get("edges", []), "roots": data.get("roots", [])}


def load_branches(root: Path) -> list[dict[str, Any]]:
    return read_envelope_data(paths.knowledge_branches_path(root)).get("branches", [])


def load_nodes(root: Path) -> list[dict[str, Any]]:
    return read_envelope_data(paths.knowledge_nodes_path(root)).get("knowledge_nodes", [])
