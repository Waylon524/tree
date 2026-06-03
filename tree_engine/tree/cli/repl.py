"""Interactive TREE> shell with lightweight slash-command routing."""

from __future__ import annotations

from pathlib import Path

from rich.console import Console
from rich import print as rprint

from tree.cli import theme
from tree.cli.commands import config_cmd
from tree.cli.commands import inspect
from tree.cli.dashboard.live import run_watch as run_watch_panel
from tree.cli.commands.lifecycle import quit_tree, start_engine, stop_engine
from tree.io import paths
from tree.planner.pipeline import load_dag
from tree.planner.svg import write_dag_svg


_HELP_ROWS = (
    ("/init", "initialize this TREE workspace"),
    ("/setup", "run the global setup wizard"),
    ("/materials", "list supported materials"),
    ("/run", "start the pipeline in the background"),
    ("/watch", "watch the dashboard until ESC"),
    ("/status", "show workspace status"),
    ("/dag", "write the DAG SVG to outputs"),
    ("/stop", "stop the background engine"),
    ("/quit", "stop TREE services and leave the shell"),
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
        if command == "/watch":
            run_watch_panel(root, console=console)
            continue
        result = handle_slash_command(command, root=root)
        rprint(result)
        if should_exit_repl(command):
            return


def should_exit_repl(command: str) -> bool:
    return command.strip() == "/quit"


def handle_slash_command(command: str, *, root: Path | None = None) -> str:
    root = root or Path.cwd()
    command = command.strip()
    if command == "/help":
        return _help_text()
    if command == "/init":
        paths.ensure_workspace_dirs(root)
        return f"{theme.success('Initialized')} {theme.label('workspace')}."
    if command == "/setup":
        paths.ensure_workspace_dirs(root)
        path = config_cmd.run_setup_wizard(
            root,
            env_path=paths.global_config_path(),
            scope="global",
        )
        return f"{theme.success('Wrote')} {theme.path(path)}"
    if command == "/status":
        return inspect.status_text(root)
    if command == "/dag":
        return _dag_text(root)
    if command == "/progress":
        return "Use /status or /watch inside TREE>; raw JSON is available with `tre progress`."
    if command == "/watch":
        return inspect.watch_text(root)
    if command == "/materials":
        return inspect.materials_text(root)
    if command in {"/run", "/start"}:
        return start_engine(root).message
    if command == "/stop":
        return stop_engine(root).message
    if command == "/quit":
        return quit_tree(root).message
    if command == "/exit":
        return "Use /quit to stop TREE services and leave the shell."
    return f"Unknown command: {command}"


def _dag_text(root: Path) -> str:
    if not paths.knowledge_dag_path(root).exists():
        return "knowledge-dag.json not found; run /run first, then try /dag again."
    write_dag_svg(root, load_dag(root))
    return f"{theme.success('Wrote')} {theme.path(paths.outputs_dag_svg_path(root))}"
