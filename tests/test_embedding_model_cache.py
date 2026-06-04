"""Embedding model cache discovery and download tests."""

from __future__ import annotations

import pytest


def test_default_embedding_model_metadata_targets_0_6b():
    from tree.rag import model_cache

    assert model_cache.HF_REPO == "Qwen/Qwen3-Embedding-0.6B-GGUF"
    assert model_cache.GGUF_FILE == "Qwen3-Embedding-0.6B-Q8_0.gguf"
    assert model_cache.MODEL_NAME == "Qwen3-Embedding-0.6B-Q8_0"


def test_env_model_path_is_used_without_download(tmp_path, monkeypatch):
    from tree.rag import model_cache

    model = tmp_path / "Qwen3-Embedding-0.6B-Q8_0.gguf"
    model.write_text("model", encoding="utf-8")
    downloads = []

    monkeypatch.setenv("EMBED_MODEL_PATH", str(model))
    monkeypatch.setattr(model_cache, "_try_hf_cache", lambda: None)
    monkeypatch.setattr(model_cache, "_download_from_huggingface", lambda: downloads.append("download"))

    result = model_cache.ensure_embedding_model()

    assert result.path == model
    assert result.source == "env"
    assert downloads == []


def test_huggingface_cache_is_used_without_download(tmp_path, monkeypatch):
    from tree.rag import model_cache

    cached = tmp_path / "snapshots" / "model.gguf"
    cached.parent.mkdir(parents=True)
    cached.write_text("model", encoding="utf-8")
    downloads = []

    monkeypatch.delenv("EMBED_MODEL_PATH", raising=False)
    monkeypatch.setattr(model_cache, "_try_hf_cache", lambda: cached)
    monkeypatch.setattr(model_cache, "_try_legacy_hf_cache_glob", lambda: None)
    monkeypatch.setattr(model_cache, "_download_from_huggingface", lambda: downloads.append("download"))

    result = model_cache.ensure_embedding_model()

    assert result.path == cached
    assert result.source == "huggingface-cache"
    assert downloads == []


def test_missing_model_downloads_from_huggingface(tmp_path, monkeypatch):
    from tree.rag import model_cache

    downloaded = tmp_path / "downloaded.gguf"
    downloaded.write_text("model", encoding="utf-8")

    monkeypatch.delenv("EMBED_MODEL_PATH", raising=False)
    monkeypatch.setattr(model_cache, "_try_hf_cache", lambda: None)
    monkeypatch.setattr(model_cache, "_try_legacy_hf_cache_glob", lambda: None)
    monkeypatch.setattr(model_cache, "_download_from_huggingface", lambda: downloaded)

    result = model_cache.ensure_embedding_model()

    assert result.path == downloaded
    assert result.source == "downloaded"


def test_missing_model_with_download_disabled_raises_clear_error(monkeypatch):
    from tree.rag import model_cache

    monkeypatch.delenv("EMBED_MODEL_PATH", raising=False)
    monkeypatch.setenv("EMBED_AUTO_DOWNLOAD", "false")
    monkeypatch.setattr(model_cache, "_try_hf_cache", lambda: None)
    monkeypatch.setattr(model_cache, "_try_legacy_hf_cache_glob", lambda: None)

    with pytest.raises(model_cache.EmbeddingModelError, match="EMBED_AUTO_DOWNLOAD=false"):
        model_cache.ensure_embedding_model()
