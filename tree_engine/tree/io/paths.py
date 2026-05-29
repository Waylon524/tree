"""Workspace path helpers."""

from __future__ import annotations

import os
from pathlib import Path


def app_home() -> Path:
    """Return the per-user TREE home used for global config and services."""
    override = os.environ.get("TREE_HOME")
    return Path(override).expanduser() if override else Path.home() / ".tree"


def global_config_path() -> Path:
    return app_home() / "config.env"


def global_services_root() -> Path:
    return app_home() / "services"


def workspace_home(root: Path) -> Path:
    return root / ".tree"


def workspace_config_path(root: Path) -> Path:
    return workspace_home(root) / "config.env"


def legacy_workspace_env_path(root: Path) -> Path:
    return root / ".env"


def legacy_runtime_root(root: Path) -> Path:
    return root / "tree_engine" / ".runtime"


def runtime_root(root: Path) -> Path:
    return workspace_home(root) / "runtime"


def materials_root(root: Path) -> Path:
    return root / "materials"


def outputs_root(root: Path) -> Path:
    return root / "outputs"


def source_root(root: Path) -> Path:
    return runtime_root(root) / "source_materials"


def drafts_root(root: Path) -> Path:
    return runtime_root(root) / "drafts"


def pipeline_temp_root(root: Path) -> Path:
    return runtime_root(root) / "pipeline-temp"


def pipeline_state_path(root: Path) -> Path:
    return runtime_root(root) / "pipeline-state.json"


def progress_path(root: Path) -> Path:
    return runtime_root(root) / "progress.json"


def rag_store_path(root: Path) -> Path:
    return runtime_root(root) / "rag-store"


def services_root(root: Path) -> Path:
    return runtime_root(root) / "services"


def service_root(root: Path, name: str) -> Path:
    if name == "embedding":
        return global_services_root()
    return services_root(root)


def service_pid_path(root: Path, name: str) -> Path:
    return service_root(root, name) / f"{name}.pid"


def service_log_path(root: Path, name: str) -> Path:
    return service_root(root, name) / f"{name}.log"


def service_stop_path(root: Path, name: str) -> Path:
    return service_root(root, name) / f"{name}.stop"
