"""Engine-integrated ingest pipeline: PaddleOCR → Archivist → Markdown."""

from __future__ import annotations

import asyncio
import logging
import re
import time
from pathlib import Path
from typing import Protocol

from ingest.ocr_engine import get_engine
from ingest.pipeline import extract_text

from tree.config import Settings

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


async def ingest_path(
    input_path: Path,
    output_dir: Path,
    settings: Settings,
    archivist: MarkdownStructurer | None = None,
    collection: str | None = None,
    indexer: SourceIndexer | None = None,
) -> list[Path]:
    """Ingest one file or a directory into structured source Markdown files."""
    _configure_ocr(settings)
    input_path = Path(input_path)
    output_dir = Path(output_dir)
    ocr_sem = asyncio.Semaphore(settings.source_ocr_concurrency)
    archivist_sem = asyncio.Semaphore(settings.source_archivist_concurrency)
    embedding_sem = asyncio.Semaphore(settings.source_embedding_concurrency)

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
        )
        return outputs
    if input_path.is_dir():
        tasks = [
            _ingest_file_pipeline(
                path,
                output_dir,
                settings,
                archivist=archivist,
                collection=collection,
                indexer=indexer,
                ocr_sem=ocr_sem,
                archivist_sem=archivist_sem,
                embedding_sem=embedding_sem,
            )
            for path in sorted(input_path.iterdir())
            if path.is_file() and not path.name.startswith(".")
        ]
        output_groups = await asyncio.gather(*tasks)
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
) -> list[Path]:
    outputs = await ingest_file(
        input_path,
        output_dir,
        settings.source_archivist_chunk_chars,
        archivist=archivist,
        ocr_sem=ocr_sem,
        archivist_sem=archivist_sem,
    )
    for out in outputs:
        await _index_output(settings.project_root, collection, out, indexer, embedding_sem)
    return outputs


async def ingest_file(
    input_path: Path,
    output_dir: Path,
    archivist_chunk_chars: int,
    archivist: MarkdownStructurer | None = None,
    ocr_sem: asyncio.Semaphore | None = None,
    archivist_sem: asyncio.Semaphore | None = None,
) -> list[Path]:
    """Process one file using PaddleOCR extraction and optional archivist cleanup."""
    start = time.time()
    if ocr_sem is None:
        raw_text = await asyncio.to_thread(extract_text, input_path)
    else:
        async with ocr_sem:
            raw_text = await asyncio.to_thread(extract_text, input_path)
    if not raw_text.strip():
        logger.warning("No text extracted from %s, skipping", input_path.name)
        return []

    raw_chunks = _split_raw_text_by_headings(raw_text, archivist_chunk_chars)
    if archivist:
        final_texts = await asyncio.gather(
            *[
                _structure_chunk(chunk, archivist, archivist_sem)
                for chunk in raw_chunks
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
) -> str:
    if archivist_sem is None:
        return await archivist.structure(raw_text)
    async with archivist_sem:
        return await archivist.structure(raw_text)


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

    return sections


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


def _configure_ocr(settings: Settings) -> None:
    get_engine(
        job_url=settings.paddleocr_api_url or None,
        token=settings.paddleocr_api_token or None,
        model=settings.paddleocr_model,
    )


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
