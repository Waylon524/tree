"""Embedding service lifecycle tests."""

from __future__ import annotations

from tree.io import paths


def test_start_embedding_service_downloads_model_and_writes_global_pid(tmp_path, monkeypatch):
    from tree.rag import model_cache, service

    monkeypatch.setenv("TREE_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("EMBED_API_URL", "http://localhost:8788")
    started = []

    class _Proc:
        pid = 8842

    def _fake_spawn(cmd, *, cwd=None, stdout=None, stderr=None):
        started.append(cmd)
        return _Proc()

    monkeypatch.setattr(service.process, "spawn_detached", _fake_spawn)
    monkeypatch.setattr(service, "_require_local_server_deps", lambda: None)
    monkeypatch.setattr(service, "_embedding_health", lambda base_url: len(started) > 0)
    monkeypatch.setattr(
        service,
        "ensure_embedding_model",
        lambda: model_cache.EmbeddingModel(path=tmp_path / "model.gguf", source="downloaded"),
    )

    result = service.start_embedding_service(timeout_sec=0.01)

    assert result.status == "started"
    assert paths.service_pid_path(tmp_path, "embedding").read_text(encoding="utf-8") == "8842"
    assert paths.service_log_path(tmp_path, "embedding").exists()
    assert started[0][:3] == [service.sys.executable, "-m", "tree.rag.server"]


def test_start_embedding_service_does_not_restart_healthy_server(tmp_path, monkeypatch):
    from tree.rag import service

    monkeypatch.setenv("TREE_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("EMBED_API_URL", "http://127.0.0.1:8788")
    started = []

    monkeypatch.setattr(service, "_embedding_health", lambda base_url: True)
    monkeypatch.setattr(
        service.process, "spawn_detached", lambda *args, **kwargs: started.append(args)
    )

    result = service.start_embedding_service(timeout_sec=0.01)

    assert result.status == "running"
    assert started == []


def test_start_embedding_service_missing_local_deps_fails_fast(tmp_path, monkeypatch):
    import pytest

    from tree.rag import service

    monkeypatch.setenv("TREE_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("EMBED_API_URL", "http://localhost:8788")
    started = []

    monkeypatch.setattr(service, "_embedding_health", lambda base_url: False)
    monkeypatch.setattr(service.importlib.util, "find_spec", lambda name: None)
    monkeypatch.setattr(
        service.process, "spawn_detached", lambda *args, **kwargs: started.append(args)
    )

    with pytest.raises(RuntimeError, match="local-embed"):
        service.start_embedding_service(timeout_sec=0.01)
    assert started == []


def test_start_embedding_service_cleans_stale_pid_and_restarts(tmp_path, monkeypatch):
    from tree.rag import model_cache, service

    monkeypatch.setenv("TREE_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("EMBED_API_URL", "http://localhost:8788")
    pid_path = paths.service_pid_path(tmp_path, "embedding")
    pid_path.parent.mkdir(parents=True)
    pid_path.write_text("12345", encoding="utf-8")
    killed = []

    class _Proc:
        pid = 9001

    monkeypatch.setattr(service, "_require_local_server_deps", lambda: None)
    monkeypatch.setattr(service, "_embedding_health", lambda base_url: False)
    monkeypatch.setattr(service, "_wait_for_health", lambda base_url, timeout_sec=None: True)
    monkeypatch.setattr(service.process, "pid_alive", lambda pid: False)
    monkeypatch.setattr(
        service.process, "terminate_pid", lambda pid, *, force=False: killed.append(pid)
    )
    monkeypatch.setattr(service.process, "spawn_detached", lambda *args, **kwargs: _Proc())
    monkeypatch.setattr(
        service,
        "ensure_embedding_model",
        lambda: model_cache.EmbeddingModel(path=tmp_path / "model.gguf", source="env"),
    )

    result = service.start_embedding_service(timeout_sec=0.01)

    assert result.status == "started"
    assert killed == []
    assert pid_path.read_text(encoding="utf-8") == "9001"


def test_stop_embedding_service_kills_recorded_pid_and_removes_file(tmp_path, monkeypatch):
    from tree.rag import service

    monkeypatch.setenv("TREE_HOME", str(tmp_path / "home"))
    pid_path = paths.service_pid_path(tmp_path, "embedding")
    pid_path.parent.mkdir(parents=True)
    pid_path.write_text("7788", encoding="utf-8")
    killed = []

    monkeypatch.setattr(
        service.process, "terminate_pid", lambda pid, *, force=False: killed.append((pid, force))
    )

    result = service.stop_embedding_service(force=True)

    assert result.status == "stopped"
    assert killed == [(7788, True)]
    assert not pid_path.exists()


def test_remote_embedding_url_skips_local_service(tmp_path, monkeypatch):
    from tree.rag import service

    monkeypatch.setenv("TREE_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("EMBED_API_URL", "https://example.com/embeddings")
    started = []
    monkeypatch.setattr(
        service.process, "spawn_detached", lambda *args, **kwargs: started.append(args)
    )

    result = service.start_embedding_service(timeout_sec=0.01)

    assert result.status == "external"
    assert started == []


def test_autostart_disabled_skips_loopback_management(tmp_path, monkeypatch):
    """Windows + Ollama case: a loopback endpoint TREE does not host."""
    from tree.rag import service

    monkeypatch.setenv("TREE_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("EMBED_API_URL", "http://localhost:11434")
    monkeypatch.setenv("EMBED_AUTO_START", "false")
    started = []
    monkeypatch.setattr(
        service.process, "spawn_detached", lambda *args, **kwargs: started.append(args)
    )

    result = service.start_embedding_service(timeout_sec=0.01)

    assert result.status == "disabled"
    assert started == []
    assert service.embedding_service_status() == "external"
