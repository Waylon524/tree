"""Interactive TREE> shell with lightweight slash-command routing."""

from __future__ import annotations

from pathlib import Path

from rich.console import Console
from rich import print as rprint

from tree.cli import theme
from tree.cli.commands import inspect
from tree.cli.commands.lifecycle import quit_tree, start_engine, stop_engine


_HELP_ROWS = (
    ("/start", "start the foreground engine in a background process"),
    ("/stop", "stop the background engine"),
    ("/quit", "stop the background engine and leave the shell"),
    ("/exit", "leave the shell without stopping services"),
    ("/status", "show workspace status"),
    ("/progress", "print progress.json"),
    ("/watch", "render the dashboard once"),
    ("/materials", "list supported materials"),
    ("/help", "show this help"),
)


def _help_text() -> str:
    lines = [theme.section("commands:")]
    for command, description in _HELP_ROWS:
        lines.append(f"  {theme.success(command.ljust(10))} {theme.label(description)}")
    return "\n".join(lines) + "\n"


def run_repl() -> None:
    root = Path.cwd()
    console = Console()
    rprint(f"{theme.brand()} {theme.section('interactive shell')}.")
    rprint(f"Type {theme.success('/help')} for commands.")
    while True:
        try:
            command = console.input(f"{theme.label('TREE>')} ").strip()
        except (EOFError, KeyboardInterrupt):
            rprint(handle_slash_command("/quit", root=root))
            return
        if not command:
            continue
        result = handle_slash_command(command, root=root)
        if command == "/progress":
            console.print(result, markup=False)
        else:
            rprint(result)
        if command in {"/quit", "/exit"}:
            return


def handle_slash_command(command: str, *, root: Path | None = None) -> str:
    root = root or Path.cwd()
    command = command.strip()
    if command == "/help":
        return _help_text()
    if command == "/status":
        return inspect.status_text(root)
    if command == "/progress":
        return inspect.progress_text(root)
    if command == "/watch":
        return inspect.watch_text(root)
    if command == "/materials":
        return inspect.materials_text(root)
    if command == "/start":
        return start_engine(root).message
    if command == "/stop":
        return stop_engine(root).message
    if command == "/quit":
        return quit_tree(root).message
    if command == "/exit":
        return "bye"
    return f"Unknown command: {command}"
