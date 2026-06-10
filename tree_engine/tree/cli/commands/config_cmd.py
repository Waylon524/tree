"""Config command helpers: setup / models / prompts."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import typer
from dotenv import dotenv_values
from rich import print as rprint

from tree.cli import theme
from tree.config import ROLES
from tree.io import paths

_DEFAULT_ENV = {
    "LLM_BASE_URL": "https://api.deepseek.com",
    "LLM_MODEL": "deepseek-v4-flash",
    "PADDLEOCR_API_URL": "https://paddleocr.aistudio-app.com/api/v2/ocr/jobs",
    "PADDLEOCR_MODEL": "PaddleOCR-VL-1.6",
}

_WRITE_ORDER = (
    "LLM_API_KEY",
    "LLM_BASE_URL",
    "LLM_MODEL",
    "EXAMINER_MODEL",
    "STUDENT_MODEL",
    "WRITER_MODEL",
    "ARCHIVIST_MODEL",
    "DAGGER_MODEL",
    "PADDLEOCR_API_URL",
    "PADDLEOCR_API_TOKEN",
    "PADDLEOCR_MODEL",
)


def write_quick_config(
    root: Path,
    *,
    env_path: Path | None = None,
    llm_api_key: str = "",
    llm_base_url: str = "",
    llm_model: str = "",
    paddleocr_api_token: str = "",
    paddleocr_api_url: str = "",
) -> Path:
    """Write non-interactive setup values while preserving existing keys."""
    paths.ensure_workspace_dirs(root)
    config_path = env_path or paths.workspace_config_path(root)
    existing = read_env_file(config_path)
    updates = {
        "LLM_API_KEY": llm_api_key,
        "LLM_BASE_URL": llm_base_url,
        "LLM_MODEL": llm_model,
        "PADDLEOCR_API_TOKEN": paddleocr_api_token,
        "PADDLEOCR_API_URL": paddleocr_api_url,
    }
    existing.update({key: _clean_prompt_value(value) for key, value in updates.items() if value})
    if existing.get("PADDLEOCR_API_TOKEN"):
        existing.setdefault("PADDLEOCR_API_URL", _DEFAULT_ENV["PADDLEOCR_API_URL"])
        existing.setdefault("PADDLEOCR_MODEL", _DEFAULT_ENV["PADDLEOCR_MODEL"])
    write_env_file(config_path, existing)
    return config_path


def run_setup_wizard(root: Path, *, env_path: Path, scope: str) -> Path:
    """Run the interactive setup wizard and write ``env_path``."""
    paths.ensure_workspace_dirs(root)
    env_path.parent.mkdir(parents=True, exist_ok=True)

    existing = read_env_file(env_path)
    values = {**_DEFAULT_ENV, **read_effective_env(root), **existing}

    rprint(f"{theme.brand('T.R.E.E.')} {theme.section(scope + ' setup')}")
    rprint(f"[dim]Secrets are written to {theme.path(env_path)}.[/dim]\n")

    values["LLM_API_KEY"] = _prompt_secret(
        "Shared LLM / agent API key",
        current=values.get("LLM_API_KEY", ""),
        required=True,
    )
    values["LLM_BASE_URL"] = _clean_prompt_value(
        _prompt_visible("LLM base URL", current=values.get("LLM_BASE_URL", ""), required=True)
    )
    values["LLM_MODEL"] = _clean_prompt_value(
        _prompt_visible("Default LLM model", current=values.get("LLM_MODEL", ""), required=True)
    )

    default_model = values["LLM_MODEL"]
    for role in ROLES:
        key = f"{role.upper()}_MODEL"
        values[key] = _clean_prompt_value(
            typer.prompt(
                f"{role.title()} model",
                default=existing.get(key) or values.get(key) or default_model,
            )
        )

    values["PADDLEOCR_API_TOKEN"] = _prompt_secret(
        "PaddleOCR API key",
        current=values.get("PADDLEOCR_API_TOKEN", ""),
        required=True,
    )
    values["PADDLEOCR_API_URL"] = _clean_prompt_value(
        values.get("PADDLEOCR_API_URL") or _DEFAULT_ENV["PADDLEOCR_API_URL"]
    )
    values["PADDLEOCR_MODEL"] = _DEFAULT_ENV["PADDLEOCR_MODEL"]

    write_env_file(env_path, values)
    rprint(f"\n{theme.success('Wrote')} {theme.path(env_path)}")
    rprint(f"{theme.success('Ready')} {theme.path(paths.materials_root(root))}")
    rprint(f"{theme.success('Ready')} {theme.path(paths.outputs_root(root))}")
    rprint(f"{theme.success('Ready')} {theme.path(paths.workspace_home(root))}")
    return env_path


def read_env_file(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    return {key: value for key, value in dotenv_values(path).items() if value is not None}


def read_effective_env(root: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for path in (
        paths.global_config_path(),
        paths.legacy_workspace_env_path(root),
        paths.workspace_config_path(root),
    ):
        values.update(read_env_file(path))
    return values


def write_env_file(path: Path, values: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    keys = [key for key in _WRITE_ORDER if values.get(key)]
    keys.extend(sorted(key for key, value in values.items() if value and key not in set(keys)))
    lines = [f"{key}={values[key]}" for key in keys]
    # Contains API keys: keep it owner-only. No-op on filesystems without POSIX modes.
    path.touch(mode=0o600, exist_ok=True)
    path.chmod(0o600)
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def models_text(settings: Any) -> str:
    rows = []
    for role in ROLES:
        config = settings.role(role) if hasattr(settings, "role") else getattr(settings, role)
        rows.append(f"{role}: {config.model} @ {config.base_url}")
    return "\n".join(rows)


def prompts_text() -> str:
    return "\n".join(ROLES)


def _prompt_secret(label: str, *, current: str = "", required: bool = False) -> str:
    if current:
        keep = typer.confirm(f"{label} is already set. Keep existing value?", default=True)
        if keep:
            return current
    while True:
        value = typer.prompt(label, hide_input=True)
        value = str(value).strip()
        if value or not required:
            return value
        rprint("[red]This value is required.[/red]")


def _prompt_visible(label: str, *, current: str = "", required: bool = False) -> str:
    while True:
        value = typer.prompt(label, default=current) if current else typer.prompt(label)
        value = str(value).strip()
        if value or not required:
            return value
        rprint("[red]This value is required.[/red]")


def _clean_prompt_value(value: str) -> str:
    return re.sub(r"(?:\x1b\[[0-9;]*m|\[[0-9;]*m\])", "", str(value)).strip()
