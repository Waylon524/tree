"""Local embedding client (OpenAI-compatible /v1/embeddings API).

Works with the Qwen3-Embedding-4B local server (rag.server) or any
OpenAI-compatible embedding endpoint (LM Studio, Ollama, etc.).

Usage:
    from rag.embed import EmbeddingClient

    client = EmbeddingClient()                          # Qwen3-Embedding-4B-Q8_0 on localhost:8788
    client = EmbeddingClient(base_url="http://localhost:1234",
                             model="text-embedding-nomic-embed-text-v1.5")  # LM Studio
    vectors = client.embed(["质点是在研究物体运动时...", "位移是位置的改变量..."])
"""

import json
import logging
import os
import urllib.request

logger = logging.getLogger(__name__)

_DEFAULT_URL = "http://localhost:8788"
_DEFAULT_MODEL = "Qwen3-Embedding-4B-Q8_0"


class EmbeddingClient:
    def __init__(
        self,
        base_url: str | None = None,
        model: str | None = None,
    ):
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

        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read())

        embeddings = sorted(data["data"], key=lambda x: x["index"])
        return [e["embedding"] for e in embeddings]

    @property
    def dimensions(self) -> int:
        if self._dims is None:
            vec = self.embed(["test"])[0]
            self._dims = len(vec)
        return self._dims

    def health_check(self) -> dict:
        try:
            vec = self.embed(["health check"])[0]
            return {"ok": True, "model": self.model, "dimensions": len(vec)}
        except Exception as e:
            return {"ok": False, "error": str(e)}


if __name__ == "__main__":
    client = EmbeddingClient()
    info = client.health_check()
    if info["ok"]:
        print(f"OK: model={info['model']}, dims={info['dimensions']}")
    else:
        print(f"FAILED: {info['error']}")
