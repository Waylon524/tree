"""PaddleOCR-VL API client (async job model).

Workflow: submit job -> poll status -> download JSONL results.
Authentication: Authorization: bearer {TOKEN}

Configuration via environment variables (or .env):
  - PADDLEOCR_API_URL:   Job endpoint URL
  - PADDLEOCR_API_TOKEN: API access token
  - PADDLEOCR_MODEL:     Model name (default: PaddleOCR-VL-1.6)
"""

from __future__ import annotations

import json
import hashlib
import io
import logging
import os
import re
import tempfile
import threading
import time
from contextlib import contextmanager
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from pathlib import Path
from typing import Any, Callable, ClassVar, Iterator

import httpx

logger = logging.getLogger(__name__)
_progress_callback: Callable[[dict[str, Any]], None] | None = None
_job_store_root: Path | None = None
_PDF_READER_LOCK = threading.RLock()

_DEFAULT_JOB_URL = "https://paddleocr.aistudio-app.com/api/v2/ocr/jobs"
_DEFAULT_MODEL = "PaddleOCR-VL-1.6"
_POLL_INTERVAL = 5
_POLL_TIMEOUT = 600
_OCR_CONCURRENCY = 5
_OCR_UPLOAD_INTERVAL_SEC = 5.0
_PDF_MAX_PAGES_PER_JOB = 99
_HTTP_RETRY_ATTEMPTS = 5
_HTTP_RETRY_INITIAL_DELAY = 2.0
_HTTP_RETRY_MAX_DELAY = 30.0
_RETRYABLE_HTTP_STATUS = {408, 425, 429, 500, 502, 503, 504}
_RETRYABLE_API_CODES = {500, 10010, 12002}

_DEFAULT_OPTIONS = {
    "useDocOrientationClassify": True,
    "useDocUnwarping": True,
    "useChartRecognition": True,
    "visualize": False,
}
_MARKDOWN_IMAGE_RE = re.compile(r"!\[[^\]]*\]\([^)]+\)")
_MARKDOWN_IMAGE_REF_RE = re.compile(r"!\[[^\]]*\]\[[^\]]*\]")
_HTML_IMAGE_RE = re.compile(r"<img\b[^>]*>", re.IGNORECASE)
_BARE_IMAGE_URL_RE = re.compile(
    r"^https?://\S+\.(?:png|jpe?g|gif|webp|svg|bmp|tiff?)(?:[?#]\S*)?$",
    re.IGNORECASE,
)
_MARKDOWN_IMAGES_HEADER_RE = re.compile(r"^markdown(?:\.|_)?images\b\s*[:：]?\s*$", re.IGNORECASE)
_IMAGE_LIST_ITEM_RE = re.compile(
    r"^\s*(?:[-*+]\s+)?(?:https?://\S+|[\\/\w .-]+\.(?:png|jpe?g|gif|webp|svg|bmp|tiff?))(?:\s*)$",
    re.IGNORECASE,
)


class PdfCryptoDependencyError(RuntimeError):
    """The runtime cannot decrypt an AES-protected PDF."""


class PdfPasswordRequiredError(RuntimeError):
    """A PDF cannot be opened without a user-supplied password."""


class PdfStreamLimitError(RuntimeError):
    """A PDF declares a stream larger than the local file can safely contain."""


@contextmanager
def _allow_local_pdf_streams(pdf_path: Path) -> Iterator[None]:
    """Allow a legitimate raw PDF stream up to the local file's byte size.

    pypdf intentionally defaults declared streams to 75 MB. Slide decks and
    scanned textbooks can legitimately contain a single embedded resource over
    that limit, which otherwise makes page-level OCR splitting fail. Raising
    only this raw-stream limit to the already-present file size keeps malformed
    declarations larger than the file blocked and leaves all decompression and
    image safety limits unchanged.

    The pypdf limit is process-global, so page inspection/splitting is serialized
    and the original value is restored before another reader can observe it.
    """
    from pypdf import filters

    file_size = max(0, pdf_path.stat().st_size)
    with _PDF_READER_LOCK:
        original_limit = filters.MAX_DECLARED_STREAM_LENGTH
        filters.MAX_DECLARED_STREAM_LENGTH = max(original_limit, file_size)
        try:
            yield
        finally:
            filters.MAX_DECLARED_STREAM_LENGTH = original_limit


def pdf_crypto_runtime_status() -> tuple[bool, str]:
    """Exercise AES write/read through pypdf in the current (possibly frozen) runtime."""
    try:
        from pypdf import PdfReader, PdfWriter
        from pypdf._crypt_providers import crypt_provider

        buffer = io.BytesIO()
        writer = PdfWriter()
        writer.add_blank_page(width=72, height=72)
        writer.encrypt("", algorithm="AES-256")
        writer.write(buffer)
        buffer.seek(0)
        reader = PdfReader(buffer)
        if len(reader.pages) != 1:
            return False, "AES PDF round-trip returned an unexpected page count"
        provider = " ".join(str(item) for item in crypt_provider if str(item)).strip()
        return True, provider or "AES provider ready"
    except Exception as exc:  # noqa: BLE001 - health check must report any frozen-runtime defect
        return False, f"{type(exc).__name__}: {exc}"


class OCREngine:
    """Async-job client for Baidu AI Studio PaddleOCR-VL."""

    _instance: ClassVar[OCREngine | None] = None
    _ocr_semaphore: ClassVar[threading.BoundedSemaphore] = threading.BoundedSemaphore(
        _OCR_CONCURRENCY
    )
    _upload_lock: ClassVar[threading.Lock] = threading.Lock()
    _last_upload_at: ClassVar[float] = 0.0
    _job_url: str
    _token: str
    _model: str
    _poll_interval: float
    _poll_timeout: float
    _pdf_max_pages_per_job: int
    _ocr_concurrency: int
    _upload_interval: float
    _options: dict[str, Any]
    _client: httpx.Client

    def __new__(
        cls,
        job_url: str | None = None,
        token: str | None = None,
        **kwargs: Any,
    ) -> OCREngine:
        if cls._instance is None:
            instance = super().__new__(cls)
            instance._job_url = (job_url or os.environ.get("PADDLEOCR_API_URL", _DEFAULT_JOB_URL)).rstrip("/")
            instance._token = token or os.environ.get("PADDLEOCR_API_TOKEN", "")
            instance._model = kwargs.pop("model", os.environ.get("PADDLEOCR_MODEL", _DEFAULT_MODEL))
            instance._poll_interval = kwargs.pop("poll_interval", _POLL_INTERVAL)
            instance._poll_timeout = kwargs.pop("poll_timeout", _POLL_TIMEOUT)
            instance._pdf_max_pages_per_job = kwargs.pop(
                "pdf_max_pages_per_job",
                int(os.environ.get("SOURCE_OCR_PDF_MAX_PAGES_PER_JOB", _PDF_MAX_PAGES_PER_JOB)),
            )
            instance._ocr_concurrency = max(
                1, kwargs.pop("ocr_concurrency", int(os.environ.get("SOURCE_OCR_CONCURRENCY", _OCR_CONCURRENCY)))
            )
            instance._upload_interval = max(
                0.0,
                float(
                    kwargs.pop(
                        "upload_interval_sec",
                        os.environ.get(
                            "SOURCE_OCR_UPLOAD_INTERVAL_SEC",
                            str(_OCR_UPLOAD_INTERVAL_SEC),
                        ),
                    )
                ),
            )
            instance._options = {**_DEFAULT_OPTIONS, **kwargs.pop("options", {})}
            instance._client = httpx.Client(timeout=kwargs.pop("timeout", 30.0))
            # Publish the singleton only after construction fully succeeds. Assigning
            # cls._instance mid-init would cache a half-built object, so any later
            # failure (e.g. httpx.Client) would surface as a confusing permanent
            # `AttributeError: '_client'` on every request instead of the real cause.
            cls._ocr_semaphore = threading.BoundedSemaphore(instance._ocr_concurrency)
            cls._instance = instance
            logger.info("OCR API client: %s", instance._job_url)
        elif "pdf_max_pages_per_job" in kwargs:
            cls._instance._pdf_max_pages_per_job = kwargs["pdf_max_pages_per_job"]
        elif "ocr_concurrency" in kwargs:
            cls._instance._ocr_concurrency = max(1, kwargs["ocr_concurrency"])
            cls._ocr_semaphore = threading.BoundedSemaphore(cls._instance._ocr_concurrency)
        assert cls._instance is not None
        return cls._instance

    def _headers(self, content_type: str | None = None) -> dict[str, str]:
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
            return self._download_result(jsonl_url, context)

        local_path = Path(path)
        if local_path.suffix.lower() == ".pdf":
            return self._ocr_local_pdf(local_path)
        return self._ocr_single_local(local_path)

    def _ocr_local_pdf(self, pdf_path: Path) -> str:
        """OCR a local PDF, splitting files over the API page limit."""
        page_count = self._pdf_page_count(pdf_path)
        source_sha256 = _file_sha256(pdf_path)
        max_pages = self._pdf_max_pages_per_job
        if page_count <= max_pages:
            return self._ocr_single_local(
                pdf_path,
                {
                    "current_file": pdf_path.name,
                    "current_chunk": pdf_path.name,
                    "chunk_index": 1,
                    "chunks_total": 1,
                    "file_pages_total": page_count,
                    "source_sha256": source_sha256,
                },
            )

        logger.info(
            "PDF has %d pages; splitting into chunks of <=%d pages before OCR: %s",
            page_count,
            max_pages,
            pdf_path.name,
        )
        with tempfile.TemporaryDirectory(prefix="tree-pdf-split-") as temp_dir:
            chunk_paths = self._split_pdf(pdf_path, Path(temp_dir), max_pages)
            jobs = []
            for index, chunk_path in enumerate(chunk_paths, start=1):
                chunk_pages = self._pdf_page_count(chunk_path)
                jobs.append(
                    (
                        chunk_path,
                        {
                            "current_file": pdf_path.name,
                            "current_chunk": chunk_path.name,
                            "chunk_index": index,
                            "chunks_total": len(chunk_paths),
                            "file_pages_total": page_count,
                            "chunk_pages_total": chunk_pages,
                            "pdf_max_pages_per_job": max_pages,
                            "source_sha256": source_sha256,
                        },
                    )
                )
            parts = self._ocr_pdf_chunks(jobs)
        return clean_ocr_markdown_text("\n\n".join(parts))

    def _ocr_pdf_chunks(self, jobs: list[tuple[Path, dict[str, Any]]]) -> list[str]:
        """OCR split PDF chunks with bounded file-level concurrency."""
        if not jobs:
            return []

        results: list[str | None] = [None] * len(jobs)
        pending = list(range(len(jobs)))
        running: dict[Future[str], int] = {}
        retry_counts = {index: 0 for index in pending}
        wait_for_success_before_retry = False

        def submit_available(executor: ThreadPoolExecutor) -> None:
            nonlocal wait_for_success_before_retry
            while (
                pending
                and len(running) < self._ocr_concurrency
                and not wait_for_success_before_retry
            ):
                index = pending.pop(0)
                chunk_path, context = jobs[index]
                logger.info(
                    "OCR-ing PDF chunk %d/%d: %s",
                    context.get("chunk_index", index + 1),
                    context.get("chunks_total", len(jobs)),
                    chunk_path.name,
                )
                running[executor.submit(self._ocr_single_local, chunk_path, context)] = index

        with ThreadPoolExecutor(max_workers=self._ocr_concurrency) as executor:
            submit_available(executor)
            while running:
                done, _ = wait(running, return_when=FIRST_COMPLETED)
                saw_success = False
                for future in done:
                    index = running.pop(future)
                    try:
                        results[index] = future.result()
                        saw_success = True
                    except Exception as exc:
                        if not _is_ocr_concurrency_error(exc):
                            raise
                        retry_counts[index] += 1
                        if retry_counts[index] > _HTTP_RETRY_ATTEMPTS:
                            raise
                        pending.insert(0, index)
                        if running:
                            wait_for_success_before_retry = True

                if saw_success:
                    wait_for_success_before_retry = False
                if not running and pending:
                    wait_for_success_before_retry = False
                submit_available(executor)

        missing = [index + 1 for index, text in enumerate(results) if not text or not text.strip()]
        if missing:
            raise RuntimeError(f"OCR returned empty or missing PDF chunks: {missing}")
        return [str(text) for text in results]

    def _ocr_single_local(self, path: Path, context: dict[str, Any] | None = None) -> str:
        """OCR one local file without additional splitting."""
        with self._ocr_semaphore:
            return self._ocr_single_local_unlocked(path, context)

    def _ocr_single_local_unlocked(self, path: Path, context: dict[str, Any] | None = None) -> str:
        """OCR one local file after the file-level concurrency gate is acquired."""
        context = {
            "current_file": path.name,
            "current_chunk": path.name,
            "chunk_index": 1,
            "chunks_total": 1,
            **(context or {}),
        }
        context.setdefault("source_sha256", _file_sha256(path))
        checkpoint = self._load_job_checkpoint(context)
        result_path = self._job_result_path(context)
        if checkpoint.get("status") == "downloaded" and result_path and result_path.exists():
            text = result_path.read_text(encoding="utf-8")
            if text.strip() and _text_sha256(text) == checkpoint.get("result_sha256"):
                _emit_progress({"state": "reused_result", **context})
                return text

        job_id = str(checkpoint.get("job_id") or "")
        jsonl_url = str(checkpoint.get("result_url") or "")
        if not job_id or checkpoint.get("status") == "failed":
            job_id = self._submit_local(str(path))
            self._save_job_checkpoint(context, status="submitted", job_id=job_id)
            _emit_progress({"state": "submitted", "job_id": job_id, **context})
            logger.info("Job submitted: %s", job_id)
        if not jsonl_url:
            try:
                jsonl_url = self._poll(job_id, context)
            except Exception as exc:
                # A remote terminal failure is safe to resubmit next run; a
                # timeout/network interruption retains the job id for polling.
                self._save_job_checkpoint(
                    context,
                    status="failed" if isinstance(exc, RuntimeError) else "submitted",
                    job_id=job_id,
                )
                raise
            self._save_job_checkpoint(
                context,
                status="result_ready",
                job_id=job_id,
                result_url=jsonl_url,
            )
        text = self._download_result(jsonl_url, context)
        if result_path is not None:
            result_path.parent.mkdir(parents=True, exist_ok=True)
            result_path.write_text(text, encoding="utf-8")
        self._save_job_checkpoint(
            context,
            status="downloaded",
            job_id=job_id,
            result_url=jsonl_url,
            result_sha256=_text_sha256(text),
        )
        return text

    def _job_checkpoint_path(self, context: dict[str, Any]) -> Path | None:
        if _job_store_root is None:
            return None
        source_sha256 = str(context.get("source_sha256") or "").removeprefix("sha256:")
        if not source_sha256:
            return None
        options_hash = hashlib.sha256(
            json.dumps(
                {"endpoint": self._job_url, "model": self._model, "options": self._options},
                sort_keys=True,
                ensure_ascii=False,
            ).encode("utf-8")
        ).hexdigest()[:12]
        chunk_index = int(context.get("chunk_index") or 1)
        return _job_store_root / source_sha256 / f"chunk-{chunk_index:04d}-{options_hash}.json"

    def _job_result_path(self, context: dict[str, Any]) -> Path | None:
        checkpoint = self._job_checkpoint_path(context)
        return checkpoint.with_suffix(".md") if checkpoint is not None else None

    def _load_job_checkpoint(self, context: dict[str, Any]) -> dict[str, Any]:
        path = self._job_checkpoint_path(context)
        if path is None or not path.exists():
            return {}
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return loaded if isinstance(loaded, dict) else {}

    def _save_job_checkpoint(self, context: dict[str, Any], **changes: Any) -> None:
        path = self._job_checkpoint_path(context)
        if path is None:
            return
        from tree.planner.store import write_json_atomic

        data = self._load_job_checkpoint(context)
        data.update(
            {
                "schema_version": 1,
                "source_sha256": context.get("source_sha256", ""),
                "chunk_index": int(context.get("chunk_index") or 1),
                "chunks_total": int(context.get("chunks_total") or 1),
                "chunk_pages_total": context.get("chunk_pages_total"),
                "model": self._model,
                **changes,
            }
        )
        write_json_atomic(path, data)

    @staticmethod
    def _pdf_page_count(pdf_path: Path) -> int:
        try:
            from pypdf import PdfReader
            from pypdf.errors import (
                DependencyError,
                FileNotDecryptedError,
                LimitReachedError,
                WrongPasswordError,
            )
        except ImportError as exc:
            raise RuntimeError(
                "PDF page counting and splitting requires pypdf. "
                "Reinstall tree with current dependencies."
            ) from exc

        try:
            with _allow_local_pdf_streams(pdf_path):
                reader = PdfReader(str(pdf_path))
                return len(reader.pages)
        except DependencyError as exc:
            raise PdfCryptoDependencyError(
                f"Cannot read AES-encrypted PDF '{pdf_path.name}': the TREE runtime is missing "
                "pypdf crypto support. Reinstall or update TREE."
            ) from exc
        except (FileNotDecryptedError, WrongPasswordError) as exc:
            raise PdfPasswordRequiredError(
                f"Encrypted PDF '{pdf_path.name}' requires a password. Remove the password "
                "protection or import an unlocked copy."
            ) from exc
        except LimitReachedError as exc:
            raise PdfStreamLimitError(
                f"PDF '{pdf_path.name}' contains a declared stream larger than the file itself. "
                "Export or print it to a repaired PDF, then import the repaired copy."
            ) from exc

    @staticmethod
    def _split_pdf(pdf_path: Path, output_dir: Path, max_pages: int) -> list[Path]:
        try:
            from pypdf import PdfReader, PdfWriter
            from pypdf.errors import (
                DependencyError,
                FileNotDecryptedError,
                LimitReachedError,
                WrongPasswordError,
            )
        except ImportError as exc:
            raise RuntimeError(
                "PDF page counting and splitting requires pypdf. "
                "Reinstall tree with current dependencies."
            ) from exc

        try:
            with _allow_local_pdf_streams(pdf_path):
                reader = PdfReader(str(pdf_path))
                output_dir.mkdir(parents=True, exist_ok=True)
                chunk_paths = []
                total_pages = len(reader.pages)
                for start in range(0, total_pages, max_pages):
                    writer = PdfWriter()
                    end = min(start + max_pages, total_pages)
                    for page_index in range(start, end):
                        writer.add_page(reader.pages[page_index])
                    chunk_path = (
                        output_dir / f"{pdf_path.stem}__pages-{start + 1:04d}-{end:04d}.pdf"
                    )
                    with chunk_path.open("wb") as file:
                        writer.write(file)
                    chunk_paths.append(chunk_path)
                return chunk_paths
        except DependencyError as exc:
            raise PdfCryptoDependencyError(
                f"Cannot split AES-encrypted PDF '{pdf_path.name}': the TREE runtime is missing "
                "pypdf crypto support. Reinstall or update TREE."
            ) from exc
        except (FileNotDecryptedError, WrongPasswordError) as exc:
            raise PdfPasswordRequiredError(
                f"Encrypted PDF '{pdf_path.name}' requires a password. Remove the password "
                "protection or import an unlocked copy."
            ) from exc
        except LimitReachedError as exc:
            raise PdfStreamLimitError(
                f"PDF '{pdf_path.name}' contains a declared stream larger than the file itself. "
                "Export or print it to a repaired PDF, then import the repaired copy."
            ) from exc

    def _submit_url(self, file_url: str) -> str:
        """Submit job with a file URL (JSON body)."""
        payload = {"fileUrl": file_url, "model": self._model, "optionalPayload": self._options}
        resp = self._request_with_retries(
            "submit URL OCR job",
            lambda: self._client.post(
                self._job_url, json=payload, headers=self._headers("application/json")
            ),
            check_api_code=True,
        )
        return _api_data(resp)["jobId"]

    def _submit_local(self, file_path: str) -> str:
        """Submit job with a local file (multipart upload)."""
        data = {"model": self._model, "optionalPayload": json.dumps(self._options)}

        def post_file() -> httpx.Response:
            with open(file_path, "rb") as f:
                files = {"file": (Path(file_path).name, f)}
                return self._client.post(self._job_url, headers=self._headers(), data=data, files=files)

        self._wait_for_upload_slot()
        resp = self._request_with_retries("submit local OCR job", post_file, check_api_code=True)
        return _api_data(resp)["jobId"]

    def _wait_for_upload_slot(self) -> None:
        if self._upload_interval <= 0:
            return
        with self._upload_lock:
            remaining = self._upload_interval - (time.monotonic() - self._last_upload_at)
            if remaining > 0:
                time.sleep(remaining)
            type(self)._last_upload_at = time.monotonic()

    def _poll(self, job_id: str, context: dict[str, Any] | None = None) -> str:
        """Poll job status until done, return JSONL result URL."""
        context = context or {}
        deadline = time.time() + self._poll_timeout
        while time.time() < deadline:
            resp = self._request_with_retries(
                "poll OCR job",
                lambda: self._client.get(f"{self._job_url}/{job_id}", headers=self._headers()),
                check_api_code=True,
                progress_context={"job_id": job_id, **context},
            )
            body = _api_data(resp)
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
                    _int_or_none(done), _int_or_none(total), context
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

    def _download_result(self, jsonl_url: str, context: dict[str, Any] | None = None) -> str:
        """Download JSONL result and extract merged markdown text."""
        context = context or {}
        _emit_progress({**context, "state": "downloading_result"})
        resp = self._request_with_retries(
            "download OCR result",
            lambda: self._client.get(jsonl_url),
            check_api_code=False,
            progress_context=context,
        )
        resp.raise_for_status()
        parts = []
        line_count = 0
        page_count = 0
        for line in resp.text.strip().splitlines():
            line = line.strip()
            if not line:
                continue
            line_count += 1
            result = json.loads(line)["result"]
            for page in result["layoutParsingResults"]:
                page_count += 1
                md = page.get("markdown", {})
                text = md.get("text", "") if isinstance(md, dict) else ""
                if text:
                    parts.append(text)
        text = clean_ocr_markdown_text("\n\n".join(parts))
        expected_pages = _int_or_none(
            context.get("chunk_pages_total") or context.get("file_pages_total")
        )
        if expected_pages is not None and page_count != expected_pages:
            raise RuntimeError(
                f"OCR result is incomplete for {context.get('current_chunk', 'document')}: "
                f"expected {expected_pages} pages, received {page_count}"
            )
        if not text.strip():
            raise RuntimeError(
                f"OCR result contained no usable text for {context.get('current_chunk', 'document')}"
            )
        _emit_progress(
            {
                **context,
                "state": "downloaded_result",
                "jsonl_lines": line_count,
                "result_pages": page_count,
                "text_chars": len(text),
            }
        )
        return text

    def close(self) -> None:
        client = getattr(self, "_client", None)
        if client is not None:
            client.close()

    def _request_with_retries(
        self,
        action: str,
        send: Callable[[], httpx.Response],
        *,
        check_api_code: bool,
        progress_context: dict[str, Any] | None = None,
    ) -> httpx.Response:
        delay = _HTTP_RETRY_INITIAL_DELAY
        last_error: Exception | None = None
        for attempt in range(1, _HTTP_RETRY_ATTEMPTS + 1):
            try:
                resp = send()
                if _should_retry_response(resp, check_api_code):
                    last_error = _response_error(resp)
                    if attempt == _HTTP_RETRY_ATTEMPTS:
                        break
                    _emit_retry_progress(action, attempt, delay, progress_context)
                    logger.warning(
                        "PaddleOCR %s retry %d/%d after response %s",
                        action,
                        attempt,
                        _HTTP_RETRY_ATTEMPTS,
                        _response_summary(resp),
                    )
                    time.sleep(delay)
                    delay = min(delay * 2, _HTTP_RETRY_MAX_DELAY)
                    continue
                return resp
            except httpx.RequestError as exc:
                last_error = exc
                if attempt == _HTTP_RETRY_ATTEMPTS:
                    break
                _emit_retry_progress(action, attempt, delay, progress_context)
                logger.warning(
                    "PaddleOCR %s retry %d/%d after network error: %s",
                    action,
                    attempt,
                    _HTTP_RETRY_ATTEMPTS,
                    exc,
                )
                time.sleep(delay)
                delay = min(delay * 2, _HTTP_RETRY_MAX_DELAY)

        assert last_error is not None
        raise last_error

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass


def get_engine(
    job_url: str | None = None,
    token: str | None = None,
    **kwargs: Any,
) -> OCREngine:
    """Get or create the singleton OCR engine."""
    return OCREngine(job_url=job_url, token=token, **kwargs)


def set_progress_callback(callback: Callable[[dict[str, Any]], None] | None) -> None:
    global _progress_callback
    _progress_callback = callback


def set_job_store_root(root: Path | None) -> None:
    """Configure durable OCR job checkpoints for the active workspace."""
    global _job_store_root
    _job_store_root = Path(root) if root is not None else None
    if _job_store_root is not None:
        _job_store_root.mkdir(parents=True, exist_ok=True)


def clean_ocr_markdown_text(text: str) -> str:
    """Remove OCR image artifacts while preserving ordinary Markdown links."""
    cleaned_lines: list[str] = []
    skipping_markdown_images = False
    for line in text.splitlines():
        stripped = line.strip()
        if _MARKDOWN_IMAGES_HEADER_RE.match(stripped):
            skipping_markdown_images = True
            continue
        if skipping_markdown_images:
            if not stripped or _IMAGE_LIST_ITEM_RE.match(stripped):
                continue
            skipping_markdown_images = False

        line = _MARKDOWN_IMAGE_RE.sub("", line)
        line = _MARKDOWN_IMAGE_REF_RE.sub("", line)
        line = _HTML_IMAGE_RE.sub("", line)
        if not line.strip():
            cleaned_lines.append("")
            continue
        if _BARE_IMAGE_URL_RE.match(line.strip()):
            continue
        cleaned_lines.append(line.rstrip())

    cleaned = "\n".join(cleaned_lines)
    return re.sub(r"\n{3,}", "\n\n", cleaned).strip()


def _emit_progress(event: dict[str, Any]) -> None:
    if _progress_callback is None:
        return
    try:
        _progress_callback(event)
    except Exception:
        logger.debug("OCR progress callback failed", exc_info=True)


def _emit_retry_progress(
    action: str, attempt: int, delay: float, context: dict[str, Any] | None
) -> None:
    _emit_progress(
        {
            "state": "retrying",
            "retry_action": action,
            "retry_attempt": attempt,
            "retry_delay_sec": delay,
            **(context or {}),
        }
    )


def _should_retry_response(resp: httpx.Response, check_api_code: bool) -> bool:
    if resp.status_code in _RETRYABLE_HTTP_STATUS:
        return True
    if not check_api_code:
        return False
    code = _api_code(resp)
    return code in _RETRYABLE_API_CODES


def _api_data(resp: httpx.Response) -> dict[str, Any]:
    if resp.status_code >= 400:
        resp.raise_for_status()
    body = resp.json()
    code = body.get("code", 0)
    if code not in (0, None):
        raise RuntimeError(f"PaddleOCR API error {code}: {body.get('msg', 'unknown error')}")
    data = body.get("data")
    if not isinstance(data, dict):
        raise RuntimeError("PaddleOCR API response missing data object")
    return data


def _api_code(resp: httpx.Response) -> int | None:
    try:
        body = resp.json()
    except ValueError:
        return None
    code = body.get("code") if isinstance(body, dict) else None
    return _int_or_none(code)


def _response_error(resp: httpx.Response) -> Exception:
    if resp.status_code >= 400:
        try:
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            return exc
    code = _api_code(resp)
    try:
        body = resp.json()
    except ValueError:
        body = {}
    msg = body.get("msg", "retryable PaddleOCR API response") if isinstance(body, dict) else ""
    return RuntimeError(f"PaddleOCR API retryable response {code}: {msg}")


def _response_summary(resp: httpx.Response) -> str:
    code = _api_code(resp)
    if code is None:
        return f"HTTP {resp.status_code}"
    return f"HTTP {resp.status_code}, API code {code}"


def _is_ocr_concurrency_error(exc: Exception) -> bool:
    status_code = getattr(exc, "status_code", None)
    response = getattr(exc, "response", None)
    if status_code == 429 or getattr(response, "status_code", None) == 429:
        return True
    name = exc.__class__.__name__.lower()
    if "ratelimit" in name or "rate_limit" in name:
        return True
    message = str(exc).lower()
    markers = (
        "too many",
        "rate limit",
        "rate_limit",
        "concurrent",
        "concurrency",
        "overload",
        "server busy",
        "429",
        "并发",
        "限流",
    )
    return any(marker in message for marker in markers)


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _text_sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _progress_pages(
    pages_done: int | None, pages_total: int | None, context: dict[str, Any]
) -> tuple[int | None, int | None]:
    chunks_total = _int_or_none(context.get("chunks_total")) or 1
    if chunks_total <= 1:
        return pages_done, pages_total

    file_total = _int_or_none(context.get("file_pages_total"))
    chunk_index = _int_or_none(context.get("chunk_index")) or 1
    if file_total is None or pages_done is None:
        return pages_done, pages_total

    max_pages = _int_or_none(context.get("pdf_max_pages_per_job")) or _PDF_MAX_PAGES_PER_JOB
    pages_before_chunk = max(0, chunk_index - 1) * max_pages
    return min(file_total, pages_before_chunk + pages_done), file_total
