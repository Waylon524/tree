"""Source ingest + embedding orchestration.

Incremental through planner manifests: changed materials are re-extracted into
cleaned Markdown, cut into MTUs, folded into the Dagger DAG, then each source MTU
is embedded and the cleaned Markdown is removed.

See docs/REBUILD-DESIGN.md §4 ⑤, docs/LEGACY-DESIGN.md §4.5.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from tree.ingest.pipeline import extract_text
from tree.io import file_ops, paths
from tree.planner.mtu import mtu_text
from tree.planner.models import MTU
from tree.planner.pipeline import load_nodes, rebuild_planner


async def prepare_sources(engine: object) -> dict[str, Any]:
    """Build planner artifacts from materials and ensure source MTUs are indexed."""
    root = _root(engine)
    paths.ensure_workspace_dirs(root)

    summary = await rebuild_planner(
        root,
        settings=engine.settings,
        agents=engine.agents,
        mtu_producer=lambda root, material: _produce_mtus(engine, root, material),
    )
    await ensure_all_embedded(engine)
    return summary


async def ensure_all_embedded(engine: object) -> int:
    """Index every MTU from the latest planner output, then delete intermediates."""
    root = _root(engine)
    indexer = getattr(engine, "rag_indexer", None)
    if indexer is None:
        raise RuntimeError(
            "RAG indexer is unavailable. Install RAG dependencies and start the embedding "
            "service before running TREE."
        )

    mtus = _load_mtus(root)
    mtu_to_node = _mtu_to_node(root)
    indexed = 0
    for mtu in mtus:
        if hasattr(indexer, "is_mtu_indexed") and indexer.is_mtu_indexed(mtu.mtu_id):
            continue
        source_path = source_markdown_path(root, mtu.collection, mtu.source_file)
        if not source_path.exists():
            raise RuntimeError(f"Missing cleaned source Markdown for {mtu.mtu_id}: {source_path}")
        text = mtu_text(source_path.read_text(encoding="utf-8"), mtu.line_range)
        indexed += await asyncio.to_thread(
            indexer.index_mtu,
            mtu,
            text,
            node_id=mtu_to_node.get(mtu.mtu_id, ""),
        )

    _delete_source_markdown(root)
    return indexed


async def _produce_mtus(engine: object, root: Path, material: dict[str, Any]) -> list[MTU]:
    """Extract one material, clean it, persist source Markdown, and cut MTUs."""
    material_path = paths.materials_root(root) / material["path"]
    raw = await asyncio.to_thread(extract_text, material_path)
    if not raw.strip():
        return []

    archivist = engine.archivist
    cleaned = await archivist.clean(
        raw,
        timeout_sec=getattr(engine.settings, "archivist_mtu_cut_timeout_sec", None),
    )
    if not cleaned.strip():
        return []

    source_path = source_markdown_path(root, material["collection"], material["source_file"])
    file_ops.write_text(source_path, cleaned)
    return await archivist.cut_mtus(
        cleaned,
        collection=material["collection"],
        source_file=material["source_file"],
        timeout_sec=getattr(engine.settings, "archivist_mtu_cut_timeout_sec", None),
        repair_attempts=getattr(engine.settings, "archivist_mtu_repair_attempts", 1),
    )


def source_markdown_path(root: Path, collection: str, source_file: str) -> Path:
    """Path for a cleaned intermediate Markdown file."""
    return paths.source_markdown_root(root) / collection / f"{source_file}.md"


def _load_mtus(root: Path) -> list[MTU]:
    from tree.planner.store import read_envelope_data

    return [MTU.model_validate(raw) for raw in read_envelope_data(paths.mtus_path(root)).get("mtus", [])]


def _mtu_to_node(root: Path) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for node in load_nodes(root):
        node_id = node.get("node_id", "")
        for mtu_id in node.get("member_mtu_ids", []) or []:
            if node_id:
                mapping[mtu_id] = node_id
    return mapping


def _delete_source_markdown(root: Path) -> None:
    source_root = paths.source_markdown_root(root)
    if not source_root.exists():
        return
    for path in source_root.rglob("*.md"):
        path.unlink()


def _root(engine: object) -> Path:
    return Path(engine.settings.project_root)
