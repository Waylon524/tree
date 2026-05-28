"""Engine-integrated ingest pipeline: PaddleOCR → Archivist → Markdown."""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Protocol

from ingest.math_fix import fix_math_symbols
from ingest.ocr_engine import get_engine
from ingest.pipeline import extract_text

from tree.config import Settings

logger = logging.getLogger(__name__)


class MarkdownStructurer(Protocol):
    async def structure(self, raw_text: str) -> str:
        """Return structured Markdown for raw OCR text."""


async def ingest_path(
    input_path: Path,
    output_dir: Path,
    settings: Settings,
    archivist: MarkdownStructurer | None = None,
) -> list[Path]:
    """Ingest one file or a directory into structured source Markdown files."""
    _configure_ocr(settings)
    input_path = Path(input_path)
    output_dir = Path(output_dir)

    if input_path.is_file():
        out = await ingest_file(input_path, output_dir, archivist=archivist)
        return [out] if out else []
    if input_path.is_dir():
        outputs = []
        for path in sorted(input_path.iterdir()):
            if path.is_file() and not path.name.startswith("."):
                out = await ingest_file(path, output_dir, archivist=archivist)
                if out:
                    outputs.append(out)
        return outputs
    raise FileNotFoundError(f"Input not found: {input_path}")


async def ingest_file(
    input_path: Path,
    output_dir: Path,
    archivist: MarkdownStructurer | None = None,
) -> Path | None:
    """Process one file using PaddleOCR extraction and optional archivist cleanup."""
    start = time.time()
    raw_text = extract_text(input_path)
    if not raw_text.strip():
        logger.warning("No text extracted from %s, skipping", input_path.name)
        return None

    raw_text = fix_math_symbols(raw_text)
    final_text = await archivist.structure(raw_text) if archivist else raw_text

    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{input_path.stem}.md"
    output_path.write_text(final_text, encoding="utf-8")

    elapsed = time.time() - start
    logger.info("Ingested %s → %s (%.1fs)", input_path.name, output_path.name, elapsed)
    return output_path


def _configure_ocr(settings: Settings) -> None:
    get_engine(
        job_url=settings.paddleocr_api_url or None,
        token=settings.paddleocr_api_token or None,
        model=settings.paddleocr_model,
    )
