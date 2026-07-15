"""ProgressTracker: writes progress.json consumed by the dashboard."""

from __future__ import annotations

import time
import threading
import uuid
from pathlib import Path
from typing import Any

from tree.io import paths
from tree.planner.store import write_json_atomic


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())


STAGES: tuple[tuple[str, str], ...] = (
    ("ocr", "OCR"),
    ("clean", "Clean"),
    ("cut", "Cut"),
    ("embed", "Embed"),
    ("cluster", "Cluster"),
    ("link", "Link"),
    ("noderun", "NodeRun"),
)


def _empty_stage(label: str) -> dict[str, Any]:
    return {
        "label": label,
        "done": 0,
        "total": 0,
        "active": [],
        "status": "pending",
        "message": "",
    }


def _empty_stages() -> dict[str, Any]:
    return {key: _empty_stage(label) for key, label in STAGES}


def _empty_state() -> dict[str, Any]:
    return {
        "phase": "idle",
        "message": "",
        "updated_at": _now(),
        "run_id": "",
        "generation_id": "",
        "errors": [],
        "source_ingest": {},
        "planner": {},
        "learning_loop": {},
        "stages": _empty_stages(),
    }


class ProgressTracker:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.path = paths.progress_path(self.root)
        self._lock = threading.Lock()
        self._run_id: str | None = None

    def reset(self) -> None:
        self._run_id = None
        write_json_atomic(self.path, _empty_state())

    def begin_run(self) -> str:
        """Start a run without discarding durable cumulative stage counters."""
        with self._lock:
            state = self.load()
            self._run_id = uuid.uuid4().hex
            for stage in state["stages"].values():
                stage["active"] = []
                if str(stage.get("status") or "pending").lower() not in {"complete", "completed"}:
                    stage["status"] = "pending"
            state.update(
                {
                    "run_id": self._run_id,
                    "phase": "running",
                    "message": "Preparing pipeline",
                    "errors": [],
                    "updated_at": _now(),
                }
            )
            write_json_atomic(self.path, state)
            return self._run_id

    def set_generation_id(self, generation_id: str) -> None:
        with self._lock:
            state = self.load()
            if not self._may_update(state):
                return
            state["generation_id"] = str(generation_id or "")
            state["updated_at"] = _now()
            write_json_atomic(self.path, state)

    def load(self) -> dict[str, Any]:
        if not self.path.exists():
            return _empty_state()
        import json

        return _ensure_stage_defaults(json.loads(self.path.read_text(encoding="utf-8")))

    def update(self, patch: dict[str, Any]) -> None:
        with self._lock:
            state = self.load()
            if not self._may_update(state):
                return
            _deep_update(state, patch)
            state["updated_at"] = _now()
            write_json_atomic(self.path, state)

    def complete(self, message: str) -> None:
        self.update({"phase": "complete", "message": message})

    def record_error(
        self,
        *,
        stage: str,
        message: str,
        code: str = "pipeline_error",
        resource: str = "",
        retry_count: int = 0,
        recoverable: bool = False,
        action: str = "",
    ) -> None:
        """Persist one deduplicated, actionable error for the current run."""
        with self._lock:
            state = self.load()
            if not self._may_update(state):
                return
            record = {
                "run_id": str(state.get("run_id") or ""),
                "generation_id": str(state.get("generation_id") or ""),
                "stage": str(stage),
                "code": str(code),
                "resource": str(resource),
                "message": str(message),
                "retry_count": max(0, int(retry_count)),
                "recoverable": bool(recoverable),
                "action": str(action),
                "timestamp": _now(),
            }
            errors = [item for item in state.get("errors", []) if isinstance(item, dict)]
            key = _error_key(record)
            errors = [item for item in errors if _error_key(item) != key]
            errors.append(record)
            state["errors"] = errors[-20:]
            state["updated_at"] = _now()
            write_json_atomic(self.path, state)

    def set_stage(
        self,
        stage: str,
        *,
        total: int | None = None,
        done: int | None = None,
        status: str | None = None,
        message: str | None = None,
        active: list[str] | str | None = None,
    ) -> None:
        with self._lock:
            state = self.load()
            if not self._may_update(state):
                return
            data = _stage_data(state, stage)
            if total is not None:
                data["total"] = max(0, int(total))
            if done is not None:
                data["done"] = max(0, int(done))
            if data.get("total", 0):
                data["done"] = min(int(data.get("done", 0)), int(data["total"]))
            if status is not None:
                data["status"] = status
            if message is not None:
                data["message"] = message
            if active is not None:
                data["active"] = _active_items(active)
            state["updated_at"] = _now()
            write_json_atomic(self.path, state)

    def add_stage_total(
        self,
        stage: str,
        amount: int,
        *,
        status: str | None = None,
        message: str | None = None,
        active: list[str] | str | None = None,
    ) -> None:
        with self._lock:
            state = self.load()
            if not self._may_update(state):
                return
            data = _stage_data(state, stage)
            data["total"] = max(0, int(data.get("total", 0)) + int(amount))
            if status is not None:
                data["status"] = status
            if message is not None:
                data["message"] = message
            if active is not None:
                data["active"] = _active_items(active)
            state["updated_at"] = _now()
            write_json_atomic(self.path, state)

    def advance_stage(
        self,
        stage: str,
        *,
        step: int = 1,
        message: str | None = None,
        active: list[str] | str | None = None,
    ) -> None:
        with self._lock:
            state = self.load()
            if not self._may_update(state):
                return
            data = _stage_data(state, stage)
            total = int(data.get("total", 0))
            done = int(data.get("done", 0)) + int(step)
            data["done"] = min(done, total) if total else max(0, done)
            if message is not None:
                data["message"] = message
            if active is not None:
                data["active"] = _active_items(active)
            if total and data["done"] >= total:
                data["status"] = "complete"
                data["active"] = []
            elif data.get("status") == "pending":
                data["status"] = "running"
            state["updated_at"] = _now()
            write_json_atomic(self.path, state)

    def complete_stage(self, stage: str, message: str | None = None) -> None:
        with self._lock:
            state = self.load()
            if not self._may_update(state):
                return
            data = _stage_data(state, stage)
            total = int(data.get("total", 0))
            if total:
                data["done"] = total
            data["status"] = "complete"
            data["active"] = []
            if message is not None:
                data["message"] = message
            state["updated_at"] = _now()
            write_json_atomic(self.path, state)

    def _may_update(self, state: dict[str, Any]) -> bool:
        return self._run_id is None or str(state.get("run_id") or "") == self._run_id


def load_progress(root: Path) -> dict[str, Any]:
    return ProgressTracker(root).load()


def _deep_update(target: dict[str, Any], patch: dict[str, Any]) -> None:
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            _deep_update(target[key], value)
        else:
            target[key] = value


def _ensure_stage_defaults(state: dict[str, Any]) -> dict[str, Any]:
    state.setdefault("run_id", "")
    state.setdefault("generation_id", "")
    state.setdefault("errors", [])
    state.setdefault("source_ingest", {})
    state.setdefault("planner", {})
    state.setdefault("learning_loop", {})
    stages = state.setdefault("stages", {})
    for key, label in STAGES:
        existing = stages.get(key)
        if not isinstance(existing, dict):
            stages[key] = _empty_stage(label)
            continue
        stage = _empty_stage(label)
        stage.update(existing)
        stage["label"] = label
        stage["done"] = max(0, int(stage.get("done") or 0))
        stage["total"] = max(0, int(stage.get("total") or 0))
        stage["active"] = _active_items(stage.get("active") or [])
        stages[key] = stage
    return state


def _stage_data(state: dict[str, Any], stage: str) -> dict[str, Any]:
    _ensure_stage_defaults(state)
    stages = state["stages"]
    if stage not in stages:
        stages[stage] = _empty_stage(stage)
    return stages[stage]


def _active_items(active: list[str] | str | Any) -> list[str]:
    if isinstance(active, str):
        items = [active] if active else []
    elif isinstance(active, list):
        items = [str(item) for item in active if str(item)]
    else:
        items = [str(active)] if active else []
    return items[:5]


def _error_key(item: dict[str, Any]) -> tuple[str, str, str, str]:
    return (
        str(item.get("stage") or ""),
        str(item.get("code") or ""),
        str(item.get("resource") or ""),
        str(item.get("message") or ""),
    )
