"""PaddleOCR-VL API client (async job model).

Workflow: submit job → poll status → download JSONL results.

Authentication: Authorization: bearer {TOKEN}

Configuration via environment variables (or .env):
  - PADDLEOCR_API_URL:   Job endpoint URL
  - PADDLEOCR_API_TOKEN: API access token
  - PADDLEOCR_MODEL:     Model name (default: PaddleOCR-VL-1.6)

Usage:
  engine = get_engine()
  text = engine.ocr_file("document.pdf")       # local file upload
  text = engine.ocr_file("https://.../doc.pdf") # URL mode
"""

import json
import logging
import os
import tempfile
import time
from pathlib import Path
from typing import Callable, Any

import httpx

logger = logging.getLogger(__name__)
_progress_callback: Callable[[dict[str, Any]], None] | None = None

_DEFAULT_JOB_URL = "https://paddleocr.aistudio-app.com/api/v2/ocr/jobs"
_DEFAULT_MODEL = "PaddleOCR-VL-1.6"
_POLL_INTERVAL = 5
_POLL_TIMEOUT = 600
_PDF_MAX_PAGES_PER_JOB = 100

_DEFAULT_OPTIONS = {
    "useDocOrientationClassify": True,
    "useDocUnwarping": True,
    "useChartRecognition": True,
}


class OCREngine:
    """Async-job client for Baidu AI Studio PaddleOCR-VL."""

    _instance = None

    def __new__(cls, job_url: str | None = None, token: str | None = None, **kwargs):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._job_url = (job_url or os.environ.get("PADDLEOCR_API_URL", _DEFAULT_JOB_URL)).rstrip("/")
            cls._instance._token = token or os.environ.get("PADDLEOCR_API_TOKEN", "")
            cls._instance._model = kwargs.pop("model", os.environ.get("PADDLEOCR_MODEL", _DEFAULT_MODEL))
            cls._instance._poll_interval = kwargs.pop("poll_interval", _POLL_INTERVAL)
            cls._instance._poll_timeout = kwargs.pop("poll_timeout", _POLL_TIMEOUT)
            cls._instance._options = {**_DEFAULT_OPTIONS, **kwargs.pop("options", {})}
            cls._instance._client = httpx.Client(timeout=kwargs.pop("timeout", 30.0))
            logger.info("OCR API client: %s", cls._instance._job_url)
        return cls._instance

    def _headers(self, content_type: str | None = None) -> dict:
        h = {"Authorization": f"bearer {self._token}"}
        if content_type:
            h["Content-Type"] = content_type
        return h

    def ocr_file(self, path: str | Path) -> str:
        """OCR a local file or URL, return merged markdown text."""
        path = str(path)
        if path.startswith("http://") or path.startswith("https://"):
            job_id = self._submit_url(path)
            context = {
                "state": "submitted",
                "job_id": job_id,
                "current_file": path,
                "current_chunk": path,
                "chunk_index": 1,
                "chunks_total": 1,
            }
            _emit_progress(context)
            logger.info("Job submitted: %s", job_id)
            jsonl_url = self._poll(job_id, context)
            return self._download_result(jsonl_url)

        local_path = Path(path)
        if local_path.suffix.lower() == ".pdf":
            return self._ocr_local_pdf(local_path)
        return self._ocr_single_local(local_path)

    def _ocr_local_pdf(self, pdf_path: Path) -> str:
        """OCR a local PDF, splitting files over the API page limit."""
        page_count = self._pdf_page_count(pdf_path)
        if page_count <= _PDF_MAX_PAGES_PER_JOB:
            return self._ocr_single_local(
                pdf_path,
                {
                    "current_file": pdf_path.name,
                    "current_chunk": pdf_path.name,
                    "chunk_index": 1,
                    "chunks_total": 1,
                    "file_pages_total": page_count,
                },
            )

        logger.info(
            "PDF has %d pages; splitting into chunks of <=%d pages before OCR: %s",
            page_count,
            _PDF_MAX_PAGES_PER_JOB,
            pdf_path.name,
        )
        parts = []
        with tempfile.TemporaryDirectory(prefix="tree-pdf-split-") as temp_dir:
            chunk_paths = self._split_pdf(pdf_path, Path(temp_dir), _PDF_MAX_PAGES_PER_JOB)
            for index, chunk_path in enumerate(chunk_paths, start=1):
                logger.info(
                    "OCR-ing PDF chunk %d/%d: %s",
                    index,
                    len(chunk_paths),
                    chunk_path.name,
                )
                chunk_pages = self._pdf_page_count(chunk_path)
                text = self._ocr_single_local(
                    chunk_path,
                    {
                        "current_file": pdf_path.name,
                        "current_chunk": chunk_path.name,
                        "chunk_index": index,
                        "chunks_total": len(chunk_paths),
                        "file_pages_total": page_count,
                        "chunk_pages_total": chunk_pages,
                    },
                )
                if text.strip():
                    parts.append(text)
        return "\n\n".join(parts)

    def _ocr_single_local(self, path: Path, context: dict[str, Any] | None = None) -> str:
        """OCR one local file without additional splitting."""
        context = {
            "current_file": path.name,
            "current_chunk": path.name,
            "chunk_index": 1,
            "chunks_total": 1,
            **(context or {}),
        }
        job_id = self._submit_local(str(path))
        _emit_progress({"state": "submitted", "job_id": job_id, **context})
        logger.info("Job submitted: %s", job_id)
        jsonl_url = self._poll(job_id, context)
        return self._download_result(jsonl_url)

    @staticmethod
    def _pdf_page_count(pdf_path: Path) -> int:
        try:
            from pypdf import PdfReader
        except ImportError as exc:
            raise RuntimeError(
                "PDF page counting and splitting requires pypdf. "
                "Reinstall tree with current dependencies."
            ) from exc

        reader = PdfReader(str(pdf_path))
        return len(reader.pages)

    @staticmethod
    def _split_pdf(pdf_path: Path, output_dir: Path, max_pages: int) -> list[Path]:
        try:
            from pypdf import PdfReader, PdfWriter
        except ImportError as exc:
            raise RuntimeError(
                "PDF page counting and splitting requires pypdf. "
                "Reinstall tree with current dependencies."
            ) from exc

        reader = PdfReader(str(pdf_path))
        output_dir.mkdir(parents=True, exist_ok=True)
        chunk_paths = []
        total_pages = len(reader.pages)
        for start in range(0, total_pages, max_pages):
            writer = PdfWriter()
            end = min(start + max_pages, total_pages)
            for page_index in range(start, end):
                writer.add_page(reader.pages[page_index])
            chunk_path = output_dir / f"{pdf_path.stem}__pages-{start + 1:04d}-{end:04d}.pdf"
            with chunk_path.open("wb") as file:
                writer.write(file)
            chunk_paths.append(chunk_path)
        return chunk_paths

    def _submit_url(self, file_url: str) -> str:
        """Submit job with a file URL (JSON body)."""
        payload = {
            "fileUrl": file_url,
            "model": self._model,
            "optionalPayload": self._options,
        }
        resp = self._client.post(self._job_url, json=payload, headers=self._headers("application/json"))
        resp.raise_for_status()
        return resp.json()["data"]["jobId"]

    def _submit_local(self, file_path: str) -> str:
        """Submit job with a local file (multipart upload)."""
        data = {
            "model": self._model,
            "optionalPayload": json.dumps(self._options),
        }
        with open(file_path, "rb") as f:
            files = {"file": (Path(file_path).name, f)}
            resp = self._client.post(self._job_url, headers=self._headers(), data=data, files=files)
        resp.raise_for_status()
        return resp.json()["data"]["jobId"]

    def _poll(self, job_id: str, context: dict[str, Any] | None = None) -> str:
        """Poll job status until done, return JSONL result URL."""
        context = context or {}
        deadline = time.time() + self._poll_timeout
        while time.time() < deadline:
            resp = self._client.get(f"{self._job_url}/{job_id}", headers=self._headers())
            resp.raise_for_status()
            body = resp.json()["data"]
            state = body["state"]

            if state == "done":
                pages = body.get("extractProgress", {}).get("extractedPages", "?")
                pages_done, pages_total = _progress_pages(
                    _int_or_none(pages),
                    _int_or_none(body.get("extractProgress", {}).get("totalPages", pages)),
                    context,
                )
                _emit_progress(
                    {
                        "state": "done",
                        "job_id": job_id,
                        "pages_done": pages_done,
                        "pages_total": pages_total,
                        **context,
                    }
                )
                logger.info("Job done: %s pages extracted", pages)
                return body["resultUrl"]["jsonUrl"]

            if state == "failed":
                _emit_progress({"state": "failed", "job_id": job_id, **context})
                raise RuntimeError(f"OCR job failed: {body.get('errorMsg', 'unknown')}")

            if state == "running":
                prog = body.get("extractProgress", {})
                total = prog.get("totalPages", "?")
                done = prog.get("extractedPages", "?")
                pages_done, pages_total = _progress_pages(
                    _int_or_none(done),
                    _int_or_none(total),
                    context,
                )
                _emit_progress(
                    {
                        "state": "running",
                        "job_id": job_id,
                        "pages_done": pages_done,
                        "pages_total": pages_total,
                        **context,
                    }
                )
                logger.debug("Job running: %s/%s pages", done, total)
            else:
                _emit_progress({"state": state, "job_id": job_id, **context})
                logger.debug("Job state: %s", state)

            time.sleep(self._poll_interval)

        raise TimeoutError(f"OCR job {job_id} timed out after {self._poll_timeout}s")

    def _download_result(self, jsonl_url: str) -> str:
        """Download JSONL result and extract merged markdown text."""
        resp = self._client.get(jsonl_url)
        resp.raise_for_status()
        parts = []
        for line in resp.text.strip().splitlines():
            line = line.strip()
            if not line:
                continue
            result = json.loads(line)["result"]
            for page in result["layoutParsingResults"]:
                md = page.get("markdown", {})
                text = md.get("text", "") if isinstance(md, dict) else ""
                if text:
                    parts.append(text)
        return "\n\n".join(parts)

    def close(self):
        self._client.close()

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass


def get_engine(job_url: str | None = None, token: str | None = None, **kwargs) -> OCREngine:
    """Get or create the singleton OCR engine.

    Args:
        job_url: API job endpoint, or set PADDLEOCR_API_URL env var.
        token: API token, or set PADDLEOCR_API_TOKEN env var.
    """
    return OCREngine(job_url=job_url, token=token, **kwargs)


def set_progress_callback(callback: Callable[[dict[str, Any]], None] | None) -> None:
    global _progress_callback
    _progress_callback = callback


def _emit_progress(event: dict[str, Any]) -> None:
    if _progress_callback is None:
        return
    try:
        _progress_callback(event)
    except Exception:
        logger.debug("OCR progress callback failed", exc_info=True)


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _progress_pages(
    pages_done: int | None,
    pages_total: int | None,
    context: dict[str, Any],
) -> tuple[int | None, int | None]:
    chunks_total = _int_or_none(context.get("chunks_total")) or 1
    if chunks_total <= 1:
        return pages_done, pages_total

    file_total = _int_or_none(context.get("file_pages_total"))
    chunk_index = _int_or_none(context.get("chunk_index")) or 1
    if file_total is None or pages_done is None:
        return pages_done, pages_total

    pages_before_chunk = max(0, chunk_index - 1) * _PDF_MAX_PAGES_PER_JOB
    return min(file_total, pages_before_chunk + pages_done), file_total
