"""Config command helpers: setup / models / prompts."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from tree.config import ROLES
from tree.io import paths


def write_workspace_config(
    root: Path,
    *,
    llm_api_key: str = "",
    llm_base_url: str = "",
    llm_model: str = "",
    paddleocr_api_token: str = "",
    paddleocr_api_url: str = "",
) -> Path:
    paths.ensure_workspace_dirs(root)
    config_path = paths.workspace_config_path(root)
    values = {
        "LLM_API_KEY": llm_api_key,
        "LLM_BASE_URL": llm_base_url,
        "LLM_MODEL": llm_model,
        "PADDLEOCR_API_TOKEN": paddleocr_api_token,
        "PADDLEOCR_API_URL": paddleocr_api_url,
    }
    lines = [f"{key}={value}" for key, value in values.items() if value]
    config_path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    return config_path


def models_text(settings: Any) -> str:
    rows = []
    for role in ROLES:
        config = settings.role(role) if hasattr(settings, "role") else getattr(settings, role)
        rows.append(f"{role}: {config.model} @ {config.base_url}")
    return "\n".join(rows)


def prompts_text() -> str:
    return "\n".join(ROLES)
