"""Image extractor using PaddleOCR-VL 1.6 API.

Sends images directly to the remote PaddleOCR-VL service for OCR + formula
recognition. Preprocessing (orientation, dewarping) is handled server-side.
"""

import logging
from pathlib import Path

from tree.ingest.ocr_engine import get_engine

logger = logging.getLogger(__name__)


def extract(image_path: str | Path) -> str:
    image_path = Path(image_path)
    if not image_path.exists():
        raise FileNotFoundError(f"Image not found: {image_path}")

    logger.info("OCR-ing image: %s", image_path.name)
    engine = get_engine()
    text = engine.ocr_file(image_path)

    if not text.strip():
        logger.warning("OCR API returned empty result for %s", image_path.name)
    return text
