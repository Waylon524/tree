"""CLI entry point: tree run / resume / status / step."""

from __future__ import annotations

import asyncio
from pathlib import Path

import typer
from rich import print as rprint
from rich.panel import Panel

app = typer.Typer(name="tree", help="T.R.E.E. independent orchestration engine")


@app.command()
def run(
    dry_run: bool = typer.Option(False, "--dry-run", help="Print prompts without calling API"),
) -> None:
    """Start the T.R.E.E. pipeline from current state."""
    from tree.config import Settings
    from tree.engine import TreeEngine

    settings = Settings.from_env()
    engine = TreeEngine(settings)
    try:
        asyncio.run(engine.run())
    except KeyboardInterrupt:
        rprint("[yellow]Pipeline interrupted. Use 'tree resume' to continue.[/yellow]")
    finally:
        asyncio.run(engine.close())


@app.command()
def resume() -> None:
    """Resume pipeline from last checkpoint in pipeline-state.json."""
    from tree.config import Settings
    from tree.engine import TreeEngine

    settings = Settings.from_env()
    engine = TreeEngine(settings)
    try:
        asyncio.run(engine.run())
    except KeyboardInterrupt:
        rprint("[yellow]Pipeline interrupted. Use 'tree resume' to continue.[/yellow]")
    finally:
        asyncio.run(engine.close())


@app.command()
def status(
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Display current pipeline state, chapter progress, iteration counts."""
    from tree.state.manager import StateManager

    project_root = Path.cwd()
    mgr = StateManager(project_root / "pipeline-state.json")
    state = mgr.load()

    if not state.chapters:
        rprint("[dim]No chapters yet. Pipeline has not started.[/dim]")
        return

    for ch in state.chapters:
        status_color = "green" if ch.status == "completed" else "yellow"
        files = ", ".join(ch.files_completed) if ch.files_completed else "(none)"
        rprint(
            Panel(
                f"[{status_color}]{ch.status}[/{status_color}]\n"
                f"Files completed: {files}",
                title=ch.chapter_name,
            )
        )

    if verbose:
        trace_path = project_root / "pipeline-temp" / "trace.jsonl"
        if trace_path.exists():
            lines = trace_path.read_text(encoding="utf-8").strip().splitlines()
            rprint(f"\n[dim]Trace entries: {len(lines)}[/dim]")
            for line in lines[-5:]:
                rprint(f"  [dim]{line}[/dim]")


@app.command()
def step(
    chapter: str = typer.Option(..., help="Chapter name"),
    step_num: int = typer.Option(..., help="Step number (1-4)"),
) -> None:
    """Run a single step for debugging."""
    rprint(f"[yellow]Single-step mode: chapter={chapter}, step={step_num}[/yellow]")
    rprint("[dim]Not yet implemented — use 'tree run' for full pipeline.[/dim]")
