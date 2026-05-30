"""Engine-integrated ingest pipeline: PaddleOCR → Archivist → Markdown."""

from __future__ import annotations

import asyncio
import logging
import re
import time
from pathlib import Path
from typing import Protocol

from ingest.ocr_engine import get_engine, set_progress_callback
from ingest.pipeline import extract_text

from tree.config import Settings
from tree.observability.progress import ProgressTracker

logger = logging.getLogger(__name__)


class MarkdownStructurer(Protocol):
    async def structure(self, raw_text: str) -> str:
        """Return structured Markdown for raw OCR text."""


class SourceIndexer(Protocol):
    def index_source_file(self, root: Path, collection: str, path: Path) -> int:
        """Index one structured source Markdown file."""


_IMPLICIT_HEADING_RE = re.compile(
    r"^\s*(第[一二三四五六七八九十百千万\d]+[章节篇单元]|"
    r"\d+(?:\.\d+)+\s+\S|"
    r"\d+[、.．]\s+\S)"
)
_ocr_upload_locks: dict[int, asyncio.Lock] = {}
_last_ocr_upload_at = 0.0


async def ingest_path(
    input_path: Path,
    output_dir: Path,
    settings: Settings,
    archivist: MarkdownStructurer | None = None,
    collection: str | None = None,
    indexer: SourceIndexer | None = None,
    progress: ProgressTracker | None = None,
    track_files: bool = True,
) -> list[Path]:
    """Ingest one file or a directory into structured source Markdown files."""
    _configure_ocr(settings, progress)
    input_path = Path(input_path)
    output_dir = Path(output_dir)
    ocr_sem = asyncio.Semaphore(settings.source_ocr_concurrency)
    archivist_sem = asyncio.Semaphore(settings.source_archivist_concurrency)
    embedding_sem = asyncio.Semaphore(settings.source_embedding_concurrency)

    async def ingest_file_with_path(path: Path) -> tuple[Path, list[Path]]:
        outputs = await _ingest_file_pipeline(
            path,
            output_dir,
            settings,
            archivist=archivist,
            collection=collection,
            indexer=indexer,
            ocr_sem=ocr_sem,
            archivist_sem=archivist_sem,
            embedding_sem=embedding_sem,
            progress=progress,
        )
        return path, outputs

    if input_path.is_file():
        outputs = await _ingest_file_pipeline(
            input_path,
            output_dir,
            settings,
            archivist=archivist,
            collection=collection,
            indexer=indexer,
            ocr_sem=ocr_sem,
            archivist_sem=archivist_sem,
            embedding_sem=embedding_sem,
            progress=progress,
        )
        if progress and track_files:
            progress.source_file_done(input_path.name, 1, 1)
        return outputs
    if input_path.is_dir():
        input_files = [
            path
            for path in sorted(input_path.iterdir())
            if path.is_file() and not path.name.startswith(".")
        ]
        tasks = [asyncio.create_task(ingest_file_with_path(path)) for path in input_files]
        output_groups = []
        files_done = 0
        for task in asyncio.as_completed(tasks):
            path, outputs = await task
            output_groups.append(outputs)
            files_done += 1
            if progress and track_files:
                progress.source_file_done(path.name, files_done, len(input_files))
        return [out for outputs in output_groups for out in outputs]
    raise FileNotFoundError(f"Input not found: {input_path}")


async def _ingest_file_pipeline(
    input_path: Path,
    output_dir: Path,
    settings: Settings,
    archivist: MarkdownStructurer | None,
    collection: str | None,
    indexer: SourceIndexer | None,
    ocr_sem: asyncio.Semaphore,
    archivist_sem: asyncio.Semaphore,
    embedding_sem: asyncio.Semaphore,
    progress: ProgressTracker | None = None,
) -> list[Path]:
    outputs = await ingest_file(
        input_path,
        output_dir,
        settings.source_archivist_chunk_chars,
        settings.source_ocr_upload_interval_sec,
        archivist=archivist,
        ocr_sem=ocr_sem,
        archivist_sem=archivist_sem,
        progress=progress,
    )
    for out in outputs:
        await _index_output(settings.project_root, collection, out, indexer, embedding_sem)
    return outputs


async def ingest_file(
    input_path: Path,
    output_dir: Path,
    archivist_chunk_chars: int,
    ocr_upload_interval_sec: float,
    archivist: MarkdownStructurer | None = None,
    ocr_sem: asyncio.Semaphore | None = None,
    archivist_sem: asyncio.Semaphore | None = None,
    progress: ProgressTracker | None = None,
) -> list[Path]:
    """Process one file using PaddleOCR extraction and optional archivist cleanup."""
    start = time.time()
    if ocr_sem is None:
        await _wait_for_ocr_upload_slot(ocr_upload_interval_sec)
        raw_text = await asyncio.to_thread(extract_text, input_path)
    else:
        async with ocr_sem:
            await _wait_for_ocr_upload_slot(ocr_upload_interval_sec)
            raw_text = await asyncio.to_thread(extract_text, input_path)
    if progress:
        progress.ocr_file_done(input_path.name)
    if not raw_text.strip():
        logger.warning("No text extracted from %s, skipping", input_path.name)
        return []

    raw_chunks = _split_raw_text_by_headings(raw_text, archivist_chunk_chars)
    if archivist:
        final_texts = await asyncio.gather(
            *[
                _structure_chunk(
                    chunk,
                    archivist,
                    archivist_sem,
                    source_name=input_path.name,
                    chunk_index=index,
                    progress=progress,
                )
                for index, chunk in enumerate(raw_chunks, start=1)
            ]
        )
    else:
        final_texts = raw_chunks

    output_dir.mkdir(parents=True, exist_ok=True)
    output_paths = []
    use_part_names = len(final_texts) > 1
    for idx, final_text in enumerate(final_texts, start=1):
        filename = (
            f"{input_path.stem}__part-{idx:02d}.md"
            if use_part_names
            else f"{input_path.stem}.md"
        )
        output_path = output_dir / filename
        output_path.write_text(final_text, encoding="utf-8")
        output_paths.append(output_path)

    elapsed = time.time() - start
    logger.info("Ingested %s → %d source chunk(s) (%.1fs)", input_path.name, len(output_paths), elapsed)
    return output_paths


async def _structure_chunk(
    raw_text: str,
    archivist: MarkdownStructurer,
    archivist_sem: asyncio.Semaphore | None,
    *,
    source_name: str,
    chunk_index: int,
    progress: ProgressTracker | None = None,
) -> str:
    try:
        if archivist_sem is None:
            return await archivist.structure(raw_text)
        async with archivist_sem:
            return await archivist.structure(raw_text)
    except Exception as exc:
        logger.exception(
            "Structurer failed, using raw text: source=%s chunk=%s error=%s",
            source_name,
            chunk_index,
            type(exc).__name__,
        )
        if progress is not None:
            progress.archivist_degraded(
                current_file=source_name,
                chunk_index=chunk_index,
                error_type=type(exc).__name__,
            )
        return raw_text


async def _wait_for_ocr_upload_slot(interval_sec: float) -> None:
    """Rate-limit PaddleOCR job submission starts across concurrent ingest tasks."""
    if interval_sec <= 0:
        return
    global _last_ocr_upload_at
    loop = asyncio.get_running_loop()
    lock = _ocr_upload_locks.setdefault(id(loop), asyncio.Lock())
    async with lock:
        now = time.monotonic()
        wait_sec = _last_ocr_upload_at + interval_sec - now
        if wait_sec > 0:
            await asyncio.sleep(wait_sec)
            now = time.monotonic()
        _last_ocr_upload_at = now


def _split_raw_text_by_headings(raw_text: str, max_chars: int) -> list[str]:
    """Split large OCR Markdown on heading boundaries, keeping each heading section intact."""
    if max_chars <= 0 or len(raw_text) <= max_chars:
        return [raw_text]

    lines = raw_text.splitlines(keepends=True)
    heading_candidates = [
        (idx, level)
        for idx, line in enumerate(lines)
        if (level := _heading_level(line)) is not None
    ]
    if not heading_candidates:
        return [raw_text]

    levels = sorted({level for _, level in heading_candidates})
    selected_level = next(
        (level for level in levels if sum(1 for _, candidate in heading_candidates if candidate == level) >= 2),
        levels[0],
    )
    starts = [idx for idx, level in heading_candidates if level == selected_level]
    if len(starts) <= 1:
        return [raw_text]
    if starts[0] > 0:
        starts[0] = 0

    sections = []
    for pos, start in enumerate(starts):
        end = starts[pos + 1] if pos + 1 < len(starts) else len(lines)
        section = "".join(lines[start:end]).strip()
        if section:
            sections.append(section)
    if len(sections) <= 1:
        return [raw_text]

    return _group_sections_by_size(sections, max_chars)


def _group_sections_by_size(sections: list[str], max_chars: int) -> list[str]:
    grouped = []
    current = ""
    for section in sections:
        separator = "\n\n" if current else ""
        candidate = f"{current}{separator}{section}"
        if current and len(candidate) > max_chars:
            grouped.append(current)
            current = section
        else:
            current = candidate
    if current:
        grouped.append(current)
    return grouped or sections


def _heading_level(line: str) -> int | None:
    stripped = line.strip()
    if not stripped:
        return None
    markdown = re.match(r"^(#{1,6})\s+\S", stripped)
    if markdown:
        return len(markdown.group(1))
    if len(stripped) <= 80 and _IMPLICIT_HEADING_RE.match(stripped):
        if re.match(r"^第[一二三四五六七八九十百千万\d]+[章篇单元]", stripped):
            return 1
        return 2
    return None


def _configure_ocr(settings: Settings, progress: ProgressTracker | None = None) -> None:
    get_engine(
        job_url=settings.paddleocr_api_url or None,
        token=settings.paddleocr_api_token or None,
        model=settings.paddleocr_model,
    )
    set_progress_callback(progress.ocr_event if progress else None)


async def _index_output(
    root: Path,
    collection: str | None,
    path: Path,
    indexer: SourceIndexer | None,
    embedding_sem: asyncio.Semaphore,
) -> None:
    if not indexer or not collection:
        return
    async with embedding_sem:
        await asyncio.to_thread(indexer.index_source_file, root, collection, path)
        path.unlink(missing_ok=True)
