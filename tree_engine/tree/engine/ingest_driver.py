"""Source ingest + embedding orchestration.

Incremental through planner manifests: changed materials are re-extracted into
cleaned Markdown, cut into MTUs, folded into the Dagger DAG, then each source MTU
is embedded and the cleaned Markdown is removed.
"""

from __future__ import annotations

import asyncio
import html
import re
from pathlib import Path
from typing import Any

from tree.ingest.pipeline import extract_text
from tree.io import file_ops, paths
from tree.planner.manifest import scan_materials
from tree.planner.ids import prefixed_id
from tree.planner.mtu import mtu_text
from tree.planner.models import MTU
from tree.planner.pipeline import load_nodes, planner_generation_id, rebuild_planner
from tree.planner.store import artifact_hash, read_envelope_data, read_json

LONG_DOCUMENT_CHAR_THRESHOLD = 100_000
CLEAN_CHUNK_MIN_CHARS = 70_000
CLEAN_CHUNK_MAX_CHARS = 100_000


async def prepare_sources(engine: object) -> dict[str, Any]:
    """Build planner artifacts from materials and ensure source MTUs are indexed."""
    root = _root(engine)
    paths.ensure_workspace_dirs(root)
    indexer = getattr(engine, "rag_indexer", None)
    _install_ocr_progress_callback(engine)

    async def pre_dag_index(mtus: list[MTU]) -> None:
        await _ensure_mtus_embedded(engine, mtus, backfill_node_ids=False)

    try:
        if _can_resume_existing_planner(root, engine.settings):
            _set_stage(engine, "ocr", total=0, done=0, status="complete", message="No changed materials")
            _set_stage(engine, "clean", total=0, done=0, status="complete", message="Cleaning complete")
            _set_stage(engine, "cut", total=0, done=0, status="complete", message="Cutting complete")
            summary = _existing_planner_summary(root)
            _set_stage(
                engine,
                "cluster",
                total=summary["node_count"],
                done=summary["node_count"],
                status="complete",
                message="Reused existing nodes",
            )
            edge_count = summary["hard_edge_count"] + summary["soft_order_edge_count"]
            _set_stage(
                engine,
                "link",
                total=edge_count,
                done=edge_count,
                status="complete",
                message="Reused existing DAG",
            )
            await ensure_all_embedded(engine)
            return summary

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


def _can_resume_existing_planner(root: Path, settings: Any) -> bool:
    manifest_path = paths.material_manifest_path(root)
    previous = read_json(manifest_path) if manifest_path.exists() else None
    manifest = scan_materials(root, previous=previous)
    if manifest.get("inactive_materials"):
        return False
    if any(material.get("status") != "unchanged" for material in manifest.get("materials", [])):
        return False
    if not _planner_artifacts_ready(root, settings):
        return False
    return True


def _planner_artifacts_ready(root: Path, settings: Any) -> bool:
    try:
        manifest = read_json(paths.material_manifest_path(root))
        mtus_env = read_json(paths.mtus_path(root))
        nodes_env = read_json(paths.knowledge_nodes_path(root))
        dag_env = read_json(paths.knowledge_dag_path(root))
    except Exception:
        return False
    generation_id = planner_generation_id(manifest, settings)
    if manifest.get("generation_id") != generation_id:
        return False
    envelopes = (
        (mtus_env, "tree.mtus"),
        (nodes_env, "tree.knowledge-nodes"),
        (dag_env, "tree.knowledge-dag"),
    )
    if any(env.get("schema") != schema or env.get("generation_id") != generation_id for env, schema in envelopes):
        return False
    if not _input_hash_matches(mtus_env, artifact_hash(manifest)):
        return False
    if not _input_hash_matches(nodes_env, artifact_hash(mtus_env)):
        return False
    if not _input_hash_matches(dag_env, artifact_hash(nodes_env)):
        return False
    mtus = mtus_env.get("data", {}).get("mtus")
    nodes = nodes_env.get("data", {}).get("knowledge_nodes")
    dag = dag_env.get("data", {})
    return isinstance(mtus, list) and isinstance(nodes, list) and isinstance(dag.get("nodes"), list)


def _input_hash_matches(envelope_data: dict[str, Any], expected: str) -> bool:
    inputs = envelope_data.get("inputs")
    return bool(isinstance(inputs, list) and inputs and inputs[0].get("hash") == expected)


def _existing_planner_summary(root: Path) -> dict[str, Any]:
    manifest = read_json(paths.material_manifest_path(root))
    mtus = read_envelope_data(paths.mtus_path(root)).get("mtus", [])
    dag = read_envelope_data(paths.knowledge_dag_path(root))
    edges = dag.get("edges", [])
    return {
        "materials": manifest,
        "mtu_count": len(mtus),
        "node_count": len(dag.get("nodes", [])),
        "hard_edge_count": sum(1 for e in edges if e.get("relation") == "prerequisite"),
        "soft_order_edge_count": sum(1 for e in edges if e.get("relation") == "order"),
        "dag_svg_path": str(paths.outputs_dag_svg_path(root)),
        "resumed": True,
    }


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
    concurrency = max(1, int(getattr(engine.settings, "source_embedding_concurrency", 1)))
    semaphore = asyncio.Semaphore(concurrency)

    async def embed_one(mtu: MTU) -> int:
        if hasattr(indexer, "is_mtu_indexed") and indexer.is_mtu_indexed(mtu.mtu_id):
            _advance_stage(engine, "embed", message=f"Already indexed {mtu.title}")
            return 0
        source_path = source_markdown_path(
            root,
            mtu.collection,
            mtu.source_file,
            source_id=mtu.source_id,
        )
        if not source_path.exists():
            raise RuntimeError(f"Missing cleaned source Markdown for {mtu.mtu_id}: {source_path}")
        text = mtu_text(source_path.read_text(encoding="utf-8"), mtu.line_range)
        _set_stage(engine, "embed", status="running", active=mtu.title, message=f"Embedding {mtu.title}")
        async with semaphore:
            count = await asyncio.to_thread(
                indexer.index_mtu,
                mtu,
                text,
                node_id=mtu_to_node.get(mtu.mtu_id, ""),
            )
        _advance_stage(engine, "embed", message=f"Embedded {mtu.title}")
        return int(count)

    tasks = [asyncio.create_task(embed_one(mtu)) for mtu in mtus]
    try:
        for task in tasks:
            indexed += await task
    except BaseException:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise

    if backfill_node_ids and mtu_to_node and hasattr(indexer, "update_mtu_node_ids"):
        indexer.update_mtu_node_ids(mtu_to_node)
    _complete_stage(engine, "embed", "Embedding complete")
    return indexed


async def _produce_mtus(engine: object, root: Path, material: dict[str, Any]) -> list[MTU]:
    """Extract one material, clean it, persist source Markdown, and cut MTUs.

    OCR reuses a saved checkpoint when the material is unchanged (no re-OCR on
    resume). OCR / Clean / Cut each advance exactly once per material, so their
    totals (= number of changed materials, set upfront) stay stable and correct
    even though materials and chunks run concurrently.
    """
    material_path = paths.materials_root(root) / material["path"]
    material_label = str(material.get("path") or material_path.name)
    collection = material["collection"]
    source_file = material["source_file"]
    source_id = str(material.get("source_id") or material["path"])
    fingerprint = str(material.get("fingerprint") or "")
    checkpoint_fingerprint = artifact_hash(
        [fingerprint, str(material.get("ocr_signature") or "")]
    )
    ocr_path = paths.ocr_markdown_source_path(root, source_id)
    ocr_done = False
    clean_cut_started = False
    try:
        cached = _reuse_ocr_checkpoint(ocr_path, checkpoint_fingerprint)
        if cached is not None:
            _set_stage(engine, "ocr", status="running", active=material_label,
                       message=f"Reusing OCR checkpoint {material_label}")
            raw = cached
        else:
            _set_stage(engine, "ocr", status="running", active=material_label,
                       message=f"Extracting {material_label}")
            # Preserve the vendor/structural extractor output byte-for-byte. Any
            # image cleanup or HTML normalization belongs to the derived input,
            # never to the only auditable OCR checkpoint.
            raw = await asyncio.to_thread(extract_text, material_path)
            persist_ocr_markdown(root, collection, source_file, raw, source_id=source_id)
            _write_ocr_fingerprint(ocr_path, checkpoint_fingerprint)
        _advance_stage(engine, "ocr", message=f"Extracted {material_label}", active=[])
        ocr_done = True
        _record_ocr_checkpoint(engine, root, ocr_path, raw)

        checkpoint_raw = remove_ocr_image_html(ocr_path.read_text(encoding="utf-8"))
        if not checkpoint_raw.strip():
            raise RuntimeError(
                f"Extraction produced no usable text for {material_label}; "
                "the material was not marked complete."
            )
        raw_chunks = split_raw_markdown_for_cleaning(checkpoint_raw)
        _set_stage(engine, "clean", status="running", active=material_label,
                   message=f"Cleaning {material_label}")
        _set_stage(engine, "cut", status="running", active=material_label,
                   message=f"Cutting {material_label}")
        clean_cut_started = True
        mtus = await _clean_and_cut_chunks(engine, root, raw_chunks, material=material)
        if not mtus:
            raise RuntimeError(
                f"Archivist produced no teachable units for {material_label}; "
                "the material was not marked complete."
            )
        _advance_stage(engine, "clean", active=[])
        _advance_stage(engine, "cut", active=[])
        return mtus
    except Exception as exc:
        if not ocr_done:
            _fail_stage(engine, "ocr", f"{type(exc).__name__}: {exc}", active=material_label)
        elif clean_cut_started:
            _fail_stage(engine, "clean", f"{type(exc).__name__}: {exc}", active=material_label)
            _fail_stage(engine, "cut", f"{type(exc).__name__}: {exc}", active=material_label)
        raise


def source_markdown_path(
    root: Path,
    collection: str,
    source_file: str,
    *,
    source_id: str = "",
) -> Path:
    """Path for a cleaned intermediate Markdown file."""
    return paths.source_markdown_source_path(root, source_id or f"{collection}/{source_file}")


async def _clean_and_cut_chunk(
    engine: object,
    root: Path,
    raw_chunk: str,
    *,
    collection: str,
    source_file: str,
    source_id: str,
    source_sha256: str,
    order_offset: int,
) -> list[MTU]:
    archivist = engine.archivist
    # Per-chunk calls update the display only; the done counter advances once per
    # material in _produce_mtus, so concurrent chunks never inflate the count.
    _set_stage(engine, "clean", status="running", active=source_file, message=f"Cleaning {source_file}")
    cleaned = await archivist.clean(
        raw_chunk,
        timeout_sec=getattr(engine.settings, "archivist_mtu_cut_timeout_sec", None),
        repair_attempts=getattr(engine.settings, "archivist_mtu_repair_attempts", 1),
    )
    _set_stage(engine, "clean", message=f"Cleaned {source_file}")
    if not cleaned.strip():
        _set_stage(engine, "cut", message=f"Skipped empty cleaned chunk {source_file}")
        return []

    source_path = source_markdown_path(
        root,
        collection,
        source_file,
        source_id=source_id,
    )
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
    for mtu in mtus:
        mtu.source_id = source_id
        mtu.source_sha256 = source_sha256
        mtu.mtu_id = prefixed_id(
            "mtu",
            [source_id, source_sha256, mtu.line_range[0], mtu.line_range[1]],
        )
    _set_stage(engine, "cut", message=f"Cut {source_file}")
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
    chunk_concurrency = max(
        1,
        int(getattr(engine.settings, "archivist_chunk_concurrency", 5)),
    )
    wait_for_success_before_retry = False

    def start_available() -> None:
        nonlocal wait_for_success_before_retry
        if wait_for_success_before_retry:
            return
        while pending and len(running) < chunk_concurrency:
            index = pending.pop(0)
            source_file = chunk_source_file_name(material["source_file"], index=index + 1, total=total)
            material_source_id = str(material.get("source_id") or material["path"])
            source_id = (
                material_source_id
                if total == 1
                else f"{material_source_id}.part-{index + 1:03d}"
            )
            task = asyncio.create_task(
                _clean_and_cut_chunk(
                    engine,
                    root,
                    raw_chunks[index],
                    collection=material["collection"],
                    source_file=source_file,
                    source_id=source_id,
                    source_sha256=str(material.get("fingerprint") or ""),
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
                    for sibling in running:
                        sibling.cancel()
                    await asyncio.gather(*running, return_exceptions=True)
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
    """Remove image artifacts while retaining OCR table cell contents."""
    text = re.sub(
        r"<table\b.*?</table>",
        _html_table_to_text,
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


def _html_table_to_text(match: re.Match[str]) -> str:
    table = match.group(0)
    table = re.sub(r"</(?:td|th)\s*>", "\t", table, flags=re.IGNORECASE)
    table = re.sub(r"</tr\s*>", "\n", table, flags=re.IGNORECASE)
    table = re.sub(r"<br\s*/?>", "\n", table, flags=re.IGNORECASE)
    table = re.sub(r"<[^>]+>", "", table)
    rows = []
    for row in html.unescape(table).splitlines():
        cells = [cell.strip() for cell in row.split("\t") if cell.strip()]
        if cells:
            rows.append(" | ".join(cells))
    return "\n" + "\n".join(rows) + "\n"


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


def persist_ocr_markdown(
    root: Path,
    collection: str,
    source_file: str,
    raw_markdown: str,
    *,
    source_id: str = "",
) -> Path:
    """Persist raw OCR Markdown before Archivist cleanup for inspection and retries."""
    path = paths.ocr_markdown_source_path(root, source_id or f"{collection}/{source_file}")
    file_ops.write_text(path, raw_markdown)
    return path


def _ocr_fingerprint_path(ocr_path: Path) -> Path:
    return ocr_path.with_name(ocr_path.name + ".fingerprint")


def _reuse_ocr_checkpoint(ocr_path: Path, fingerprint: str) -> str | None:
    """Return the saved OCR Markdown if it is still valid for this material.

    Valid means the material's fingerprint matches the one recorded when the
    checkpoint was written, so a re-run (e.g. resuming after a crash) skips the
    paid OCR call instead of re-extracting.
    """
    if not fingerprint or not ocr_path.exists():
        return None
    fp_path = _ocr_fingerprint_path(ocr_path)
    if not fp_path.exists() or fp_path.read_text(encoding="utf-8").strip() != fingerprint:
        return None
    return ocr_path.read_text(encoding="utf-8")


def _write_ocr_fingerprint(ocr_path: Path, fingerprint: str) -> None:
    if fingerprint:
        file_ops.write_text(_ocr_fingerprint_path(ocr_path), fingerprint)


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
        from tree.ingest.ocr_engine import set_job_store_root, set_progress_callback
    except Exception:
        return
    if engine is None:
        set_progress_callback(None)
        set_job_store_root(None)
        return

    set_job_store_root(paths.ocr_jobs_root(_root(engine)))

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


def _fail_stage(engine: object, stage: str, message: str, *, active: str = "") -> None:
    _set_stage(engine, stage, status="failed", message=message, active=active)
    progress = _progress(engine)
    if progress is None or not hasattr(progress, "update"):
        return
    try:
        state = progress.load() if hasattr(progress, "load") else {}
        errors = list(state.get("errors") or [])
        if message not in errors:
            errors.append(message)
        progress.update({"phase": "failed", "message": message, "errors": errors[-8:]})
    except Exception:
        return
