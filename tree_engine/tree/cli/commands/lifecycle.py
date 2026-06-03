"""Lifecycle helpers for foreground and background engine commands."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from tree.cli import theme
from tree.io import paths
from tree.rag.service import start_embedding_service, stop_embedding_service


@dataclass(frozen=True)
class LifecycleResult:
    message: str


def start_engine(root: Path) -> LifecycleResult:
    paths.ensure_workspace_dirs(root)
    embedding = start_embedding_service()
    pid_path = paths.service_pid_path(root, "engine")
    if pid_path.exists():
        pid = pid_path.read_text(encoding="utf-8").strip()
        return LifecycleResult(
            f"{embedding.message}\n"
            f"{theme.label('engine')} {theme.status('running')} "
            f"({theme.label('pid')} {theme.path(pid)})"
        )

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
    return LifecycleResult(
        f"{embedding.message}\n"
        f"{theme.label('engine')} {theme.success('started')} "
        f"({theme.label('pid')} {theme.path(proc.pid)})"
    )


def stop_engine(root: Path) -> LifecycleResult:
    pid_path = paths.service_pid_path(root, "engine")
    if not pid_path.exists():
        return LifecycleResult(f"{theme.label('engine')} {theme.status('not found')}")
    pid = int(pid_path.read_text(encoding="utf-8").strip())
    _kill_pid(pid)
    pid_path.unlink(missing_ok=True)
    return LifecycleResult(
        f"{theme.label('engine')} {theme.success('stopped')} "
        f"({theme.label('pid')} {theme.path(pid)})"
    )


def quit_tree(root: Path) -> LifecycleResult:
    engine = stop_engine(root)
    embedding = stop_embedding_service(force=True)
    return LifecycleResult(f"{engine.message}\n{embedding.message}")


def _kill_pid(pid: int) -> None:
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return
