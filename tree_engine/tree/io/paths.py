"""Workspace path helpers for the current runtime layout."""

from __future__ import annotations

import os
from pathlib import Path


# --- user-level global -------------------------------------------------------

def app_home() -> Path:
    """Per-user TREE home for global config and the shared embedding service."""
    override = os.environ.get("TREE_HOME")
    return Path(override).expanduser() if override else Path.home() / ".tree"


def global_config_path() -> Path:
    return app_home() / "config.env"


def global_services_root() -> Path:
    return app_home() / "services"


def llama_server_cache_root() -> Path:
    """Per-user cache for TREE-managed prebuilt llama.cpp ``llama-server`` binaries."""
    override = os.environ.get("LLAMA_SERVER_CACHE_DIR")
    if override:
        return Path(override).expanduser()
    return app_home() / "bin"


# --- workspace ---------------------------------------------------------------

def workspace_home(root: Path) -> Path:
    return root / ".tree"


def workspace_config_path(root: Path) -> Path:
    return workspace_home(root) / "config.env"


def prompts_root(root: Path) -> Path:
    return workspace_home(root) / "prompts"


def prompt_overrides_path(root: Path) -> Path:
    return prompts_root(root) / "overrides.json"


def legacy_workspace_env_path(root: Path) -> Path:
    return root / ".env"


def runtime_root(root: Path) -> Path:
    return workspace_home(root) / "runtime"


def materials_root(root: Path) -> Path:
    return root / "materials"


def outputs_root(root: Path) -> Path:
    return root / "outputs"


def outputs_dag_svg_path(root: Path) -> Path:
    return outputs_root(root) / "knowledge-dag.svg"


# --- runtime artifacts -------------------------------------------------------

def source_markdown_root(root: Path) -> Path:
    """Cleaned intermediate Markdown; deleted after embedding."""
    return runtime_root(root) / "source"


def ocr_markdown_root(root: Path) -> Path:
    """Raw OCR Markdown checkpoints; retained for inspection and retries."""
    return runtime_root(root) / "ocr"


def ocr_jobs_root(root: Path) -> Path:
    """Durable remote OCR job and per-chunk result checkpoints."""
    return runtime_root(root) / "ocr-jobs"


def ocr_markdown_path(root: Path, collection: str, source_file: str) -> Path:
    return ocr_markdown_root(root) / collection / f"{source_file}.md"


def ocr_markdown_source_path(root: Path, source_id: str) -> Path:
    """OCR checkpoint keyed by the full workspace-relative material path."""
    return _safe_source_artifact_path(ocr_markdown_root(root), source_id)


def source_markdown_source_path(root: Path, source_id: str) -> Path:
    """Cleaned Markdown keyed by full source identity (including chunk suffix)."""
    return _safe_source_artifact_path(source_markdown_root(root), source_id)


def _safe_source_artifact_path(base: Path, source_id: str) -> Path:
    rel = Path(source_id.replace("\\", "/"))
    if rel.is_absolute() or not rel.parts or any(part in {"", ".", ".."} for part in rel.parts):
        raise ValueError(f"Invalid material source id: {source_id!r}")
    return base.joinpath(*rel.parts).with_name(rel.name + ".md")


def drafts_root(root: Path) -> Path:
    return runtime_root(root) / "drafts"


def rag_store_path(root: Path) -> Path:
    return runtime_root(root) / "rag-store"


def pipeline_temp_root(root: Path) -> Path:
    return runtime_root(root) / "pipeline-temp"


def pipeline_state_path(root: Path) -> Path:
    return runtime_root(root) / "pipeline-state.json"


def progress_path(root: Path) -> Path:
    return runtime_root(root) / "progress.json"


def knowledge_ledger_path(root: Path) -> Path:
    return runtime_root(root) / "knowledge-ledger.json"


def output_transactions_root(root: Path) -> Path:
    return runtime_root(root) / "output-transactions"


def output_archive_root(root: Path) -> Path:
    return runtime_root(root) / "output-archive"


def learning_state_path(root: Path) -> Path:
    return runtime_root(root) / "learning-state.json"


def learning_revisions_root(root: Path) -> Path:
    return runtime_root(root) / "learning-revisions"


def import_manifest_path(root: Path) -> Path:
    """UI/product import history for files copied into materials/."""
    return runtime_root(root) / "import-manifest.json"


# --- planner artifacts (all under runtime/planner/) --------------------------

def planner_root(root: Path) -> Path:
    return runtime_root(root) / "planner"


def material_manifest_path(root: Path) -> Path:
    return planner_root(root) / "material-manifest.json"


def mtus_path(root: Path) -> Path:
    return planner_root(root) / "mtus.json"


def knowledge_nodes_path(root: Path) -> Path:
    return planner_root(root) / "knowledge-nodes.json"


def knowledge_dag_path(root: Path) -> Path:
    return planner_root(root) / "knowledge-dag.json"


def knowledge_dag_svg_path(root: Path) -> Path:
    return planner_root(root) / "knowledge-dag.svg"


# --- services ----------------------------------------------------------------

def services_root(root: Path) -> Path:
    return runtime_root(root) / "services"


def service_root(root: Path, name: str) -> Path:
    # The embedding server is global and shared across workspaces.
    if name == "embedding":
        return global_services_root()
    return services_root(root)


def service_pid_path(root: Path, name: str) -> Path:
    return service_root(root, name) / f"{name}.pid"


def service_log_path(root: Path, name: str) -> Path:
    return service_root(root, name) / f"{name}.log"


def service_stop_path(root: Path, name: str) -> Path:
    return service_root(root, name) / f"{name}.stop"


def ensure_workspace_dirs(root: Path) -> None:
    for path in (
        materials_root(root),
        outputs_root(root),
        runtime_root(root),
        prompts_root(root),
        ocr_markdown_root(root),
        ocr_jobs_root(root),
        source_markdown_root(root),
        drafts_root(root),
        output_transactions_root(root),
        output_archive_root(root),
        learning_revisions_root(root),
        planner_root(root),
        pipeline_temp_root(root),
        services_root(root),
    ):
        path.mkdir(parents=True, exist_ok=True)
    _ensure_workspace_gitignore(root)


def _ensure_workspace_gitignore(root: Path) -> None:
    """Keep workspace config (API keys) and runtime state out of any enclosing git repo."""
    gitignore = workspace_home(root) / ".gitignore"
    if not gitignore.exists():
        gitignore.write_text("*\n", encoding="utf-8")
