"""Typer application assembly (thin).

Commands live in tree/cli/commands/*; the interactive REPL in tree/cli/repl.py;
dashboard rendering in tree/cli/dashboard/*. See docs/REBUILD-DESIGN.md §2/§8.
"""

from __future__ import annotations

import asyncio
import shutil
import sys
from pathlib import Path

import typer
from rich import print as rprint

from tree.cli.commands import config_cmd
from tree.cli.commands import inspect as inspect_cmd
from tree.cli.commands import lifecycle as lifecycle_cmd
from tree.cli.commands import rag as rag_cmd
from tree.config import Settings
from tree.engine.orchestrator import TreeEngine
from tree.ingest.pipeline import MATERIAL_EXTENSIONS
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
def setup(
    llm_api_key: str = typer.Option("", "--llm-api-key"),
    llm_base_url: str = typer.Option("", "--llm-base-url"),
    llm_model: str = typer.Option("", "--llm-model"),
    paddleocr_api_token: str = typer.Option("", "--paddleocr-api-token"),
    paddleocr_api_url: str = typer.Option("", "--paddleocr-api-url"),
) -> None:
    """Write workspace .tree/config.env."""
    path = config_cmd.write_workspace_config(
        Path.cwd(),
        llm_api_key=llm_api_key,
        llm_base_url=llm_base_url,
        llm_model=llm_model,
        paddleocr_api_token=paddleocr_api_token,
        paddleocr_api_url=paddleocr_api_url,
    )
    rprint(f"[green]Wrote {path}[/green]")


@app.command()
def models() -> None:
    """Show configured role models."""
    rprint(config_cmd.models_text(Settings.from_env(require_llm=False)))


@app.command()
def prompts() -> None:
    """List bundled prompt roles."""
    rprint(config_cmd.prompts_text())


@app.command()
def status() -> None:
    """Show current workspace status."""
    rprint(inspect_cmd.status_text(Path.cwd()))


@app.command()
def progress() -> None:
    """Print progress.json."""
    rprint(inspect_cmd.progress_text(Path.cwd()))


@app.command()
def watch() -> None:
    """Render a one-shot dashboard view."""
    rprint(inspect_cmd.watch_text(Path.cwd()))


@app.command()
def materials() -> None:
    """List supported files under materials/."""
    rprint(inspect_cmd.materials_text(Path.cwd()))


@app.command()
def logs() -> None:
    """List runtime log files."""
    rprint(inspect_cmd.logs_text(Path.cwd()))


@app.command()
def clean() -> None:
    """Remove .tree/runtime artifacts."""
    rprint(inspect_cmd.clean_runtime(Path.cwd()))


@app.command()
def run() -> None:
    """Run the pipeline in the foreground (implemented in step 8)."""
    settings = Settings.from_env()
    asyncio.run(TreeEngine(settings).run())


@app.command()
def start() -> None:
    """Start the engine in the background (implemented in step 9)."""
    rprint(lifecycle_cmd.start_engine(Path.cwd()).message)


@app.command()
def stop() -> None:
    """Stop the background engine."""
    rprint(lifecycle_cmd.stop_engine(Path.cwd()).message)


@app.command()
def quit() -> None:
    """Stop background services for this workspace."""
    rprint(lifecycle_cmd.quit_tree(Path.cwd()).message)


@app.command()
def resume() -> None:
    """Resume the pipeline in the foreground."""
    run()


@app.command("continue")
def continue_() -> None:
    """Continue the pipeline in the foreground."""
    run()


@app.command()
def ingest(
    input: str = typer.Option(..., "--input"),
    collection: str | None = typer.Option(None, "--collection"),
) -> None:
    """Manually ingest a file/dir (implemented in step 8)."""
    settings = Settings.from_env()
    copied = _copy_input_to_materials(Path(input), settings.project_root, collection=collection)
    rprint(f"[green]Copied {copied} material file(s).[/green]")
    summary = asyncio.run(TreeEngine(settings).prepare_sources())
    rprint(
        "[green]Ingest complete.[/green] "
        f"MTUs={summary.get('mtu_count', 0)}, nodes={summary.get('node_count', 0)}, "
        f"branches={summary.get('branch_count', 0)}"
    )


@planner_app.command("rebuild")
def planner_rebuild() -> None:
    """Rebuild MTUs/nodes/DAG/branches without running BranchRuns (step 6)."""
    summary = asyncio.run(TreeEngine(Settings.from_env()).prepare_sources())
    rprint(
        "[green]Planner rebuilt.[/green] "
        f"MTUs={summary.get('mtu_count', 0)}, nodes={summary.get('node_count', 0)}, "
        f"branches={summary.get('branch_count', 0)}"
    )


@rag_app.command("status")
def rag_status() -> None:
    """Show RAG artifact status."""
    rprint(rag_cmd.status_text(Path.cwd()))


@rag_app.command("inventory")
def rag_inventory() -> None:
    """Show MTU inventory."""
    rprint(rag_cmd.inventory_text(Path.cwd()))


@rag_app.command("nodes")
def rag_nodes() -> None:
    """Show knowledge nodes."""
    rprint(rag_cmd.nodes_text(Path.cwd()))


@rag_app.command("graph")
def rag_graph() -> None:
    """Show DAG edges."""
    rprint(rag_cmd.graph_text(Path.cwd()))


@rag_app.command("search")
def rag_search(query: str, top_k: int = typer.Option(5, "--top-k")) -> None:
    """Search the local RAG store."""
    rprint(rag_cmd.search_text(Path.cwd(), query, top_k=top_k))


def _exists(path: Path) -> str:
    return "[green]ok[/green]" if path.exists() else "[yellow]missing[/yellow]"


def _copy_input_to_materials(input_path: Path, root: Path, *, collection: str | None) -> int:
    input_path = input_path.expanduser()
    if not input_path.exists():
        raise typer.BadParameter(f"Input not found: {input_path}")
    target_collection = collection or input_path.stem
    target_dir = paths.materials_root(root) / target_collection
    target_dir.mkdir(parents=True, exist_ok=True)

    if input_path.is_file():
        _copy_material_file(input_path, target_dir)
        return 1

    copied = 0
    for path in sorted(input_path.rglob("*")):
        if not path.is_file() or path.name.startswith("."):
            continue
        if path.suffix.lower() not in MATERIAL_EXTENSIONS:
            continue
        rel_parent = path.parent.relative_to(input_path)
        dest_dir = target_dir / rel_parent
        _copy_material_file(path, dest_dir)
        copied += 1
    return copied


def _copy_material_file(source: Path, target_dir: Path) -> None:
    if source.suffix.lower() not in MATERIAL_EXTENSIONS:
        raise typer.BadParameter(f"Unsupported material type: {source.name}")
    target_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target_dir / source.name)


if __name__ == "__main__":
    app()
