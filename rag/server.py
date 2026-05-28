"""Local Qwen3-Embedding-4B embedding server (OpenAI-compatible API).

Loads Qwen3-Embedding-4B-Q8_0 (GGUF) via llama-cpp-python and serves /v1/embeddings.

Usage:
    python -m rag.server                        # default: 0.0.0.0:8788, all GPU layers
    python -m rag.server --port 8080            # custom port
    python -m rag.server --n-gpu-layers 0       # CPU only
    python -m rag.server --n-gpu-layers -1      # all layers on GPU (default)
"""

import argparse
import asyncio
import logging
import time
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from llama_cpp import Llama
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

_HF_REPO = "Qwen/Qwen3-Embedding-4B-GGUF"
_GGUF_FILE = "Qwen3-Embedding-4B-Q8_0.gguf"
_MODEL_NAME = "Qwen3-Embedding-4B-Q8_0"
_DEFAULT_PORT = 8788


class EmbedRequest(BaseModel):
    model: str = Field(default=_MODEL_NAME)
    input: str | list[str]


_model: Llama | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _model
    n_gpu = app.state.n_gpu_layers
    logger.info("Loading %s/%s (n_gpu_layers=%d)...", _HF_REPO, _GGUF_FILE, n_gpu)
    t0 = time.time()
    _model = Llama.from_pretrained(
        repo_id=_HF_REPO,
        filename=_GGUF_FILE,
        embedding=True,
        n_gpu_layers=n_gpu,
        verbose=False,
    )
    logger.info("Model loaded in %.1fs", time.time() - t0)
    yield
    _model = None


app = FastAPI(title="Qwen3-Embedding-4B Server", lifespan=lifespan)


@app.get("/v1/models")
async def list_models():
    return {"object": "list", "data": [{"id": _MODEL_NAME, "object": "model", "owned": "local"}]}


@app.post("/v1/embeddings")
async def create_embeddings(req: EmbedRequest):
    if _model is None:
        return JSONResponse(status_code=503, content={"error": "Model not loaded"})

    texts = req.input if isinstance(req.input, list) else [req.input]
    t0 = time.time()
    result = await asyncio.to_thread(_model.create_embedding, texts)
    elapsed = time.time() - t0

    logger.info("Embedded %d texts in %.3fs", len(texts), elapsed)
    return result


@app.get("/health")
async def health():
    return {"status": "ok", "model": _MODEL_NAME, "loaded": _model is not None}


def main():
    parser = argparse.ArgumentParser(description="Qwen3-Embedding-4B local embedding server")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=_DEFAULT_PORT)
    parser.add_argument("--n-gpu-layers", type=int, default=-1, help="GPU layers: -1=all, 0=CPU only")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s: %(message)s",
    )
    app.state.n_gpu_layers = args.n_gpu_layers
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
