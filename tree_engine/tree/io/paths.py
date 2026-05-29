"""Workspace path helpers."""

from __future__ import annotations

from pathlib import Path


def engine_dir(root: Path) -> Path:
    return root / "tree_engine"


def runtime_root(root: Path) -> Path:
    return engine_dir(root) / ".runtime"


def source_root(root: Path) -> Path:
    return runtime_root(root) / "source_materials"


def drafts_root(root: Path) -> Path:
    return runtime_root(root) / "drafts"


def pipeline_temp_root(root: Path) -> Path:
    return runtime_root(root) / "pipeline-temp"


def pipeline_state_path(root: Path) -> Path:
    return runtime_root(root) / "pipeline-state.json"


def rag_store_path(root: Path) -> Path:
    return runtime_root(root) / "rag-store"


def services_root(root: Path) -> Path:
    return runtime_root(root) / "services"


def service_pid_path(root: Path, name: str) -> Path:
    return services_root(root) / f"{name}.pid"


def service_log_path(root: Path, name: str) -> Path:
    return services_root(root) / f"{name}.log"


def service_stop_path(root: Path, name: str) -> Path:
    return services_root(root) / f"{name}.stop"
