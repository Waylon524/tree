"""PDF extractor using PaddleOCR-VL 1.6.

All PDFs go through PaddleOCR-VL for OCR + formula recognition. No embedded-text
shortcut — even PDFs with embedded text are fully OCR'd for formula consistency.
"""

import logging
from pathlib import Path

from tree.ingest.ocr_engine import get_engine

logger = logging.getLogger(__name__)


def extract(pdf_path: str | Path) -> str:
    pdf_path = Path(pdf_path)
    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    logger.info("OCR-ing PDF with PaddleOCR-VL 1.6: %s", pdf_path.name)
    engine = get_engine()
    text = engine.ocr_file(pdf_path)

    if not text.strip():
        raise RuntimeError(f"PaddleOCR returned empty result for {pdf_path.name}")
    return text
