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
def ingest(
    input_path: Path = typer.Option(..., "--input", "-i", exists=True, help="Input file or directory"),
    collection: str | None = typer.Option(None, "--collection", "-c", help="Source collection name"),
    output_dir: Path | None = typer.Option(None, "--output", "-o", help="Output source-material directory"),
    no_structure: bool = typer.Option(False, "--no-structure", help="Skip Archivist cleanup"),
    no_index: bool = typer.Option(False, "--no-index", help="Skip RAG indexing for generated source files"),
) -> None:
    """Run PaddleOCR ingest, then optionally structure OCR output with Archivist."""
    from tree.config import Settings
    from tree.engine import TreeEngine
    from tree.ingest import ingest_path

    target_dir = output_dir or Path.cwd() / "source_materials" / (collection or input_path.stem)

    indexer = None
    if not no_index:
        from tree.rag.client import RAGClient
        from tree.rag.indexer import RAGIndexer

        indexer = RAGIndexer(RAGClient())

    collection_name = collection or input_path.stem

    if no_structure:
        settings = Settings.from_env(require_llm=False)
        outputs = asyncio.run(
            ingest_path(
                input_path,
                target_dir,
                settings,
                archivist=None,
                collection=collection_name,
                indexer=indexer,
            )
        )
    else:
        settings = Settings.from_env()
        engine = TreeEngine(settings)
        try:
            outputs = asyncio.run(
                engine.ingest(
                    input_path,
                    target_dir,
                    use_archivist=True,
                    collection=collection_name,
                    indexer=indexer,
                )
            )
        finally:
            asyncio.run(engine.close())

    for path in outputs:
        if path.exists():
            rprint(f"[green]Wrote[/green] {path}")
        else:
            rprint(f"[green]Indexed[/green] {path} [dim](intermediate source Markdown removed)[/dim]")


@app.command()
def step(
    chapter: str = typer.Option(..., help="Chapter name"),
    step_num: int = typer.Option(..., help="Step number (1-4)"),
) -> None:
    """Run a single step for debugging."""
    rprint(f"[yellow]Single-step mode: chapter={chapter}, step={step_num}[/yellow]")
    rprint("[dim]Not yet implemented — use 'tree run' for full pipeline.[/dim]")
