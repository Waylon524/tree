"""Step 9 config and planner CLI tests."""

from __future__ import annotations

from typer.testing import CliRunner

from tree.cli.app import app
from tree.io import paths
from tree.planner.store import envelope, write_json_atomic


def test_planner_rebuild_invokes_prepare_sources(monkeypatch):
    calls = []

    class _Settings:
        @classmethod
        def from_env(cls):
            return "settings"

    class _Engine:
        def __init__(self, settings):
            calls.append(("init", settings))

        async def prepare_sources(self):
            calls.append(("prepare", None))
            return {"mtu_count": 2, "node_count": 2, "dag_svg_path": ".tree/runtime/planner/knowledge-dag.svg"}

    monkeypatch.setattr("tree.cli.app.Settings", _Settings)
    monkeypatch.setattr("tree.cli.app.TreeEngine", _Engine)
    monkeypatch.setattr("tree.cli.app.ensure_embedding_ready", lambda: calls.append(("embed", None)))

    result = CliRunner().invoke(app, ["planner", "rebuild"])

    assert result.exit_code == 0
    assert calls == [("embed", None), ("init", "settings"), ("prepare", None)]
    assert "nodes=2" in result.stdout
    assert "knowledge-dag.svg" in result.stdout
    assert "branches" not in result.stdout


def test_planner_dag_svg_command_writes_from_existing_dag(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    write_json_atomic(
        paths.knowledge_dag_path(tmp_path),
        envelope(
            schema="tree.knowledge-dag",
            data={
                "nodes": [{"node_id": "n1", "title": "根知识点", "source_order_index": 0}],
                "edges": [],
                "roots": ["n1"],
            },
        ),
    )

    result = CliRunner().invoke(app, ["planner", "dag-svg"])

    assert result.exit_code == 0
    assert "knowledge-dag.svg" in result.stdout
    assert "001. 根知识点" in paths.knowledge_dag_svg_path(tmp_path).read_text(encoding="utf-8")
    assert "001. 根知识点" in (paths.outputs_root(tmp_path) / "knowledge-dag.svg").read_text(
        encoding="utf-8"
    )


def test_planner_dag_svg_command_requires_existing_dag(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    result = CliRunner().invoke(app, ["planner", "dag-svg"])

    assert result.exit_code != 0
    assert "knowledge-dag.json not found" in result.output


def test_doctor_shows_embedding_model_and_server_status(monkeypatch):
    monkeypatch.setattr("tree.cli.app.embedding_model_status", lambda: "cached")
    monkeypatch.setattr("tree.cli.app.embedding_service_status", lambda: "running")
    monkeypatch.setattr("tree.cli.app.pdf_crypto_runtime_status", lambda: (True, "cryptography"))

    result = CliRunner().invoke(app, ["doctor"])

    assert result.exit_code == 0
    assert "embedding model" in result.stdout
    assert "cached" in result.stdout
    assert "embedding server" in result.stdout
    assert "running" in result.stdout
    assert "PDF AES crypto" in result.stdout


def test_doctor_strict_fails_when_pdf_crypto_is_missing(monkeypatch):
    monkeypatch.setattr("tree.cli.app.pdf_crypto_runtime_status", lambda: (False, "missing"))

    result = CliRunner().invoke(app, ["doctor", "--strict"])

    assert result.exit_code == 1
    assert "PDF AES crypto" in result.stdout


def test_embedding_commands_route_to_model_and_service_helpers(tmp_path, monkeypatch):
    from tree.rag.model_cache import EmbeddingModel

    calls = []
    model_path = tmp_path / "model.gguf"
    model_path.write_text("model", encoding="utf-8")
    monkeypatch.setattr(
        "tree.cli.app.ensure_embedding_model",
        lambda: calls.append("install") or EmbeddingModel(path=model_path, source="env"),
    )
    monkeypatch.setattr("tree.cli.app.embedding_model_status", lambda: "cached")
    monkeypatch.setattr("tree.cli.app.embedding_service_status", lambda: "running")
    monkeypatch.setattr(
        "tree.cli.app.start_embedding_service",
        lambda: calls.append("start") or type("Result", (), {"message": "embedding started"})(),
    )
    monkeypatch.setattr(
        "tree.cli.app.stop_embedding_service",
        lambda force=True: calls.append(("stop", force))
        or type("Result", (), {"message": "embedding stopped"})(),
    )
    runner = CliRunner()

    install = runner.invoke(app, ["embedding", "install"])
    status = runner.invoke(app, ["embedding", "status"])
    start = runner.invoke(app, ["embedding", "start"])
    stop = runner.invoke(app, ["embedding", "stop"])

    assert install.exit_code == 0
    assert "env" in install.stdout
    assert "cached" in status.stdout
    assert "running" in status.stdout
    assert "embedding started" in start.stdout
    assert "embedding stopped" in stop.stdout
    assert calls == ["install", "start", ("stop", True)]


def test_models_and_prompts_commands_show_current_config(monkeypatch):
    class _Role:
        def __init__(self, model):
            self.model = model
            self.base_url = "https://example.test"
            self.api_key = "secret"

    class _Settings:
        examiner = _Role("exam-model")
        student = _Role("student-model")
        writer = _Role("writer-model")
        archivist = _Role("arch-model")
        dagger = _Role("dagger-model")

        @classmethod
        def from_env(cls, require_llm=True):
            return cls()

    monkeypatch.setattr("tree.cli.app.Settings", _Settings)
    runner = CliRunner()

    models = runner.invoke(app, ["models"])
    prompts = runner.invoke(app, ["prompts"])

    assert models.exit_code == 0
    assert "examiner" in models.stdout
    assert "exam-model" in models.stdout
    assert prompts.exit_code == 0
    assert "examiner" in prompts.stdout
    assert "dagger" in prompts.stdout


def test_setup_writes_workspace_config(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TREE_HOME", str(tmp_path / "home"))
    result = CliRunner().invoke(
        app,
        [
            "setup",
            "--workspace",
            "--llm-api-key",
            "key",
            "--llm-base-url",
            "https://llm.test",
            "--llm-model",
            "model",
        ],
    )

    assert result.exit_code == 0
    config = tmp_path / ".tree" / "config.env"
    assert "LLM_API_KEY=key" in config.read_text(encoding="utf-8")


def test_setup_wizard_writes_global_config(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TREE_HOME", str(tmp_path / "home"))
    input_text = "\n".join(
        [
            "llm-key",
            "https://llm.test",
            "default-model",
            "exam-model",
            "student-model",
            "writer-model",
            "arch-model",
            "dagger-model",
            "ocr-key",
            "",
        ]
    )

    result = CliRunner().invoke(app, ["setup"], input=input_text)

    assert result.exit_code == 0
    config = (tmp_path / "home" / "config.env").read_text(encoding="utf-8")
    assert "LLM_API_KEY=llm-key" in config
    assert "LLM_BASE_URL=https://llm.test" in config
    assert "LLM_MODEL=default-model" in config
    assert "EXAMINER_MODEL=exam-model" in config
    assert "STUDENT_MODEL=student-model" in config
    assert "WRITER_MODEL=writer-model" in config
    assert "ARCHIVIST_MODEL=arch-model" in config
    assert "DAGGER_MODEL=dagger-model" in config
    assert "PADDLEOCR_API_TOKEN=ocr-key" in config
    assert "PADDLEOCR_API_URL=https://paddleocr.aistudio-app.com/api/v2/ocr/jobs" in config
    assert "PADDLEOCR_MODEL=PaddleOCR-VL-1.6" in config


def test_setup_wizard_defaults_to_deepseek_v4_flash(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TREE_HOME", str(tmp_path / "home"))
    input_text = "\n".join(
        [
            "llm-key",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "ocr-key",
            "",
        ]
    )

    result = CliRunner().invoke(app, ["setup"], input=input_text)

    assert result.exit_code == 0
    config = (tmp_path / "home" / "config.env").read_text(encoding="utf-8")
    assert "LLM_MODEL=deepseek-v4-flash" in config
    assert "EXAMINER_MODEL=deepseek-v4-flash" in config
    assert "STUDENT_MODEL=deepseek-v4-flash" in config
    assert "WRITER_MODEL=deepseek-v4-flash" in config
    assert "ARCHIVIST_MODEL=deepseek-v4-flash" in config
    assert "DAGGER_MODEL=deepseek-v4-flash" in config


def test_setup_wizard_workspace_scope(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TREE_HOME", str(tmp_path / "home"))
    input_text = "\n".join(
        [
            "llm-key",
            "https://llm.test",
            "default-model",
            "exam-model",
            "student-model",
            "writer-model",
            "arch-model",
            "dagger-model",
            "ocr-key",
            "",
        ]
    )

    result = CliRunner().invoke(app, ["setup", "--workspace"], input=input_text)

    assert result.exit_code == 0
    assert (tmp_path / ".tree" / "config.env").exists()
    assert not (tmp_path / "home" / "config.env").exists()


def test_setup_existing_config_requires_force(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TREE_HOME", str(tmp_path / "home"))
    config = tmp_path / "home" / "config.env"
    config.parent.mkdir(parents=True)
    config.write_text("LLM_MODEL=old-model\n", encoding="utf-8")

    result = CliRunner().invoke(app, ["setup"], input="unused\n")

    assert result.exit_code == 0
    assert "already exists" in result.stdout
    assert config.read_text(encoding="utf-8") == "LLM_MODEL=old-model\n"


def test_setup_force_uses_existing_defaults_and_updates(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TREE_HOME", str(tmp_path / "home"))
    config = tmp_path / "home" / "config.env"
    config.parent.mkdir(parents=True)
    config.write_text(
        "\n".join(
            [
                "LLM_API_KEY=old-key",
                "LLM_BASE_URL=https://old.test",
                "LLM_MODEL=old-model",
                "PADDLEOCR_API_TOKEN=old-ocr",
                "",
            ]
        ),
        encoding="utf-8",
    )
    input_text = "\n".join(
        [
            "n",
            "new-key",
            "https://new.test",
            "new-model",
            "exam-new",
            "student-new",
            "writer-new",
            "arch-new",
            "dagger-new",
            "n",
            "new-ocr",
            "",
        ]
    )

    result = CliRunner().invoke(app, ["setup", "--force"], input=input_text)

    assert result.exit_code == 0
    written = config.read_text(encoding="utf-8")
    assert "LLM_API_KEY=new-key" in written
    assert "LLM_BASE_URL=https://new.test" in written
    assert "LLM_MODEL=new-model" in written
    assert "DAGGER_MODEL=dagger-new" in written
    assert "PADDLEOCR_API_TOKEN=new-ocr" in written
