"""Tests for the local TREE GUI server and launcher (no real socket served)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from tree.io import paths

TOKEN = "test-token"


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("TREE_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("EMBED_AUTO_START", "false")  # avoid probing localhost:8788
    paths.ensure_workspace_dirs(tmp_path)
    return tmp_path


def _client(workspace):
    from tree.gui.server import create_app

    return TestClient(create_app(workspace, token=TOKEN))


def _authed_client(workspace):
    client = _client(workspace)
    assert client.get("/", params={"token": TOKEN}).status_code == 200
    return client


def test_index_requires_token(workspace):
    client = _client(workspace)
    assert client.get("/").status_code == 403


def test_index_with_token_sets_cookie(workspace):
    client = _client(workspace)
    resp = client.get("/", params={"token": TOKEN})
    assert resp.status_code == 200
    assert "T.R.E.E." in resp.text
    assert "tree_gui_token" in resp.cookies


def test_status_returns_stage_model(workspace):
    client = _authed_client(workspace)
    data = client.get("/api/status").json()
    assert set(data) >= {"phase", "materials", "nodes", "edges", "active", "rows", "errors"}
    assert [r["label"] for r in data["rows"]] == [
        "OCR",
        "Clean",
        "Cut",
        "Embed",
        "Cluster",
        "Link",
        "NodeRun",
    ]


def test_progress_partial_renders(workspace):
    client = _authed_client(workspace)
    resp = client.get("/partials/progress")
    assert resp.status_code == 200
    assert "NodeRun" in resp.text


def test_run_and_stop_invoke_lifecycle(workspace, monkeypatch):
    calls = []
    monkeypatch.setattr("tree.gui.server.start_engine", lambda root: calls.append(("run", root)))
    monkeypatch.setattr("tree.gui.server.stop_engine", lambda root: calls.append(("stop", root)))
    client = _authed_client(workspace)

    assert client.post("/api/run").status_code == 200
    assert client.post("/api/stop").status_code == 200
    assert [c[0] for c in calls] == ["run", "stop"]


def test_run_requires_token(workspace):
    client = _client(workspace)
    assert client.post("/api/run").status_code == 403


def test_dag_svg_404_then_served(workspace):
    client = _authed_client(workspace)
    assert client.get("/dag.svg").status_code == 404

    svg = paths.outputs_dag_svg_path(workspace)
    svg.parent.mkdir(parents=True, exist_ok=True)
    svg.write_text("<svg xmlns='http://www.w3.org/2000/svg'></svg>", encoding="utf-8")
    resp = client.get("/dag.svg")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("image/svg+xml")


def test_outputs_listing_and_render(workspace):
    out = paths.outputs_root(workspace)
    (out / "001.intro.md").write_text("# Intro\n\nHello **world**.", encoding="utf-8")
    client = _authed_client(workspace)

    listing = client.get("/partials/outputs")
    assert "001.intro.md" in listing.text

    rendered = client.get("/outputs/001.intro.md")
    assert rendered.status_code == 200
    assert "<strong>world</strong>" in rendered.text


def test_output_path_traversal_rejected(workspace):
    from tree.gui import server

    (workspace / "secret.md").write_text("secret", encoding="utf-8")
    with pytest.raises(Exception) as exc:
        server._safe_output_path(workspace, "../secret.md")
    assert "404" in str(exc.value) or "Not found" in str(exc.value)


def test_output_missing_returns_404(workspace):
    client = _authed_client(workspace)
    assert client.get("/outputs/nope.md").status_code == 404


def test_setup_writes_global_config(workspace):
    client = _authed_client(workspace)
    resp = client.post(
        "/api/setup",
        data={"llm_api_key": "sk-xyz", "llm_base_url": "https://api.deepseek.com"},
    )
    assert resp.status_code == 200
    assert "Saved" in resp.text
    written = paths.global_config_path().read_text(encoding="utf-8")
    assert "LLM_API_KEY=sk-xyz" in written


def test_require_gui_deps_passes_when_installed():
    from tree.gui import launch

    launch.require_gui_deps()  # deps installed in the dev/test env


def test_require_gui_deps_raises_when_missing(monkeypatch):
    from tree.gui import launch

    monkeypatch.setattr(launch.importlib.util, "find_spec", lambda name: None)
    with pytest.raises(launch.GuiDependencyError, match="gui"):
        launch.require_gui_deps()


def test_resolve_port_prefers_default_when_free(monkeypatch):
    from tree.gui import launch

    monkeypatch.setattr(launch, "_port_free", lambda host, port: True)
    assert launch._resolve_port("127.0.0.1", None) == launch._DEFAULT_PORT


def test_resolve_port_falls_back_when_busy(monkeypatch):
    from tree.gui import launch

    monkeypatch.setattr(launch, "_port_free", lambda host, port: False)
    monkeypatch.setattr(launch, "_free_port", lambda host: 54321)
    assert launch._resolve_port("127.0.0.1", None) == 54321
    assert launch._resolve_port("127.0.0.1", 9000) == 9000
