"""Bounded, prompt-free structured telemetry for LLM operations."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import threading
from typing import Any

from tree.io import paths

_MAX_LOG_BYTES = 2 * 1024 * 1024
_BACKUP_COUNT = 3
_WRITE_LOCK = threading.Lock()


def operation_log_path(root: Path) -> Path:
    return paths.services_root(Path(root)) / "llm-operations.jsonl"


class OperationLog:
    """Append safe operation metadata and rotate it at a fixed size."""

    def __init__(self, root: Path):
        self.path = operation_log_path(root)

    def append(self, record: dict[str, Any]) -> None:
        try:
            payload = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                **record,
            }
            encoded = (
                json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"
            ).encode("utf-8")
            with _WRITE_LOCK:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                current_size = self.path.stat().st_size if self.path.exists() else 0
                if current_size and current_size + len(encoded) > _MAX_LOG_BYTES:
                    _rotate(self.path)
                with self.path.open("ab") as handle:
                    handle.write(encoded)
        except (OSError, TypeError, ValueError):
            # Telemetry must never turn a valid provider response into a pipeline failure.
            return


def recent_operation_events(root: Path, *, limit: int = 20) -> list[dict[str, Any]]:
    """Return recent valid operation records, oldest to newest."""
    if limit <= 0:
        return []
    path = operation_log_path(root)
    candidates = [path.with_name(f"{path.name}.{index}") for index in range(_BACKUP_COUNT, 0, -1)]
    candidates.append(path)
    records: list[dict[str, Any]] = []
    for candidate in candidates:
        if not candidate.exists():
            continue
        try:
            lines = candidate.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for line in lines:
            try:
                item = json.loads(line)
            except ValueError:
                continue
            if isinstance(item, dict):
                records.append(item)
    return records[-limit:]


def _rotate(path: Path) -> None:
    oldest = path.with_name(f"{path.name}.{_BACKUP_COUNT}")
    oldest.unlink(missing_ok=True)
    for index in range(_BACKUP_COUNT - 1, 0, -1):
        source = path.with_name(f"{path.name}.{index}")
        if source.exists():
            source.replace(path.with_name(f"{path.name}.{index + 1}"))
    if path.exists():
        path.replace(path.with_name(f"{path.name}.1"))


__all__ = ["OperationLog", "operation_log_path", "recent_operation_events"]
