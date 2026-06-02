"""Source ingest + embedding orchestration.

Incremental through planner manifests: changed materials are re-extracted into
cleaned Markdown, cut into MTUs, folded into the Dagger DAG, then each source MTU
is embedded and the cleaned Markdown is removed.
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
MAX_ARCHIVIST_CHUNK_CONCURRENCY = 5


async def prepare_sources(engine: object) -> dict[str, Any]:
    """Build planner artifacts from materials and ensure source MTUs are indexed."""
    root = _root(engine)
    paths.ensure_workspace_dirs(root)
    indexer = getattr(engine, "rag_indexer", None)
    _install_ocr_progress_callback(engine)

    async def pre_dag_index(mtus: list[MTU]) -> None:
        await _ensure_mtus_embedded(engine, mtus, backfill_node_ids=False)

    try:
        summary = await rebuild_planner(
            root,
            settings=engine.settings,
            agents=engine.agents,
            mtu_producer=lambda root, material: _produce_mtus(engine, root, material),
            pre_dag_hook=pre_dag_index if indexer is not None else None,
            vector_provider=indexer if indexer is not None else None,
            progress=getattr(engine, "progress", None),
        )
        await ensure_all_embedded(engine)
        return summary
    finally:
        _install_ocr_progress_callback(None)


async def ensure_all_embedded(engine: object) -> int:
    """Index every MTU from the latest planner output, then delete intermediates."""
    root = _root(engine)
    mtus = _load_mtus(root)
    indexed = await _ensure_mtus_embedded(engine, mtus, backfill_node_ids=True)
    _delete_source_markdown(root)
    return indexed


async def _ensure_mtus_embedded(
    engine: object,
    mtus: list[MTU],
    *,
    backfill_node_ids: bool,
) -> int:
    root = _root(engine)
    indexer = getattr(engine, "rag_indexer", None)
    if indexer is None:
        raise RuntimeError(
            "RAG indexer is unavailable. Install RAG dependencies and start the embedding "
            "service before running TREE."
        )

    mtu_to_node = _mtu_to_node(root) if backfill_node_ids else {}
    _set_stage(
        engine,
        "embed",
        total=len(mtus),
        done=0,
        status="running" if mtus else "complete",
        message="Embedding source MTUs" if not backfill_node_ids else "Backfilling MTU node ids",
    )
    indexed = 0
    for mtu in mtus:
        if hasattr(indexer, "is_mtu_indexed") and indexer.is_mtu_indexed(mtu.mtu_id):
            _advance_stage(engine, "embed", message=f"Already indexed {mtu.title}")
            continue
        source_path = source_markdown_path(root, mtu.collection, mtu.source_file)
        if not source_path.exists():
            raise RuntimeError(f"Missing cleaned source Markdown for {mtu.mtu_id}: {source_path}")
        text = mtu_text(source_path.read_text(encoding="utf-8"), mtu.line_range)
        _set_stage(engine, "embed", status="running", active=mtu.title, message=f"Embedding {mtu.title}")
        indexed += await asyncio.to_thread(
            indexer.index_mtu,
            mtu,
            text,
            node_id=mtu_to_node.get(mtu.mtu_id, ""),
        )
        _advance_stage(engine, "embed", message=f"Embedded {mtu.title}")

    if backfill_node_ids and mtu_to_node and hasattr(indexer, "update_mtu_node_ids"):
        indexer.update_mtu_node_ids(mtu_to_node)
    _complete_stage(engine, "embed", "Embedding complete")
    return indexed


async def _produce_mtus(engine: object, root: Path, material: dict[str, Any]) -> list[MTU]:
    """Extract one material, clean it, persist source Markdown, and cut MTUs."""
    material_path = paths.materials_root(root) / material["path"]
    material_label = str(material.get("path") or material_path.name)
    _set_stage(engine, "ocr", status="running", active=material_label, message=f"Extracting {material_label}")
    raw = remove_ocr_image_html(await asyncio.to_thread(extract_text, material_path))
    _advance_stage(engine, "ocr", message=f"Extracted {material_label}", active=[])
    ocr_path = persist_ocr_markdown(root, material["collection"], material["source_file"], raw)
    _record_ocr_checkpoint(engine, root, ocr_path, raw)
    checkpoint_raw = ocr_path.read_text(encoding="utf-8")
    if not checkpoint_raw.strip():
        return []

    raw_chunks = split_raw_markdown_for_cleaning(checkpoint_raw)
    _add_stage_total(
        engine,
        "clean",
        len(raw_chunks),
        status="running" if raw_chunks else "complete",
        message=f"Cleaning {material_label}",
    )
    _add_stage_total(
        engine,
        "cut",
        len(raw_chunks),
        status="running" if raw_chunks else "complete",
        message=f"Cutting {material_label}",
    )
    return await _clean_and_cut_chunks(engine, root, raw_chunks, material=material)


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
    _set_stage(engine, "clean", status="running", active=source_file, message=f"Cleaning {source_file}")
    cleaned = await archivist.clean(
        raw_chunk,
        timeout_sec=getattr(engine.settings, "archivist_mtu_cut_timeout_sec", None),
    )
    _advance_stage(engine, "clean", message=f"Cleaned {source_file}", active=[])
    if not cleaned.strip():
        _advance_stage(engine, "cut", message=f"Skipped empty cleaned chunk {source_file}", active=[])
        return []

    source_path = source_markdown_path(root, collection, source_file)
    file_ops.write_text(source_path, cleaned)
    _set_stage(engine, "cut", status="running", active=source_file, message=f"Cutting {source_file}")
    mtus = await archivist.cut_mtus(
        cleaned,
        collection=collection,
        source_file=source_file,
        order_offset=order_offset,
        timeout_sec=getattr(engine.settings, "archivist_mtu_cut_timeout_sec", None),
        repair_attempts=getattr(engine.settings, "archivist_mtu_repair_attempts", 1),
    )
    _advance_stage(engine, "cut", message=f"Cut {source_file}", active=[])
    return mtus


async def _clean_and_cut_chunks(
    engine: object,
    root: Path,
    raw_chunks: list[str],
    *,
    material: dict[str, Any],
) -> list[MTU]:
    total = len(raw_chunks)
    results: list[list[MTU] | None] = [None] * total
    pending = list(range(total))
    running: dict[asyncio.Task[list[MTU]], int] = {}
    retry_counts: dict[int, int] = {}
    max_retries = max(0, int(getattr(engine.settings, "max_retries", 3)))
    wait_for_success_before_retry = False

    def start_available() -> None:
        nonlocal wait_for_success_before_retry
        if wait_for_success_before_retry:
            return
        while pending and len(running) < MAX_ARCHIVIST_CHUNK_CONCURRENCY:
            index = pending.pop(0)
            source_file = chunk_source_file_name(material["source_file"], index=index + 1, total=total)
            task = asyncio.create_task(
                _clean_and_cut_chunk(
                    engine,
                    root,
                    raw_chunks[index],
                    collection=material["collection"],
                    source_file=source_file,
                    order_offset=0,
                )
            )
            running[task] = index

    start_available()
    while pending or running:
        if not running:
            wait_for_success_before_retry = False
            start_available()
        done, _pending_tasks = await asyncio.wait(running, return_when=asyncio.FIRST_COMPLETED)
        saw_success = False
        saw_concurrency_retry = False

        for task in done:
            index = running.pop(task)
            try:
                results[index] = task.result()
                saw_success = True
            except Exception as exc:
                if not _is_api_concurrency_error(exc):
                    raise
                retry_counts[index] = retry_counts.get(index, 0) + 1
                if retry_counts[index] > max_retries:
                    raise
                pending.insert(0, index)
                saw_concurrency_retry = True

        if saw_success:
            wait_for_success_before_retry = False
        elif saw_concurrency_retry and running:
            wait_for_success_before_retry = True
        else:
            wait_for_success_before_retry = False
        start_available()

    mtus: list[MTU] = []
    for chunk_mtus in results:
        for mtu in chunk_mtus or []:
            mtu.source_order_index = len(mtus)
            mtus.append(mtu)
    return mtus


def _is_api_concurrency_error(exc: Exception) -> bool:
    status_code = getattr(exc, "status_code", None)
    response = getattr(exc, "response", None)
    if status_code is None and response is not None:
        status_code = getattr(response, "status_code", None)
    if status_code == 429:
        return True

    name = type(exc).__name__.lower()
    if "ratelimit" in name or "rate_limit" in name:
        return True

    message = str(exc).lower()
    return any(
        token in message
        for token in (
            "too many",
            "rate limit",
            "rate_limit",
            "concurrent",
            "concurrency",
            "overload",
            "server busy",
            "429",
            "并发",
            "限流",
        )
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
    """Remove OCR-emitted HTML image/table blocks before chunking or LLM cleanup."""
    text = re.sub(
        r"<table\b.*?</table>",
        "",
        raw_markdown,
        flags=re.IGNORECASE | re.DOTALL,
    )
    text = re.sub(
        r'<div\b[^>]*>\s*<img\b[^>]*>\s*</div>',
        "",
        text,
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


def _install_ocr_progress_callback(engine: object | None) -> None:
    try:
        from tree.ingest.ocr_engine import set_progress_callback
    except Exception:
        return
    if engine is None:
        set_progress_callback(None)
        return

    def callback(event: dict[str, Any]) -> None:
        current = event.get("current_file") or event.get("current_chunk") or event.get("job_id") or ""
        state = event.get("state") or ""
        pages_done = event.get("pages_done")
        pages_total = event.get("pages_total")
        if pages_done is not None and pages_total is not None:
            message = f"OCR {current}: {pages_done}/{pages_total} pages ({state})"
        else:
            message = f"OCR {current}: {state}" if state else f"OCR {current}"
        _set_stage(engine, "ocr", status="running", active=str(current), message=message)

    set_progress_callback(callback)


def _progress(engine: object) -> Any | None:
    return getattr(engine, "progress", None)


def _set_stage(engine: object, stage: str, **kwargs: Any) -> None:
    progress = _progress(engine)
    if progress is not None and hasattr(progress, "set_stage"):
        try:
            progress.set_stage(stage, **kwargs)
        except Exception:
            return


def _add_stage_total(engine: object, stage: str, amount: int, **kwargs: Any) -> None:
    progress = _progress(engine)
    if progress is not None and hasattr(progress, "add_stage_total"):
        try:
            progress.add_stage_total(stage, amount, **kwargs)
        except Exception:
            return


def _advance_stage(engine: object, stage: str, **kwargs: Any) -> None:
    progress = _progress(engine)
    if progress is not None and hasattr(progress, "advance_stage"):
        try:
            progress.advance_stage(stage, **kwargs)
        except Exception:
            return


def _complete_stage(engine: object, stage: str, message: str) -> None:
    progress = _progress(engine)
    if progress is not None and hasattr(progress, "complete_stage"):
        try:
            progress.complete_stage(stage, message)
        except Exception:
            return
