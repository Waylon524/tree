"""Planner orchestration: scan -> MTUs -> DAG, envelope-persisted.

Single entry point shared by the runtime engine and `tre planner rebuild`
(no dual planner paths). Incremental: unchanged materials reuse cached MTUs.
"""

from __future__ import annotations

import asyncio
import inspect
from pathlib import Path
from typing import Any, Awaitable, Callable, Protocol

from tree.agents.prompts import get_prompt, prompt_hash
from tree.io import paths
from tree.planner.dag import build_dag
from tree.planner.manifest import manifest_generation_id, scan_materials
from tree.planner.models import MTU
from tree.planner.store import (
    artifact_hash,
    envelope,
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


ARCHIVIST_ALGORITHM_VERSION = "v3-strict-agent-schema"
DAGGER_ALGORITHM_VERSION = "v3-explicit-prerequisites"


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
    producer_signature = planner_producer_signature(settings)
    generation_id = planner_generation_id(manifest, settings)
    manifest["generation_id"] = generation_id
    if progress is not None and hasattr(progress, "set_generation_id"):
        progress.set_generation_id(generation_id)
    for material in manifest["materials"]:
        material["producer_signature"] = producer_signature
        material["ocr_signature"] = planner_ocr_signature(settings)

    cache = _load_mtu_cache(root)
    completed_materials = sum(
        1
        for material in manifest["materials"]
        if (cached := cache.get(str(material.get("source_id") or material["path"]))) is not None
        and cached["fingerprint"] == material.get("fingerprint", "")
        and cached["producer_signature"] == material.get("producer_signature", "")
    )
    total_materials = len(manifest["materials"])
    _set_stage(
        progress,
        "ocr",
        total=total_materials,
        done=completed_materials,
        status="running" if completed_materials < total_materials else "complete",
        message="Extracting materials" if completed_materials < total_materials else "Extraction complete",
    )
    # Clean/Cut totals = number of changed materials (known upfront), advanced
    # once per material in the ingest driver. Stable, monotonic counters even
    # though OCR/Clean/Cut overlap across concurrently-processed materials.
    _set_stage(progress, "clean", total=total_materials, done=completed_materials,
               status="running" if completed_materials < total_materials else "complete")
    _set_stage(progress, "cut", total=total_materials, done=completed_materials,
               status="running" if completed_materials < total_materials else "complete")

    mtus = await _collect_mtus(root, manifest, producer=mtu_producer, settings=settings)
    # All changed materials are ingested once _collect_mtus returns; clamp the
    # ingest stages to complete regardless of per-material advance timing.
    _complete_stage(progress, "clean", "Cleaning complete")
    _complete_stage(progress, "cut", "Cutting complete")
    mtus_env = envelope(
        schema="tree.mtus",
        data={"mtus": [m.model_dump(mode="json") for m in mtus]},
        inputs=[{"path": str(manifest_path), "hash": artifact_hash(manifest)}],
        algorithm_versions={"archivist": ARCHIVIST_ALGORITHM_VERSION},
        generation_id=generation_id,
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
    _validate_dagger_fallback_rate(dag, mtus, settings)
    nodes_env = envelope(
        schema="tree.knowledge-nodes",
        data={"knowledge_nodes": dag["nodes"]},
        inputs=[{"path": str(paths.mtus_path(root)), "hash": artifact_hash(mtus_env)}],
        diagnostics=dag.get("diagnostics", []),
        algorithm_versions={"dagger": DAGGER_ALGORITHM_VERSION},
        generation_id=generation_id,
    )
    write_json_atomic(paths.knowledge_nodes_path(root), nodes_env)

    dag_env = envelope(
        schema="tree.knowledge-dag",
        data={"nodes": dag["nodes"], "edges": dag["edges"], "roots": dag["roots"]},
        inputs=[{"path": str(paths.knowledge_nodes_path(root)), "hash": artifact_hash(nodes_env)}],
        diagnostics=dag.get("diagnostics", []),
        algorithm_versions={"dagger": DAGGER_ALGORITHM_VERSION},
        generation_id=generation_id,
    )
    write_json_atomic(paths.knowledge_dag_path(root), dag_env)
    dag_svg_path = write_dag_svg(root, dag_env["data"])

    # The manifest is the commit pointer for a planner generation. Writing it
    # last means an interrupted rebuild can never make partial new artifacts
    # look compatible with the last successful material snapshot.
    write_json_atomic(manifest_path, manifest)

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
            mtus = await producer(root, material)
        # Persist this material's MTUs as soon as it finishes, so a crash mid-run
        # resumes at material granularity (no re-OCR / re-cut of done materials).
        _save_material_cache(root, material, mtus)
        return mtus

    for material in manifest["materials"]:
        source_id = str(material.get("source_id") or material["path"])
        cached = cache.get(source_id)
        if (
            cached is not None
            and cached["fingerprint"] == material.get("fingerprint", "")
            and cached["producer_signature"] == material.get("producer_signature", "")
        ):
            collected.extend(cached["mtus"])
        elif producer is not None:
            produce_tasks.append(asyncio.create_task(produce(material)))
        else:
            raise PlannerError(
                f"Material {source_id} needs OCR/Archivist rebuild but no mtu_producer was supplied."
            )

    try:
        for task in produce_tasks:
            collected.extend(await task)
    except BaseException:
        for task in produce_tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*produce_tasks, return_exceptions=True)
        raise

    # Deterministic global ordering used for source-order edges.
    collected.sort(key=lambda m: (m.collection, m.source_file, m.line_range[0]))
    for index, mtu in enumerate(collected):
        mtu.source_order_index = index
    return collected


def _material_cache_root(root: Path) -> Path:
    return paths.planner_root(root) / "mtu-cache"


def _material_cache_path(root: Path, source_id: str) -> Path:
    return _material_cache_root(root) / f"{source_id}.json"


def _save_material_cache(root: Path, material: dict[str, Any], mtus: list[MTU]) -> None:
    """Write one material's MTUs to its own cache file (crash-safe, incremental)."""
    source_id = str(material.get("source_id") or material["path"])
    path = _material_cache_path(root, source_id)
    write_json_atomic(
        path,
        {
            "fingerprint": material.get("fingerprint", ""),
            "source_id": source_id,
            "producer_signature": material.get("producer_signature", ""),
            "mtus": [m.model_dump(mode="json") for m in mtus],
        },
    )


def _load_mtu_cache(root: Path) -> dict[str, dict[str, Any]]:
    """Reuse cache for unchanged materials.

    Prefer the per-material cache files (written incrementally, so they survive a
    crash mid-run); fall back to the assembled mtus.json for workspaces created
    before per-material caches existed.
    """
    cache: dict[str, dict[str, Any]] = {}
    cache_root = _material_cache_root(root)
    cache_files = sorted(cache_root.rglob("*.json")) if cache_root.exists() else []
    if cache_files:
        for path in cache_files:
            try:
                data = read_json(path)
            except (OSError, ValueError):
                continue
            mtus = [MTU.model_validate(raw) for raw in data.get("mtus", [])]
            source_id = str(data.get("source_id") or _legacy_cache_source_id(path, cache_root, mtus))
            if source_id:
                cache[source_id] = {
                    "fingerprint": str(data.get("fingerprint", "")),
                    "producer_signature": str(data.get("producer_signature", "")),
                    "mtus": mtus,
                }
        return cache

    # Legacy assembled artifacts lack a trustworthy per-material fingerprint,
    # so they must not be reused as proof that current source content matches.
    return cache


def _legacy_cache_source_id(path: Path, cache_root: Path, mtus: list[MTU]) -> str:
    try:
        rel = str(path.relative_to(cache_root))
        if rel.endswith(".json"):
            return rel[:-5]
    except ValueError:
        pass
    if mtus:
        mtu = mtus[0]
        return mtu.source_id or f"{mtu.collection}/{mtu.source_file}"
    return ""


def planner_producer_signature(settings: Any) -> str:
    archivist = getattr(settings, "archivist", None)
    return artifact_hash(
        {
            "algorithm": ARCHIVIST_ALGORITHM_VERSION,
            "ocr": planner_ocr_signature(settings),
            "archivist_model": getattr(archivist, "model", ""),
            "archivist_base_url": getattr(archivist, "base_url", ""),
            "archivist_repair_attempts": getattr(
                settings, "archivist_mtu_repair_attempts", 2
            ),
            "prompts": _effective_prompt_hashes(
                settings, ("archivist_clean", "archivist_mtu")
            ),
            "long_document_threshold": 100_000,
        }
    )


def planner_ocr_signature(settings: Any) -> str:
    return artifact_hash(
        {
            "model": getattr(settings, "paddleocr_model", "PaddleOCR-VL-1.6"),
            "endpoint": getattr(settings, "paddleocr_api_url", ""),
            "orientation": True,
            "unwarping": True,
            "chart_recognition": True,
            "pdf_max_pages_per_job": getattr(
                settings, "source_ocr_pdf_max_pages_per_job", 99
            ),
        }
    )


def planner_generation_id(manifest: dict[str, Any], settings: Any) -> str:
    dagger = getattr(settings, "dagger", None)
    return "gen:" + artifact_hash(
        {
            "materials": manifest_generation_id(manifest),
            "producer": planner_producer_signature(settings),
            "dagger_algorithm": DAGGER_ALGORITHM_VERSION,
            "dagger_model": getattr(dagger, "model", ""),
            "dagger_base_url": getattr(dagger, "base_url", ""),
            "dagger_repair_attempts": getattr(settings, "dagger_repair_attempts", 2),
            "dagger_max_nodes_per_call": getattr(
                settings, "dagger_max_nodes_per_call", 400
            ),
            "dagger_cluster_auto_accept_singleton": getattr(
                settings, "dagger_cluster_auto_accept_singleton", True
            ),
            "prompts": _effective_prompt_hashes(
                settings, ("dagger", "dagger_prerequisites")
            ),
            "embed_cluster_enabled": getattr(settings, "dagger_embed_cluster_enabled", True),
            "cluster_similarity_threshold": getattr(
                settings, "dagger_cluster_similarity_threshold", 0.80
            ),
            "cluster_top_k": getattr(settings, "dagger_cluster_top_k", 5),
            "cluster_max_size": getattr(settings, "dagger_cluster_max_size", 8),
            "max_unassigned_ratio": getattr(settings, "dagger_max_unassigned_ratio", 0.10),
        }
    )[:24]


def _effective_prompt_hashes(settings: Any, names: tuple[str, ...]) -> dict[str, str]:
    root_value = getattr(settings, "project_root", None)
    project_root = Path(root_value) if root_value else None
    return {
        name: prompt_hash(get_prompt(name, project_root=project_root))
        for name in names
    }


def _validate_dagger_fallback_rate(dag: dict[str, Any], mtus: list[MTU], settings: Any) -> None:
    if not mtus:
        return
    unassigned = {
        str(item.get("mtu_id") or "")
        for item in dag.get("diagnostics", [])
        if item.get("reason_code") == "mtu_unassigned"
    }
    ratio = len(unassigned) / len(mtus)
    maximum = max(0.0, float(getattr(settings, "dagger_max_unassigned_ratio", 0.10)))
    if ratio > maximum:
        dag.setdefault("diagnostics", []).append(
            {
                "reason_code": "high_unassigned_ratio",
                "severity": "warning",
                "unassigned_count": len(unassigned),
                "mtu_count": len(mtus),
                "ratio": ratio,
                "configured_threshold": maximum,
                "message": (
                    f"{len(unassigned)}/{len(mtus)} MTUs used safe singleton fallback "
                    f"({ratio:.1%}); review clustering quality."
                ),
            }
        )


# --- artifact loaders (used by engine / cli) --------------------------------

def load_dag(root: Path) -> dict[str, Any]:
    data = _read_current_envelope_data(root, paths.knowledge_dag_path(root))
    return {"nodes": data.get("nodes", []), "edges": data.get("edges", []), "roots": data.get("roots", [])}




def load_nodes(root: Path) -> list[dict[str, Any]]:
    nodes = _read_current_envelope_data(root, paths.knowledge_nodes_path(root)).get(
        "knowledge_nodes", []
    )
    return nodes if isinstance(nodes, list) else []


def _read_current_envelope_data(root: Path, artifact_path: Path) -> dict[str, Any]:
    """Hide artifacts from an interrupted, not-yet-committed generation."""
    if not artifact_path.exists():
        return {}
    loaded = read_json(artifact_path)
    manifest_path = paths.material_manifest_path(root)
    if manifest_path.exists():
        manifest = read_json(manifest_path)
        committed_generation = str(manifest.get("generation_id") or "")
        artifact_generation = str(loaded.get("generation_id") or "")
        if committed_generation and artifact_generation != committed_generation:
            return {}
    data = loaded.get("data") if isinstance(loaded, dict) else None
    if isinstance(data, dict):
        return data
    return loaded if isinstance(loaded, dict) else {}


def _set_stage(progress: Any | None, stage: str, **kwargs: Any) -> None:
    if progress is not None and hasattr(progress, "set_stage"):
        try:
            progress.set_stage(stage, **kwargs)
        except Exception:
            return


def _complete_stage(progress: Any | None, stage: str, message: str) -> None:
    if progress is not None and hasattr(progress, "complete_stage"):
        try:
            progress.complete_stage(stage, message)
        except Exception:
            return
