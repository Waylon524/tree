"""Local Qwen3-Embedding-4B embedding server (OpenAI-compatible API).

Loads Qwen3-Embedding-4B-Q8_0 (GGUF) via llama-cpp-python and serves
/v1/embeddings + /health on EMBED_PORT (default 8788).

    python -m tree.rag.server                 # 0.0.0.0:8788, all GPU layers
    python -m tree.rag.server --n-gpu-layers 0  # CPU only
"""

import argparse
import asyncio
import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from llama_cpp import Llama
from llama_cpp import llama as llama_module
from pydantic import BaseModel, Field

from tree.rag.model_cache import (
    GGUF_FILE,
    HF_REPO,
    MODEL_NAME,
    ensure_embedding_model,
    resolve_embedding_model_path,
)

logger = logging.getLogger(__name__)

_DEFAULT_PORT = 8788
_DEFAULT_N_CTX = 32768
_DEFAULT_N_SEQ_MAX = 1


class EmbedRequest(BaseModel):
    model: str = Field(default=MODEL_NAME)
    input: str | list[str]


_model: Llama | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _model
    n_gpu = app.state.n_gpu_layers
    n_ctx = app.state.n_ctx
    n_seq_max = app.state.n_seq_max
    logger.info(
        "Loading %s/%s (n_gpu_layers=%d, n_ctx=%d, n_seq_max=%d)...",
        HF_REPO, GGUF_FILE, n_gpu, n_ctx, n_seq_max,
    )
    t0 = time.time()
    _model = _load_llama_model(n_gpu_layers=n_gpu, n_ctx=n_ctx, n_seq_max=n_seq_max)
    logger.info("Model loaded in %.1fs", time.time() - t0)
    yield
    _model = None


app = FastAPI(title="Qwen3-Embedding-4B Server", lifespan=lifespan)


@app.get("/v1/models")
async def list_models():
    return {"object": "list", "data": [{"id": MODEL_NAME, "object": "model", "owned": "local"}]}


@app.post("/v1/embeddings")
async def create_embeddings(req: EmbedRequest):
    if _model is None:
        return JSONResponse(status_code=503, content={"error": "Model not loaded"})

    texts = req.input if isinstance(req.input, list) else [req.input]
    t0 = time.time()
    result = await asyncio.to_thread(_create_embedding_response, texts)
    logger.info("Embedded %d texts in %.3fs", len(texts), time.time() - t0)
    return result


@app.get("/health")
async def health():
    return {"status": "ok", "model": MODEL_NAME, "loaded": _model is not None}


def _create_embedding_response(texts: list[str]) -> dict[str, Any]:
    if _model is None:
        raise RuntimeError("Model not loaded")

    data = []
    usage: dict[str, int] = {}
    model_name = MODEL_NAME
    for index, text in enumerate(texts):
        result = _model.create_embedding(text)
        model_name = result.get("model", model_name)
        item = dict(result["data"][0])
        item["index"] = index
        data.append(item)
        for key, value in (result.get("usage") or {}).items():
            if isinstance(value, int):
                usage[key] = usage.get(key, 0) + value

    return {"object": "list", "data": data, "model": model_name, "usage": usage}


def _load_llama_model(n_gpu_layers: int, n_ctx: int, n_seq_max: int) -> Llama:
    """Load the embedding model while constraining llama.cpp parallel sequences."""
    original = llama_module.llama_cpp.llama_max_parallel_sequences
    if n_seq_max < 1:
        raise ValueError("n_seq_max must be >= 1")

    model_path = _resolve_model_path()
    kwargs = {
        "embedding": True,
        "n_gpu_layers": n_gpu_layers,
        "n_ctx": n_ctx,
        "n_batch": n_ctx,
        "verbose": False,
    }
    try:
        llama_module.llama_cpp.llama_max_parallel_sequences = lambda: n_seq_max
        if model_path is not None:
            logger.info("Loading embedding model from local file: %s", model_path)
            return Llama(model_path=str(model_path), **kwargs)
        model = ensure_embedding_model()
        logger.info("Loading embedding model from resolved file: %s", model.path)
        return Llama(model_path=str(model.path), **kwargs)
    finally:
        llama_module.llama_cpp.llama_max_parallel_sequences = original


def _resolve_model_path() -> Path | None:
    return resolve_embedding_model_path()


def main():
    parser = argparse.ArgumentParser(description="Qwen3-Embedding-4B local embedding server")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=_DEFAULT_PORT)
    parser.add_argument("--n-gpu-layers", type=int, default=-1, help="GPU layers: -1=all, 0=CPU only")
    parser.add_argument("--n-ctx", type=int, default=_DEFAULT_N_CTX)
    parser.add_argument("--n-seq-max", type=int, default=_DEFAULT_N_SEQ_MAX)
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s: %(message)s",
    )
    app.state.n_gpu_layers = args.n_gpu_layers
    app.state.n_ctx = args.n_ctx
    app.state.n_seq_max = args.n_seq_max
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
