"""Local embedding server (llama-cpp-python + FastAPI).

★ INTERFACE UNCHANGED — migrate from previous engine (step 3).
Serves OpenAI-compatible /v1/embeddings + /health on EMBED_PORT (8788).
Global/shared across workspaces (pid/log under ~/.tree/services).
Model Qwen3-Embedding-4B-Q8_0.gguf (auto-download ~4.3GB on first start).
See docs/LEGACY-DESIGN.md §5.1.

TODO (step 3): paste migrated server.
"""

from __future__ import annotations
