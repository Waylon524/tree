"""Cross-platform process helper tests (run on the host platform)."""

from __future__ import annotations

import os
import sys
import time

from tree.io import process


def _wait_until_dead(pid: int, *, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not process.pid_alive(pid):
            return True
        time.sleep(0.02)
    return False


def test_pid_alive_for_current_process():
    assert process.pid_alive(os.getpid()) is True


def test_pid_alive_false_for_reaped_child():
    proc = process.spawn_detached([sys.executable, "-c", "pass"])
    proc.wait(timeout=5)
    assert _wait_until_dead(proc.pid)


def test_spawn_detached_runs_and_terminate_stops_it():
    proc = process.spawn_detached([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        assert process.pid_alive(proc.pid) is True
        process.terminate_pid(proc.pid, force=True)
        # wait() both confirms exit and reaps the child so it can't linger as a
        # zombie (which would still look "alive" to os.kill(pid, 0) on POSIX).
        assert proc.wait(timeout=5) is not None
    finally:
        if proc.poll() is None:
            process.terminate_pid(proc.pid, force=True)
            proc.wait(timeout=5)


def test_terminate_missing_pid_is_noop():
    # A reaped child's pid is safe to terminate again (no exception).
    proc = process.spawn_detached([sys.executable, "-c", "pass"])
    proc.wait(timeout=5)
    _wait_until_dead(proc.pid)
    process.terminate_pid(proc.pid)
    process.terminate_pid(proc.pid, force=True)


def test_spawn_detached_uses_platform_appropriate_flags(monkeypatch):
    captured = {}

    def _fake_popen(args, cwd=None, stdout=None, stderr=None, **kwargs):
        captured["kwargs"] = kwargs

        class _P:
            pid = 1

        return _P()

    monkeypatch.setattr(process.subprocess, "Popen", _fake_popen)
    process.spawn_detached(["x"])

    if sys.platform == "win32":
        assert "creationflags" in captured["kwargs"]
        assert "start_new_session" not in captured["kwargs"]
    else:
        assert captured["kwargs"].get("start_new_session") is True
        assert "creationflags" not in captured["kwargs"]
