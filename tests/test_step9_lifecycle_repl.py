"""Step 9 lifecycle and REPL tests."""

from __future__ import annotations

from types import SimpleNamespace

from typer.testing import CliRunner

from tree.cli.app import app
from tree.cli.repl import handle_slash_command
from tree.io import paths


def test_repl_routes_inspection_commands(tmp_path):
    paths.ensure_workspace_dirs(tmp_path)

    assert "phase:" in handle_slash_command("/status", root=tmp_path)
    assert "TREE Watch" in handle_slash_command("/watch", root=tmp_path)
    assert "phase" in handle_slash_command("/progress", root=tmp_path)
    assert "commands" in handle_slash_command("/help", root=tmp_path)


def test_start_and_stop_manage_engine_pid_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    started = []
    killed = []

    class _Proc:
        pid = 4242

    def _fake_popen(cmd, cwd, stdout, stderr, start_new_session):
        started.append((cmd, cwd, start_new_session))
        return _Proc()

    monkeypatch.setattr("tree.cli.commands.lifecycle.subprocess.Popen", _fake_popen)
    monkeypatch.setattr("tree.cli.commands.lifecycle._kill_pid", lambda pid: killed.append(pid))
    runner = CliRunner()

    start = runner.invoke(app, ["start"])
    assert start.exit_code == 0
    assert "started" in start.stdout
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
    monkeypatch.setattr(
        "tree.cli.commands.lifecycle.stop_engine",
        lambda root: calls.append(root) or SimpleNamespace(message="engine stopped"),
    )

    result = CliRunner().invoke(app, ["quit"])

    assert result.exit_code == 0
    assert calls == [tmp_path]
    assert "engine stopped" in result.stdout
