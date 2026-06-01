"""CLI smoke tests for Step 8 foreground commands."""

from __future__ import annotations

from typer.testing import CliRunner

from tree.cli.app import app


def test_run_command_invokes_foreground_engine(monkeypatch):
    calls = []

    class _Settings:
        @classmethod
        def from_env(cls):
            return "settings"

    class _Engine:
        def __init__(self, settings):
            calls.append(("init", settings))

        async def run(self):
            calls.append(("run", None))

    monkeypatch.setattr("tree.cli.app.Settings", _Settings)
    monkeypatch.setattr("tree.cli.app.TreeEngine", _Engine)

    result = CliRunner().invoke(app, ["run"])

    assert result.exit_code == 0
    assert calls == [("init", "settings"), ("run", None)]


def test_ingest_command_copies_input_into_materials_and_prepares_sources(tmp_path, monkeypatch):
    source = tmp_path / "outside.md"
    source.write_text("hello", encoding="utf-8")
    calls = []

    class _Settings:
        project_root = tmp_path

        @classmethod
        def from_env(cls):
            return cls()

    class _Engine:
        def __init__(self, settings):
            self.settings = settings
            calls.append(("init", settings.project_root))

        async def prepare_sources(self):
            calls.append(("prepare", None))
            return {"mtu_count": 1}

    monkeypatch.setattr("tree.cli.app.Settings", _Settings)
    monkeypatch.setattr("tree.cli.app.TreeEngine", _Engine)

    result = CliRunner().invoke(app, ["ingest", "--input", str(source), "--collection", "课件"])

    assert result.exit_code == 0
    assert (tmp_path / "materials" / "课件" / "outside.md").read_text(encoding="utf-8") == "hello"
    assert calls == [("init", tmp_path), ("prepare", None)]
