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
import time
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

_DEFAULT_JOB_URL = "https://paddleocr.aistudio-app.com/api/v2/ocr/jobs"
_DEFAULT_MODEL = "PaddleOCR-VL-1.6"
_POLL_INTERVAL = 5
_POLL_TIMEOUT = 600

_DEFAULT_OPTIONS = {
    "useDocOrientationClassify": False,
    "useDocUnwarping": False,
    "useChartRecognition": False,
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
        else:
            job_id = self._submit_local(path)
        logger.info("Job submitted: %s", job_id)
        jsonl_url = self._poll(job_id)
        return self._download_result(jsonl_url)

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

    def _poll(self, job_id: str) -> str:
        """Poll job status until done, return JSONL result URL."""
        deadline = time.time() + self._poll_timeout
        while time.time() < deadline:
            resp = self._client.get(f"{self._job_url}/{job_id}", headers=self._headers())
            resp.raise_for_status()
            body = resp.json()["data"]
            state = body["state"]

            if state == "done":
                pages = body.get("extractProgress", {}).get("extractedPages", "?")
                logger.info("Job done: %s pages extracted", pages)
                return body["resultUrl"]["jsonUrl"]

            if state == "failed":
                raise RuntimeError(f"OCR job failed: {body.get('errorMsg', 'unknown')}")

            if state == "running":
                prog = body.get("extractProgress", {})
                total = prog.get("totalPages", "?")
                done = prog.get("extractedPages", "?")
                logger.debug("Job running: %s/%s pages", done, total)
            else:
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
