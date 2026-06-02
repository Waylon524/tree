"""Planner orchestration: scan -> MTUs -> DAG, envelope-persisted.

Single entry point shared by the runtime engine and `tre planner rebuild`
(no dual planner paths). Incremental: unchanged materials reuse cached MTUs.
"""

from __future__ import annotations

import asyncio
import inspect
import re
from pathlib import Path
from typing import Any, Awaitable, Callable, Protocol

from tree.io import paths
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
from tree.planner.svg import write_dag_svg

# A producer turns one material into its MTUs (runs OCR -> clean -> cut + writes
# cleaned Markdown to runtime/source). Supplied by the ingest driver (step 8);
# tests inject a fake. None means "no rebuild needed" must hold.
MtuProducer = Callable[[Path, dict[str, Any]], Awaitable[list[MTU]]]
PreDagHook = Callable[[list[MTU]], Awaitable[None] | None]


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
    pre_dag_hook: PreDagHook | None = None,
    vector_provider: Any | None = None,
    progress: Any | None = None,
) -> dict[str, Any]:
    paths.ensure_workspace_dirs(root)

    manifest_path = paths.material_manifest_path(root)
    previous = read_json(manifest_path) if manifest_path.exists() else None
    manifest = scan_materials(root, previous=previous)
    write_json_atomic(manifest_path, manifest)

    changed = [m for m in manifest["materials"] if m["status"] != "unchanged"]
    _set_stage(
        progress,
        "ocr",
        total=len(changed),
        done=0,
        status="running" if changed else "complete",
        message="Extracting materials" if changed else "No changed materials",
    )
    _set_stage(progress, "clean", total=0, done=0, status="pending" if changed else "complete")
    _set_stage(progress, "cut", total=0, done=0, status="pending" if changed else "complete")

    needs_build = any(m["status"] != "unchanged" for m in manifest["materials"])
    if needs_build and mtu_producer is None:
        raise PlannerError("Materials are new/changed but no mtu_producer was supplied.")

    mtus = await _collect_mtus(root, manifest, producer=mtu_producer, settings=settings)
    _complete_stage_if_zero(progress, "clean", "No chunks to clean")
    _complete_stage_if_zero(progress, "cut", "No chunks to cut")
    mtus_env = envelope(
        schema="tree.mtus",
        data={"mtus": [m.model_dump(mode="json") for m in mtus]},
        inputs=[{"path": str(manifest_path), "hash": artifact_hash(manifest)}],
        algorithm_versions={"archivist": "v1"},
    )
    write_json_atomic(paths.mtus_path(root), mtus_env)

    if pre_dag_hook is not None:
        hook_result = pre_dag_hook(mtus)
        if inspect.isawaitable(hook_result):
            await hook_result
    elif progress is not None:
        _set_stage(
            progress,
            "embed",
            total=len(mtus),
            done=0 if mtus else 0,
            status="complete" if not mtus else "pending",
            message="Embedding skipped; no RAG indexer",
        )

    dag = await build_dag(
        agents.dagger,
        mtus,
        settings=settings,
        vector_provider=vector_provider,
        progress=progress,
    )
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
    dag_svg_path = write_dag_svg(root, dag_env["data"])

    edges = dag["edges"]
    return {
        "materials": manifest,
        "mtu_count": len(mtus),
        "node_count": len(dag["nodes"]),
        "hard_edge_count": sum(1 for e in edges if e["relation"] == "prerequisite"),
        "soft_order_edge_count": sum(1 for e in edges if e["relation"] == "order"),
        "dag_svg_path": str(dag_svg_path),
    }


async def _collect_mtus(
    root: Path, manifest: dict[str, Any], *, producer: MtuProducer | None, settings: Any
) -> list[MTU]:
    cache = _load_mtu_cache(root)
    collected: list[MTU] = []
    produce_tasks: list[asyncio.Task[list[MTU]]] = []
    concurrency = max(1, int(getattr(settings, "source_ingest_concurrency", 1)))
    semaphore = asyncio.Semaphore(concurrency)

    async def produce(material: dict[str, Any]) -> list[MTU]:
        if producer is None:
            return []
        async with semaphore:
            return await producer(root, material)

    for material in manifest["materials"]:
        key = (material["collection"], material["source_file"])
        if material["status"] == "unchanged" and key in cache:
            collected.extend(cache[key])
        elif producer is not None:
            produce_tasks.append(asyncio.create_task(produce(material)))

    for task in produce_tasks:
        collected.extend(await task)

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
        if original_source_file := _chunk_original_source_file(mtu.source_file):
            cache.setdefault((mtu.collection, original_source_file), []).append(mtu)
    return cache


def _chunk_original_source_file(source_file: str) -> str:
    match = re.match(r"^(.+)\.part-\d{3}$", source_file)
    return match.group(1) if match else ""


# --- artifact loaders (used by engine / cli) --------------------------------

def load_dag(root: Path) -> dict[str, Any]:
    data = read_envelope_data(paths.knowledge_dag_path(root))
    return {"nodes": data.get("nodes", []), "edges": data.get("edges", []), "roots": data.get("roots", [])}




def load_nodes(root: Path) -> list[dict[str, Any]]:
    return read_envelope_data(paths.knowledge_nodes_path(root)).get("knowledge_nodes", [])


def _set_stage(progress: Any | None, stage: str, **kwargs: Any) -> None:
    if progress is not None and hasattr(progress, "set_stage"):
        try:
            progress.set_stage(stage, **kwargs)
        except Exception:
            return


def _complete_stage_if_zero(progress: Any | None, stage: str, message: str) -> None:
    if progress is None or not hasattr(progress, "load") or not hasattr(progress, "complete_stage"):
        return
    try:
        data = progress.load().get("stages", {}).get(stage, {})
        if int(data.get("total") or 0) == 0:
            progress.complete_stage(stage, message)
    except Exception:
        return
