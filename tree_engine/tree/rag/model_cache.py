"""Qwen3 embedding model discovery and first-run download."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

HF_REPO = "Qwen/Qwen3-Embedding-0.6B-GGUF"
GGUF_FILE = "Qwen3-Embedding-0.6B-Q8_0.gguf"
MODEL_NAME = "Qwen3-Embedding-0.6B-Q8_0"


class EmbeddingModelError(RuntimeError):
    """Raised when the local embedding model cannot be resolved."""


@dataclass(frozen=True)
class EmbeddingModel:
    path: Path
    source: str


def ensure_embedding_model() -> EmbeddingModel:
    env_path = _env_model_path()
    if env_path is not None:
        return EmbeddingModel(path=env_path, source="env")

    cached = _try_hf_cache()
    if cached is not None:
        return EmbeddingModel(path=cached, source="huggingface-cache")

    legacy = _try_legacy_hf_cache_glob()
    if legacy is not None:
        return EmbeddingModel(path=legacy, source="huggingface-cache")

    if not _env_bool("EMBED_AUTO_DOWNLOAD", True):
        raise EmbeddingModelError(
            f"{GGUF_FILE} is not available locally and EMBED_AUTO_DOWNLOAD=false. "
            "Set EMBED_MODEL_PATH or enable automatic download."
        )

    try:
        return EmbeddingModel(path=_download_from_huggingface(), source="downloaded")
    except Exception as exc:  # noqa: BLE001
        raise EmbeddingModelError(
            f"Failed to download {HF_REPO}/{GGUF_FILE}. Check Hugging Face network access "
            "or set EMBED_MODEL_PATH to a local GGUF file."
        ) from exc


def resolve_embedding_model_path() -> Path | None:
    env_path = _env_model_path()
    if env_path is not None:
        return env_path
    cached = _try_hf_cache()
    if cached is not None:
        return cached
    return _try_legacy_hf_cache_glob()


def embedding_model_status() -> str:
    model = resolve_embedding_model_path()
    return "cached" if model is not None else "missing"


def _env_model_path() -> Path | None:
    env_path = os.environ.get("EMBED_MODEL_PATH", "").strip()
    if not env_path:
        return None
    path = Path(env_path).expanduser()
    return path if path.is_file() else None


def _try_hf_cache() -> Path | None:
    try:
        from huggingface_hub import try_to_load_from_cache

        cached = try_to_load_from_cache(HF_REPO, GGUF_FILE)
    except Exception:
        return None
    if isinstance(cached, str):
        path = Path(cached)
        if path.is_file():
            return path
    return None


def _try_legacy_hf_cache_glob() -> Path | None:
    pattern = (
        ".cache/huggingface/hub/models--Qwen--Qwen3-Embedding-0.6B-GGUF/"
        "snapshots/*/Qwen3-Embedding-0.6B-Q8_0.gguf"
    )
    for path in Path.home().glob(pattern):
        if path.is_file():
            return path
    return None


def _download_from_huggingface() -> Path:
    try:
        from huggingface_hub import hf_hub_download
    except Exception as exc:  # noqa: BLE001
        raise EmbeddingModelError(
            "huggingface-hub is required to download the embedding model. "
            "Install tree-engine with the [rag] extra."
        ) from exc

    return Path(hf_hub_download(repo_id=HF_REPO, filename=GGUF_FILE, endpoint=_hf_endpoint()))


def _hf_endpoint() -> str | None:
    raw = os.environ.get("EMBED_HF_ENDPOINT", "").strip()
    return raw.rstrip("/") if raw else None


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off"}
