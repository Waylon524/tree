"""Built-in and project-overridden agent prompts."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tree.agents.prompts.archivist import (
    ARCHIVIST_CLEAN_PROMPT,
    ARCHIVIST_MTU_PROMPT,
    ARCHIVIST_PROMPT,
)
from tree.agents.prompts.dagger import DAGGER_PREREQUISITES_PROMPT, DAGGER_PROMPT
from tree.agents.prompts.examiner import EXAMINER_PROMPT
from tree.agents.prompts.student import STUDENT_PROMPT
from tree.agents.prompts.writer import WRITER_PROMPT
from tree.io import paths
from tree.planner.store import read_json, write_json_atomic

PROMPTS = {
    "examiner": EXAMINER_PROMPT,
    "student": STUDENT_PROMPT,
    "writer": WRITER_PROMPT,
    "archivist": ARCHIVIST_PROMPT,
    "archivist_clean": ARCHIVIST_CLEAN_PROMPT,
    "archivist_mtu": ARCHIVIST_MTU_PROMPT,
    "dagger": DAGGER_PROMPT,
    "dagger_prerequisites": DAGGER_PREREQUISITES_PROMPT,
}

PROMPT_LABELS = {
    "examiner": "Examiner",
    "student": "Student",
    "writer": "Writer",
    "archivist_clean": "Archivist Clean",
    "archivist_mtu": "Archivist MTU",
    "dagger": "Dagger Nodes",
    "dagger_prerequisites": "Dagger Prerequisites",
}

EDITABLE_PROMPT_KEYS = tuple(PROMPT_LABELS.keys())


def get_prompt(name: str, *, project_root: Path | None = None) -> str:
    builtin = _builtin_prompt(name)
    if project_root is None:
        return builtin
    override = _prompt_override(project_root, name)
    return override or builtin


def list_prompt_settings(project_root: Path) -> dict[str, Any]:
    overrides = _read_overrides(project_root)
    items = []
    for key in EDITABLE_PROMPT_KEYS:
        builtin = _builtin_prompt(key)
        record = overrides.get(key) if isinstance(overrides.get(key), dict) else {}
        custom = str(record.get("text") or "")
        current = custom or builtin
        base_hash = prompt_hash(builtin)
        saved_base_hash = str(record.get("base_hash") or "") if record else ""
        items.append(
            {
                "key": key,
                "label": PROMPT_LABELS[key],
                "default_text": builtin,
                "current_text": current,
                "custom_text": custom,
                "is_custom": bool(custom),
                "base_hash": base_hash,
                "saved_base_hash": saved_base_hash,
                "base_changed": bool(saved_base_hash and saved_base_hash != base_hash),
                "updated_at": record.get("updated_at") if record else None,
            }
        )
    return {
        "path": str(paths.prompt_overrides_path(project_root)),
        "prompts": items,
    }


def save_prompt_override(project_root: Path, name: str, text: str) -> dict[str, Any]:
    _builtin_prompt(name)
    text = text.strip()
    if not text:
        raise ValueError("Prompt text cannot be empty.")
    overrides = _read_overrides(project_root)
    overrides[name] = {
        "text": text,
        "base_hash": prompt_hash(_builtin_prompt(name)),
        "updated_at": _utc_now(),
    }
    _write_overrides(project_root, overrides)
    return list_prompt_settings(project_root)


def reset_prompt_override(project_root: Path, name: str) -> dict[str, Any]:
    _builtin_prompt(name)
    overrides = _read_overrides(project_root)
    overrides.pop(name, None)
    _write_overrides(project_root, overrides)
    return list_prompt_settings(project_root)


def reset_all_prompt_overrides(project_root: Path) -> dict[str, Any]:
    _write_overrides(project_root, {})
    return list_prompt_settings(project_root)


def prompt_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _builtin_prompt(name: str) -> str:
    try:
        return PROMPTS[name]
    except KeyError as exc:
        raise KeyError(f"Unknown agent prompt: {name}") from exc


def _prompt_override(project_root: Path, name: str) -> str:
    record = _read_overrides(project_root).get(name)
    if isinstance(record, dict):
        return str(record.get("text") or "").strip()
    return ""


def _read_overrides(project_root: Path) -> dict[str, Any]:
    path = paths.prompt_overrides_path(project_root)
    if not path.exists():
        return {}
    loaded = read_json(path)
    if not isinstance(loaded, dict):
        return {}
    prompts = loaded.get("prompts")
    return prompts if isinstance(prompts, dict) else {}


def _write_overrides(project_root: Path, overrides: dict[str, Any]) -> None:
    path = paths.prompt_overrides_path(project_root)
    write_json_atomic(
        path,
        {
            "version": 1,
            "updated_at": _utc_now(),
            "prompts": overrides,
        },
    )


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


__all__ = [
    "PROMPTS",
    "PROMPT_LABELS",
    "EDITABLE_PROMPT_KEYS",
    "get_prompt",
    "list_prompt_settings",
    "save_prompt_override",
    "reset_prompt_override",
    "reset_all_prompt_overrides",
    "DAGGER_PROMPT",
    "DAGGER_PREREQUISITES_PROMPT",
]
