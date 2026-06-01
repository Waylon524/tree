"""Embedding client for the local Qwen3-Embedding-4B service.

★ INTERFACE UNCHANGED — migrate from previous engine (step 3).
OpenAI-compatible POST /v1/embeddings at EMBED_API_URL (default :8788),
model Qwen3-Embedding-4B-Q8_0. See docs/LEGACY-DESIGN.md §5.1.

    class EmbeddingClient:
        def embed(self, texts: str | list[str]) -> list[list[float]]
"""

from __future__ import annotations


class EmbeddingClient:
    def __init__(self, base_url: str | None = None, model: str | None = None):
        raise NotImplementedError("EmbeddingClient — migrate in step 3")
