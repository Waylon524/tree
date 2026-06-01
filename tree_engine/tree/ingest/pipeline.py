"""Ingest pipeline: extract raw text -> Archivist clean -> MTU cut.

Routes a material file to the right extractor (all roads lead through OCR for
PDFs/images for formula fidelity), then hands cleaned Markdown to the Archivist.

See docs/REBUILD-DESIGN.md §1/§4.

TODO (step 2 for extraction, step 4 for MTU):
  - detect_type(path) / extract_text(path)
  - ingest_file(path, collection, archivist) -> (cleaned_md_path, list[MTU])
"""

from __future__ import annotations

from pathlib import Path

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
    raise NotImplementedError("ingest.extract_text — migrate extractors in step 2")
