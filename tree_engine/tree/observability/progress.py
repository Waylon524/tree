"""Shared progress state for TREE background runs."""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tree.io import paths


_WRITE_LOCK = threading.RLock()


class ProgressTracker:
    """Write current progress snapshots to .tree/runtime/progress.json."""

    def __init__(self, root: Path):
        self.root = root
        self.path = paths.progress_path(root)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def reset(self) -> None:
        self.write(
            {
                "phase": "starting",
                "message": "TREE is starting",
                "source_ingest": _empty_source_ingest(),
                "planner_progress": _empty_planner_progress(),
                "learning_loop": _empty_learning_loop(),
                "branch_run_progress": {},
            }
        )

    def source_ingest_start(self, files_total: int) -> None:
        self.update(
            {
                "phase": "source_ingest",
                "message": "Preparing source materials",
                "source_ingest": {
                    "files_total": files_total,
                    "files_done": 0,
                    "ocr": {
                        "state": "pending",
                        "files_total": files_total,
                        "files_done": 0,
                    },
                    "archivist": {
                        "state": "idle",
                    },
                    "embedding": {
                        "state": "pending",
                        "chunks_total": 0,
                        "chunks_done": 0,
                    },
                },
            }
        )

    def source_file_done(self, current_file: str, files_done: int, files_total: int) -> None:
        self.update_nested(
            "source_ingest",
            {
                "files_done": files_done,
                "files_total": files_total,
                "current_file": current_file,
            },
        )

    def ocr_event(self, event: dict[str, Any]) -> None:
        self.update({"phase": "source_ingest", "message": "OCR processing source materials"})
        self.update_nested("source_ingest.ocr", event)

    def ocr_file_done(self, current_file: str) -> None:
        with _WRITE_LOCK:
            state = self.read()
            source_ingest = _dict_node(state, "source_ingest")
            ocr = _dict_node(source_ingest, "ocr")
            files_total = _int_value(ocr.get("files_total")) or _int_value(source_ingest.get("files_total"))
            files_done = _int_value(ocr.get("files_done")) + 1
            if files_total:
                files_done = min(files_done, files_total)
            ocr.update(
                {
                    "current_file": current_file,
                    "files_done": files_done,
                    "files_total": files_total,
                    "state": "done" if files_total and files_done >= files_total else "running",
                    "updated_at": _now(),
                }
            )
            source_ingest["ocr"] = ocr
            state["source_ingest"] = source_ingest
            self.write(state)

    def embedding_start(self, chunks_total: int) -> None:
        self.update({"phase": "source_ingest", "message": "Embedding source materials"})
        self.update_nested(
            "source_ingest.embedding",
            {
                "state": "running" if chunks_total else "pending",
                "chunks_total": chunks_total,
                "chunks_done": 0,
            },
        )

    def embedding_done(self, current_chunk: str, chunks_done: int, chunks_total: int) -> None:
        self.update_nested(
            "source_ingest.embedding",
            {
                "state": "done" if chunks_done >= chunks_total else "running",
                "current_chunk": current_chunk,
                "chunks_done": chunks_done,
                "chunks_total": chunks_total,
            },
        )

    def archivist_degraded(self, *, current_file: str, chunk_index: int, error_type: str) -> None:
        self.update_nested(
            "source_ingest.archivist",
            {
                "state": "degraded",
                "current_file": current_file,
                "chunk_index": chunk_index,
                "error_type": error_type,
                "fallback": "raw_ocr",
            },
        )

    def planner_stage(
        self,
        *,
        stage: str,
        stage_label: str,
        stage_index: int,
        stage_total: int = 6,
        details: dict[str, Any] | None = None,
        diagnostics: list[dict[str, Any]] | None = None,
        message: str = "",
    ) -> None:
        entry = {
            "stage": stage,
            "stage_label": stage_label,
            "stage_index": stage_index,
            "stage_total": stage_total,
            "details": details or {},
            "diagnostics": diagnostics or [],
            "updated_at": _now(),
        }
        self.update(
            {
                "phase": "planner",
                "message": message or stage_label,
                "planner_progress": entry,
            }
        )

    def learning_stage(
        self,
        *,
        stage: str,
        stage_label: str,
        stage_index: int,
        stage_total: int,
        chapter: str = "",
        execution_path: str = "",
        tree_id: str = "",
        branch_id: str = "",
        branch_run_id: str = "",
        file_seq: str = "",
        knowledge_point: str = "",
        span_title: str = "",
        iteration: int = 0,
        message: str = "",
    ) -> None:
        execution_path = execution_path or chapter
        span_title = span_title or knowledge_point
        updated_at = _now()
        entry = {
            "stage": stage,
            "stage_label": stage_label,
            "stage_index": stage_index,
            "stage_total": stage_total,
            "execution_path": execution_path,
            "tree_id": tree_id,
            "branch_id": branch_id,
            "chapter": execution_path,
            "file_seq": file_seq,
            "span_title": span_title,
            "knowledge_point": span_title,
            "iteration": iteration,
            "updated_at": updated_at,
        }
        patch: dict[str, Any] = {
            "phase": "learning_loop",
            "message": message or stage_label,
            "learning_loop": entry,
        }
        branch_progress_key = branch_run_id or execution_path
        if branch_progress_key:
            patch["branch_run_progress"] = {
                branch_progress_key: entry,
            }
        self.update(patch)

    def complete(self, message: str) -> None:
        self.update({"phase": "complete", "message": message})

    def update_nested(self, dotted_key: str, value: dict[str, Any]) -> None:
        with _WRITE_LOCK:
            state = self.read()
            node = state
            parts = dotted_key.split(".")
            for key in parts[:-1]:
                child = node.get(key)
                if not isinstance(child, dict):
                    child = {}
                    node[key] = child
                node = child
            leaf = node.get(parts[-1])
            if not isinstance(leaf, dict):
                leaf = {}
            leaf.update(value)
            leaf["updated_at"] = _now()
            node[parts[-1]] = leaf
            self.write(state)

    def update(self, patch: dict[str, Any]) -> None:
        with _WRITE_LOCK:
            state = self.read()
            _deep_update(state, patch)
            state["updated_at"] = _now()
            self.write(state)

    def read(self) -> dict[str, Any]:
        try:
            state = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(state, dict):
                state.setdefault("source_ingest", _empty_source_ingest())
                state.setdefault("planner_progress", _empty_planner_progress())
                state.setdefault("learning_loop", _empty_learning_loop())
                state.setdefault("branch_run_progress", {})
                return state
        except (OSError, json.JSONDecodeError):
            pass
        return {
            "phase": "idle",
            "message": "",
            "source_ingest": _empty_source_ingest(),
            "planner_progress": _empty_planner_progress(),
            "learning_loop": _empty_learning_loop(),
            "branch_run_progress": {},
        }

    def write(self, state: dict[str, Any]) -> None:
        state["updated_at"] = _now()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.path.with_suffix(".tmp")
        with _WRITE_LOCK:
            tmp_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(tmp_path, self.path)


def load_progress(root: Path) -> dict[str, Any]:
    return ProgressTracker(root).read()


def _empty_source_ingest() -> dict[str, Any]:
    return {
        "files_total": 0,
        "files_done": 0,
        "ocr": {"state": "idle", "files_total": 0, "files_done": 0},
        "archivist": {"state": "idle"},
        "embedding": {"state": "idle", "chunks_total": 0, "chunks_done": 0},
    }


def _empty_learning_loop() -> dict[str, Any]:
    return {
        "stage": "idle",
        "stage_label": "Idle",
        "stage_index": 0,
        "stage_total": 5,
        "execution_path": "",
        "tree_id": "",
        "branch_id": "",
        "chapter": "",
        "file_seq": "",
        "span_title": "",
        "knowledge_point": "",
        "iteration": 0,
    }


def _empty_planner_progress() -> dict[str, Any]:
    return {
        "stage": "idle",
        "stage_label": "Idle",
        "stage_index": 0,
        "stage_total": 6,
        "details": {},
        "diagnostics": [],
    }


def _deep_update(target: dict[str, Any], patch: dict[str, Any]) -> None:
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            _deep_update(target[key], value)
        else:
            target[key] = value


def _dict_node(parent: dict[str, Any], key: str) -> dict[str, Any]:
    value = parent.get(key)
    if isinstance(value, dict):
        return value
    child: dict[str, Any] = {}
    parent[key] = child
    return child


def _int_value(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
