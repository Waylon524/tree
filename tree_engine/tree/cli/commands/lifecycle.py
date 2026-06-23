"""Lifecycle helpers for foreground and background engine commands."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

from tree.cli import theme
from tree.config import load_runtime_env
from tree.io import paths, process
from tree.rag.service import start_embedding_service, stop_embedding_service


@dataclass(frozen=True)
class LifecycleResult:
    message: str


def start_engine(root: Path) -> LifecycleResult:
    paths.ensure_workspace_dirs(root)
    load_runtime_env(root)
    embedding = start_embedding_service()
    pid_path = paths.service_pid_path(root, "engine")
    if pid_path.exists():
        try:
            pid = int(pid_path.read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            pid_path.unlink(missing_ok=True)
        else:
            if process.pid_alive(pid):
                return LifecycleResult(
                    f"{embedding.message}\n"
                    f"{theme.label('engine')} {theme.status('running')} "
                    f"({theme.label('pid')} {theme.path(pid)})"
                )
            pid_path.unlink(missing_ok=True)

    log_path = paths.service_log_path(root, "engine")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    # Truncate (not append): each fresh engine start gets its own log, so the
    # /watch error panel never resurfaces tracebacks from a previous run.
    log = log_path.open("wb")
    proc = process.spawn_detached(
        _engine_run_argv(),
        cwd=root,
        stdout=log,
        stderr=log,
    )
    pid_path.write_text(str(proc.pid), encoding="utf-8")
    return LifecycleResult(
        f"{embedding.message}\n"
        f"{theme.label('engine')} {theme.success('started')} "
        f"({theme.label('pid')} {theme.path(proc.pid)})"
    )


def _engine_run_argv() -> list[str]:
    if getattr(sys, "frozen", False):
        return [sys.executable, "run"]
    return [sys.executable, "-m", "tree.cli.app", "run"]


def stop_engine(root: Path) -> LifecycleResult:
    pid_path = paths.service_pid_path(root, "engine")
    if not pid_path.exists():
        embedding = stop_embedding_service(force=True)
        return LifecycleResult(
            f"{theme.label('engine')} {theme.status('not found')}\n{embedding.message}"
        )
    pid = int(pid_path.read_text(encoding="utf-8").strip())
    process.terminate_pid(pid)
    pid_path.unlink(missing_ok=True)
    embedding = stop_embedding_service(force=True)
    return LifecycleResult(
        f"{theme.label('engine')} {theme.success('stopped')} "
        f"({theme.label('pid')} {theme.path(pid)})\n{embedding.message}"
    )


def quit_tree(root: Path) -> LifecycleResult:
    return stop_engine(root)


def engine_status(root: Path) -> str:
    """Return "running" if the background engine process is alive, else "stopped".

    Cleans up a stale pid file left by an engine that finished or crashed, so the
    status reflects the live process — not just whether a pid file exists.
    """
    pid_path = paths.service_pid_path(root, "engine")
    if not pid_path.exists():
        return "stopped"
    try:
        pid = int(pid_path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return "stopped"
    if process.pid_alive(pid):
        return "running"
    pid_path.unlink(missing_ok=True)
    return "stopped"
