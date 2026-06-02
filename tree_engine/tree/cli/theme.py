"""Shared Rich markup helpers for the TREE CLI."""

from __future__ import annotations

from rich.markup import escape

TREE_GREEN = "#2E7D32"
TREE_BROWN = "#8B5A2B"
TREE_BROWN_DIM = "#A8794A"

_SUCCESS_STATUSES = {"complete", "completed", "running", "in_progress", "active", "ok", "ready", "installed"}
_WARNING_STATUSES = {"pending", "idle", "missing", "not found", "not_found"}
_ERROR_STATUSES = {"failed", "blocked", "error"}


def brand(text: str = "TREE") -> str:
    return _markup(text, TREE_GREEN, bold=True)


def section(text: str) -> str:
    return _markup(text, TREE_BROWN, bold=True)


def label(text: str) -> str:
    return _markup(text, TREE_BROWN)


def path(value: object) -> str:
    return _markup(str(value), TREE_BROWN)


def success(text: str) -> str:
    return _markup(text, TREE_GREEN)


def active(text: str) -> str:
    return _markup(text, TREE_GREEN)


def status(text: str) -> str:
    normalized = text.strip().lower()
    if normalized in _ERROR_STATUSES:
        return _markup(text, "red")
    if normalized in _WARNING_STATUSES:
        return _markup(text, "yellow")
    if normalized in _SUCCESS_STATUSES:
        return _markup(text, TREE_GREEN)
    return _markup(text, TREE_BROWN)


def progress_bar(done: int, total: int, *, width: int = 18) -> str:
    if total <= 0:
        filled = width if done else 0
    else:
        filled = round(width * min(done, total) / total)
    return (
        "["
        + _markup("#" * filled, TREE_GREEN)
        + _markup("-" * (width - filled), TREE_BROWN_DIM)
        + "]"
    )


def kv(name: str, value: object, *, value_style: str = "plain") -> str:
    rendered = str(value)
    if value_style == "path":
        rendered = path(rendered)
    elif value_style == "status":
        rendered = status(rendered)
    elif value_style == "active":
        rendered = active(rendered)
    return f"{label(name + ':')} {rendered}"


def _markup(text: str, color: str, *, bold: bool = False) -> str:
    style = f"bold {color}" if bold else color
    return f"[{style}]{escape(str(text))}[/]"
