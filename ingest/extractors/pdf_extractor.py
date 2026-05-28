"""PDF extractor using PaddleOCR-VL v1.5.

All PDFs go through PaddleOCR-VL for OCR + formula recognition.
No PyMuPDF text extraction shortcut — even PDFs with embedded text
are fully OCR'd to ensure formula accuracy.
"""

import logging
from pathlib import Path

from ingest.ocr_engine import get_engine

logger = logging.getLogger(__name__)


def extract(pdf_path: str | Path) -> str:
    """Extract text from PDF via PaddleOCR-VL v1.5.

    Returns raw markdown text with OCR results and LaTeX formulas.
    """
    pdf_path = Path(pdf_path)
    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    logger.info("OCR-ing PDF with PaddleOCR-VL v1.5: %s", pdf_path.name)
    engine = get_engine()
    text = engine.ocr_file(pdf_path)

    if not text.strip():
        logger.warning("PaddleOCR returned empty result for %s", pdf_path.name)

    return text
