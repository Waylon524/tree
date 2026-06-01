"""Step 9 config and planner CLI tests."""

from __future__ import annotations

from typer.testing import CliRunner

from tree.cli.app import app


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
            return {"mtu_count": 2, "node_count": 2, "branch_count": 1}

    monkeypatch.setattr("tree.cli.app.Settings", _Settings)
    monkeypatch.setattr("tree.cli.app.TreeEngine", _Engine)

    result = CliRunner().invoke(app, ["planner", "rebuild"])

    assert result.exit_code == 0
    assert calls == [("init", "settings"), ("prepare", None)]
    assert "branches=1" in result.stdout


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
    result = CliRunner().invoke(
        app,
        [
            "setup",
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
