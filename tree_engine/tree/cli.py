"""CLI entry point for the T.R.E.E. engine."""

from __future__ import annotations

import asyncio
import json
import os
import re
import signal
import shlex
import shutil
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable

import click
import typer
from rich.console import Group
from rich.live import Live
from rich import print as rprint
from rich.panel import Panel
from rich.table import Table

from tree.io import paths

app = typer.Typer(
    name="tree",
    help="T.R.E.E. independent orchestration engine",
    no_args_is_help=False,
)
rag_app = typer.Typer(help="Inspect and query the local RAG index")
app.add_typer(rag_app, name="rag")

_ROLE_NAMES = ("EXAMINER", "STUDENT", "WRITER", "ARCHIVIST")
_INTERACTIVE_ALIASES = {
    "?": "help",
    "c": "start",
    "continue": "start",
    "s": "status",
    "q": "quit",
}
_INTERACTIVE_COMMANDS = [
    ("/start", "Start TREE in the background"),
    ("/init", "Initialize the current folder as a TREE workspace"),
    ("/status", "Show service and pipeline status"),
    ("/progress", "Show current services, ingest, chapter, and recent trace"),
    ("/watch", "Refresh /progress until Esc or Ctrl+C"),
    ("/stop", "Stop TREE but keep embedding running"),
    ("/quit", "Stop TREE and embedding, then leave interactive mode"),
    ("/logs --tail 20", "Show recent pipeline trace entries"),
    ("/materials", "Show material ingest and embedding status"),
    ("/doctor", "Check installation, configuration, and services"),
    ("/models", "Show or update model/provider settings"),
    ("/setup", "Create or update workspace configuration"),
    ("/rag status", "Show local RAG index status"),
    ("/rag ledger", "Show finished-output knowledge ledger"),
    ("/rag inventory", "Show chunk-level source inventory"),
    ("/rag candidates", "Show generated candidate knowledge nodes"),
    ("/rag graph", "Show knowledge graph nodes and relations"),
    ("/help", "Show this slash-command help"),
    ("/exit", "Leave interactive mode without stopping services"),
]
_TREE_BORDER = "#8B5A2B"
_TREE_TITLE = "#2E7D32"
_DEFAULT_ENV = {
    "PADDLEOCR_API_URL": "https://paddleocr.aistudio-app.com/api/v2/ocr/jobs",
    "PADDLEOCR_MODEL": "PaddleOCR-VL-1.6",
    "SOURCE_INGEST_CONCURRENCY": "16",
    "SOURCE_OCR_CONCURRENCY": "16",
    "SOURCE_OCR_UPLOAD_INTERVAL_SEC": "5",
    "SOURCE_ARCHIVIST_CONCURRENCY": "16",
    "SOURCE_EMBEDDING_CONCURRENCY": "1",
    "SOURCE_ARCHIVIST_CHUNK_CHARS": "24000",
    "EMBED_API_URL": "http://localhost:8788",
    "EMBED_MODEL": "Qwen3-Embedding-4B-Q8_0",
    "EMBED_PORT": "8788",
    "EMBED_N_CTX": "32768",
    "EMBED_N_GPU_LAYERS": "-1",
    "EMBED_N_SEQ_MAX": "1",
}


class _InteractiveQuitRequested(Exception):
    """Raised when the interactive shell receives a terminal close signal."""


@app.callback(invoke_without_command=True)
def main(ctx: typer.Context) -> None:
    """Run a command, or enter interactive slash-command mode with no arguments."""
    if ctx.invoked_subcommand is None:
        _ensure_workspace_dirs(Path.cwd())
        _interactive_shell()
        raise typer.Exit()


@app.command()
def run() -> None:
    """Start the T.R.E.E. pipeline from current state."""
    from tree.config import Settings
    from tree.engine import StopRequested, TreeEngine
    from tree.services import clear_stop

    root = Path.cwd()
    _ensure_workspace_config(root)
    clear_stop(root, "tree")
    settings = Settings.from_env(project_root=root)
    engine = TreeEngine(settings)
    try:
        asyncio.run(_run_engine(engine))
    except StopRequested:
        rprint("[yellow]TREE stopped at a safe checkpoint. Use 'tre start' to resume.[/yellow]")
    except KeyboardInterrupt:
        rprint("[yellow]Pipeline interrupted. Use 'tre start' to continue.[/yellow]")


@app.command()
def resume() -> None:
    """Resume pipeline from the last workspace checkpoint."""
    from tree.config import Settings
    from tree.engine import StopRequested, TreeEngine
    from tree.services import clear_stop

    root = Path.cwd()
    _ensure_workspace_config(root)
    clear_stop(root, "tree")
    settings = Settings.from_env(project_root=root)
    engine = TreeEngine(settings)
    try:
        asyncio.run(_run_engine(engine))
    except StopRequested:
        rprint("[yellow]TREE stopped at a safe checkpoint. Use 'tre start' to resume.[/yellow]")
    except KeyboardInterrupt:
        rprint("[yellow]Pipeline interrupted. Use 'tre start' to continue.[/yellow]")


@app.command("start")
def start(
    wait_embedding: bool = typer.Option(
        True,
        "--wait-embedding/--no-wait-embedding",
        help="Wait until the embedding server health check is ready.",
    ),
) -> None:
    """Start TREE in the background."""
    _start_background_tree(wait_embedding=wait_embedding)


@app.command("continue", hidden=True)
def continue_(
    wait_embedding: bool = typer.Option(
        True,
        "--wait-embedding/--no-wait-embedding",
        help="Wait until the embedding server health check is ready.",
    ),
) -> None:
    """Backward-compatible alias for start."""
    _start_background_tree(wait_embedding=wait_embedding)


def _start_background_tree(wait_embedding: bool) -> None:
    from tree.services import start_embedding, start_tree, wait_for_embedding

    root = Path.cwd()
    _ensure_workspace_dirs(root)
    material_files = _supported_material_files(root)
    if not material_files:
        materials_dir = paths.materials_root(root)
        rprint(
            f"[red]Cannot start TREE: no supported files found in {materials_dir}.[/red]\n"
            "[dim]Put course files into materials/ first, then run /start again.[/dim]"
        )
        raise typer.Exit(1)

    _ensure_workspace_config(root)
    embed = start_embedding(root)
    rprint(f"[green]{embed.message}[/green] pid={embed.pid} log={embed.log_path}")
    if wait_embedding:
        rprint("[dim]Waiting for embedding server health check...[/dim]")
        if wait_for_embedding(root, timeout_sec=7200):
            rprint("[green]Embedding server ready.[/green]")
        else:
            rprint(f"[yellow]Embedding server is not ready yet. Check {embed.log_path}[/yellow]")
            return

    tree = start_tree(root)
    rprint(f"[green]{tree.message}[/green] pid={tree.pid} log={tree.log_path}")
    rprint("[dim]Use /watch in interactive mode, or tre watch, to follow progress.[/dim]")


def _supported_material_files(root: Path) -> list[Path]:
    from tree.engine import _is_supported_material

    materials_root = paths.materials_root(root)
    if not materials_root.exists():
        return []
    return [path for path in sorted(materials_root.rglob("*")) if _is_supported_material(path)]


@app.command()
def init() -> None:
    """Initialize the current directory as a TREE workspace."""
    root = Path.cwd()
    _ensure_workspace_dirs(root)
    rprint(f"[green]Workspace ready[/green] {root}")
    rprint(f"[green]Ready[/green] {paths.materials_root(root)}")
    rprint(f"[green]Ready[/green] {paths.outputs_root(root)}")
    rprint(f"[green]Ready[/green] {paths.workspace_home(root)}")


@app.command()
def stop(
    force: bool = typer.Option(False, "--force", help="Terminate TREE immediately"),
) -> None:
    """Stop TREE while keeping the embedding server running."""
    from tree.services import request_tree_stop, service_status, stop_service

    root = Path.cwd()
    request_tree_stop(root)
    status = stop_service(root, "tree", force=force)
    if status.running:
        rprint(
            f"[yellow]TREE stop requested.[/yellow] "
            f"It will exit at the next safe checkpoint. pid={status.pid} log={status.log_path}"
        )
    else:
        rprint("[green]TREE is stopped. Embedding server was left running.[/green]")
    embed = service_status(root, "embedding")
    if embed.running:
        rprint(f"[dim]Embedding server still running: pid={embed.pid} log={embed.log_path}[/dim]")


@app.command()
def quit() -> None:
    """Stop TREE and the embedding server."""
    _quit_services(Path.cwd())


@app.command("start-embedding", hidden=True)
def start_embedding_command(
    wait: bool = typer.Option(False, "--wait/--no-wait", help="Wait for health check"),
) -> None:
    """Internal helper used by bootstrap to start embedding in the background."""
    from tree.services import start_embedding, wait_for_embedding

    root = Path.cwd()
    _ensure_workspace_config(root, require_llm=False)
    result = start_embedding(root)
    rprint(f"{result.message}: pid={result.pid} log={result.log_path}")
    if wait and not wait_for_embedding(root, timeout_sec=7200):
        raise typer.Exit(1)


@app.command()
def status(
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Display current pipeline state, chapter progress, iteration counts."""
    from tree.state.manager import StateManager
    from tree.services import service_status

    project_root = Path.cwd()
    mgr = StateManager(paths.pipeline_state_path(project_root))
    state = mgr.load()

    services = Table(title="Services")
    services.add_column("Service")
    services.add_column("Status")
    services.add_column("PID")
    services.add_column("Log")
    for name in ("tree", "embedding"):
        svc = service_status(project_root, name)
        services.add_row(
            name,
            "[green]running[/green]" if svc.running else "[dim]stopped[/dim]",
            str(svc.pid or ""),
            str(svc.log_path),
        )
    rprint(services)

    if not state.chapters:
        rprint("[dim]No chapters yet. Pipeline has not started.[/dim]")
        return

    for ch in state.chapters:
        status_color = "green" if ch.status == "completed" else "yellow"
        files = ", ".join(ch.files_completed) if ch.files_completed else "(none)"
        title = ch.chapter_title or ch.provisional_chapter_title or "unnamed"
        rprint(
            Panel(
                f"[{status_color}]{ch.status}[/{status_color}]\n"
                f"Title: {title}\n"
                f"Files completed: {files}",
                title=ch.chapter_name,
            )
        )

    if verbose:
        trace_path = paths.pipeline_temp_root(project_root) / "trace.jsonl"
        if trace_path.exists():
            lines = trace_path.read_text(encoding="utf-8").strip().splitlines()
            rprint(f"\n[dim]Trace entries: {len(lines)}[/dim]")
            for line in lines[-5:]:
                rprint(f"  [dim]{line}[/dim]")


@app.command()
def doctor() -> None:
    """Check installation, configuration, workspace directories, services, and Git state."""
    from tree.config import Settings

    root = Path.cwd()
    _ensure_workspace_dirs(root)
    settings = Settings.from_env(project_root=root, require_llm=False)
    table = Table(title="T.R.E.E. Doctor")
    table.add_column("Check")
    table.add_column("Status")
    table.add_column("Details")

    _add_check(table, "Python", sys.version_info >= (3, 12), _python_summary())
    cli_ok, cli_detail = _cli_executable_summary()
    _add_check(table, "tre command", cli_ok, cli_detail)
    pkg_ok, pkg_detail = _package_summary()
    _add_check(table, "tree package", pkg_ok, pkg_detail)
    _add_check(table, "TREE_HOME", True, str(paths.app_home()))
    _add_check(table, "Workspace root", paths.workspace_home(root).exists(), str(root))
    _add_check(table, "Global config", paths.global_config_path().exists(), str(paths.global_config_path()))
    _add_check(table, "Workspace config", paths.workspace_config_path(root).exists(), str(paths.workspace_config_path(root)))
    _add_check(table, "Legacy .env", paths.legacy_workspace_env_path(root).exists(), "loaded if present")
    _add_check(
        table,
        "LLM API key",
        _has_any_llm_key(),
        "LLM_API_KEY or role-specific *_API_KEY",
    )
    _add_check(
        table,
        "PaddleOCR token",
        bool(settings.paddleocr_api_token),
        settings.paddleocr_model,
    )
    _add_check(table, "materials", paths.materials_root(root).exists(), "user uploads")
    _add_check(table, "outputs", paths.outputs_root(root).exists(), "final outputs")
    _add_check(table, "Runtime", paths.runtime_root(root).exists(), str(paths.runtime_root(root)))
    _add_check(table, "Global services", paths.global_services_root().exists(), str(paths.global_services_root()))
    _add_check(
        table,
        "pipeline-state.json",
        paths.pipeline_state_path(root).exists(),
        "resume state",
    )
    _add_check(table, "rag-store", paths.rag_store_path(root).exists(), "embedded Qdrant store")

    embed_ok, embed_detail = _embedding_health()
    _add_check(table, "Embedding server", embed_ok, embed_detail)

    git_ok, git_detail = _git_status_summary(root)
    _add_check(table, "Git", git_ok, git_detail)

    rprint(table)


@app.command()
def materials() -> None:
    """Show material ingest and embedding status from the local manifest."""
    from tree.engine import (
        _collection_for_material,
        _file_fingerprint,
        _is_supported_material,
        _load_source_manifest,
    )

    root = Path.cwd()
    materials_root = paths.materials_root(root)
    manifest = _load_source_manifest(root)
    if not materials_root.exists():
        rprint("[yellow]materials/ does not exist yet.[/yellow]")
        return

    rows = []
    for path in sorted(materials_root.rglob("*")):
        if not _is_supported_material(path):
            continue
        rel = _relative_to_root(root, path)
        collection = _collection_for_material(materials_root, path)
        entry = manifest.get(rel, {})
        fingerprint = _file_fingerprint(path)
        if not entry:
            status_text = "[yellow]new[/yellow]"
        elif entry.get("fingerprint") != fingerprint:
            status_text = "[yellow]changed[/yellow]"
        elif entry.get("embedded") is True:
            status_text = "[green]embedded[/green]"
        elif entry.get("outputs"):
            status_text = "[cyan]ocr/structure done[/cyan]"
        else:
            status_text = "[red]pending[/red]"
        rows.append((rel, collection, status_text, _format_bytes(path.stat().st_size)))

    if not rows:
        rprint("[dim]No supported materials found.[/dim]")
        return

    table = Table(title="Materials")
    table.add_column("Path")
    table.add_column("Collection")
    table.add_column("Status")
    table.add_column("Size", justify="right")
    for row in rows:
        table.add_row(*row)
    rprint(table)


@app.command()
def logs(
    tail: int = typer.Option(20, "--tail", "-n", min=1, help="Number of recent entries"),
    agent: str | None = typer.Option(None, "--agent", help="Filter by agent name"),
    step_name: str | None = typer.Option(None, "--step", help="Filter by step, e.g. S1"),
) -> None:
    """Show recent pipeline trace entries."""
    trace_path = paths.pipeline_temp_root(Path.cwd()) / "trace.jsonl"
    if not trace_path.exists():
        rprint(f"[dim]No trace log found at {trace_path}.[/dim]")
        return

    entries = []
    for line in trace_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if agent and entry.get("agent") != agent:
            continue
        if step_name and entry.get("step") != step_name:
            continue
        entries.append(entry)

    table = Table(title=f"Trace Log (last {tail})")
    for col in ("ts", "step", "agent", "action", "chapter", "file_seq", "route", "duration_ms"):
        table.add_column(col)
    for entry in entries[-tail:]:
        table.add_row(
            str(entry.get("ts", "")),
            str(entry.get("step", "")),
            str(entry.get("agent", "")),
            str(entry.get("action", "")),
            str(entry.get("chapter", "")),
            str(entry.get("file_seq", "")),
            str(entry.get("route", "")),
            str(entry.get("duration_ms", "")),
        )
    rprint(table)


@app.command()
def progress(
    tail: int = typer.Option(5, "--tail", "-n", min=1, max=20, help="Recent trace entries"),
) -> None:
    """Show a dashboard-style snapshot of current TREE progress."""
    rprint(_build_progress_view(Path.cwd(), tail=tail))


@app.command()
def watch(
    interval: float = typer.Option(3.0, "--interval", "-i", min=1.0, help="Refresh interval in seconds"),
    tail: int = typer.Option(5, "--tail", "-n", min=1, max=20, help="Recent trace entries"),
) -> None:
    """Continuously refresh current TREE progress until Esc or Ctrl+C."""
    root = Path.cwd()
    rprint("[dim]Watching TREE progress. Press Esc or Ctrl+C to return to the prompt.[/dim]")
    try:
        with _watch_escape_reader() as escape_pressed, Live(
            _build_progress_view(root, tail=tail),
            refresh_per_second=4,
        ) as live:
            next_refresh = time.monotonic() + interval
            while True:
                if escape_pressed():
                    break
                now = time.monotonic()
                if now >= next_refresh:
                    live.update(_build_progress_view(root, tail=tail))
                    next_refresh = now + interval
                time.sleep(0.05)
    except KeyboardInterrupt:
        pass
    rprint("\n[dim]Stopped watching. TREE services were not changed.[/dim]")


@contextmanager
def _watch_escape_reader() -> Callable[[], bool]:
    if not sys.stdin.isatty():
        yield lambda: False
        return

    if os.name == "nt":
        import msvcrt

        def windows_escape_pressed() -> bool:
            if not msvcrt.kbhit():
                return False
            return msvcrt.getwch() == "\x1b"

        yield windows_escape_pressed
        return

    import select
    import termios
    import tty

    fd = sys.stdin.fileno()
    previous = termios.tcgetattr(fd)
    tty.setcbreak(fd)

    def posix_escape_pressed() -> bool:
        ready, _, _ = select.select([sys.stdin], [], [], 0)
        if not ready:
            return False
        return sys.stdin.read(1) == "\x1b"

    try:
        yield posix_escape_pressed
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, previous)


@app.command()
def clean(
    pycache: bool = typer.Option(True, "--pycache/--no-pycache", help="Remove Python caches"),
    pipeline_temp: bool = typer.Option(
        False,
        "--pipeline-temp",
        help="Remove internal .tree/runtime/pipeline-temp/",
    ),
    source_materials: bool = typer.Option(
        False,
        "--source-materials",
        help="Remove internal .tree/runtime/source_materials/",
    ),
    all_targets: bool = typer.Option(False, "--all", help="Clean all supported runtime targets"),
    dry_run: bool = typer.Option(True, "--dry-run/--apply", help="Preview by default; use --apply to delete"),
) -> None:
    """Clean runtime artifacts with a dry-run-first workflow."""
    root = Path.cwd()
    targets: list[Path] = []
    if pycache or all_targets:
        targets.extend(_iter_project_pycache_dirs(root))
        targets.extend(path for path in (root / ".pytest_cache", root / ".ruff_cache") if path.exists())
    if pipeline_temp or all_targets:
        targets.append(paths.pipeline_temp_root(root))
    if source_materials or all_targets:
        targets.append(paths.source_root(root))

    targets = sorted({path for path in targets if path.exists()})
    if not targets:
        rprint("[dim]Nothing to clean.[/dim]")
        return

    action = "Would remove" if dry_run else "Removing"
    for path in targets:
        rprint(f"[yellow]{action}[/yellow] {_relative_to_root(root, path)}")
        if not dry_run:
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()
    if dry_run:
        rprint("[dim]Re-run with --apply to delete these paths.[/dim]")


@app.command()
def prompts(
    role: str | None = typer.Argument(None, help="examiner, student, writer, or archivist"),
    full: bool = typer.Option(False, "--full", help="Print the full prompt"),
) -> None:
    """Inspect built-in agent prompts."""
    from tree.agents.prompts import PROMPTS

    if role is None:
        table = Table(title="Built-in Prompts")
        table.add_column("Role")
        table.add_column("Length")
        for name, prompt in PROMPTS.items():
            table.add_row(name, f"{len(prompt)} chars")
        rprint(table)
        return

    if role not in PROMPTS:
        raise typer.BadParameter(f"Unknown role: {role}. Choose one of: {', '.join(PROMPTS)}")
    prompt = PROMPTS[role]
    if full:
        rprint(prompt)
    else:
        preview = "\n".join(prompt.splitlines()[:30])
        rprint(Panel(preview, title=f"{role} prompt preview"))
        rprint("[dim]Use --full to print the complete prompt.[/dim]")


@app.command()
def setup(
    force: bool = typer.Option(False, "--force", help="Run the setup wizard even if config exists"),
    workspace: bool = typer.Option(False, "--workspace", help="Write settings only for the current workspace"),
) -> None:
    """Create or update global or workspace configuration interactively."""
    root = Path.cwd()
    _ensure_workspace_dirs(root)
    env_path = paths.workspace_config_path(root) if workspace else paths.global_config_path()
    if env_path.exists() and not force:
        rprint(f"[green]{env_path} already exists.[/green] Use --force to run the wizard again.")
        return
    _run_setup_wizard(root, env_path=env_path, force=force, scope="workspace" if workspace else "global")


@app.command()
def models(
    base_url: str | None = typer.Option(None, "--base-url", help="Set default LLM base URL"),
    model: str | None = typer.Option(None, "--model", help="Set default LLM model"),
    examiner: str | None = typer.Option(None, "--examiner", help="Set Examiner model"),
    student: str | None = typer.Option(None, "--student", help="Set Student model"),
    writer: str | None = typer.Option(None, "--writer", help="Set Writer model"),
    archivist: str | None = typer.Option(None, "--archivist", help="Set Archivist model"),
    api_key: bool = typer.Option(False, "--api-key", help="Prompt for shared LLM API key"),
    paddleocr_key: bool = typer.Option(False, "--paddleocr-key", help="Prompt for PaddleOCR API key"),
    workspace: bool = typer.Option(False, "--workspace", help="Read and update current workspace config"),
) -> None:
    """Show or update model/provider settings."""
    root = Path.cwd()
    _ensure_workspace_dirs(root)
    env_path = paths.workspace_config_path(root) if workspace else paths.global_config_path()
    if not env_path.exists():
        rprint(f"[yellow]{env_path} does not exist yet. Starting setup wizard.[/yellow]")
        _run_setup_wizard(root, env_path=env_path, force=False, scope="workspace" if workspace else "global")

    env = _read_effective_env(root)
    target_env = _read_env_file(env_path)
    updates: dict[str, str] = {}
    if base_url is not None:
        updates["LLM_BASE_URL"] = _clean_prompt_value(base_url)
    if model is not None:
        updates["LLM_MODEL"] = _clean_prompt_value(model)
    for key, value in {
        "EXAMINER_MODEL": examiner,
        "STUDENT_MODEL": student,
        "WRITER_MODEL": writer,
        "ARCHIVIST_MODEL": archivist,
    }.items():
        if value is not None:
            updates[key] = _clean_prompt_value(value)
    if api_key:
        updates["LLM_API_KEY"] = typer.prompt("Shared LLM / agent API key", hide_input=True)
    if paddleocr_key:
        updates["PADDLEOCR_API_TOKEN"] = typer.prompt("PaddleOCR API key", hide_input=True)

    if updates:
        target_env.update(updates)
        _write_env_file(env_path, target_env)
        env.update(updates)
        _load_env_into_process(env)
        rprint(f"[green]Updated[/green] {env_path}")

    _print_model_config(env)


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
    from tree.observability.progress import ProgressTracker

    _ensure_workspace_config(Path.cwd(), require_llm=not no_structure)
    root = Path.cwd()
    target_dir = output_dir or paths.source_root(root) / (collection or input_path.stem)
    progress_tracker = ProgressTracker(root)
    progress_tracker.reset()
    ingest_total = (
        1
        if input_path.is_file()
        else sum(1 for path in input_path.iterdir() if path.is_file() and not path.name.startswith("."))
    )
    progress_tracker.source_ingest_start(ingest_total)

    indexer = None
    if not no_index:
        from tree.rag.client import RAGClient
        from tree.rag.indexer import RAGIndexer

        indexer = RAGIndexer(RAGClient(store_path=paths.rag_store_path(root)))

    collection_name = collection or input_path.stem

    if no_structure:
        settings = Settings.from_env(project_root=root, require_llm=False)
        outputs = asyncio.run(
            ingest_path(
                input_path,
                target_dir,
                settings,
                archivist=None,
                collection=collection_name,
                indexer=indexer,
                progress=progress_tracker,
            )
        )
    else:
        settings = Settings.from_env(project_root=root)
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


@rag_app.command("status")
def rag_status() -> None:
    """Show local RAG chunk counts grouped by content kind."""
    try:
        from tree.rag.client import RAGClient

        chunks = RAGClient(store_path=paths.rag_store_path(Path.cwd())).scroll_chunks(
            limit=10000,
            include_drafts=True,
        )
    except Exception as exc:
        rprint(f"[red]RAG unavailable:[/red] {exc}")
        return

    if not chunks:
        rprint("[dim]RAG index is empty.[/dim]")
        return

    grouped: dict[tuple[str, str], set[str]] = {}
    for chunk in chunks:
        metadata = chunk.get("metadata", {})
        kind = metadata.get("content_kind", "unknown")
        collection = metadata.get("source_collection") or metadata.get("chapter") or "unknown"
        key = (kind, collection)
        grouped.setdefault(key, set()).add(metadata.get("doc_id") or metadata.get("path") or "")

    table = Table(title="RAG Status")
    table.add_column("Content Kind")
    table.add_column("Collection/Chapter")
    table.add_column("Documents", justify="right")
    table.add_column("Chunks", justify="right")
    for key in sorted(grouped):
        chunk_count = sum(
            1
            for chunk in chunks
            if (
                chunk.get("metadata", {}).get("content_kind", "unknown"),
                chunk.get("metadata", {}).get("source_collection")
                or chunk.get("metadata", {}).get("chapter")
                or "unknown",
            ) == key
        )
        table.add_row(key[0], key[1], str(len(grouped[key])), str(chunk_count))
    rprint(table)


@rag_app.command("ledger")
def rag_ledger(
    limit: int = typer.Option(20, "--limit", "-n", min=1, max=100),
) -> None:
    """Show the compact finished-output knowledge ledger used for duplicate checks."""
    try:
        from tree.curriculum.ledger import reconcile_finished_outputs

        ledger = reconcile_finished_outputs(Path.cwd())
    except Exception as exc:
        rprint(f"[red]Knowledge ledger unavailable:[/red] {exc}")
        return

    records = [item for item in ledger.get("records", []) if isinstance(item, dict)]
    if not records:
        rprint("[dim]Knowledge ledger is empty. Finish at least one output first.[/dim]")
        return

    table = Table(title="Knowledge Ledger")
    table.add_column("Seq")
    table.add_column("Chapter")
    table.add_column("Knowledge Point")
    table.add_column("Concepts")
    table.add_column("Path")
    for record in records[:limit]:
        concepts = ", ".join(record.get("covered_concepts", [])[:6])
        table.add_row(
            str(record.get("file_seq", "")),
            str(record.get("chapter", "")),
            str(record.get("knowledge_point", "")),
            _truncate(concepts, 72),
            str(record.get("path", "")),
        )
    rprint(table)
    if len(records) > limit:
        rprint(f"[dim]{len(records) - limit} more records omitted. Use --limit to show more.[/dim]")


@rag_app.command("inventory")
def rag_inventory(
    rebuild: bool = typer.Option(False, "--rebuild", help="Rebuild from source RAG chunks first"),
    limit: int = typer.Option(20, "--limit", "-n", min=1, max=100),
) -> None:
    """Show chunk-level source inventory used for chapter selection."""
    try:
        from tree.curriculum.inventory import load_inventory, rebuild_source_inventory
        from tree.rag.client import RAGClient

        root = Path.cwd()
        if rebuild or not paths.source_inventory_path(root).exists():
            rag = RAGClient(store_path=paths.rag_store_path(root))
            try:
                source_chunks = rag.scroll_chunks(
                    filters={"content_kind": "source"},
                    include_drafts=False,
                    limit=10000,
                )
                inventory = rebuild_source_inventory(root, source_chunks)
            finally:
                rag.close()
        else:
            inventory = load_inventory(root)
    except Exception as exc:
        rprint(f"[red]Source inventory unavailable:[/red] {exc}")
        return

    collections = [item for item in inventory.get("collections", []) if isinstance(item, dict)]
    if not collections:
        rprint("[dim]Source inventory is empty. Ingest and embed source materials first.[/dim]")
        return

    table = Table(title="Source Inventory")
    table.add_column("Collection")
    table.add_column("Docs", justify="right")
    table.add_column("Chunks", justify="right")
    table.add_column("Core Concepts")
    table.add_column("Sections")
    table.add_column("Related")
    for collection in collections[:limit]:
        concepts = ", ".join(collection.get("core_concepts", [])[:8])
        sections = ", ".join(collection.get("section_ids", [])[:5])
        related = ", ".join(
            f"{item.get('source_collection')}:{item.get('score', 0):.2f}"
            for item in collection.get("related_collections", [])[:4]
            if isinstance(item, dict)
        )
        table.add_row(
            str(collection.get("source_collection", "")),
            str(collection.get("doc_count", 0)),
            str(collection.get("chunk_count", 0)),
            _truncate(concepts, 72),
            _truncate(sections, 58),
            related,
        )
    rprint(table)
    if len(collections) > limit:
        rprint(f"[dim]{len(collections) - limit} more collections omitted. Use --limit to show more.[/dim]")


@rag_app.command("candidates")
def rag_candidates(
    rebuild: bool = typer.Option(False, "--rebuild", help="Rebuild inventory and candidate nodes first"),
    limit: int = typer.Option(20, "--limit", "-n", min=1, max=100),
) -> None:
    """Show candidate knowledge nodes derived from source inventory."""
    try:
        from tree.curriculum.inventory import load_inventory, rebuild_source_inventory
        from tree.curriculum.candidate_nodes import load_candidate_nodes, rebuild_candidate_nodes
        from tree.rag.client import RAGClient
        from tree.state.manager import StateManager

        root = Path.cwd()
        state = StateManager(paths.pipeline_state_path(root)).load()
        completed = {
            collection
            for chapter in state.chapters
            if chapter.status == "completed" and chapter.chapter_title
            for collection in ([chapter.source_collection] + list(chapter.source_collections or []))
            if collection
        }
        if rebuild or not paths.candidate_nodes_path(root).exists():
            if rebuild or not paths.source_inventory_path(root).exists():
                rag = RAGClient(store_path=paths.rag_store_path(root))
                try:
                    source_chunks = rag.scroll_chunks(
                        filters={"content_kind": "source"},
                        include_drafts=False,
                        limit=10000,
                    )
                    inventory = rebuild_source_inventory(root, source_chunks)
                finally:
                    rag.close()
            else:
                inventory = load_inventory(root)
            candidate_nodes = rebuild_candidate_nodes(root, inventory, completed_collections=completed)
        else:
            candidate_nodes = load_candidate_nodes(root)
    except Exception as exc:
        rprint(f"[red]Candidate nodes unavailable:[/red] {exc}")
        return

    candidates = [
        item for item in candidate_nodes.get("chapter_candidates", []) if isinstance(item, dict)
    ]
    if not candidates:
        rprint("[dim]Candidate nodes are empty. Build source inventory first.[/dim]")
        return

    table = Table(title="Candidate Knowledge Nodes")
    table.add_column("Candidate")
    table.add_column("Status")
    table.add_column("Title Hint")
    table.add_column("Collections")
    table.add_column("Prerequisites")
    table.add_column("Core Concepts")
    for candidate in candidates[:limit]:
        table.add_row(
            str(candidate.get("candidate_id", "")),
            str(candidate.get("status", "")),
            _truncate(str(candidate.get("title_hint", "")), 34),
            ", ".join(candidate.get("source_collections", [])),
            _truncate(", ".join(candidate.get("prerequisite_concepts", [])[:5]), 42),
            _truncate(", ".join(candidate.get("core_concepts", [])[:8]), 72),
        )
    rprint(table)
    if len(candidates) > limit:
        rprint(f"[dim]{len(candidates) - limit} more candidates omitted. Use --limit to show more.[/dim]")


@rag_app.command("map")
def rag_map(
    rebuild: bool = typer.Option(False, "--rebuild", help="Rebuild inventory and candidate nodes first"),
    limit: int = typer.Option(20, "--limit", "-n", min=1, max=100),
) -> None:
    """Compatibility alias for `rag candidates`."""
    rag_candidates(rebuild=rebuild, limit=limit)


@rag_app.command("graph")
def rag_graph(
    rebuild: bool = typer.Option(False, "--rebuild", help="Rebuild graph from candidate nodes and ledger first"),
    limit: int = typer.Option(20, "--limit", "-n", min=1, max=100),
) -> None:
    """Show knowledge graph nodes and relation edges."""
    try:
        from tree.curriculum.graph import load_knowledge_graph, rebuild_knowledge_graph
        from tree.curriculum.ledger import reconcile_finished_outputs
        from tree.curriculum.candidate_nodes import load_candidate_nodes

        root = Path.cwd()
        if rebuild or not paths.knowledge_graph_path(root).exists():
            ledger = reconcile_finished_outputs(root)
            candidate_nodes = load_candidate_nodes(root)
            graph = rebuild_knowledge_graph(root, candidate_nodes, ledger)
        else:
            graph = load_knowledge_graph(root)
    except Exception as exc:
        rprint(f"[red]Knowledge graph unavailable:[/red] {exc}")
        return

    nodes = [item for item in graph.get("nodes", []) if isinstance(item, dict)]
    edges = [item for item in graph.get("edges", []) if isinstance(item, dict)]
    stats = graph.get("stats", {})
    planner = graph.get("planner", {})
    rprint(
        "[dim]"
        f"finished={stats.get('finished_count', 0)} "
        f"planned={stats.get('planned_count', 0)} "
        f"eligible={stats.get('eligible_count', 0)} "
        f"blocked={stats.get('blocked_count', 0)} "
        f"roots={stats.get('root_count', 0)} "
        f"branches={stats.get('branch_count', 0)} "
        f"edges={stats.get('edge_count', 0)}"
        "[/dim]"
    )
    rprint(
        "[dim]"
        f"mode={planner.get('mode', 'n/a')} "
        f"selection_mode={planner.get('selection_mode', 'n/a')} "
        f"planner_selected={planner.get('selected_node') or 'none'} "
        f"frontier={len(planner.get('frontier_nodes', []) or [])}"
        "[/dim]"
    )
    if not nodes:
        rprint("[dim]Knowledge graph is empty. Build candidate nodes first.[/dim]")
        return
    selected = next(
        (node for node in nodes if node.get("node_id") == planner.get("selected_node")),
        None,
    )
    if selected:
        rprint(f"[dim]why_selected={selected.get('why_selected', 'n/a')}[/dim]")

    node_table = Table(title="Knowledge Graph Nodes")
    node_table.add_column("Node")
    node_table.add_column("Status")
    node_table.add_column("Eligible")
    node_table.add_column("Forest")
    node_table.add_column("Title")
    node_table.add_column("Requires")
    node_table.add_column("Concepts")
    for node in nodes[:limit]:
        tree_flags = []
        if node.get("is_root"):
            tree_flags.append("root")
        if node.get("is_new_root"):
            tree_flags.append("new-root")
        if node.get("planner_selected"):
            tree_flags.append("selected")
        parent = node.get("parent_output") or node.get("backbone_parent")
        if parent:
            tree_flags.append(f"p:{parent}")
        if node.get("branch_score"):
            tree_flags.append(f"b:{node.get('branch_score'):.2f}")
        if node.get("supporting_parents"):
            tree_flags.append(f"sup:{len(node.get('supporting_parents') or [])}")
        node_table.add_row(
            str(node.get("node_id", "")),
            str(node.get("status", "")),
            "yes" if node.get("eligible") else "",
            _truncate(", ".join(tree_flags), 42),
            _truncate(str(node.get("title", "")), 34),
            _truncate(", ".join(node.get("required_nodes", [])[:4]), 42),
            _truncate(", ".join(node.get("core_concepts", [])[:6]), 64),
        )
    rprint(node_table)

    if edges:
        edge_table = Table(title="Knowledge Graph Relations")
        edge_table.add_column("Relation")
        edge_table.add_column("From")
        edge_table.add_column("To")
        edge_table.add_column("Scores")
        edge_table.add_column("Evidence")
        for edge in edges[:limit]:
            scores = edge.get("scores", {})
            evidence = edge.get("evidence", {})
            evidence_text = ", ".join(evidence.get("matched_concepts", [])[:4])
            if not evidence_text:
                evidence_text = ", ".join(evidence.get("prerequisite_hits", [])[:4])
            if not evidence_text:
                evidence_text = ", ".join(evidence.get("matched_chunks", [])[:3])
            edge_table.add_row(
                str(edge.get("relation", "")),
                _truncate(str(edge.get("from", "")), 36),
                _truncate(str(edge.get("to", "")), 36),
                (
                    f"a={scores.get('affinity', 0):.2f} "
                    f"c={scores.get('concept', 0):.2f} "
                    f"k={scores.get('chunk', 0):.2f} "
                    f"s={scores.get('source', 0):.2f}"
                ),
                _truncate(evidence_text or "n/a", 50),
            )
        rprint(edge_table)
        if len(edges) > limit:
            rprint(f"[dim]{len(edges) - limit} more edges omitted. Use --limit to show more.[/dim]")


@rag_app.command("search")
def rag_search(
    query: str = typer.Argument(..., help="Search query"),
    top_k: int = typer.Option(5, "--top-k", "-k", min=1, max=20),
    content_kind: str | None = typer.Option(None, "--kind", help="source or finished"),
    collection: str | None = typer.Option(None, "--collection", help="source_collection filter"),
    chapter: str | None = typer.Option(None, "--chapter", help="chapter filter"),
) -> None:
    """Search the local RAG index."""
    try:
        from tree.rag.client import RAGClient

        filters = {}
        if content_kind:
            filters["content_kind"] = content_kind
        if collection:
            filters["source_collection"] = collection
        if chapter:
            filters["chapter"] = chapter
        hits = RAGClient(store_path=paths.rag_store_path(Path.cwd())).query(
            query,
            top_k=top_k,
            filters=filters or None,
            include_drafts=False,
        )
    except Exception as exc:
        rprint(f"[red]RAG search failed:[/red] {exc}")
        return

    if not hits:
        rprint("[dim]No RAG hits.[/dim]")
        return

    for idx, hit in enumerate(hits, start=1):
        metadata = hit.get("metadata", {})
        source = metadata.get("path") or metadata.get("filename") or metadata.get("doc_id") or "unknown"
        score = hit.get("score")
        score_text = f"{score:.4f}" if isinstance(score, float) else "n/a"
        excerpt = _truncate((hit.get("text") or "").replace("\n", " "), 700)
        rprint(Panel(excerpt, title=f"#{idx} {source} score={score_text}"))


def _build_progress_view(root: Path, tail: int = 5) -> Group:
    from tree.observability.progress import load_progress
    from tree.services import service_status
    from tree.state.manager import StateManager

    services = Table(title="Services", expand=True)
    services.add_column("Service")
    services.add_column("Status")
    services.add_column("PID")
    for name in ("tree", "embedding"):
        svc = service_status(root, name)
        services.add_row(
            name,
            "[green]running[/green]" if svc.running else "[dim]stopped[/dim]",
            str(svc.pid or ""),
        )

    progress_state = load_progress(root)
    state = StateManager(paths.pipeline_state_path(root)).load()
    completed_files = sum(len(ch.files_completed) for ch in state.chapters)
    active = next((ch for ch in state.chapters if ch.status == "in_progress"), None)
    active_chapter = active.chapter_name if active else ""
    live_progress = _live_progress_table(
        progress_state,
        completed_files=completed_files,
        active_chapter=active_chapter,
    )
    current_tree = _current_tree_panel(root, active_chapter=active_chapter)
    tree_log = _tail_panel(paths.service_log_path(root, "tree"), title="TREE Log Tail")
    return Group(services, live_progress, current_tree, tree_log)


def _current_tree_panel(root: Path, active_chapter: str = "") -> Panel | Table:
    try:
        from tree.curriculum.graph import load_knowledge_graph

        graph = load_knowledge_graph(root)
    except Exception as exc:
        return Panel(
            f"[dim]Knowledge graph unavailable: {exc}[/dim]",
            title="Current Tree",
            border_style=_TREE_BORDER,
        )
    model = _current_tree_view_model(graph, active_chapter=active_chapter)
    if not model["nodes"]:
        return Panel(
            "[dim]No knowledge graph nodes yet.[/dim]",
            title="Current Tree",
            border_style=_TREE_BORDER,
        )
    if model["mode"] == "table":
        return _current_tree_relation_table(model)
    return Panel(
        Group(
            _current_tree_node_table(model),
            _current_tree_edge_table(model),
        ),
        title=f"Current Tree: {model['current_tree'] or 'n/a'}",
        border_style=_TREE_BORDER,
    )


def _current_tree_view_model(
    graph: dict[str, Any],
    active_chapter: str = "",
    limit: int = 12,
) -> dict[str, Any]:
    nodes = [item for item in graph.get("nodes", []) if isinstance(item, dict)]
    edges = [item for item in graph.get("edges", []) if isinstance(item, dict)]
    planner = graph.get("planner") if isinstance(graph.get("planner"), dict) else {}
    selected_id = str(planner.get("selected_node") or "")
    selected = next((node for node in nodes if node.get("node_id") == selected_id), None)
    current_tree = _current_tree_id(active_chapter, selected, nodes)
    selected_parent_tree = _node_tree_id(selected) if selected else ""

    finished = [
        node
        for node in nodes
        if node.get("kind") == "finished"
        and (not current_tree or _node_tree_id(node) == current_tree)
    ]
    visible = sorted(finished, key=_tree_node_sort_key)
    if selected and selected.get("status") == "planned":
        if (
            selected.get("is_new_root")
            or not current_tree
            or current_tree == "new-root"
            or selected_parent_tree == current_tree
        ):
            visible.append(selected)

    all_ids = {str(node.get("node_id") or "") for node in nodes}
    rows = []
    missing_reasons = []
    for node in visible[:limit]:
        parents = _tree_parent_ids(node)
        missing = [parent for parent in parents if parent not in all_ids]
        parent_status = "missing" if missing else ("ok" if parents else "root")
        if missing:
            missing_reasons.append(
                f"missing parent for {_short_node_id(str(node.get('node_id') or ''))}: "
                + ", ".join(_short_node_id(parent) for parent in missing)
            )
        if node.get("kind") == "candidate" and not node.get("is_new_root") and not parents:
            parent_status = "missing"
            missing_reasons.append(f"missing parent for {_short_node_id(str(node.get('node_id') or ''))}")
        rows.append(
            {
                "node_id": str(node.get("node_id") or ""),
                "marker": "▶" if node.get("node_id") == selected_id else "✓",
                "kind": str(node.get("kind") or ""),
                "status": str(node.get("status") or ""),
                "title": str(node.get("title") or ""),
                "parents": parents,
                "parent_status": parent_status,
                "depth": _tree_depth(node, visible),
                "concepts": [str(item) for item in (node.get("core_concepts") or [])[:4]],
                "branch_score": float(node.get("branch_score") or 0),
                "support_score": float(node.get("support_score") or 0),
            }
        )
    relation_rows = _current_tree_relations(rows, edges)
    if len(visible) > limit:
        relation_rows.append(
            {
                "relation": "omitted",
                "from": "",
                "to": f"{len(visible) - limit} more nodes",
                "score": "",
            }
        )
    mode = "table" if missing_reasons else "tree"
    return {
        "mode": mode,
        "reason": "; ".join(missing_reasons),
        "current_tree": current_tree,
        "selected_node": selected_id,
        "selection_mode": str(planner.get("selection_mode") or ""),
        "nodes": rows,
        "relations": relation_rows,
    }


def _current_tree_id(
    active_chapter: str,
    selected: dict[str, Any] | None,
    nodes: list[dict[str, Any]],
) -> str:
    if active_chapter:
        return active_chapter
    if selected:
        selected_tree = _node_tree_id(selected)
        if selected_tree:
            return selected_tree
        if selected.get("is_new_root"):
            return "new-root"
    for node in nodes:
        if node.get("kind") == "finished":
            tree_id = _node_tree_id(node)
            if tree_id:
                return tree_id
    return ""


def _node_tree_id(node: dict[str, Any] | None) -> str:
    if not node:
        return ""
    chapter = str(node.get("chapter") or "")
    if chapter:
        return chapter
    for value in [
        node.get("path"),
        node.get("node_id"),
        node.get("parent_output"),
        *((node.get("required_nodes") or []) if isinstance(node.get("required_nodes"), list) else []),
    ]:
        tree_id = _tree_id_from_output_ref(str(value or ""))
        if tree_id:
            return tree_id
    supporting = node.get("supporting_parents") or []
    if isinstance(supporting, list):
        for item in supporting:
            if isinstance(item, dict):
                tree_id = _tree_id_from_output_ref(str(item.get("node_id") or ""))
                if tree_id:
                    return tree_id
    return ""


def _tree_id_from_output_ref(value: str) -> str:
    match = re.search(r"(?:^|:)outputs/([^/]+)/", value)
    return match.group(1) if match else ""


def _tree_parent_ids(node: dict[str, Any]) -> list[str]:
    parents: list[str] = []
    parent_output = str(node.get("parent_output") or "")
    if parent_output:
        parents.append(parent_output)
    required = node.get("required_nodes") or []
    if isinstance(required, list):
        parents.extend(str(item) for item in required if item)
    supporting = node.get("supporting_parents") or []
    if isinstance(supporting, list):
        for item in supporting:
            if isinstance(item, dict) and item.get("node_id"):
                parents.append(str(item.get("node_id")))
    seen = set()
    unique = []
    for parent in parents:
        if parent in seen:
            continue
        seen.add(parent)
        unique.append(parent)
    return unique


def _tree_depth(node: dict[str, Any], visible: list[dict[str, Any]]) -> int:
    explicit = node.get("tree_depth")
    if isinstance(explicit, int):
        return max(0, explicit)
    by_id = {str(item.get("node_id") or ""): item for item in visible}
    parents = [parent for parent in _tree_parent_ids(node) if parent in by_id]
    if not parents:
        return 0
    return 1 + min(_tree_depth(by_id[parent], visible) for parent in parents)


def _tree_node_sort_key(node: dict[str, Any]) -> tuple[Any, ...]:
    return (
        int(node.get("tree_depth") or 0),
        str(node.get("file_seq") or ""),
        str(node.get("path") or ""),
        str(node.get("node_id") or ""),
    )


def _current_tree_relations(
    rows: list[dict[str, Any]],
    edges: list[dict[str, Any]],
) -> list[dict[str, str]]:
    row_ids = {row["node_id"] for row in rows}
    relation_rows = []
    for row in rows:
        for index, parent in enumerate(row["parents"]):
            relation_rows.append(
                {
                    "relation": "branch" if index == 0 else "support",
                    "from": parent,
                    "to": row["node_id"],
                    "score": _relation_score(parent, row["node_id"], edges),
                }
            )
    warnings = [
        edge
        for edge in edges
        if edge.get("relation") in {"duplicate", "merge_needed", "adjacent"}
        and (edge.get("from") in row_ids or edge.get("to") in row_ids)
    ]
    for edge in warnings[:4]:
        relation_rows.append(
            {
                "relation": str(edge.get("relation") or ""),
                "from": str(edge.get("from") or ""),
                "to": str(edge.get("to") or ""),
                "score": _edge_score_label(edge),
            }
        )
    return relation_rows[:12]


def _relation_score(parent: str, child: str, edges: list[dict[str, Any]]) -> str:
    for edge in edges:
        if edge.get("from") == parent and edge.get("to") == child:
            return _edge_score_label(edge)
    return ""


def _edge_score_label(edge: dict[str, Any]) -> str:
    scores = edge.get("scores") if isinstance(edge.get("scores"), dict) else {}
    for key in ("affinity", "concept", "chunk", "source"):
        value = scores.get(key)
        if isinstance(value, int | float):
            return f"{key[0]}={value:.2f}"
    return ""


def _current_tree_node_table(model: dict[str, Any]) -> Table:
    table = Table(title="Nodes", expand=True)
    table.add_column("")
    table.add_column("Node")
    table.add_column("Title")
    table.add_column("Parents")
    table.add_column("Concepts")
    for row in model["nodes"]:
        indent = "  " * int(row.get("depth") or 0)
        marker = "[yellow]▶[/yellow]" if row["marker"] == "▶" else "[green]✓[/green]"
        table.add_row(
            marker,
            indent + _short_node_id(row["node_id"]),
            _truncate(row["title"], 28),
            _truncate(", ".join(_short_node_id(parent) for parent in row["parents"]) or "root", 40),
            _truncate(", ".join(row["concepts"]), 44),
        )
    return table


def _current_tree_edge_table(model: dict[str, Any]) -> Table:
    table = Table(title="Relations", expand=True)
    table.add_column("Relation")
    table.add_column("From")
    table.add_column("To")
    table.add_column("Score")
    if not model["relations"]:
        table.add_row("root", "", "[dim]No parent relations yet[/dim]", "")
        return table
    for row in model["relations"]:
        table.add_row(
            str(row.get("relation") or ""),
            _truncate(_short_node_id(str(row.get("from") or "")), 32),
            _truncate(_short_node_id(str(row.get("to") or "")), 32),
            str(row.get("score") or ""),
        )
    return table


def _current_tree_relation_table(model: dict[str, Any]) -> Table:
    table = Table(title=f"Current Tree: {model['current_tree'] or 'n/a'} (relation table)", expand=True)
    table.add_column("Status")
    table.add_column("Node")
    table.add_column("Title")
    table.add_column("Parents")
    table.add_column("Reason")
    for row in model["nodes"]:
        status = "[red]missing parent[/red]" if row["parent_status"] == "missing" else row["parent_status"]
        table.add_row(
            status,
            _short_node_id(row["node_id"]),
            _truncate(row["title"], 30),
            _truncate(", ".join(_short_node_id(parent) for parent in row["parents"]) or "root", 44),
            _truncate(model["reason"], 54),
        )
    return table


def _short_node_id(node_id: str) -> str:
    if not node_id:
        return ""
    value = node_id.removeprefix("finished:").removeprefix("candidate:")
    if value.startswith("outputs/"):
        parts = value.split("/")
        if len(parts) >= 3:
            return "/".join(parts[1:])
    return value


def _live_progress_table(
    progress_state: dict[str, object],
    completed_files: int = 0,
    active_chapter: str = "",
) -> Table:
    table = Table(title="Live Progress", expand=True)
    table.add_column("Track")
    table.add_column("Progress")
    table.add_column("Current")

    source_ingest = _dict(progress_state.get("source_ingest"))
    ocr = _dict(source_ingest.get("ocr"))
    embedding = _dict(source_ingest.get("embedding"))
    learning = _dict(progress_state.get("learning_loop"))

    ocr_done = _int_value(ocr.get("pages_done"))
    ocr_total = _int_value(ocr.get("pages_total"))
    ocr_file_done = _int_value(ocr.get("files_done"))
    ocr_file_total = _int_value(ocr.get("files_total"))
    ocr_progress_done = ocr_file_done if ocr_file_total else ocr_done
    ocr_progress_total = ocr_file_total if ocr_file_total else ocr_total
    ocr_label = _ocr_progress_label(ocr_file_done, ocr_file_total, ocr_done, ocr_total)
    ocr_current = _ocr_current_label(ocr)
    table.add_row(
        "OCR",
        f"{_progress_bar(ocr_progress_done, ocr_progress_total)} {ocr_label}",
        _truncate(ocr_current, 42),
    )

    embed_done = _int_value(embedding.get("chunks_done"))
    embed_total = _int_value(embedding.get("chunks_total"))
    table.add_row(
        "Embedding",
        f"{_progress_bar(embed_done, embed_total)} {_progress_label(embed_done, embed_total)}",
        _truncate(str(embedding.get("current_chunk") or embedding.get("state") or ""), 42),
    )

    stage_index = _int_value(learning.get("stage_index"))
    stage_total = _int_value(learning.get("stage_total"))
    point = str(learning.get("knowledge_point") or learning.get("stage_label") or "")
    table.add_row(
        "Knowledge",
        f"{_progress_bar(stage_index, stage_total)} {_progress_label(stage_index, stage_total)}",
        _truncate(point, 42),
    )

    stage_rows = _knowledge_stage_rows(str(learning.get("stage") or ""), stage_total or 6)
    table.add_row("Stage", stage_rows, _truncate(str(progress_state.get("message") or ""), 42))

    table.add_row(
        "Completed files",
        str(completed_files),
        _truncate(active_chapter, 42),
    )
    return table


def _knowledge_stage_rows(current_stage: str, stage_total: int) -> str:
    stages = [
        ("find_knowledge_point", "Find point"),
        ("examiner_compose_exam", "Examiner exam"),
        ("student_blind_test", "Student test"),
        ("examiner_audit", "Examiner audit"),
        ("writer_drafting", "Writer draft"),
        ("pass_save_output", "PASS/save"),
    ][:stage_total]
    current_index = next((idx for idx, (key, _) in enumerate(stages) if key == current_stage), -1)
    rendered = []
    for idx, (key, label) in enumerate(stages):
        if idx < current_index:
            marker = "[green]✓[/green]"
        elif idx == current_index:
            marker = "[yellow]▶[/yellow]"
        else:
            marker = "[dim]·[/dim]"
        rendered.append(f"{marker} {label}")
    return "  ".join(rendered)


def _source_material_progress(root: Path) -> dict[str, int]:
    from tree.engine import _load_source_manifest, _pending_materials

    manifest = _load_source_manifest(root)
    pending = len(_pending_materials(root, manifest))
    return _progress_counts(manifest, pending=pending)


def _progress_counts(manifest: dict[str, object], pending: int = 0) -> dict[str, int]:
    entries = [entry for entry in manifest.values() if isinstance(entry, dict)]
    embedded = sum(1 for entry in entries if entry.get("embedded") is True)
    structured = sum(1 for entry in entries if entry.get("outputs") and entry.get("embedded") is not True)
    waiting = sum(1 for entry in entries if not entry.get("outputs") and entry.get("embedded") is not True)
    return {
        "total": len(entries) + pending,
        "embedded": embedded,
        "structured": structured,
        "pending": pending + waiting,
    }


def _dict(value: object) -> dict[str, object]:
    return value if isinstance(value, dict) else {}


def _int_value(value: object) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _progress_label(done: int, total: int) -> str:
    if total <= 0:
        return "n/a"
    return f"{min(done, total)}/{total}"


def _ocr_progress_label(
    files_done: int,
    files_total: int,
    pages_done: int,
    pages_total: int,
) -> str:
    labels = []
    if files_total:
        labels.append(f"files {_progress_label(files_done, files_total)}")
    if pages_total:
        labels.append(f"pages {_progress_label(pages_done, pages_total)}")
    return ", ".join(labels) if labels else "n/a"


def _ocr_current_label(ocr: dict[str, object]) -> str:
    current = str(ocr.get("current_chunk") or ocr.get("current_file") or ocr.get("state") or "")
    pages_done = _int_value(ocr.get("pages_done"))
    pages_total = _int_value(ocr.get("pages_total"))
    if pages_total:
        return f"{current} pages {_progress_label(pages_done, pages_total)}"
    return current


def _progress_bar(done: int, total: int, width: int = 18) -> str:
    if total <= 0:
        return "[dim]" + ("░" * width) + "[/dim]"
    ratio = max(0.0, min(float(done) / float(total), 1.0))
    filled = int(round(ratio * width))
    return "[green]" + ("█" * filled) + "[/green][dim]" + ("░" * (width - filled)) + "[/dim]"


def _trace_table(root: Path, tail: int) -> Table:
    entries = _load_trace_entries(root)[-tail:]
    table = Table(title=f"Recent Trace (last {tail})", expand=True)
    for col in ("step", "agent", "action", "chapter", "file_seq", "route"):
        table.add_column(col)
    if not entries:
        table.add_row("", "", "[dim]No trace yet[/dim]", "", "", "")
        return table
    for entry in entries:
        table.add_row(
            str(entry.get("step", "")),
            str(entry.get("agent", "")),
            str(entry.get("action", "")),
            _truncate(str(entry.get("chapter", "")), 28),
            str(entry.get("file_seq", "")),
            str(entry.get("route", "")),
        )
    return table


def _load_trace_entries(root: Path) -> list[dict[str, object]]:
    trace_path = paths.pipeline_temp_root(root) / "trace.jsonl"
    if not trace_path.exists():
        return []
    entries = []
    for line in trace_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(entry, dict):
            entries.append(entry)
    return entries


def _tail_panel(path: Path, title: str, line_count: int = 6) -> Panel:
    if not path.exists():
        return Panel("[dim]No log yet.[/dim]", title=title)
    lines = [line for line in path.read_text(encoding="utf-8", errors="replace").splitlines() if line.strip()]
    if not lines:
        return Panel("[dim]No log entries yet.[/dim]", title=title)
    return Panel("\n".join(lines[-line_count:]), title=title)


def _interactive_shell() -> None:
    root = Path.cwd()
    old_signal_handlers = _install_interactive_quit_handlers()
    try:
        rprint(
            Panel(
                "Type [bold]/start[/bold] to start TREE, [bold]/status[/bold] to inspect it, "
                "[bold]/watch[/bold] to follow progress, or [bold]/help[/bold] for commands.",
                title=f"[bold {_TREE_TITLE}]TREE[/]",
                border_style=_TREE_BORDER,
            )
        )
        while True:
            try:
                line = input("TREE> ")
            except (EOFError, KeyboardInterrupt, _InteractiveQuitRequested):
                rprint("\n[yellow]TREE interactive closed. Running /quit...[/yellow]")
                _quit_services(root)
                return

            try:
                args = _parse_interactive_command(line)
            except ValueError as exc:
                rprint(f"[red]{exc}[/red]")
                continue
            if not args:
                continue

            command = args[0]
            if command == "exit":
                rprint("[dim]Leaving TREE. TREE services were not changed.[/dim]")
                return
            if command == "help":
                _print_interactive_help(args[1:])
                continue

            _invoke_cli_args(args)
            if command == "quit":
                return
    except _InteractiveQuitRequested:
        rprint("\n[yellow]TREE interactive closed. Running /quit...[/yellow]")
        _quit_services(root)
    finally:
        _restore_signal_handlers(old_signal_handlers)


def _install_interactive_quit_handlers() -> dict[signal.Signals, object]:
    handlers = {}

    def request_quit(signum, frame) -> None:
        raise _InteractiveQuitRequested

    for signal_name in ("SIGHUP", "SIGTERM"):
        signum = getattr(signal, signal_name, None)
        if signum is None:
            continue
        handlers[signum] = signal.getsignal(signum)
        signal.signal(signum, request_quit)
    return handlers


def _restore_signal_handlers(handlers: dict[signal.Signals, object]) -> None:
    for signum, handler in handlers.items():
        signal.signal(signum, handler)


def _quit_services(root: Path) -> None:
    from tree.services import _service_state_label, request_tree_stop, stop_service

    request_tree_stop(root)
    tree = stop_service(root, "tree", force=True)
    embed = stop_service(root, "embedding", force=True)
    level = "green" if not tree.running and not embed.running else "yellow"
    rprint(
        f"[{level}]Quit complete.[/{level}] "
        f"TREE={_service_state_label(tree.running)}; "
        f"embedding={_service_state_label(embed.running)}"
    )
    if tree.running or embed.running:
        rprint("[yellow]One or more services did not exit within the timeout. Run /status to inspect.[/yellow]")


def _parse_interactive_command(line: str) -> list[str] | None:
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return None
    if stripped.startswith("/"):
        stripped = stripped[1:].strip()
    if not stripped:
        return None
    try:
        parts = shlex.split(stripped)
    except ValueError as exc:
        raise ValueError(f"Could not parse command: {exc}") from exc
    if not parts:
        return None
    command = _INTERACTIVE_ALIASES.get(parts[0].lower(), parts[0].lower())
    return [command, *parts[1:]]


def _print_interactive_help(args: list[str] | None = None) -> None:
    if args:
        _invoke_cli_args([args[0], "--help", *args[1:]])
        return
    table = Table(
        title=f"[bold {_TREE_TITLE}]TREE Slash Commands[/]",
        border_style=_TREE_BORDER,
    )
    table.add_column("Command")
    table.add_column("Action")
    for command, description in _INTERACTIVE_COMMANDS:
        table.add_row(command, description)
    rprint(table)
    rprint("[dim]Options work too, for example: /logs --tail 50 or /rag search \"equilibrium\"[/dim]")


def _invoke_cli_args(args: list[str]) -> None:
    command = typer.main.get_command(app)
    try:
        command.main(args=args, prog_name="tre", standalone_mode=False)
    except click.ClickException as exc:
        exc.show()
    except click.exceptions.Exit as exc:
        if exc.exit_code not in (0, None):
            rprint(f"[red]Command exited with code {exc.exit_code}[/red]")
    except Exception as exc:
        rprint(f"[red]{type(exc).__name__}:[/red] {exc}")


def _add_check(table: Table, name: str, ok: bool, details: str) -> None:
    status_text = "[green]ok[/green]" if ok else "[yellow]check[/yellow]"
    table.add_row(name, status_text, details)


async def _run_engine(engine) -> None:
    try:
        await engine.run()
    finally:
        await engine.close()


def _has_any_llm_key() -> bool:
    keys = ["LLM_API_KEY", "EXAMINER_API_KEY", "STUDENT_API_KEY", "WRITER_API_KEY", "ARCHIVIST_API_KEY"]
    return any(os.environ.get(key) for key in keys)


def _ensure_workspace_dirs(root: Path) -> None:
    paths.materials_root(root).mkdir(exist_ok=True)
    paths.outputs_root(root).mkdir(exist_ok=True)
    paths.runtime_root(root).mkdir(parents=True, exist_ok=True)


def _has_any_config(root: Path) -> bool:
    config_file_exists = any(
        path.exists()
        for path in (
            paths.global_config_path(),
            paths.workspace_config_path(root),
            paths.legacy_workspace_env_path(root),
        )
    )
    return config_file_exists or any(os.environ.get(key) for key in ("LLM_API_KEY", "PADDLEOCR_API_TOKEN"))


def _ensure_workspace_config(root: Path, require_llm: bool = True) -> None:
    _ensure_workspace_dirs(root)
    if _has_any_config(root):
        return
    rprint(Panel(
        "TREE has no global provider config yet.\n"
        "These settings are written once to your user-level TREE home and reused across workspaces.",
        title="First-time setup",
    ))
    _run_setup_wizard(root, env_path=paths.global_config_path(), force=False, require_llm=require_llm, scope="global")


def _run_setup_wizard(
    root: Path,
    env_path: Path,
    force: bool,
    require_llm: bool = True,
    scope: str = "global",
) -> None:
    existed = env_path.exists()
    existing = _read_env_file(env_path) if env_path.exists() else {}
    values = {**_DEFAULT_ENV, **_read_effective_env(root), **existing}

    _ensure_workspace_dirs(root)
    env_path.parent.mkdir(parents=True, exist_ok=True)

    rprint(f"[bold]T.R.E.E. {scope} setup[/bold]")
    rprint(f"[dim]Secrets are written to {env_path}.[/dim]\n")

    if require_llm or typer.confirm("Configure LLM / agent provider now?", default=True):
        values["LLM_API_KEY"] = _prompt_secret(
            "Shared LLM / agent API key",
            current=values.get("LLM_API_KEY", ""),
            required=require_llm,
        )
        values["LLM_BASE_URL"] = _prompt_visible(
            "LLM base URL",
            current=existing.get("LLM_BASE_URL", ""),
            required=require_llm,
        )
        values["LLM_BASE_URL"] = _clean_prompt_value(values["LLM_BASE_URL"])
        values["LLM_MODEL"] = _prompt_visible(
            "Default LLM model",
            current=existing.get("LLM_MODEL", ""),
            required=require_llm,
        )
        values["LLM_MODEL"] = _clean_prompt_value(values["LLM_MODEL"])
        default_model = values["LLM_MODEL"]
        values["EXAMINER_MODEL"] = typer.prompt(
            "Examiner model",
            default=existing.get("EXAMINER_MODEL", default_model),
        )
        values["EXAMINER_MODEL"] = _clean_prompt_value(values["EXAMINER_MODEL"])
        values["STUDENT_MODEL"] = typer.prompt(
            "Student model",
            default=existing.get("STUDENT_MODEL", default_model),
        )
        values["STUDENT_MODEL"] = _clean_prompt_value(values["STUDENT_MODEL"])
        values["WRITER_MODEL"] = typer.prompt(
            "Writer model",
            default=existing.get("WRITER_MODEL", default_model),
        )
        values["WRITER_MODEL"] = _clean_prompt_value(values["WRITER_MODEL"])
        values["ARCHIVIST_MODEL"] = typer.prompt(
            "Archivist model",
            default=existing.get("ARCHIVIST_MODEL", default_model),
        )
        values["ARCHIVIST_MODEL"] = _clean_prompt_value(values["ARCHIVIST_MODEL"])

    values["PADDLEOCR_API_TOKEN"] = _prompt_secret(
        "PaddleOCR API key",
        current=values.get("PADDLEOCR_API_TOKEN", ""),
        required=True,
    )
    values["PADDLEOCR_API_URL"] = _DEFAULT_ENV["PADDLEOCR_API_URL"]
    values["PADDLEOCR_MODEL"] = _DEFAULT_ENV["PADDLEOCR_MODEL"]

    for key, default in _DEFAULT_ENV.items():
        values.setdefault(key, default)

    _write_env_file(env_path, values)
    _load_env_into_process(values)
    action = "Updated" if existed else "Created"
    rprint(f"\n[green]{action}[/green] {env_path}")
    rprint(f"[green]Ready[/green] {paths.materials_root(root)}")
    rprint(f"[green]Ready[/green] {paths.outputs_root(root)}")
    rprint(f"[green]Ready[/green] {paths.workspace_home(root)}")
    rprint("[dim]Use 'tre models' to view or update provider/model settings later.[/dim]")


def _prompt_secret(label: str, current: str = "", required: bool = False) -> str:
    if current:
        keep = typer.confirm(f"{label} is already set. Keep existing value?", default=True)
        if keep:
            return current
    while True:
        if required:
            value = typer.prompt(label, hide_input=True)
        else:
            value = typer.prompt(label, hide_input=True, default="")
        value = value.strip()
        if value or not required:
            return value
        rprint("[red]This value is required.[/red]")


def _prompt_visible(label: str, current: str = "", required: bool = False) -> str:
    while True:
        if current:
            value = typer.prompt(label, default=current)
        elif required:
            value = typer.prompt(label)
        else:
            value = typer.prompt(label, default="")
        value = str(value).strip()
        if value or not required:
            return value
        rprint("[red]This value is required.[/red]")


def _clean_prompt_value(value: str) -> str:
    """Remove pasted terminal styling fragments from visible setup fields."""
    return re.sub(r"(?:\x1b\[[0-9;]*m|\[[0-9;]*m\])", "", value).strip()


def _read_env_file(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    values = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        values[key.strip()] = _unquote_env_value(value.strip())
    return values


def _read_effective_env(root: Path) -> dict[str, str]:
    env: dict[str, str] = {
        key: value
        for key, value in os.environ.items()
        if key.startswith(_ROLE_NAMES)
        or key.startswith(("LLM_", "PADDLEOCR_", "SOURCE_", "EMBED_", "MAX_", "PRO_"))
    }
    env.update(_read_env_file(paths.global_config_path()))
    env.update(_read_env_file(paths.legacy_workspace_env_path(root)))
    env.update(_read_env_file(paths.workspace_config_path(root)))
    return env


def _write_env_file(path: Path, values: dict[str, str]) -> None:
    ordered_sections = [
        ("OpenAI-compatible LLM", ["LLM_API_KEY", "LLM_BASE_URL", "LLM_MODEL"]),
        (
            "Role-specific models",
            ["EXAMINER_MODEL", "STUDENT_MODEL", "WRITER_MODEL", "ARCHIVIST_MODEL"],
        ),
        (
            "PaddleOCR",
            ["PADDLEOCR_API_URL", "PADDLEOCR_API_TOKEN", "PADDLEOCR_MODEL"],
        ),
        (
            "Source ingest concurrency",
            [
                "SOURCE_INGEST_CONCURRENCY",
                "SOURCE_OCR_CONCURRENCY",
                "SOURCE_OCR_UPLOAD_INTERVAL_SEC",
                "SOURCE_ARCHIVIST_CONCURRENCY",
                "SOURCE_EMBEDDING_CONCURRENCY",
                "SOURCE_ARCHIVIST_CHUNK_CHARS",
            ],
        ),
        (
            "Local embedding server",
            [
                "EMBED_API_URL",
                "EMBED_MODEL",
                "EMBED_PORT",
                "EMBED_N_CTX",
                "EMBED_N_GPU_LAYERS",
                "EMBED_N_SEQ_MAX",
            ],
        ),
    ]
    written = set()
    lines = []
    for title, keys in ordered_sections:
        lines.append(f"# {title}")
        for key in keys:
            if key in values:
                lines.append(f"{key}={_quote_env_value(values[key])}")
                written.add(key)
        lines.append("")
    extra_keys = sorted(key for key in values if key not in written)
    if extra_keys:
        lines.append("# Additional settings")
        for key in extra_keys:
            lines.append(f"{key}={_quote_env_value(values[key])}")
        lines.append("")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def _load_env_into_process(values: dict[str, str]) -> None:
    for key, value in values.items():
        os.environ[key] = value


def _print_model_config(env: dict[str, str]) -> None:
    table = Table(title="Model / Provider Settings")
    table.add_column("Setting")
    table.add_column("Value")
    table.add_row("LLM_BASE_URL", env.get("LLM_BASE_URL", ""))
    table.add_row("LLM_MODEL", env.get("LLM_MODEL", ""))
    for role in _ROLE_NAMES:
        table.add_row(f"{role}_MODEL", env.get(f"{role}_MODEL", env.get("LLM_MODEL", "")))
    table.add_row("PADDLEOCR_MODEL", env.get("PADDLEOCR_MODEL", ""))
    table.add_row("LLM_API_KEY", _secret_state(env.get("LLM_API_KEY", "")))
    table.add_row(
        "PADDLEOCR_API_TOKEN",
        _secret_state(env.get("PADDLEOCR_API_TOKEN", "")),
    )
    rprint(table)


def _secret_state(value: str) -> str:
    if not value:
        return "[yellow]not set[/yellow]"
    return "[green]set[/green]"


def _unquote_env_value(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def _quote_env_value(value: str) -> str:
    if not value:
        return ""
    if any(char.isspace() for char in value) or "#" in value:
        escaped = value.replace('"', '\\"')
        return f'"{escaped}"'
    return value


def _embedding_health() -> tuple[bool, str]:
    from tree.services import embedding_health

    ok, detail = embedding_health(Path.cwd())
    return ok, _truncate(detail, 120)


def _python_summary() -> str:
    version = ".".join(str(part) for part in sys.version_info[:3])
    return f"{version} at {sys.executable}"


def _cli_executable_summary() -> tuple[bool, str]:
    executable = shutil.which("tre")
    if executable:
        return True, executable
    launched = Path(sys.argv[0]).name if sys.argv else ""
    if launched == "tre":
        return True, str(Path(sys.argv[0]).resolve())
    return False, "tre is not on PATH; run pipx ensurepath or reinstall"


def _package_summary() -> tuple[bool, str]:
    try:
        from importlib.metadata import PackageNotFoundError, version

        package_version = version("tree-engine")
    except PackageNotFoundError:
        package_version = "unknown"
    try:
        import tree

        package_path = Path(tree.__file__ or "").resolve()
    except Exception as exc:
        return False, f"cannot import tree package: {exc}"
    return True, f"tree-engine {package_version} at {package_path}"


def _git_status_summary(root: Path) -> tuple[bool, str]:
    try:
        result = subprocess.run(
            ["git", "status", "--short"],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        return False, str(exc)
    if result.returncode != 0:
        detail = result.stderr.strip() or "git status failed"
        if "not a git repository" in detail:
            return True, "not a Git workspace"
        return False, detail
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    if not lines:
        return True, "clean"
    return True, f"{len(lines)} changed path(s)"


def _format_bytes(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{size} B"


def _iter_project_pycache_dirs(root: Path) -> list[Path]:
    ignored = {".git", ".venv", ".tree", ".runtime", "rag-store", "node_modules"}
    matches = []
    for current_root, dirnames, _ in os.walk(root):
        dirnames[:] = [name for name in dirnames if name not in ignored]
        if Path(current_root).name == "__pycache__":
            matches.append(Path(current_root))
            dirnames[:] = []
    return matches


def _relative_to_root(root: Path, path: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def _truncate(text: str, max_chars: int) -> str:
    return text if len(text) <= max_chars else text[: max_chars - 1].rstrip() + "…"


if __name__ == "__main__":
    app()
