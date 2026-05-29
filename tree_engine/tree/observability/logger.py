"""Structured logging under tree_engine/.runtime/pipeline-temp/."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path


class TraceLogger:
    def __init__(self, trace_path: Path):
        self._path = trace_path
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def log_step(
        self,
        step: str,
        chapter: str,
        file_seq: str,
        agent: str,
        action: str,
        duration_ms: int = 0,
        route: str | None = None,
        iteration: int | None = None,
    ) -> None:
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "step": step,
            "chapter": chapter,
            "file_seq": file_seq,
            "agent": agent,
            "action": action,
            "duration_ms": duration_ms,
        }
        if route:
            entry["route"] = route
        if iteration is not None:
            entry["iteration"] = iteration
        with open(self._path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def log_pipeline_start(self) -> None:
        self.log_step("S0", "", "", "engine", "pipeline_start")

    def log_pipeline_complete(self) -> None:
        self.log_step("S0", "", "", "engine", "pipeline_complete")
