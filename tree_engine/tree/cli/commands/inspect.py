"""Inspection commands: status / progress / watch / materials / logs / clean."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from tree.cli import theme
from tree.cli.dashboard.model import build_watch_model
from tree.cli.dashboard.panels import render_watch
from tree.ingest.pipeline import MATERIAL_EXTENSIONS
from tree.io import paths
from tree.observability.progress import load_progress


def status_text(root: Path) -> str:
    model = build_watch_model(root)
    active_nodes = ", ".join(theme.active(node) for node in model["active_node_runs"]) or "-"
    return "\n".join(
        [
            theme.kv("phase", model["phase"], value_style="status"),
            theme.kv("message", model["message"]),
            theme.kv("materials", model["material_count"]),
            theme.kv("nodes", model["node_count"]),
            theme.kv("edges", model["edge_count"]),
            f"{theme.label('active nodes:')} {active_nodes}",
        ]
    )


def progress_text(root: Path) -> str:
    return json.dumps(load_progress(root), ensure_ascii=False, indent=2)


def watch_text(root: Path) -> str:
    return render_watch(root)


def materials_text(root: Path) -> str:
    material_root = paths.materials_root(root)
    if not material_root.exists():
        return "No materials directory."
    rows = []
    for path in sorted(material_root.rglob("*")):
        if not path.is_file() or path.name.startswith("."):
            continue
        if path.suffix.lower() not in MATERIAL_EXTENSIONS:
            continue
        rows.append(str(path.relative_to(material_root)))
    return "\n".join(rows) if rows else "No supported materials."


def logs_text(root: Path) -> str:
    log_paths: list[Path] = []
    if paths.services_root(root).exists():
        log_paths.extend(
            sorted(
                path
                for path in paths.services_root(root).iterdir()
                if path.is_file()
                and (path.suffix in {".log", ".jsonl"} or ".jsonl." in path.name)
            )
        )
    if paths.pipeline_temp_root(root).exists():
        log_paths.extend(sorted(paths.pipeline_temp_root(root).glob("*.log")))
    if not log_paths:
        return "No logs found."
    return "\n".join(str(path.relative_to(root)) for path in log_paths)


def clean_runtime(root: Path) -> str:
    runtime = paths.runtime_root(root)
    if not runtime.exists():
        return "Runtime already clean."
    shutil.rmtree(runtime)
    return "Runtime cleaned."
