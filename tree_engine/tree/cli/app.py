"""Typer application assembly (thin).

Commands live in tree/cli/commands/*; the interactive REPL in tree/cli/repl.py;
dashboard rendering in tree/cli/dashboard/*. See docs/REBUILD-DESIGN.md §2/§8.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import typer
from rich import print as rprint

from tree.io import paths

app = typer.Typer(no_args_is_help=False, add_completion=False, help="T.R.E.E. engine")
rag_app = typer.Typer(help="RAG inspection commands")
planner_app = typer.Typer(help="Planner commands")
app.add_typer(rag_app, name="rag")
app.add_typer(planner_app, name="planner")


@app.callback(invoke_without_command=True)
def main(ctx: typer.Context) -> None:
    """Run `tre` with no command to enter the interactive TREE> shell."""
    if ctx.invoked_subcommand is None:
        from tree.cli.repl import run_repl

        run_repl()


@app.command()
def doctor() -> None:
    """Read-only environment health check."""
    root = Path.cwd()
    rprint("[bold]T.R.E.E. doctor[/bold]")
    rprint(f"  python           : {sys.version.split()[0]}")
    rprint(f"  tre on PATH      : {shutil.which('tre') or '[red]not found[/red]'}")
    rprint(f"  TREE_HOME        : {paths.app_home()}")
    rprint(f"  global config    : {_exists(paths.global_config_path())}")
    rprint(f"  workspace        : {root}")
    rprint(f"  materials/       : {_exists(paths.materials_root(root))}")
    rprint(f"  .tree/runtime/   : {_exists(paths.runtime_root(root))}")
    try:
        import qdrant_client  # noqa: F401
        import llama_cpp  # noqa: F401

        rprint("  rag deps         : [green]installed[/green]")
    except Exception:
        rprint("  rag deps         : [yellow]missing (pip install '.[rag]')[/yellow]")


@app.command()
def init() -> None:
    """Create materials/ outputs/ .tree/ in the current folder."""
    paths.ensure_workspace_dirs(Path.cwd())
    rprint("[green]Initialized workspace.[/green]")


@app.command()
def run() -> None:
    """Run the pipeline in the foreground (implemented in step 8)."""
    _not_implemented("run")


@app.command()
def start() -> None:
    """Start the engine in the background (implemented in step 9)."""
    _not_implemented("start")


@app.command()
def ingest(
    input: str = typer.Option(..., "--input"),
    collection: str | None = typer.Option(None, "--collection"),
) -> None:
    """Manually ingest a file/dir (implemented in step 8)."""
    _not_implemented("ingest")


@planner_app.command("rebuild")
def planner_rebuild() -> None:
    """Rebuild MTUs/nodes/DAG/branches without running BranchRuns (step 6)."""
    _not_implemented("planner rebuild")


def _exists(path: Path) -> str:
    return "[green]ok[/green]" if path.exists() else "[yellow]missing[/yellow]"


def _not_implemented(name: str) -> None:
    rprint(f"[yellow]`{name}` is not implemented yet (skeleton).[/yellow]")
    rprint("See docs/REBUILD-DESIGN.md §9 for the build order.")
    raise typer.Exit(code=1)


if __name__ == "__main__":
    app()
