"""Main ingest pipeline orchestrator.

Processes files from raw_materials/ → .tree/runtime/source_materials/<chapter>/
using remote PaddleOCR-VL 1.6 API service for all file types.

Usage:
    python -m ingest.pipeline --input raw_materials/ --output .tree/runtime/source_materials/01-化学/
    python -m ingest.pipeline --input "raw_materials/课件/5. 化学平衡通论.pdf"
"""

import argparse
import logging
import sys
import time
from pathlib import Path

from ingest.extractors import docx_extractor, image_extractor, pdf_extractor
from ingest.ocr_engine import get_engine
from ingest.structurer import structure

logger = logging.getLogger(__name__)

_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".tiff", ".tif", ".bmp", ".webp"}
_PDF_EXTS = {".pdf"}
_DOCX_EXTS = {".docx", ".doc"}
_TEXT_EXTS = {".txt", ".md"}


def detect_type(path: Path) -> str:
    """Detect file type by extension."""
    ext = path.suffix.lower()
    if ext in _PDF_EXTS:
        return "pdf"
    if ext in _IMAGE_EXTS:
        return "image"
    if ext in _DOCX_EXTS:
        return "docx"
    if ext in _TEXT_EXTS:
        return "text"
    return "unknown"


def extract_text(path: Path) -> str:
    """Extract text from a file using the appropriate extractor.

    All files go through PaddleOCR-VL 1.6 — even PDFs with
    embedded text are fully OCR'd for formula accuracy.
    """
    ftype = detect_type(path)
    logger.info("Processing %s [%s]: %s", path.name, ftype, path)

    if ftype == "pdf":
        return pdf_extractor.extract(path)
    if ftype == "image":
        return image_extractor.extract(path)
    if ftype == "docx":
        return docx_extractor.extract(path)
    if ftype == "text":
        return path.read_text(encoding="utf-8", errors="replace")

    logger.warning("Unsupported file type: %s", path)
    return ""


def process_file(
    input_path: Path,
    output_dir: Path,
    use_structurer: bool = True,
) -> Path:
    """Process a single file: extract → structure → write.

    Args:
        input_path: Input file path.
        output_dir: Output directory for the markdown file.
        use_structurer: Whether to run LLM structurer.

    Returns:
        Path to the output markdown file.
    """
    start = time.time()

    # Step 1+2+3: Extract text via PaddleOCR-VL 1.6
    raw_text = extract_text(input_path)

    if not raw_text.strip():
        logger.warning("No text extracted from %s, skipping", input_path.name)
        return None

    # Step 4: Structure via LLM (optional)
    if use_structurer:
        try:
            final_text = structure(raw_text)
        except Exception:
            logger.exception("Structurer failed, using raw text")
            final_text = raw_text
    else:
        final_text = raw_text

    # Write output
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{input_path.stem}.md"
    output_path.write_text(final_text, encoding="utf-8")

    elapsed = time.time() - start
    logger.info("Processed %s → %s (%.1fs)", input_path.name, output_path.name, elapsed)
    return output_path


def process_directory(
    input_dir: Path,
    output_dir: Path,
    use_structurer: bool = True,
) -> list[Path]:
    """Process all files in a directory."""
    results = []
    for path in sorted(input_dir.iterdir()):
        if path.is_file() and not path.name.startswith("."):
            out = process_file(path, output_dir, use_structurer)
            if out:
                results.append(out)
    return results


def main():
    parser = argparse.ArgumentParser(description="Ingest pipeline: raw files → structured Markdown")
    parser.add_argument("--input", required=True, help="Input file or directory")
    parser.add_argument("--output", required=True, help="Output directory")
    parser.add_argument("--no-structure", action="store_true", help="Skip LLM structuring")
    parser.add_argument("--api-url", default=None, help="PaddleOCR API URL (or set PADDLEOCR_API_URL env var)")
    parser.add_argument("--token", default=None, help="PaddleOCR API token (or set PADDLEOCR_API_TOKEN env var)")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    input_path = Path(args.input)
    output_dir = Path(args.output)

    # Initialize OCR engine
    get_engine(job_url=args.api_url, token=args.token)

    if input_path.is_file():
        process_file(input_path, output_dir, use_structurer=not args.no_structure)
    elif input_path.is_dir():
        process_directory(input_path, output_dir, use_structurer=not args.no_structure)
    else:
        logger.error("Input not found: %s", input_path)
        sys.exit(1)


if __name__ == "__main__":
    main()
