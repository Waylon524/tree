"""Local embedding client (OpenAI-compatible /v1/embeddings API).

Works with the Qwen3 Embedding local server (tree.rag.server) or any
OpenAI-compatible embedding endpoint.
"""

import json
import logging
import os
import urllib.error
import urllib.request
from typing import Any
from urllib.parse import urlparse

from tree.config import DEFAULT_EMBED_REQUEST_TIMEOUT_SEC

logger = logging.getLogger(__name__)

_DEFAULT_URL = "http://localhost:8788"
_DEFAULT_MODEL = "Qwen3-Embedding-0.6B-Q8_0"


class EmbeddingClient:
    def __init__(
        self,
        base_url: str | None = None,
        model: str | None = None,
        timeout_sec: float | None = None,
    ):
        self.base_url = (base_url or os.environ.get("EMBED_API_URL", _DEFAULT_URL)).rstrip("/")
        self.model = model or os.environ.get("EMBED_MODEL", _DEFAULT_MODEL)
        configured_timeout = os.environ.get(
            "EMBED_REQUEST_TIMEOUT_SEC", str(DEFAULT_EMBED_REQUEST_TIMEOUT_SEC)
        )
        self.timeout_sec = float(timeout_sec if timeout_sec is not None else configured_timeout)
        self._dims: int | None = None

    def embed(self, texts: str | list[str]) -> list[list[float]]:
        if isinstance(texts, str):
            texts = [texts]

        if not texts:
            return []

        # The TREE-managed llama-server processes embedding inputs serially. Send
        # existing physical chunks as separate requests so each keeps the full
        # timeout budget; do not alter chunk boundaries or token limits here.
        if self._is_local_endpoint() and len(texts) > 1:
            embeddings: list[list[float]] = []
            for index, text in enumerate(texts):
                embeddings.extend(
                    self._request_embeddings([text], input_index=index, input_total=len(texts))
                )
            return embeddings

        return self._request_embeddings(texts, input_index=0, input_total=len(texts))

    def _request_embeddings(
        self,
        texts: list[str],
        *,
        input_index: int,
        input_total: int,
    ) -> list[list[float]]:
        max_chars = max((len(text) for text in texts), default=0)

        payload = json.dumps({"model": self.model, "input": texts}).encode()
        req = urllib.request.Request(
            f"{self.base_url}/v1/embeddings",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_sec) as resp:
                data = json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            body = exc.read(2048).decode("utf-8", errors="replace")
            raise RuntimeError(
                "Embedding request failed "
                f"(HTTP {exc.code}, model={self.model}, endpoint={self.base_url}/v1/embeddings, "
                f"inputs={len(texts)}, max_chars={max_chars}): {body or exc.reason}"
            ) from exc
        except (TimeoutError, urllib.error.URLError) as exc:
            if not _is_timeout_error(exc):
                raise
            segment = (
                f", segment={input_index + 1}/{input_total}"
                if input_total > 1
                else ""
            )
            raise RuntimeError(
                "Embedding request timed out "
                f"after {self.timeout_sec:g}s (model={self.model}, "
                f"endpoint={self.base_url}/v1/embeddings{segment}, "
                f"inputs={len(texts)}, max_chars={max_chars}). "
                "The local server may still be finishing the request; wait for it to become "
                "idle before resuming, or increase EMBED_REQUEST_TIMEOUT_SEC."
            ) from exc

        embeddings = sorted(data["data"], key=lambda x: x["index"])
        return [e["embedding"] for e in embeddings]

    def _is_local_endpoint(self) -> bool:
        host = (urlparse(self.base_url).hostname or "").lower()
        return host in {"localhost", "127.0.0.1", "::1"}

    @property
    def dimensions(self) -> int:
        if self._dims is None:
            self._dims = len(self.embed(["test"])[0])
        return self._dims

    def health_check(self) -> dict[str, Any]:
        try:
            vec = self.embed(["health check"])[0]
            return {"ok": True, "model": self.model, "dimensions": len(vec)}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": str(e)}


def _is_timeout_error(exc: BaseException) -> bool:
    if isinstance(exc, TimeoutError):
        return True
    reason = getattr(exc, "reason", None)
    return isinstance(reason, TimeoutError)


if __name__ == "__main__":
    info = EmbeddingClient().health_check()
    if info["ok"]:
        print(f"OK: model={info['model']}, dims={info['dimensions']}")
    else:
        print(f"FAILED: {info['error']}")
