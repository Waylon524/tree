"""Text dashboard rendering used by ``tre watch``."""

from __future__ import annotations

from pathlib import Path

from tree.cli.dashboard.model import build_watch_model
from tree.cli import theme

_STAGE_ORDER = ("ocr", "clean", "cut", "embed", "cluster", "link", "noderun")
_BAR_WIDTH = 18


def render_watch(root: Path) -> str:
    model = build_watch_model(root)
    lines = [
        theme.brand("TREE Watch"),
        theme.kv("phase", model["phase"], value_style="status"),
        theme.kv("message", model["message"]),
        theme.kv("materials", model["material_count"]),
        theme.kv("nodes", model["node_count"]),
        theme.kv("edges", model["edge_count"]),
        "",
        theme.section("Progress"),
    ]
    stages = model.get("stages") or {}
    for key in _STAGE_ORDER:
        lines.append(_render_stage(stages.get(key) or {"label": key.title()}))
    return "\n".join(lines)


def _render_stage(stage: dict) -> str:
    label = theme.label(str(stage.get("label") or "").ljust(8))
    done = int(stage.get("done") or 0)
    total = int(stage.get("total") or 0)
    status = theme.status(str(stage.get("status") or "pending")).ljust(8)
    message = str(stage.get("message") or "")
    active = ", ".join(theme.active(str(item)) for item in (stage.get("active") or []) if str(item))
    count = f"{done}/{total}" if total else "0/0"
    detail = f"当前: {active}" if active else message
    return f"{label} {theme.progress_bar(done, total, width=_BAR_WIDTH)} {count:>7} {status} {detail}".rstrip()
