"""Lifecycle helpers for foreground and background engine commands."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from tree.io import paths


@dataclass(frozen=True)
class LifecycleResult:
    message: str


def start_engine(root: Path) -> LifecycleResult:
    paths.ensure_workspace_dirs(root)
    pid_path = paths.service_pid_path(root, "engine")
    if pid_path.exists():
        return LifecycleResult(f"engine already running (pid {pid_path.read_text(encoding='utf-8').strip()})")

    log_path = paths.service_log_path(root, "engine")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log = log_path.open("ab")
    proc = subprocess.Popen(
        [sys.executable, "-m", "tree.cli.app", "run"],
        cwd=root,
        stdout=log,
        stderr=log,
        start_new_session=True,
    )
    pid_path.write_text(str(proc.pid), encoding="utf-8")
    return LifecycleResult(f"engine started (pid {proc.pid})")


def stop_engine(root: Path) -> LifecycleResult:
    pid_path = paths.service_pid_path(root, "engine")
    if not pid_path.exists():
        return LifecycleResult("engine not running")
    pid = int(pid_path.read_text(encoding="utf-8").strip())
    _kill_pid(pid)
    pid_path.unlink(missing_ok=True)
    return LifecycleResult(f"engine stopped (pid {pid})")


def quit_tree(root: Path) -> LifecycleResult:
    return stop_engine(root)


def _kill_pid(pid: int) -> None:
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return
