"""Source ingest + embedding orchestration.

Incremental through planner manifests: changed materials are re-extracted into
cleaned Markdown, cut into MTUs, folded into the Dagger DAG, then each source MTU
is embedded and the cleaned Markdown is removed.

See docs/REBUILD-DESIGN.md §4 ⑤, docs/LEGACY-DESIGN.md §4.5.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Any

from tree.ingest.pipeline import extract_text
from tree.io import file_ops, paths
from tree.planner.mtu import mtu_text
from tree.planner.models import MTU
from tree.planner.pipeline import load_nodes, rebuild_planner

LONG_DOCUMENT_CHAR_THRESHOLD = 100_000
CLEAN_CHUNK_MIN_CHARS = 70_000
CLEAN_CHUNK_MAX_CHARS = 100_000


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
    raw = remove_ocr_image_html(await asyncio.to_thread(extract_text, material_path))
    ocr_path = persist_ocr_markdown(root, material["collection"], material["source_file"], raw)
    _record_ocr_checkpoint(engine, root, ocr_path, raw)
    checkpoint_raw = ocr_path.read_text(encoding="utf-8")
    if not checkpoint_raw.strip():
        return []

    mtus: list[MTU] = []
    raw_chunks = split_raw_markdown_for_cleaning(checkpoint_raw)
    for index, raw_chunk in enumerate(raw_chunks, start=1):
        chunk_source_file = chunk_source_file_name(
            material["source_file"], index=index, total=len(raw_chunks)
        )
        mtus.extend(
            await _clean_and_cut_chunk(
                engine,
                root,
                raw_chunk,
                collection=material["collection"],
                source_file=chunk_source_file,
                order_offset=len(mtus),
            )
        )
    return mtus


def source_markdown_path(root: Path, collection: str, source_file: str) -> Path:
    """Path for a cleaned intermediate Markdown file."""
    return paths.source_markdown_root(root) / collection / f"{source_file}.md"


async def _clean_and_cut_chunk(
    engine: object,
    root: Path,
    raw_chunk: str,
    *,
    collection: str,
    source_file: str,
    order_offset: int,
) -> list[MTU]:
    archivist = engine.archivist
    cleaned = await archivist.clean(
        raw_chunk,
        timeout_sec=getattr(engine.settings, "archivist_mtu_cut_timeout_sec", None),
    )
    if not cleaned.strip():
        return []

    source_path = source_markdown_path(root, collection, source_file)
    file_ops.write_text(source_path, cleaned)
    return await archivist.cut_mtus(
        cleaned,
        collection=collection,
        source_file=source_file,
        order_offset=order_offset,
        timeout_sec=getattr(engine.settings, "archivist_mtu_cut_timeout_sec", None),
        repair_attempts=getattr(engine.settings, "archivist_mtu_repair_attempts", 1),
    )


def split_raw_markdown_for_cleaning(raw_markdown: str) -> list[str]:
    """Split raw OCR Markdown into cleanable chunks using heading boundaries."""
    if len(raw_markdown) <= LONG_DOCUMENT_CHAR_THRESHOLD:
        return [raw_markdown]

    chunks: list[str] = []
    start = 0
    while len(raw_markdown) - start > CLEAN_CHUNK_MAX_CHARS:
        cut = _find_heading_cut(raw_markdown, start)
        chunks.append(raw_markdown[start:cut])
        start = cut
    if start < len(raw_markdown):
        chunks.append(raw_markdown[start:])
    return [chunk for chunk in chunks if chunk]


def remove_ocr_image_html(raw_markdown: str) -> str:
    """Remove OCR-emitted HTML image blocks before chunking or LLM cleanup."""
    text = re.sub(
        r'<div\b[^>]*>\s*<img\b[^>]*>\s*</div>',
        "",
        raw_markdown,
        flags=re.IGNORECASE | re.DOTALL,
    )
    text = re.sub(r"<img\b[^>]*>", "", text, flags=re.IGNORECASE | re.DOTALL)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def chunk_source_file_name(source_file: str, *, index: int, total: int) -> str:
    """Return the source_file used by MTUs for a cleaned chunk."""
    if total == 1:
        return source_file
    return f"{source_file}.part-{index:03d}"


def _find_heading_cut(raw_markdown: str, start: int) -> int:
    window_start = min(start + CLEAN_CHUNK_MIN_CHARS, len(raw_markdown))
    window_end = min(start + CLEAN_CHUNK_MAX_CHARS, len(raw_markdown))
    for level in (1, 2, 3):
        match = _find_heading_in_region(raw_markdown, window_start, window_end, level)
        if match is not None:
            return match
    return window_start


def _find_heading_in_region(raw_markdown: str, start: int, end: int, level: int) -> int | None:
    hashes = "#" * level
    pattern = rf"(?m)^{re.escape(hashes)}(?!#)\s+\S"
    for match in re.finditer(pattern, raw_markdown):
        if start <= match.start() < end:
            return match.start()
        if match.start() >= end:
            break
    return None


def persist_ocr_markdown(root: Path, collection: str, source_file: str, raw_markdown: str) -> Path:
    """Persist raw OCR Markdown before Archivist cleanup for inspection and retries."""
    path = paths.ocr_markdown_path(root, collection, source_file)
    file_ops.write_text(path, raw_markdown)
    return path


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


def _record_ocr_checkpoint(engine: object, root: Path, path: Path, raw_markdown: str) -> None:
    progress = getattr(engine, "progress", None)
    if progress is None or not hasattr(progress, "update"):
        return
    try:
        progress.update(
            {
                "phase": "source_ingest",
                "source_ingest": {
                    "checkpoint": "ocr_markdown",
                    "path": file_ops.relative_to(paths.runtime_root(root), path),
                    "chars": len(raw_markdown),
                    "lines": len(raw_markdown.splitlines()),
                },
            }
        )
    except Exception:
        # Progress is diagnostic-only; OCR checkpoint persistence is the durable contract.
        return


def _root(engine: object) -> Path:
    return Path(engine.settings.project_root)
