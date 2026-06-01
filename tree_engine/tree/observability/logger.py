"""TraceLogger: append-only JSONL step trace for offline inspection."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any


class TraceLogger:
    def __init__(self, trace_path: Path):
        self.trace_path = Path(trace_path)
        self.trace_path.parent.mkdir(parents=True, exist_ok=True)

    def _write(self, record: dict[str, Any]) -> None:
        record.setdefault("ts", time.time())
        with self.trace_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    def log_pipeline_start(self) -> None:
        self._write({"event": "pipeline_start"})

    def log_pipeline_complete(self) -> None:
        self._write({"event": "pipeline_complete"})

    def log_step(
        self,
        step: str,
        execution_path: str,
        file_seq: str,
        role: str,
        action: str,
        **fields: Any,
    ) -> None:
        self._write(
            {
                "event": "step",
                "step": step,
                "execution_path": execution_path,
                "file_seq": file_seq,
                "role": role,
                "action": action,
                **fields,
            }
        )
