"""Tests for the local TREE GUI server and launcher (no real socket served)."""

from __future__ import annotations

import hashlib

import pytest
from fastapi.testclient import TestClient

from tree.io import paths
from tree.planner.store import envelope, write_json_atomic
from tree.state.manager import StateManager
from tree.state.models import NodeExecutionRecord, NodeRunRecord, PipelineState, WriterResult

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


def test_dag_requires_token(workspace):
    client = _client(workspace)
    assert client.get("/api/dag").status_code == 403


def test_dag_empty_payload(workspace):
    client = _authed_client(workspace)

    data = client.get("/api/dag").json()

    assert data["nodes"] == []
    assert data["edges"] == []
    assert data["roots"] == []
    assert data["stats"]["nodes"] == 0


def test_dag_payload_labels_edges_and_statuses(workspace):
    write_json_atomic(
        paths.knowledge_dag_path(workspace),
        envelope(
            schema="tree.knowledge-dag",
            data={
                "nodes": [
                    {
                        "node_id": "n1",
                        "title": "Root",
                        "defines": ["A"],
                        "collections": ["course"],
                        "source_order_index": 0,
                    },
                    {
                        "node_id": "n2",
                        "title": "Ready",
                        "defines": ["B"],
                        "collections": ["course"],
                        "source_order_index": 1,
                    },
                    {
                        "node_id": "n3",
                        "title": "Locked",
                        "defines": ["C"],
                        "collections": ["course"],
                        "source_order_index": 2,
                    },
                    {
                        "node_id": "n4",
                        "title": "Running",
                        "defines": ["D"],
                        "collections": ["lab"],
                        "source_order_index": 3,
                    },
                    {
                        "node_id": "n5",
                        "title": "Failed",
                        "defines": ["E"],
                        "collections": ["lab"],
                        "source_order_index": 4,
                    },
                ],
                "edges": [
                    {"from_node_id": "n1", "to_node_id": "n2", "relation": "prerequisite"},
                    {"from_node_id": "n2", "to_node_id": "n3", "relation": "prerequisite"},
                ],
                "roots": ["n1", "n4", "n5"],
            },
        ),
    )
    write_json_atomic(
        paths.knowledge_nodes_path(workspace),
        envelope(
            schema="tree.knowledge-nodes",
            data={
                "knowledge_nodes": [
                    {"node_id": "n1", "title": "Root", "source_order_index": 0},
                    {"node_id": "n2", "title": "Ready", "source_order_index": 1},
                    {"node_id": "n3", "title": "Locked", "source_order_index": 2},
                    {"node_id": "n4", "title": "Running", "source_order_index": 3},
                    {"node_id": "n5", "title": "Failed", "source_order_index": 4},
                ]
            },
        ),
    )
    write_json_atomic(
        paths.knowledge_ledger_path(workspace),
        {"records": [{"node_id": "n1", "node_ids": ["n1"], "output_path": "outputs/001.Root.md"}]},
    )
    StateManager(paths.pipeline_state_path(workspace)).save(
        PipelineState(
            node_executions=[
                NodeExecutionRecord(node_id="n4", status="in_progress"),
                NodeExecutionRecord(node_id="n5", status="failed"),
            ],
            node_runs=[
                NodeRunRecord(node_id="n4", run_id="n4::run", status="running"),
                NodeRunRecord(node_id="n5", run_id="n5::run", status="failed"),
            ],
        )
    )
    client = _authed_client(workspace)

    data = client.get("/api/dag").json()
    statuses = {node["id"]: node["status"] for node in data["nodes"]}

    assert statuses == {
        "n1": "complete",
        "n2": "ready",
        "n3": "locked",
        "n4": "running",
        "n5": "failed",
    }
    assert data["nodes"][0]["label"] == "001. Root"
    assert data["nodes"][0]["output_paths"] == ["outputs/001.Root.md"]
    assert data["nodes"][0]["generation_status"] == "complete"
    # A generated root fruit ripens (is recommended) even while the upper canopy
    # is still growing — recommendation is per-node, not gated on whole-tree done.
    assert data["nodes"][0]["reading_status"] == "recommended"
    assert data["nodes"][0]["recommended"] is True
    assert data["nodes"][0]["learning_ready"] is False
    assert data["nodes"][1]["prerequisites"] == ["n1"]
    assert data["nodes"][1]["dependents"] == ["n3"]
    assert data["edges"] == [
        {
            "from": "n1",
            "to": "n2",
            "relation": "prerequisite",
            "confidence": 1.0,
            "required_defines": [],
        },
        {
            "from": "n2",
            "to": "n3",
            "relation": "prerequisite",
            "confidence": 1.0,
            "required_defines": [],
        },
    ]
    assert data["stats"]["statuses"]["complete"] == 1
    assert data["stats"]["statuses"]["ready"] == 1
    assert data["stats"]["statuses"]["locked"] == 1
    assert data["stats"]["statuses"]["running"] == 1
    assert data["stats"]["statuses"]["failed"] == 1


def test_learning_state_marks_read_and_recommends_next_node(workspace):
    write_json_atomic(
        paths.knowledge_dag_path(workspace),
        envelope(
            schema="tree.knowledge-dag",
            data={
                "nodes": [
                    {"node_id": "n1", "title": "Root", "source_order_index": 0},
                    {"node_id": "n2", "title": "Next", "source_order_index": 1},
                ],
                "edges": [
                    {"from_node_id": "n1", "to_node_id": "n2", "relation": "prerequisite"},
                ],
                "roots": ["n1"],
            },
        ),
    )
    write_json_atomic(
        paths.knowledge_ledger_path(workspace),
        {
            "records": [
                {"node_id": "n1", "node_ids": ["n1"], "output_path": "outputs/001.Root.md"},
                {"node_id": "n2", "node_ids": ["n2"], "output_path": "outputs/002.Next.md"},
            ]
        },
    )
    client = _authed_client(workspace)

    initial = client.get("/api/dag").json()
    by_id = {node["id"]: node for node in initial["nodes"]}
    assert initial["learning_ready"] is True
    assert by_id["n1"]["reading_status"] == "recommended"
    assert by_id["n1"]["recommended"] is True
    assert by_id["n2"]["reading_status"] == "unread"

    opened = client.post("/api/learning/nodes/n1/open").json()
    assert opened["state"]["reading_status"] == "reading"
    read = client.post("/api/learning/nodes/n1/read", json={"read": True}).json()
    assert read["state"]["reading_status"] == "read"

    updated = client.get("/api/dag").json()
    by_id = {node["id"]: node for node in updated["nodes"]}
    assert by_id["n1"]["reading_status"] == "read"
    assert by_id["n2"]["reading_status"] == "recommended"
    assert by_id["n2"]["recommended"] is True


def test_learning_feedback_revises_output_and_marks_dependents_affected(workspace, monkeypatch):
    write_json_atomic(
        paths.knowledge_dag_path(workspace),
        envelope(
            schema="tree.knowledge-dag",
            data={
                "nodes": [
                    {"node_id": "n1", "title": "Root", "source_order_index": 0},
                    {"node_id": "n2", "title": "Next", "source_order_index": 1},
                ],
                "edges": [
                    {"from_node_id": "n1", "to_node_id": "n2", "relation": "prerequisite"},
                ],
                "roots": ["n1"],
            },
        ),
    )
    outputs = paths.outputs_root(workspace)
    (outputs / "001.Root.md").write_text("# Root\n\n旧内容\n", encoding="utf-8")
    (outputs / "002.Next.md").write_text("# Next\n\n下游内容\n", encoding="utf-8")
    write_json_atomic(
        paths.knowledge_ledger_path(workspace),
        {
            "records": [
                {"node_id": "n1", "node_ids": ["n1"], "output_path": "outputs/001.Root.md"},
                {"node_id": "n2", "node_ids": ["n2"], "output_path": "outputs/002.Next.md"},
            ]
        },
    )
    indexed: list[tuple[str, str]] = []
    captured: dict[str, str] = {}

    class _FakeSettings:
        source_mtu_chunk_tokens = 20000

    class _FakeClient:
        async def close(self):
            return None

    class _FakeWriter:
        def __init__(self, client):
            self.client = client

        async def revise_from_feedback(self, **kwargs):
            captured["feedback"] = kwargs["user_feedback"]
            captured["current"] = kwargs["current_text"]
            return WriterResult(draft_content="# Root\n\n修订内容")

    class _FakeRAG:
        def __init__(self, *args, **kwargs):
            pass

        def query(self, *args, **kwargs):
            return []

        def close(self):
            return None

    class _FakeIndexer:
        def __init__(self, rag, **kwargs):
            self.rag = rag

        def index_finished_file(self, root, node_id, path):
            indexed.append((node_id, path.name))
            return 1

    monkeypatch.setattr("tree.learning.Settings.from_env", lambda root: _FakeSettings())
    monkeypatch.setattr("tree.learning.LLMClient", lambda settings: _FakeClient())
    monkeypatch.setattr("tree.learning.WriterAgent", _FakeWriter)
    monkeypatch.setattr("tree.learning.RAGClient", _FakeRAG)
    monkeypatch.setattr("tree.learning.RAGIndexer", _FakeIndexer)

    client = _authed_client(workspace)
    assert client.post("/api/learning/nodes/n2/read", json={"read": True}).status_code == 200

    resp = client.post(
        "/api/learning/nodes/n1/feedback",
        json={"feedback": "需要补充公式含义"},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "complete"
    assert (workspace / body["backup_path"]).is_file()
    assert (outputs / "001.Root.md").read_text(encoding="utf-8") == "# Root\n\n修订内容\n"
    assert indexed == [("n1", "001.Root.md")]
    assert captured["feedback"] == "需要补充公式含义"
    assert "旧内容" in captured["current"]

    dag = client.get("/api/dag").json()
    by_id = {node["id"]: node for node in dag["nodes"]}
    assert by_id["n1"]["reading_status"] == "recommended"
    assert by_id["n2"]["affected_by_feedback"] is True


def test_learning_feedback_failure_keeps_original_output(workspace, monkeypatch):
    write_json_atomic(
        paths.knowledge_dag_path(workspace),
        envelope(
            schema="tree.knowledge-dag",
            data={
                "nodes": [{"node_id": "n1", "title": "Root", "source_order_index": 0}],
                "edges": [],
                "roots": ["n1"],
            },
        ),
    )
    output = paths.outputs_root(workspace) / "001.Root.md"
    output.write_text("# Root\n\n旧内容\n", encoding="utf-8")
    write_json_atomic(
        paths.knowledge_ledger_path(workspace),
        {"records": [{"node_id": "n1", "node_ids": ["n1"], "output_path": "outputs/001.Root.md"}]},
    )

    class _FakeSettings:
        source_mtu_chunk_tokens = 20000

    class _FakeClient:
        async def close(self):
            return None

    class _FailingWriter:
        def __init__(self, client):
            self.client = client

        async def revise_from_feedback(self, **kwargs):
            raise RuntimeError("writer failed")

    class _FakeRAG:
        def __init__(self, *args, **kwargs):
            pass

        def query(self, *args, **kwargs):
            return []

        def close(self):
            return None

    monkeypatch.setattr("tree.learning.Settings.from_env", lambda root: _FakeSettings())
    monkeypatch.setattr("tree.learning.LLMClient", lambda settings: _FakeClient())
    monkeypatch.setattr("tree.learning.WriterAgent", _FailingWriter)
    monkeypatch.setattr("tree.learning.RAGClient", _FakeRAG)

    client = _authed_client(workspace)
    resp = client.post("/api/learning/nodes/n1/feedback", json={"feedback": "补充例题"})

    assert resp.status_code == 500
    assert output.read_text(encoding="utf-8") == "# Root\n\n旧内容\n"
    dag = client.get("/api/dag").json()
    node = dag["nodes"][0]
    assert "writer failed" in node["last_feedback_error"]


def test_extension_requires_token(workspace):
    client = _client(workspace)
    assert client.get("/api/extension").status_code == 403
    assert client.post("/api/extension/install").status_code == 403


def test_extension_status_and_install(workspace, monkeypatch):
    installed = []
    monkeypatch.setattr(
        "tree.gui.server.embedding_extension_status",
        lambda: {
            "installed": False,
            "status": "missing",
            "phase": "missing",
            "progress": 0,
            "message": "Install required: embedding model",
            "model": "missing",
            "runtime": "missing",
        },
    )
    monkeypatch.setattr(
        "tree.gui.server.start_embedding_extension_install",
        lambda: installed.append(True) or True,
    )
    client = _authed_client(workspace)

    status = client.get("/api/extension").json()
    assert status["installed"] is False
    assert status["message"] == "Install required: embedding model"

    install = client.post("/api/extension/install").json()
    assert install["status"] == "missing"
    assert installed == [True]


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


def test_output_raw_requires_token(workspace):
    out = paths.outputs_root(workspace)
    (out / "001.intro.md").write_text("# Intro\n", encoding="utf-8")
    client = _client(workspace)

    assert client.get("/api/outputs/001.intro.md/raw").status_code == 403


def test_output_raw_returns_markdown_metadata(workspace):
    out = paths.outputs_root(workspace)
    body = "# Intro\n\n| A | B |\n|---|---|\n| 1 | 2 |\n\n$x + y$"
    (out / "001.intro.md").write_text(body, encoding="utf-8")
    client = _authed_client(workspace)

    resp = client.get("/api/outputs/001.intro.md/raw")

    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "001.intro.md"
    assert data["markdown"] == body
    assert data["size_bytes"] == len(body.encode("utf-8"))
    assert data["updated_at"].endswith("Z")


def test_output_raw_rejects_path_traversal(workspace):
    client = _authed_client(workspace)
    resp = client.get("/api/outputs/../secret.md/raw")

    assert resp.status_code in {404, 400}


def test_output_raw_missing_returns_404(workspace):
    client = _authed_client(workspace)

    assert client.get("/api/outputs/nope.md/raw").status_code == 404


def test_exports_copy_selected_outputs(workspace, tmp_path):
    out = paths.outputs_root(workspace)
    (out / "001.intro.md").write_text("# Intro\n", encoding="utf-8")
    (out / "002.more.md").write_text("# More\n", encoding="utf-8")
    client = _authed_client(workspace)

    resp = client.post(
        "/api/exports",
        json={
            "destination": str(tmp_path),
            "files": ["001.intro.md", "002.more.md"],
            "mode": "copy",
        },
    )

    assert resp.status_code == 200
    body = resp.json()
    assert {item["name"] for item in body["exported"]} == {"001.intro.md", "002.more.md"}
    assert body["skipped"] == []
    assert body["failed"] == []
    assert (tmp_path / "001.intro.md").read_bytes() == (out / "001.intro.md").read_bytes()
    assert (tmp_path / "002.more.md").read_bytes() == (out / "002.more.md").read_bytes()


def test_exports_rejects_path_traversal(workspace, tmp_path):
    client = _authed_client(workspace)
    resp = client.post(
        "/api/exports",
        json={"destination": str(tmp_path), "files": ["../secret.md"], "mode": "copy"},
    )

    assert resp.status_code == 400
    assert "Invalid output file" in resp.text


def test_exports_rejects_missing_output(workspace, tmp_path):
    client = _authed_client(workspace)
    resp = client.post(
        "/api/exports",
        json={"destination": str(tmp_path), "files": ["nope.md"], "mode": "copy"},
    )

    assert resp.status_code == 400
    assert "Invalid output file" in resp.text


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


def test_settings_requires_token(workspace):
    client = _client(workspace)
    assert client.get("/api/settings").status_code == 403
    assert client.post("/api/settings", json={}).status_code == 403


def test_settings_get_returns_defaults_and_masked_key_state(workspace):
    client = _authed_client(workspace)

    data = client.get("/api/settings").json()

    assert data["config_path"] == str(paths.global_config_path())
    assert data["llm_api_key_configured"] is False
    assert data["llm_base_url"] == "https://api.deepseek.com"
    assert data["llm_model"] == "deepseek-v4-flash"
    assert data["role_models"] == {
        "examiner": "deepseek-v4-flash",
        "student": "deepseek-v4-flash",
        "writer": "deepseek-v4-flash",
        "archivist": "deepseek-v4-flash",
        "dagger": "deepseek-v4-flash",
    }
    assert data["paddleocr_api_token_configured"] is False
    assert data["paddleocr_api_url"] == "https://paddleocr.aistudio-app.com/api/v2/ocr/jobs"
    assert data["paddleocr_model"] == "PaddleOCR-VL-1.6"
    assert data["llama_server_ctx"] == 22_000
    assert data["source_mtu_chunk_tokens"] == 20_000
    assert "llm_api_key" not in data
    assert "paddleocr_api_token" not in data


def test_settings_post_writes_global_config_with_role_models(workspace):
    client = _authed_client(workspace)
    resp = client.post(
        "/api/settings",
        json={
            "llm_api_key": "sk-new",
            "llm_base_url": "https://llm.test",
            "llm_model": "default-model",
            "role_models": {
                "examiner": "exam-model",
                "student": "student-model",
                "writer": "writer-model",
                "archivist": "arch-model",
                "dagger": "dagger-model",
            },
            "paddleocr_api_token": "ocr-new",
            "llama_server_ctx": "22000",
            "source_mtu_chunk_tokens": "20000",
        },
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["llm_api_key_configured"] is True
    assert data["paddleocr_api_token_configured"] is True
    assert data["role_models"]["dagger"] == "dagger-model"
    assert data["llama_server_ctx"] == 22_000
    assert data["source_mtu_chunk_tokens"] == 20_000
    written = paths.global_config_path().read_text(encoding="utf-8")
    assert "LLM_API_KEY=sk-new" in written
    assert "LLM_BASE_URL=https://llm.test" in written
    assert "LLM_MODEL=default-model" in written
    assert "EXAMINER_MODEL=exam-model" in written
    assert "STUDENT_MODEL=student-model" in written
    assert "WRITER_MODEL=writer-model" in written
    assert "ARCHIVIST_MODEL=arch-model" in written
    assert "DAGGER_MODEL=dagger-model" in written
    assert "PADDLEOCR_API_TOKEN=ocr-new" in written
    assert "PADDLEOCR_API_URL=https://paddleocr.aistudio-app.com/api/v2/ocr/jobs" in written
    assert "PADDLEOCR_MODEL=PaddleOCR-VL-1.6" in written
    assert "LLAMA_SERVER_CTX=22000" in written
    assert "SOURCE_MTU_CHUNK_TOKENS=20000" in written


def test_settings_post_rejects_invalid_runtime_numbers_without_writing(workspace):
    client = _authed_client(workspace)
    resp = client.post(
        "/api/settings",
        json={
            "llm_base_url": "https://llm.test",
            "llama_server_ctx": "40000",
            "source_mtu_chunk_tokens": "20000",
        },
    )

    assert resp.status_code == 400
    config = paths.global_config_path()
    assert not config.exists()


def test_settings_post_ignores_paddleocr_endpoint_and_model_overrides(workspace):
    config = paths.global_config_path()
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(
        "\n".join(
            [
                "LLM_API_KEY=old-llm",
                "PADDLEOCR_API_TOKEN=old-ocr",
                "PADDLEOCR_API_URL=https://old-ocr.test/jobs",
                "PADDLEOCR_MODEL=Old-OCR",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    client = _authed_client(workspace)

    resp = client.post(
        "/api/settings",
        json={
            "llm_api_key": "",
            "llm_base_url": "https://new.test",
            "llm_model": "new-default",
            "role_models": {"dagger": "dagger-new"},
            "paddleocr_api_token": "",
            "paddleocr_api_url": "https://ocr-new.test/jobs",
            "paddleocr_model": "PaddleOCR-VL-1.7",
        },
    )

    assert resp.status_code == 200
    written = config.read_text(encoding="utf-8")
    assert "LLM_API_KEY=old-llm" in written
    assert "PADDLEOCR_API_TOKEN=old-ocr" in written
    assert "LLM_BASE_URL=https://new.test" in written
    assert "DAGGER_MODEL=dagger-new" in written
    assert "PADDLEOCR_API_URL=https://paddleocr.aistudio-app.com/api/v2/ocr/jobs" in written
    assert "PADDLEOCR_MODEL=PaddleOCR-VL-1.6" in written
    assert "https://ocr-new.test/jobs" not in written
    assert "PaddleOCR-VL-1.7" not in written


def test_status_includes_engine_state(workspace):
    client = _authed_client(workspace)
    data = client.get(f"/api/status?token={TOKEN}").json()
    assert data["engine"] in {"running", "stopped"}


def test_open_dag_opens_existing_svg(workspace, monkeypatch):
    opened: list = []
    monkeypatch.setattr("tree.gui.server._open_in_default_app", lambda path: opened.append(path))
    svg = paths.outputs_dag_svg_path(workspace)
    svg.parent.mkdir(parents=True, exist_ok=True)
    svg.write_text("<svg xmlns='http://www.w3.org/2000/svg'></svg>", encoding="utf-8")

    client = _authed_client(workspace)
    resp = client.post("/api/open-dag")

    assert resp.status_code == 200
    assert "Opened" in resp.text
    assert opened == [svg]


def test_open_dag_when_not_generated(workspace, monkeypatch):
    opened: list = []
    monkeypatch.setattr("tree.gui.server._open_in_default_app", lambda path: opened.append(path))

    client = _authed_client(workspace)
    resp = client.post("/api/open-dag")

    assert resp.status_code == 200
    assert "not generated" in resp.text
    assert opened == []


def test_ws_progress_streams_status(workspace):
    client = _authed_client(workspace)
    with client.websocket_connect(f"/ws/progress?token={TOKEN}") as ws:
        first = ws.receive_json()
    assert "rows" in first
    assert [r["label"] for r in first["rows"]][0] == "OCR"


def test_ws_progress_rejects_bad_token(workspace):
    from starlette.websockets import WebSocketDisconnect

    client = _client(workspace)
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws/progress?token=wrong") as ws:
            ws.receive_json()


@pytest.mark.parametrize(
    "origin",
    [
        "http://localhost:5173",
        "http://tauri.localhost",
        "https://tauri.localhost",
        "tauri://localhost",
    ],
)
def test_cors_header_present_for_spa_origins(workspace, origin):
    client = _authed_client(workspace)
    resp = client.get(f"/api/status?token={TOKEN}", headers={"Origin": origin})
    assert resp.headers.get("access-control-allow-origin") == origin


def test_cors_preflight_allows_tauri_material_upload(workspace):
    client = _authed_client(workspace)
    resp = client.options(
        f"/api/materials?token={TOKEN}",
        headers={
            "Origin": "http://tauri.localhost",
            "Access-Control-Request-Method": "POST",
        },
    )
    assert resp.status_code == 200
    assert resp.headers.get("access-control-allow-origin") == "http://tauri.localhost"
    assert "POST" in resp.headers.get("access-control-allow-methods", "")


def test_add_materials_uploads_supported_and_skips_unsupported(workspace):
    client = _authed_client(workspace)
    resp = client.post(
        "/api/materials",
        data={"collection": "课件"},
        files=[
            ("files", ("ch1.pdf", b"%PDF-1.4", "application/pdf")),
            ("files", ("notes.txt", b"hello", "text/plain")),
            ("files", ("bad.xyz", b"nope", "application/octet-stream")),
        ],
    )
    assert resp.status_code == 200
    body = resp.json()
    assert set(body["saved"]) == {"课件/ch1.pdf", "课件/notes.txt"}
    assert body["skipped"] == ["bad.xyz"]
    assert (paths.materials_root(workspace) / "课件" / "ch1.pdf").read_bytes() == b"%PDF-1.4"

    listed = client.get("/api/materials").json()["materials"]
    assert listed == ["课件/ch1.pdf", "课件/notes.txt"]


def test_imported_files_requires_token(workspace):
    client = _client(workspace)
    assert client.get("/api/imported-files").status_code == 403


def test_add_materials_writes_import_manifest_metadata(workspace):
    client = _authed_client(workspace)
    resp = client.post(
        "/api/materials",
        data={"collection": "课件"},
        files=[
            ("files", ("ch1.pdf", b"%PDF-1.4", "application/pdf")),
            ("files", ("bad.xyz", b"nope", "application/octet-stream")),
        ],
    )

    assert resp.status_code == 200
    assert resp.json() == {"saved": ["课件/ch1.pdf"], "skipped": ["bad.xyz"]}
    manifest_path = paths.import_manifest_path(workspace)
    assert manifest_path.exists()

    files = client.get("/api/imported-files").json()["files"]
    assert len(files) == 1
    record = files[0]
    assert record["id"].startswith("src_")
    assert record["original_name"] == "ch1.pdf"
    assert record["stored_name"] == "ch1.pdf"
    assert record["relative_path"] == "课件/ch1.pdf"
    assert record["collection"] == "课件"
    assert record["size_bytes"] == len(b"%PDF-1.4")
    assert record["sha256"] == hashlib.sha256(b"%PDF-1.4").hexdigest()
    assert record["imported_at"].endswith("Z")
    assert record["status"] == "active"


def test_add_materials_renames_colliding_files_without_overwrite(workspace):
    client = _authed_client(workspace)
    first = client.post(
        "/api/materials",
        data={"collection": "default"},
        files=[("files", ("lecture.pdf", b"first", "application/pdf"))],
    )
    second = client.post(
        "/api/materials",
        data={"collection": "default"},
        files=[("files", ("lecture.pdf", b"second", "application/pdf"))],
    )

    assert first.json()["saved"] == ["default/lecture.pdf"]
    assert second.json()["saved"] == ["default/lecture 2.pdf"]
    material_root = paths.materials_root(workspace) / "default"
    assert (material_root / "lecture.pdf").read_bytes() == b"first"
    assert (material_root / "lecture 2.pdf").read_bytes() == b"second"
    files = client.get("/api/imported-files").json()["files"]
    assert [item["stored_name"] for item in files] == ["lecture.pdf", "lecture 2.pdf"]
    assert [item["original_name"] for item in files] == ["lecture.pdf", "lecture.pdf"]


def test_imported_files_synthesizes_legacy_disk_files(workspace):
    legacy = paths.materials_root(workspace) / "default" / "legacy.pdf"
    legacy.parent.mkdir(parents=True, exist_ok=True)
    legacy.write_bytes(b"legacy")
    client = _authed_client(workspace)

    files = client.get("/api/imported-files").json()["files"]

    assert files == [
        {
            "id": "legacy:default/legacy.pdf",
            "original_name": "legacy.pdf",
            "stored_name": "legacy.pdf",
            "relative_path": "default/legacy.pdf",
            "collection": "default",
            "size_bytes": len(b"legacy"),
            "sha256": hashlib.sha256(b"legacy").hexdigest(),
            "imported_at": "",
            "status": "active",
        }
    ]


def test_imported_files_marks_manifest_records_missing(workspace):
    write_json_atomic(
        paths.import_manifest_path(workspace),
        {
            "schema": "tree.import-manifest.ui",
            "version": 1,
            "files": [
                {
                    "id": "src_missing",
                    "original_name": "missing.pdf",
                    "stored_name": "missing.pdf",
                    "relative_path": "default/missing.pdf",
                    "collection": "default",
                    "size_bytes": 10,
                    "sha256": "abc",
                    "imported_at": "2026-06-18T12:00:00Z",
                    "status": "active",
                }
            ],
        },
    )
    client = _authed_client(workspace)

    files = client.get("/api/imported-files").json()["files"]

    assert files[0]["id"] == "src_missing"
    assert files[0]["status"] == "missing"


def test_add_materials_rejects_collection_path_traversal(workspace):
    client = _authed_client(workspace)
    resp = client.post(
        "/api/materials",
        data={"collection": "../../etc"},
        files=[("files", ("a.txt", b"x", "text/plain"))],
    )
    assert resp.status_code == 200
    # collection is reduced to its basename — nothing escapes materials/.
    assert resp.json()["saved"] == ["etc/a.txt"]
    assert (paths.materials_root(workspace) / "etc" / "a.txt").exists()


def test_embedding_status_and_controls(workspace, monkeypatch):
    started: list = []
    stopped: list = []
    monkeypatch.setattr("tree.gui.server.start_embedding_service", lambda **k: started.append(True))
    monkeypatch.setattr("tree.gui.server.stop_embedding_service", lambda **k: stopped.append(True))
    client = _authed_client(workspace)

    status = client.get("/api/embedding").json()
    assert set(status) == {"status", "backend", "phase", "detail"}

    assert client.post("/api/embedding/start").json() == {"status": "starting"}
    assert client.post("/api/embedding/stop").status_code == 200


def test_clean_endpoint(workspace):
    client = _authed_client(workspace)
    assert paths.runtime_root(workspace).exists()
    resp = client.post("/api/clean")
    assert resp.status_code == 200
    assert "message" in resp.json()


def test_serve_command_runs_headless(monkeypatch):
    from typer.testing import CliRunner

    from tree.cli.app import app as cli_app

    calls: dict = {}

    def fake_run_gui(root, *, host, port, token, open_browser):
        calls.update(host=host, port=port, token=token, open_browser=open_browser)

    monkeypatch.setattr("tree.gui.launch.run_gui", fake_run_gui)
    result = CliRunner().invoke(cli_app, ["serve", "--port", "8790", "--token", "abc"])

    assert result.exit_code == 0
    assert calls == {"host": "127.0.0.1", "port": 8790, "token": "abc", "open_browser": False}


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
