"""Text dashboard rendering used by ``tre watch``."""

from __future__ import annotations

from pathlib import Path

from rich.console import Console
from rich.markup import escape
from rich.panel import Panel

from tree.cli.dashboard.model import build_watch_model
from tree.cli import theme

_STAGE_ORDER = ("ocr", "clean", "cut", "embed", "cluster", "link", "noderun")
_BAR_WIDTH = 16
_STATUS_BADGES = {
    "complete": "COMPLETE",
    "completed": "COMPLETE",
    "running": "RUNNING",
    "in_progress": "RUNNING",
    "active": "RUNNING",
    "failed": "FAILED",
    "blocked": "FAILED",
    "error": "FAILED",
    "pending": "WAIT",
    "idle": "WAIT",
}


def render_watch(root: Path) -> str:
    console = Console(record=True, color_system=None, width=100)
    console.print(watch_renderable(root))
    return console.export_text(styles=False).rstrip()


def watch_renderable(root: Path) -> Panel:
    model = build_watch_model(root)
    active_count = len(
        set((model.get("active_node_runs") or []) + (model.get("running_node_ids") or []))
    )
    lines = [
        theme.section("Overview"),
        "  "
        + "  ".join(
            [
                f"{theme.label('materials')} {model['material_count']}",
                f"{theme.label('nodes')} {model['node_count']}",
                f"{theme.label('active')} {active_count}",
                f"{theme.label('exit')} {theme.success('Press ESC')}",
            ]
        ),
        "",
        theme.section("Progress"),
        f"  {theme.label('Stage'.ljust(8))} {theme.label('Progress'.ljust(_BAR_WIDTH))} "
        f"{theme.label('%'.rjust(4))} {theme.label('Count'.rjust(7))} "
        f"{theme.label('Status'.ljust(8))} {theme.label('Current')}",
    ]
    stages = model.get("stages") or {}
    node_labels = model.get("node_display_labels") or {}
    for key in _STAGE_ORDER:
        lines.append(
            _render_stage(
                stages.get(key) or {"label": key.title()},
                stage_key=key,
                node_labels=node_labels if key == "noderun" else {},
            )
        )
    errors = model.get("errors") or []
    lines.extend(["", theme.section("Errors")])
    if errors:
        lines.extend(f"- {item}" for item in errors)
    else:
        lines.append("- none")
    return Panel(
        "\n".join(lines),
        title=theme.brand("TREE Watch"),
        border_style=theme.TREE_GREEN,
        expand=True,
    )


def _render_stage(stage: dict, *, stage_key: str, node_labels: dict[str, str]) -> str:
    label = theme.label(str(stage.get("label") or "").ljust(8))
    done = int(stage.get("done") or 0)
    total = int(stage.get("total") or 0)
    status = str(stage.get("status") or "pending")
    badge = _watch_state(_status_badge(status)).ljust(8)
    message = str(stage.get("message") or "")
    active_items = [str(item) for item in (stage.get("active") or []) if str(item)]
    active_style = theme.path if stage_key == "noderun" else theme.active
    active = ", ".join(
        active_style(node_labels.get(item, item))
        for item in active_items
    )
    count = f"{done}/{total}" if total else "0/0"
    percent = _percent(done, total)
    show_active = bool(active) and (
        stage_key != "noderun" or status.strip().lower() in {"running", "in_progress", "active"}
    )
    detail = f"当前: {active}" if show_active else message
    detail = _truncate(detail, 34)
    return (
        f"  {label} {theme.progress_bar(done, total, width=_BAR_WIDTH)} "
        f"{percent:>4} {count:>7} {badge} {detail}"
    ).rstrip()


def _status_badge(status: str) -> str:
    return _STATUS_BADGES.get(status.strip().lower(), status.strip().upper() or "WAIT")


def _watch_state(text: str) -> str:
    normalized = text.strip().lower()
    if normalized in {"failed", "blocked", "error"}:
        color = "red"
    elif normalized in {"complete", "completed"}:
        color = theme.TREE_GREEN
    elif normalized in {"running", "in_progress", "active"}:
        color = theme.TREE_BROWN
    elif normalized in {"wait", "pending", "idle"}:
        color = theme.TREE_BROWN_DIM
    else:
        color = theme.TREE_BROWN
    return f"[{color}]{escape(text)}[/]"


def _percent(done: int, total: int) -> str:
    if total <= 0:
        return "0%"
    return f"{round(100 * min(done, total) / total)}%"


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)] + "…"
