"""Local embedding client (OpenAI-compatible /v1/embeddings API).

Works with the Qwen3 Embedding local server (tree.rag.server) or any
OpenAI-compatible embedding endpoint.
"""

import json
import logging
import os
import urllib.error
import urllib.request

logger = logging.getLogger(__name__)

_DEFAULT_URL = "http://localhost:8788"
_DEFAULT_MODEL = "Qwen3-Embedding-0.6B-Q8_0"


class EmbeddingClient:
    def __init__(self, base_url: str | None = None, model: str | None = None):
        self.base_url = (base_url or os.environ.get("EMBED_API_URL", _DEFAULT_URL)).rstrip("/")
        self.model = model or os.environ.get("EMBED_MODEL", _DEFAULT_MODEL)
        self._dims: int | None = None

    def embed(self, texts: str | list[str]) -> list[list[float]]:
        if isinstance(texts, str):
            texts = [texts]

        payload = json.dumps({"model": self.model, "input": texts}).encode()
        req = urllib.request.Request(
            f"{self.base_url}/v1/embeddings",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req) as resp:
                data = json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            body = exc.read(2048).decode("utf-8", errors="replace")
            max_chars = max((len(text) for text in texts), default=0)
            raise RuntimeError(
                "Embedding request failed "
                f"(HTTP {exc.code}, model={self.model}, endpoint={self.base_url}/v1/embeddings, "
                f"inputs={len(texts)}, max_chars={max_chars}): {body or exc.reason}"
            ) from exc

        embeddings = sorted(data["data"], key=lambda x: x["index"])
        return [e["embedding"] for e in embeddings]

    @property
    def dimensions(self) -> int:
        if self._dims is None:
            self._dims = len(self.embed(["test"])[0])
        return self._dims

    def health_check(self) -> dict:
        try:
            vec = self.embed(["health check"])[0]
            return {"ok": True, "model": self.model, "dimensions": len(vec)}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": str(e)}


if __name__ == "__main__":
    info = EmbeddingClient().health_check()
    if info["ok"]:
        print(f"OK: model={info['model']}, dims={info['dimensions']}")
    else:
        print(f"FAILED: {info['error']}")
