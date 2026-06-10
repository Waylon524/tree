"""Artifact envelope + atomic JSON persistence for planner stages.

Every planner artifact is wrapped in an envelope so it is traceable and
incrementally rebuildable:

    {
      "schema": "tree.knowledge-nodes",
      "inputs": [{"path": "...", "hash": "..."}],
      "diagnostics": [...],
      "data": {...},
      "algorithm_versions": {"node_canonicalize": "v2"},
    }

``artifact_hash`` of the inputs lets a stage skip rebuilding when nothing
upstream changed.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any


def artifact_hash(value: Any) -> str:
    """Stable hash of any JSON-serializable artifact."""
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


def envelope(
    *,
    schema: str,
    data: dict[str, Any],
    inputs: list[dict[str, Any]] | None = None,
    diagnostics: list[dict[str, Any]] | None = None,
    algorithm_versions: dict[str, str] | None = None,
) -> dict[str, Any]:
    return {
        "schema": schema,
        "inputs": inputs or [],
        "diagnostics": diagnostics or [],
        "data": data,
        "algorithm_versions": algorithm_versions or {},
    }


def read_json(path: Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def read_envelope_data(path: Path) -> dict[str, Any]:
    """Return the ``data`` block of an envelope file, or {} if missing."""
    if not Path(path).exists():
        return {}
    loaded = read_json(path)
    if isinstance(loaded, dict) and "data" in loaded:
        data = loaded.get("data")
        return data if isinstance(data, dict) else {}
    return loaded if isinstance(loaded, dict) else {}


def write_json_atomic(path: Path, value: Any) -> None:
    """Write JSON atomically (temp file + rename)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2, default=str)
        _replace_with_retry(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def _replace_with_retry(src: str, dst: Path, *, attempts: int = 10, delay: float = 0.05) -> None:
    """``os.replace`` can raise PermissionError on Windows if a reader (e.g. the
    live ``/watch`` panel) has the target open; retry briefly before giving up.
    On POSIX the first attempt always succeeds."""
    for attempt in range(attempts):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if attempt == attempts - 1:
                raise
            time.sleep(delay)
