"""Typer application assembly (thin).

Commands live in tree/cli/commands/*; the interactive REPL in tree/cli/repl.py;
dashboard rendering in tree/cli/dashboard/*.
"""

from __future__ import annotations

import asyncio
import importlib.util
import shutil
import sys
from pathlib import Path

import typer
from rich import print as rprint

from tree.cli import theme
from tree.cli.commands import config_cmd
from tree.cli.commands import inspect as inspect_cmd
from tree.cli.dashboard.live import run_watch as run_watch_panel
from tree.cli.commands import lifecycle as lifecycle_cmd
from tree.cli.commands import rag as rag_cmd
from tree.config import Settings
from tree.engine.orchestrator import TreeEngine
from tree.ingest.pipeline import MATERIAL_EXTENSIONS
from tree.io import paths
from tree.planner.pipeline import load_dag
from tree.planner.svg import write_dag_svg
from tree.rag.model_cache import EmbeddingModelError, embedding_model_status, ensure_embedding_model
from tree.rag.service import (
    embedding_service_status,
    start_embedding_service,
    stop_embedding_service,
)

app = typer.Typer(no_args_is_help=False, add_completion=False, help="T.R.E.E. engine")
rag_app = typer.Typer(help="RAG inspection commands")
planner_app = typer.Typer(help="Planner commands")
embedding_app = typer.Typer(help="Embedding model/server commands")
app.add_typer(rag_app, name="rag")
app.add_typer(planner_app, name="planner")
app.add_typer(embedding_app, name="embedding")


@app.callback(invoke_without_command=True)
def main(ctx: typer.Context) -> None:
    """Run `tre` with no command to enter the interactive TREE> shell."""
    if ctx.invoked_subcommand is None:
        ensure_embedding_ready()
        from tree.cli.repl import run_repl

        run_repl()


@app.command()
def doctor() -> None:
    """Read-only environment health check."""
    root = Path.cwd()
    rprint(f"{theme.brand('T.R.E.E.')} {theme.section('doctor')}")
    rprint(f"  {theme.label('python')}           : {sys.version.split()[0]}")
    rprint(
        f"  {theme.label('tre on PATH')}      : "
        f"{theme.path(shutil.which('tre')) if shutil.which('tre') else theme.status('not found')}"
    )
    rprint(f"  {theme.label('TREE_HOME')}        : {theme.path(paths.app_home())}")
    rprint(f"  {theme.label('global config')}    : {_exists(paths.global_config_path())}")
    rprint(f"  {theme.label('workspace')}        : {theme.path(root)}")
    rprint(f"  {theme.label('materials/')}       : {_exists(paths.materials_root(root))}")
    rprint(f"  {theme.label('.tree/runtime/')}   : {_exists(paths.runtime_root(root))}")
    if importlib.util.find_spec("qdrant_client") is not None:
        rprint(f"  {theme.label('rag deps')}         : {theme.status('installed')}")
    else:
        rprint(f"  {theme.label('rag deps')}         : [yellow]missing (pip install '.[rag]')[/yellow]")
    local_missing = [m for m in ("llama_cpp", "fastapi", "uvicorn") if importlib.util.find_spec(m) is None]
    if not local_missing:
        rprint(f"  {theme.label('local embed')}      : {theme.status('installed')}")
    else:
        rprint(
            f"  {theme.label('local embed')}      : "
            f"[yellow]missing (pip install '.[local-embed]', or set EMBED_API_URL)[/yellow]"
        )
    rprint(f"  {theme.label('embedding model')}  : {theme.status(embedding_model_status())}")
    rprint(f"  {theme.label('embedding server')} : {theme.status(embedding_service_status())}")


@app.command()
def init() -> None:
    """Create materials/ outputs/ .tree/ in the current folder."""
    paths.ensure_workspace_dirs(Path.cwd())
    rprint(f"{theme.success('Initialized')} {theme.label('workspace')}.")


@app.command()
def setup(
    force: bool = typer.Option(False, "--force", help="Run setup even if config already exists."),
    workspace: bool = typer.Option(False, "--workspace", help="Write settings only for this workspace."),
    llm_api_key: str = typer.Option("", "--llm-api-key"),
    llm_base_url: str = typer.Option("", "--llm-base-url"),
    llm_model: str = typer.Option("", "--llm-model"),
    paddleocr_api_token: str = typer.Option("", "--paddleocr-api-token"),
    paddleocr_api_url: str = typer.Option("", "--paddleocr-api-url"),
) -> None:
    """Create or update global/workspace configuration."""
    root = Path.cwd()
    paths.ensure_workspace_dirs(root)
    env_path = paths.workspace_config_path(root) if workspace else paths.global_config_path()
    has_quick_values = any(
        (llm_api_key, llm_base_url, llm_model, paddleocr_api_token, paddleocr_api_url)
    )
    if has_quick_values:
        path = config_cmd.write_quick_config(
            root,
            env_path=env_path,
            llm_api_key=llm_api_key,
            llm_base_url=llm_base_url,
            llm_model=llm_model,
            paddleocr_api_token=paddleocr_api_token,
            paddleocr_api_url=paddleocr_api_url,
        )
        rprint(f"{theme.success('Wrote')} {theme.path(path)}")
        return

    if env_path.exists() and not force:
        rprint(
            f"{theme.path(env_path)} {theme.status('ok')} already exists. "
            f"Use {theme.label('--force')} to run the wizard again."
        )
        return

    config_cmd.run_setup_wizard(
        Path.cwd(),
        env_path=env_path,
        scope="workspace" if workspace else "global",
    )


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
    typer.echo(inspect_cmd.progress_text(Path.cwd()))


@app.command()
def watch() -> None:
    """Render a live dashboard until ESC is pressed."""
    run_watch_panel(Path.cwd())


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
    ensure_embedding_ready()
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
    ensure_embedding_ready()
    settings = Settings.from_env()
    copied = _copy_input_to_materials(Path(input), settings.project_root, collection=collection)
    rprint(f"{theme.success('Copied')} {theme.label(str(copied))} material file(s).")
    summary = asyncio.run(TreeEngine(settings).prepare_sources())
    rprint(
        f"{theme.success('Ingest complete.')} "
        f"{theme.label('MTUs=')}{summary.get('mtu_count', 0)}, "
        f"{theme.label('nodes=')}{summary.get('node_count', 0)}"
    )


@planner_app.command("rebuild")
def planner_rebuild() -> None:
    """Rebuild MTUs/nodes/DAG without running NodeRuns."""
    ensure_embedding_ready()
    summary = asyncio.run(TreeEngine(Settings.from_env()).prepare_sources())
    svg = summary.get("dag_svg_path", "")
    rprint(
        f"{theme.success('Planner rebuilt.')} "
        f"{theme.label('MTUs=')}{summary.get('mtu_count', 0)}, "
        f"{theme.label('nodes=')}{summary.get('node_count', 0)}"
        + (f", {theme.label('svg=')}{theme.path(svg)}" if svg else "")
    )


@planner_app.command("dag-svg")
def planner_dag_svg() -> None:
    """Generate knowledge-dag.svg from the existing planner DAG."""
    root = Path.cwd()
    if not paths.knowledge_dag_path(root).exists():
        raise typer.BadParameter("knowledge-dag.json not found; run `tre planner rebuild` first.")
    svg_path = write_dag_svg(root, load_dag(root))
    rprint(f"{theme.success('Wrote')} {theme.path(svg_path)}")


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
    ensure_embedding_ready()
    rprint(rag_cmd.search_text(Path.cwd(), query, top_k=top_k))


@embedding_app.command("install")
def embedding_install() -> None:
    """Download or verify the local embedding model."""
    try:
        model = ensure_embedding_model()
    except EmbeddingModelError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc
    rprint(
        f"{theme.label('embedding model')} {theme.status('ready')} "
        f"({theme.label('source')} {theme.path(model.source)}, {theme.label('path')} {theme.path(model.path)})"
    )


@embedding_app.command("status")
def embedding_status() -> None:
    """Show embedding model and server status."""
    rprint(theme.kv("embedding model", embedding_model_status(), value_style="status"))
    rprint(theme.kv("embedding server", embedding_service_status(), value_style="status"))


@embedding_app.command("start")
def embedding_start() -> None:
    """Start the shared local embedding server."""
    rprint(start_embedding_service().message)


@embedding_app.command("stop")
def embedding_stop() -> None:
    """Stop the TREE-managed embedding server."""
    rprint(stop_embedding_service(force=True).message)


def ensure_embedding_ready() -> None:
    """Ensure the local embedding model/server is available for RAG-backed commands."""
    try:
        start_embedding_service()
    except Exception as exc:  # noqa: BLE001
        typer.echo(f"Embedding unavailable: {exc}", err=True)
        raise typer.Exit(1) from exc


def _exists(path: Path) -> str:
    return theme.status("ok") if path.exists() else theme.status("missing")


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
