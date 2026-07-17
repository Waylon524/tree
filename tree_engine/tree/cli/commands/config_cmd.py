"""Config command helpers: setup / models / prompts."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import typer
from dotenv import dotenv_values
from rich import print as rprint

from tree.cli import theme
from tree.config import (
    DEFAULT_EMBED_REQUEST_TIMEOUT_SEC,
    DEFAULT_LLAMA_SERVER_CTX,
    DEFAULT_LLM_CONTEXT_WINDOW,
    DEFAULT_LLM_MAX_OUTPUT_TOKENS,
    DEFAULT_LLM_PROMPT_SAFETY_TOKENS,
    DEFAULT_SOURCE_MTU_CHUNK_TOKENS,
    LLAMA_SERVER_CTX_MAX,
    LLAMA_SERVER_CTX_MIN,
    ROLES,
    SOURCE_MTU_CHUNK_TOKENS_MAX,
    SOURCE_MTU_CHUNK_TOKENS_MIN,
)
from tree.agents.prompts import EDITABLE_PROMPT_KEYS
from tree.io import paths

_DEFAULT_ENV = {
    "LLM_BASE_URL": "https://api.deepseek.com",
    "LLM_MODEL": "deepseek-v4-flash",
    "LLM_PROVIDER_PROFILE": "auto",
    "LLM_CONTEXT_WINDOW": str(DEFAULT_LLM_CONTEXT_WINDOW),
    "LLM_MAX_OUTPUT_TOKENS": str(DEFAULT_LLM_MAX_OUTPUT_TOKENS),
    "LLM_PROMPT_SAFETY_TOKENS": str(DEFAULT_LLM_PROMPT_SAFETY_TOKENS),
    "PADDLEOCR_API_URL": "https://paddleocr.aistudio-app.com/api/v2/ocr/jobs",
    "PADDLEOCR_MODEL": "PaddleOCR-VL-1.6",
    "LLAMA_SERVER_CTX": str(DEFAULT_LLAMA_SERVER_CTX),
    "SOURCE_MTU_CHUNK_TOKENS": str(DEFAULT_SOURCE_MTU_CHUNK_TOKENS),
    "NODE_RUN_MODE": "standard",
    "MAX_ITERATIONS": "5",
    "MAX_ACTIVE_NODE_RUNS": "3",
    "MAX_EXAMINER_SPAN_NODES": "3",
    "MAX_RETRIES": "3",
    "MAX_FORMAT_RETRIES": "2",
    "LLM_TIMEOUT_SEC": "480",
    "LLM_PROVIDER_CONCURRENCY": "4",
    "PRO_DEGRADATION_THRESHOLD": "3",
    "PRO_DEGRADATION_COOLDOWN_SEC": "600",
    "SOURCE_INGEST_CONCURRENCY": "4",
    "SOURCE_OCR_CONCURRENCY": "5",
    "SOURCE_OCR_PDF_MAX_PAGES_PER_JOB": "99",
    "SOURCE_OCR_UPLOAD_INTERVAL_SEC": "5.0",
    "SOURCE_EMBEDDING_CONCURRENCY": "1",
    "EMBED_REQUEST_TIMEOUT_SEC": str(int(DEFAULT_EMBED_REQUEST_TIMEOUT_SEC)),
    "ARCHIVIST_MTU_CUT_TIMEOUT_SEC": "480",
    "ARCHIVIST_MTU_REPAIR_ATTEMPTS": "8",
    "ARCHIVIST_CHUNK_CONCURRENCY": "2",
    "DAGGER_BUILD_TIMEOUT_SEC": "480",
    "DAGGER_REPAIR_ATTEMPTS": "3",
    "DAGGER_PREREQUISITE_CONCURRENCY": "3",
    "DAGGER_MAX_NODES_PER_CALL": "400",
    "DAGGER_EMBED_CLUSTER_ENABLED": "true",
    "DAGGER_CLUSTER_SIMILARITY_THRESHOLD": "0.80",
    "DAGGER_CLUSTER_TOP_K": "5",
    "DAGGER_CLUSTER_MAX_SIZE": "8",
    "DAGGER_CLUSTER_AUTO_ACCEPT_SINGLETON": "true",
    "DAGGER_CLUSTER_AUTO_ACCEPT_SAME_COLLECTION": "false",
}

_WRITE_ORDER = (
    "LLM_API_KEY",
    "LLM_BASE_URL",
    "LLM_MODEL",
    "LLM_PROVIDER_PROFILE",
    "LLM_CONTEXT_WINDOW",
    "LLM_MAX_OUTPUT_TOKENS",
    "LLM_PROMPT_SAFETY_TOKENS",
    "EXAMINER_MODEL",
    "STUDENT_MODEL",
    "WRITER_MODEL",
    "ARCHIVIST_MODEL",
    "DAGGER_MODEL",
    "PADDLEOCR_API_URL",
    "PADDLEOCR_API_TOKEN",
    "PADDLEOCR_MODEL",
    "LLAMA_SERVER_CTX",
    "SOURCE_MTU_CHUNK_TOKENS",
    "NODE_RUN_MODE",
    "MAX_ITERATIONS",
    "MAX_ACTIVE_NODE_RUNS",
    "MAX_EXAMINER_SPAN_NODES",
    "MAX_RETRIES",
    "MAX_FORMAT_RETRIES",
    "LLM_TIMEOUT_SEC",
    "LLM_PROVIDER_CONCURRENCY",
    "PRO_DEGRADATION_THRESHOLD",
    "PRO_DEGRADATION_COOLDOWN_SEC",
    "SOURCE_INGEST_CONCURRENCY",
    "SOURCE_OCR_CONCURRENCY",
    "SOURCE_OCR_PDF_MAX_PAGES_PER_JOB",
    "SOURCE_OCR_UPLOAD_INTERVAL_SEC",
    "SOURCE_EMBEDDING_CONCURRENCY",
    "EMBED_REQUEST_TIMEOUT_SEC",
    "ARCHIVIST_MTU_CUT_TIMEOUT_SEC",
    "ARCHIVIST_MTU_REPAIR_ATTEMPTS",
    "ARCHIVIST_CHUNK_CONCURRENCY",
    "DAGGER_BUILD_TIMEOUT_SEC",
    "DAGGER_REPAIR_ATTEMPTS",
    "DAGGER_PREREQUISITE_CONCURRENCY",
    "DAGGER_MAX_NODES_PER_CALL",
    "DAGGER_EMBED_CLUSTER_ENABLED",
    "DAGGER_CLUSTER_SIMILARITY_THRESHOLD",
    "DAGGER_CLUSTER_TOP_K",
    "DAGGER_CLUSTER_MAX_SIZE",
    "DAGGER_CLUSTER_AUTO_ACCEPT_SINGLETON",
    "DAGGER_CLUSTER_AUTO_ACCEPT_SAME_COLLECTION",
)

_INT_SETTINGS = {
    "max_iterations": ("MAX_ITERATIONS", 1, 50),
    "max_active_node_runs": ("MAX_ACTIVE_NODE_RUNS", 1, 32),
    "max_examiner_span_nodes": ("MAX_EXAMINER_SPAN_NODES", 1, 20),
    "max_retries": ("MAX_RETRIES", 0, 20),
    "llm_provider_concurrency": ("LLM_PROVIDER_CONCURRENCY", 1, 32),
    "llm_context_window": ("LLM_CONTEXT_WINDOW", 1_024, 2_000_000),
    "llm_max_output_tokens": ("LLM_MAX_OUTPUT_TOKENS", 1, 1_000_000),
    "llm_prompt_safety_tokens": ("LLM_PROMPT_SAFETY_TOKENS", 0, 100_000),
    "max_format_retries": ("MAX_FORMAT_RETRIES", 0, 10),
    "pro_degradation_threshold": ("PRO_DEGRADATION_THRESHOLD", 1, 20),
    "pro_degradation_cooldown_sec": ("PRO_DEGRADATION_COOLDOWN_SEC", 0, 86_400),
    "source_ingest_concurrency": ("SOURCE_INGEST_CONCURRENCY", 1, 64),
    "source_ocr_concurrency": ("SOURCE_OCR_CONCURRENCY", 1, 32),
    "source_ocr_pdf_max_pages_per_job": ("SOURCE_OCR_PDF_MAX_PAGES_PER_JOB", 1, 500),
    "source_embedding_concurrency": ("SOURCE_EMBEDDING_CONCURRENCY", 1, 16),
    "archivist_mtu_repair_attempts": ("ARCHIVIST_MTU_REPAIR_ATTEMPTS", 0, 20),
    "archivist_chunk_concurrency": ("ARCHIVIST_CHUNK_CONCURRENCY", 1, 16),
    "dagger_repair_attempts": ("DAGGER_REPAIR_ATTEMPTS", 0, 20),
    "dagger_prerequisite_concurrency": ("DAGGER_PREREQUISITE_CONCURRENCY", 1, 32),
    "dagger_max_nodes_per_call": ("DAGGER_MAX_NODES_PER_CALL", 1, 5_000),
    "dagger_cluster_top_k": ("DAGGER_CLUSTER_TOP_K", 1, 100),
    "dagger_cluster_max_size": ("DAGGER_CLUSTER_MAX_SIZE", 1, 100),
}

_FLOAT_SETTINGS = {
    "llm_timeout_sec": ("LLM_TIMEOUT_SEC", 10.0, 3_600.0),
    "embed_request_timeout_sec": ("EMBED_REQUEST_TIMEOUT_SEC", 10.0, 3_600.0),
    "source_ocr_upload_interval_sec": ("SOURCE_OCR_UPLOAD_INTERVAL_SEC", 0.0, 120.0),
    "archivist_mtu_cut_timeout_sec": ("ARCHIVIST_MTU_CUT_TIMEOUT_SEC", 10.0, 3_600.0),
    "dagger_build_timeout_sec": ("DAGGER_BUILD_TIMEOUT_SEC", 10.0, 3_600.0),
    "dagger_cluster_similarity_threshold": ("DAGGER_CLUSTER_SIMILARITY_THRESHOLD", 0.0, 1.0),
}

_BOOL_SETTINGS = {
    "dagger_embed_cluster_enabled": "DAGGER_EMBED_CLUSTER_ENABLED",
    "dagger_cluster_auto_accept_singleton": "DAGGER_CLUSTER_AUTO_ACCEPT_SINGLETON",
    "dagger_cluster_auto_accept_same_collection": "DAGGER_CLUSTER_AUTO_ACCEPT_SAME_COLLECTION",
}


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
    return write_settings_config(
        root,
        env_path=env_path or paths.workspace_config_path(root),
        allow_paddleocr_endpoint_override=True,
        settings={
            "llm_api_key": llm_api_key,
            "llm_base_url": llm_base_url,
            "llm_model": llm_model,
            "paddleocr_api_token": paddleocr_api_token,
            "paddleocr_api_url": paddleocr_api_url,
        },
    )


def read_settings_config(root: Path, *, env_path: Path | None = None) -> dict[str, Any]:
    """Return editable settings without exposing secret values."""
    config_path = env_path or paths.global_config_path()
    existing = read_env_file(config_path)
    values = {**_DEFAULT_ENV, **existing}
    default_model = values.get("LLM_MODEL") or _DEFAULT_ENV["LLM_MODEL"]
    role_models = {
        role: values.get(f"{role.upper()}_MODEL") or default_model for role in ROLES
    }
    result = {
        "config_path": str(config_path),
        "llm_api_key_configured": bool(existing.get("LLM_API_KEY")),
        "llm_base_url": values.get("LLM_BASE_URL", _DEFAULT_ENV["LLM_BASE_URL"]),
        "llm_model": default_model,
        "llm_provider_profile": values.get("LLM_PROVIDER_PROFILE", "auto"),
        "role_models": role_models,
        "paddleocr_api_token_configured": bool(existing.get("PADDLEOCR_API_TOKEN")),
        "paddleocr_api_url": values.get("PADDLEOCR_API_URL", _DEFAULT_ENV["PADDLEOCR_API_URL"]),
        "paddleocr_model": values.get("PADDLEOCR_MODEL", _DEFAULT_ENV["PADDLEOCR_MODEL"]),
        "llama_server_ctx": _config_int(
            values, "LLAMA_SERVER_CTX", DEFAULT_LLAMA_SERVER_CTX
        ),
        "source_mtu_chunk_tokens": _config_int(
            values, "SOURCE_MTU_CHUNK_TOKENS", DEFAULT_SOURCE_MTU_CHUNK_TOKENS
        ),
        "node_run_mode": values.get("NODE_RUN_MODE", "standard"),
    }
    for field, (key, _minimum, _maximum) in _INT_SETTINGS.items():
        result[field] = _config_int(values, key, int(_DEFAULT_ENV[key]))
    for field, (key, _minimum, _maximum) in _FLOAT_SETTINGS.items():
        result[field] = _config_float(values, key, float(_DEFAULT_ENV[key]))
    for field, key in _BOOL_SETTINGS.items():
        result[field] = _config_bool(values, key, _DEFAULT_ENV[key].lower() == "true")
    result["invalidated_stages"] = []
    return result


def write_settings_config(
    root: Path,
    *,
    env_path: Path | None = None,
    settings: dict[str, Any],
    allow_paddleocr_endpoint_override: bool = False,
) -> Path:
    """Write editable settings while keeping blank secret fields unchanged."""
    paths.ensure_workspace_dirs(root)
    config_path = env_path or paths.global_config_path()
    existing = read_env_file(config_path)
    updates = {
        "llm_api_key": "LLM_API_KEY",
        "llm_base_url": "LLM_BASE_URL",
        "llm_model": "LLM_MODEL",
        "llm_provider_profile": "LLM_PROVIDER_PROFILE",
        "paddleocr_api_token": "PADDLEOCR_API_TOKEN",
        "paddleocr_api_url": "PADDLEOCR_API_URL",
        "paddleocr_model": "PADDLEOCR_MODEL",
    }
    for field, key in updates.items():
        value = _settings_str(settings.get(field))
        if value:
            existing[key] = value

    profile = existing.get("LLM_PROVIDER_PROFILE", "auto").lower()
    if profile not in {"auto", "deepseek", "openai", "generic"}:
        raise ValueError("llm_provider_profile must be auto, deepseek, openai, or generic.")

    node_run_mode = _settings_str(settings.get("node_run_mode"))
    if node_run_mode:
        node_run_mode = node_run_mode.lower()
        if node_run_mode not in {"standard", "fast"}:
            raise ValueError("node_run_mode must be standard or fast.")
        existing["NODE_RUN_MODE"] = node_run_mode

    role_models = settings.get("role_models")
    if isinstance(role_models, dict):
        for role in ROLES:
            value = _settings_str(role_models.get(role))
            if value:
                existing[f"{role.upper()}_MODEL"] = value

    numeric_updates = {
        "llama_server_ctx": (
            "LLAMA_SERVER_CTX",
            LLAMA_SERVER_CTX_MIN,
            LLAMA_SERVER_CTX_MAX,
        ),
        "source_mtu_chunk_tokens": (
            "SOURCE_MTU_CHUNK_TOKENS",
            SOURCE_MTU_CHUNK_TOKENS_MIN,
            SOURCE_MTU_CHUNK_TOKENS_MAX,
        ),
    }
    for field, (key, minimum, maximum) in numeric_updates.items():
        value = _settings_int(
            settings.get(field),
            field=field,
            minimum=minimum,
            maximum=maximum,
        )
        if value is not None:
            existing[key] = str(value)

    for field, (key, minimum, maximum) in _INT_SETTINGS.items():
        value = _settings_int(
            settings.get(field),
            field=field,
            minimum=minimum,
            maximum=maximum,
        )
        if value is not None:
            existing[key] = str(value)

    for field, (key, minimum, maximum) in _FLOAT_SETTINGS.items():
        value = _settings_float(
            settings.get(field),
            field=field,
            minimum=minimum,
            maximum=maximum,
        )
        if value is not None:
            existing[key] = _format_float(value)

    for field, key in _BOOL_SETTINGS.items():
        value = _settings_bool(settings.get(field), field=field)
        if value is not None:
            existing[key] = "true" if value else "false"

    context_window = _config_int(
        existing, "LLM_CONTEXT_WINDOW", DEFAULT_LLM_CONTEXT_WINDOW
    )
    max_output_tokens = _config_int(
        existing, "LLM_MAX_OUTPUT_TOKENS", DEFAULT_LLM_MAX_OUTPUT_TOKENS
    )
    safety_tokens = _config_int(
        existing, "LLM_PROMPT_SAFETY_TOKENS", DEFAULT_LLM_PROMPT_SAFETY_TOKENS
    )
    if max_output_tokens + safety_tokens >= context_window:
        raise ValueError(
            "llm_max_output_tokens + llm_prompt_safety_tokens must be smaller than "
            "llm_context_window."
        )

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
    return "\n".join(EDITABLE_PROMPT_KEYS)


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


def _settings_str(value: Any) -> str:
    return _clean_prompt_value(value) if isinstance(value, str) else ""


def _settings_int(
    value: Any,
    *,
    field: str,
    minimum: int,
    maximum: int,
) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise ValueError(f"{field} must be an integer between {minimum} and {maximum}.")
    if isinstance(value, int):
        parsed = value
    elif isinstance(value, str):
        text = _clean_prompt_value(value)
        if not text:
            return None
        try:
            parsed = int(text)
        except ValueError as exc:
            raise ValueError(
                f"{field} must be an integer between {minimum} and {maximum}."
            ) from exc
    else:
        raise ValueError(f"{field} must be an integer between {minimum} and {maximum}.")
    if parsed < minimum or parsed > maximum:
        raise ValueError(f"{field} must be an integer between {minimum} and {maximum}.")
    return parsed


def _settings_float(
    value: Any,
    *,
    field: str,
    minimum: float,
    maximum: float,
) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise ValueError(f"{field} must be a number between {minimum:g} and {maximum:g}.")
    if isinstance(value, int | float):
        parsed = float(value)
    elif isinstance(value, str):
        text = _clean_prompt_value(value)
        if not text:
            return None
        try:
            parsed = float(text)
        except ValueError as exc:
            raise ValueError(
                f"{field} must be a number between {minimum:g} and {maximum:g}."
            ) from exc
    else:
        raise ValueError(f"{field} must be a number between {minimum:g} and {maximum:g}.")
    if parsed < minimum or parsed > maximum:
        raise ValueError(f"{field} must be a number between {minimum:g} and {maximum:g}.")
    return parsed


def _settings_bool(value: Any, *, field: str) -> bool | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        text = _clean_prompt_value(value).lower()
        if not text:
            return None
        if text in {"1", "true", "yes", "on"}:
            return True
        if text in {"0", "false", "no", "off"}:
            return False
    raise ValueError(f"{field} must be true or false.")


def _config_int(values: dict[str, str], key: str, default: int) -> int:
    try:
        return int(str(values.get(key, default)).strip())
    except ValueError:
        return default


def _config_float(values: dict[str, str], key: str, default: float) -> float:
    try:
        return float(str(values.get(key, default)).strip())
    except ValueError:
        return default


def _config_bool(values: dict[str, str], key: str, default: bool) -> bool:
    value = str(values.get(key, str(default))).strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    return default


def _format_float(value: float) -> str:
    return f"{value:g}"
