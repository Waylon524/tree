from __future__ import annotations

import json

import pytest

from tree.rag.embed import EmbeddingClient
from tree.rag.client import RAGClient


def test_default_dimensions_match_qwen3_embedding_0_6b(tmp_path):
    rag = RAGClient(store_path=tmp_path / "rag", embedder=_FakeEmbedder())

    try:
        assert rag.dimensions == 1024
    finally:
        rag.close()


class _FakeEmbedder:
    def embed(self, texts: str | list[str]) -> list[list[float]]:
        if isinstance(texts, str):
            texts = [texts]
        lookup = {"left": [1.0, 0.0], "right": [0.0, 1.0]}
        return [lookup.get(text, [1.0, 0.0]) for text in texts]


def test_source_mtu_vectors_average_chunks_and_node_id_backfill(tmp_path):
    rag = RAGClient(store_path=tmp_path / "rag", dimensions=2, embedder=_FakeEmbedder())
    try:
        rag.index_file(
            "mtu:1",
            "source.md",
            "left\n\nright",
            content_kind="source",
            source_collection="课件",
            path="课件/source.md",
            doc_id="mtu:1",
            chunks=[
                {
                    "chunk_id": "mtu:1-0",
                    "chunk_index": 0,
                    "text": "left",
                    "mtu_id": "mtu:1",
                    "is_draft": False,
                },
                {
                    "chunk_id": "mtu:1-1",
                    "chunk_index": 1,
                    "text": "right",
                    "mtu_id": "mtu:1",
                    "is_draft": False,
                },
            ],
        )

        vector = rag.source_mtu_vectors(["mtu:1"])["mtu:1"]
        assert vector == pytest.approx([0.707106, 0.707106], abs=1e-5)

        rag.update_source_mtu_node_ids({"mtu:1": "kn:1"})
        hits = rag.scroll_chunks(filters={"mtu_id": "mtu:1"})
        assert {hit["metadata"].get("node_id") for hit in hits} == {"kn:1"}
    finally:
        rag.close()


def test_index_file_adds_document_context_to_embedding_errors(tmp_path):
    class FailingEmbedder:
        base_url = "http://localhost:8788"
        model = "test-model"

        def embed(self, texts):
            raise RuntimeError("HTTP 400: too long")

    rag = RAGClient(store_path=tmp_path / "rag", dimensions=2, embedder=FailingEmbedder())
    try:
        with pytest.raises(RuntimeError) as exc:
            rag.index_file(
                "mtu:1",
                "source.md",
                "ignored",
                content_kind="source",
                source_collection="课件",
                path="课件/source.md",
                doc_id="mtu:1",
                chunks=[
                    {
                        "chunk_id": "mtu:1-0",
                        "chunk_index": 0,
                        "text": "x" * 12,
                        "mtu_id": "mtu:1",
                        "is_draft": False,
                    }
                ],
            )
        message = str(exc.value)
        assert "mtu:1" in message
        assert "chunks=1" in message
        assert "max_chars=12" in message
        assert "test-model" in message
        assert "HTTP 400" in message
    finally:
        rag.close()


def test_partial_embedding_response_does_not_delete_previous_document(tmp_path):
    class PartialOnSecondCall:
        model = "stable-model"

        def __init__(self):
            self.calls = 0

        def embed(self, texts):
            self.calls += 1
            if self.calls == 1:
                return [[1.0, 0.0] for _ in texts]
            return [[0.0, 1.0]]

    embedder = PartialOnSecondCall()
    rag = RAGClient(store_path=tmp_path / "rag", dimensions=2, embedder=embedder)
    chunks = [
        {"chunk_id": "a", "chunk_index": 0, "text": "left"},
        {"chunk_id": "b", "chunk_index": 1, "text": "right"},
    ]
    try:
        rag.index_file("1", "x.md", "old", doc_id="doc", chunks=chunks)

        with pytest.raises(RuntimeError, match="response count mismatch"):
            rag.index_file("1", "x.md", "new", doc_id="doc", chunks=chunks)

        hits = rag.scroll_chunks(filters={"doc_id": "doc"})
        assert {hit["text"] for hit in hits} == {"left", "right"}
        assert rag.document_indexed("doc") is True
    finally:
        rag.close()


def test_embedding_client_includes_http_error_body(monkeypatch):
    import urllib.error

    def fake_urlopen(req, timeout=None):
        assert timeout == 300
        raise urllib.error.HTTPError(
            req.full_url,
            400,
            "Bad Request",
            hdrs=None,
            fp=_Body(b'{"error":"context length exceeded"}'),
        )

    monkeypatch.setattr("tree.rag.embed.urllib.request.urlopen", fake_urlopen)
    client = EmbeddingClient(base_url="http://localhost:8788", model="m")

    with pytest.raises(RuntimeError) as exc:
        client.embed(["x" * 10])

    message = str(exc.value)
    assert "HTTP 400" in message
    assert "context length exceeded" in message
    assert "inputs=1" in message
    assert "max_chars=10" in message


def test_local_embedding_client_gives_each_existing_chunk_its_own_timeout(monkeypatch):
    calls = []

    def fake_urlopen(req, timeout=None):
        payload = json.loads(req.data)
        calls.append((payload["input"], timeout))
        value = 1.0 if payload["input"] == ["left"] else 2.0
        return _Response(
            {
                "data": [
                    {"index": 0, "embedding": [value, 0.0]},
                ]
            }
        )

    monkeypatch.setattr("tree.rag.embed.urllib.request.urlopen", fake_urlopen)
    client = EmbeddingClient(base_url="http://localhost:8788", model="m")

    assert client.embed(["left", "right"]) == [[1.0, 0.0], [2.0, 0.0]]
    assert calls == [(["left"], 300.0), (["right"], 300.0)]


def test_external_embedding_client_keeps_batch_request(monkeypatch):
    calls = []

    def fake_urlopen(req, timeout=None):
        payload = json.loads(req.data)
        calls.append((payload["input"], timeout))
        return _Response(
            {
                "data": [
                    {"index": 0, "embedding": [1.0, 0.0]},
                    {"index": 1, "embedding": [2.0, 0.0]},
                ]
            }
        )

    monkeypatch.setattr("tree.rag.embed.urllib.request.urlopen", fake_urlopen)
    client = EmbeddingClient(base_url="https://embed.example.test", model="m")

    assert client.embed(["left", "right"]) == [[1.0, 0.0], [2.0, 0.0]]
    assert calls == [(["left", "right"], 300.0)]


def test_embedding_timeout_reports_segment_and_recovery_guidance(monkeypatch):
    def fake_urlopen(req, timeout=None):
        raise TimeoutError("timed out")

    monkeypatch.setattr("tree.rag.embed.urllib.request.urlopen", fake_urlopen)
    client = EmbeddingClient(
        base_url="http://localhost:8788", model="m", timeout_sec=12
    )

    with pytest.raises(RuntimeError) as exc:
        client.embed(["left", "x" * 10])

    message = str(exc.value)
    assert "after 12s" in message
    assert "segment=1/2" in message
    assert "max_chars=4" in message
    assert "EMBED_REQUEST_TIMEOUT_SEC" in message


class _Response:
    def __init__(self, data: dict):
        self._data = json.dumps(data).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        return None

    def read(self) -> bytes:
        return self._data


class _Body:
    def __init__(self, data: bytes):
        self._data = data

    def read(self, size: int = -1) -> bytes:
        return self._data[:size]

    def close(self) -> None:
        return None
