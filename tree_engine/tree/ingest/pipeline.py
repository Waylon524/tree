"""Ingest pipeline: extract raw text -> Archivist clean -> MTU cut.

Routes a material file to the right extractor (all roads lead through OCR for
PDFs/images for formula fidelity), then hands cleaned Markdown to the Archivist.

The public entrypoint currently detects material type and extracts raw text.
"""

from __future__ import annotations

import logging
from pathlib import Path

from tree.ingest.extractors import (
    docx_extractor,
    image_extractor,
    pdf_extractor,
    presentation_extractor,
)

logger = logging.getLogger(__name__)

_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".tiff", ".tif", ".bmp", ".webp"}
_PDF_EXTS = {".pdf"}
_DOCX_EXTS = {".docx", ".doc"}
_PRESENTATION_EXTS = {".ppt", ".pptx"}
_TEXT_EXTS = {".txt", ".md"}

MATERIAL_EXTENSIONS = _IMAGE_EXTS | _PDF_EXTS | _DOCX_EXTS | _PRESENTATION_EXTS | _TEXT_EXTS


def detect_type(path: Path) -> str:
    ext = path.suffix.lower()
    if ext in _PDF_EXTS:
        return "pdf"
    if ext in _IMAGE_EXTS:
        return "image"
    if ext in _DOCX_EXTS:
        return "docx"
    if ext in _PRESENTATION_EXTS:
        return "presentation"
    if ext in _TEXT_EXTS:
        return "text"
    return "unknown"


def extract_text(path: Path) -> str:
    """Extract raw Markdown text from a material file.

    PDFs and images go through PaddleOCR-VL; docx/pptx use structural extractors
    (plus OCR for embedded images); text/md are read directly.
    """
    path = Path(path)
    ftype = detect_type(path)
    logger.info("Extracting %s [%s]", path.name, ftype)

    if ftype == "pdf":
        return pdf_extractor.extract(path)
    if ftype == "image":
        return image_extractor.extract(path)
    if ftype == "docx":
        return docx_extractor.extract(path)
    if ftype == "presentation":
        return presentation_extractor.extract(path)
    if ftype == "text":
        return path.read_text(encoding="utf-8", errors="replace")

    logger.warning("Unsupported file type: %s", path)
    return ""
