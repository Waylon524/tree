"""Live dashboard loop for ``tre watch`` and the interactive ``/watch`` command."""

from __future__ import annotations

import select
import sys
import termios
import tty
from contextlib import contextmanager
from pathlib import Path
from typing import IO, Iterator

from rich.console import Console
from rich.live import Live

from tree.cli.dashboard.panels import watch_renderable


def run_watch(root: Path, *, console: Console | None = None, refresh_seconds: float = 0.5) -> None:
    """Refresh the watch panel until ESC is pressed.

    Non-interactive output falls back to a single frame so tests and pipes never hang.
    """
    console = console or Console()
    input_stream = sys.stdin
    if not _can_run_live(console, input_stream):
        console.print(watch_renderable(root))
        return

    with _raw_terminal(input_stream):
        with Live(
            watch_renderable(root),
            console=console,
            refresh_per_second=max(1, round(1 / refresh_seconds)),
            screen=False,
        ) as live:
            while True:
                if _escape_pressed(input_stream, timeout=refresh_seconds):
                    break
                live.update(watch_renderable(root))


def _can_run_live(console: Console, input_stream: IO[str]) -> bool:
    return bool(console.is_terminal and hasattr(input_stream, "isatty") and input_stream.isatty())


def _escape_pressed(input_stream: IO[str], *, timeout: float) -> bool:
    readable, _, _ = select.select([input_stream], [], [], timeout)
    if not readable:
        return False
    return input_stream.read(1) == "\x1b"


@contextmanager
def _raw_terminal(input_stream: IO[str]) -> Iterator[None]:
    fd = input_stream.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)
        yield
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
