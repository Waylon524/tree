"""Step 9 lifecycle and REPL tests."""

from __future__ import annotations

from types import SimpleNamespace

from typer.testing import CliRunner

from tree.cli.app import app
from tree.cli import repl
from tree.cli.repl import handle_slash_command
from tree.io import paths


def test_no_args_enters_repl_after_embedding_ready(monkeypatch):
    calls = []

    monkeypatch.setattr("tree.cli.app.ensure_embedding_ready", lambda: calls.append("embed"))
    monkeypatch.setattr("tree.cli.repl.run_repl", lambda: calls.append("repl"))

    result = CliRunner().invoke(app, [])

    assert result.exit_code == 0
    assert calls == ["embed", "repl"]


def test_repl_routes_mainstream_commands(tmp_path):
    paths.ensure_workspace_dirs(tmp_path)

    assert "phase:" in handle_slash_command("/status", root=tmp_path)
    assert "TREE Watch" in handle_slash_command("/watch", root=tmp_path)
    help_text = handle_slash_command("/help", root=tmp_path)
    assert "commands" in help_text
    for command in (
        "/init",
        "/setup",
        "/materials",
        "/run",
        "/watch",
        "/status",
        "/stop",
        "/quit",
        "/help",
    ):
        assert command in help_text
    for hidden_command in ("/start", "/progress", "/exit"):
        assert hidden_command not in help_text


def test_repl_init_creates_workspace_dirs(tmp_path):
    result = handle_slash_command("/init", root=tmp_path)

    assert "Initialized" in result
    assert paths.materials_root(tmp_path).exists()
    assert paths.outputs_root(tmp_path).exists()
    assert paths.runtime_root(tmp_path).exists()


def test_repl_setup_runs_global_wizard(tmp_path, monkeypatch):
    calls = []
    config_path = tmp_path / "tree-home" / "config.env"

    monkeypatch.setenv("TREE_HOME", str(tmp_path / "tree-home"))
    monkeypatch.setattr(
        "tree.cli.repl.config_cmd.run_setup_wizard",
        lambda root, *, env_path, scope: calls.append((root, env_path, scope)) or env_path,
    )

    result = handle_slash_command("/setup", root=tmp_path)

    assert calls == [(tmp_path, config_path, "global")]
    assert "Wrote" in result
    assert str(config_path) in result


def test_repl_run_starts_background_engine(tmp_path, monkeypatch):
    calls = []

    monkeypatch.setattr(
        "tree.cli.repl.start_engine",
        lambda root: calls.append(root) or SimpleNamespace(message="engine started"),
    )

    result = handle_slash_command("/run", root=tmp_path)

    assert calls == [tmp_path]
    assert result == "engine started"


def test_repl_start_is_hidden_alias_for_run(tmp_path, monkeypatch):
    calls = []

    monkeypatch.setattr(
        "tree.cli.repl.start_engine",
        lambda root: calls.append(root) or SimpleNamespace(message="engine started"),
    )

    result = handle_slash_command("/start", root=tmp_path)

    assert calls == [tmp_path]
    assert result == "engine started"


def test_repl_stop_stops_only_engine(tmp_path, monkeypatch):
    calls = []
    embedding_stops = []
    monkeypatch.setattr(
        "tree.cli.repl.stop_engine",
        lambda root: calls.append(root) or SimpleNamespace(message="engine stopped"),
    )
    monkeypatch.setattr(
        "tree.cli.repl.stop_embedding_service",
        lambda force=True: embedding_stops.append(force),
        raising=False,
    )

    result = handle_slash_command("/stop", root=tmp_path)

    assert calls == [tmp_path]
    assert embedding_stops == []
    assert result == "engine stopped"


def test_repl_exit_warns_without_exiting(tmp_path):
    result = handle_slash_command("/exit", root=tmp_path)

    assert "/quit" in result
    assert hasattr(repl, "should_exit_repl")
    assert repl.should_exit_repl("/exit") is False
    assert repl.should_exit_repl("/quit") is True


def test_start_and_stop_manage_engine_pid_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    started = []
    killed = []
    embedding_starts = []

    class _Proc:
        pid = 4242

    def _fake_popen(cmd, cwd, stdout, stderr, start_new_session):
        started.append((cmd, cwd, start_new_session))
        return _Proc()

    monkeypatch.setattr("tree.cli.commands.lifecycle.subprocess.Popen", _fake_popen)
    monkeypatch.setattr("tree.cli.commands.lifecycle._kill_pid", lambda pid: killed.append(pid))
    monkeypatch.setattr(
        "tree.cli.commands.lifecycle.start_embedding_service",
        lambda: embedding_starts.append("embedding") or SimpleNamespace(message="embedding started"),
    )
    runner = CliRunner()

    start = runner.invoke(app, ["start"])
    assert start.exit_code == 0
    assert "started" in start.stdout
    assert embedding_starts == ["embedding"]
    assert paths.service_pid_path(tmp_path, "engine").read_text(encoding="utf-8") == "4242"
    assert started and started[0][1] == tmp_path

    stop = runner.invoke(app, ["stop"])
    assert stop.exit_code == 0
    assert "stopped" in stop.stdout
    assert killed == [4242]
    assert not paths.service_pid_path(tmp_path, "engine").exists()


def test_quit_delegates_to_stop(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    calls = []
    embedding_stops = []
    monkeypatch.setattr(
        "tree.cli.commands.lifecycle.stop_engine",
        lambda root: calls.append(root) or SimpleNamespace(message="engine stopped"),
    )
    monkeypatch.setattr(
        "tree.cli.commands.lifecycle.stop_embedding_service",
        lambda force=True: embedding_stops.append(force)
        or SimpleNamespace(message="embedding stopped"),
    )

    result = CliRunner().invoke(app, ["quit"])

    assert result.exit_code == 0
    assert calls == [tmp_path]
    assert embedding_stops == [True]
    assert "engine stopped" in result.stdout
    assert "embedding stopped" in result.stdout


def test_repl_quit_stops_embedding_service(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(
        "tree.cli.repl.quit_tree",
        lambda root: calls.append(root) or SimpleNamespace(message="engine stopped\nembedding stopped"),
    )

    result = handle_slash_command("/quit", root=tmp_path)

    assert calls == [tmp_path]
    assert "embedding stopped" in result
