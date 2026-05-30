"""Configuration: per-role LLM provider settings with fallback defaults."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

from tree.io import paths


class ConfigurationError(Exception):
    pass


@dataclass(frozen=True)
class RoleConfig:
    """LLM config for one role: api_key, base_url, model."""
    api_key: str
    base_url: str
    model: str


@dataclass(frozen=True)
class Settings:
    # Per-role LLM configs
    examiner: RoleConfig
    student: RoleConfig
    writer: RoleConfig
    archivist: RoleConfig

    # PaddleOCR
    paddleocr_api_url: str = ""
    paddleocr_api_token: str = ""
    paddleocr_model: str = "PaddleOCR-VL-1.6"

    # Pipeline
    max_iterations: int = 5
    max_retries: int = 3
    llm_timeout_sec: float = 60.0
    max_format_retries: int = 2
    source_ingest_concurrency: int = 16
    source_ocr_concurrency: int = 16
    source_ocr_upload_interval_sec: float = 5.0
    source_archivist_concurrency: int = 16
    source_embedding_concurrency: int = 1
    source_archivist_chunk_chars: int = 24000
    pro_degradation_threshold: int = 3
    pro_degradation_cooldown_sec: int = 600
    max_active_branch_runs: int = 2
    project_root: Path = field(default_factory=lambda: Path.cwd())

    @classmethod
    def from_env(cls, project_root: Path | None = None, require_llm: bool = True) -> Settings:
        root = project_root or Path.cwd()
        load_dotenv(paths.global_config_path(), override=True)
        load_dotenv(paths.legacy_workspace_env_path(root), override=True)
        load_dotenv(paths.workspace_config_path(root), override=True)

        # Default LLM config (fallback for all roles)
        default_key = os.environ.get("LLM_API_KEY", "")
        default_url = os.environ.get("LLM_BASE_URL", "https://api.openai.com/v1")
        default_model = os.environ.get("LLM_MODEL", "gpt-4o")

        if require_llm and not default_key:
            # Check if any role-specific key is set
            any_key = any(os.environ.get(f"{r}_API_KEY") for r in ("EXAMINER", "STUDENT", "WRITER", "ARCHIVIST"))
            if not any_key:
                raise ConfigurationError(
                    "No LLM_API_KEY or role-specific API key found. "
                    "Set LLM_API_KEY (default) or EXAMINER_API_KEY/STUDENT_API_KEY/WRITER_API_KEY/ARCHIVIST_API_KEY in TREE config"
                )

        examiner = _role_config("EXAMINER", default_key, default_url, default_model)
        student = _role_config("STUDENT", default_key, default_url, default_model)
        writer = _role_config("WRITER", default_key, default_url, default_model)
        archivist = _role_config("ARCHIVIST", default_key, default_url, default_model)

        return cls(
            examiner=examiner,
            student=student,
            writer=writer,
            archivist=archivist,
            paddleocr_api_url=os.environ.get("PADDLEOCR_API_URL", ""),
            paddleocr_api_token=os.environ.get("PADDLEOCR_API_TOKEN", ""),
            paddleocr_model=os.environ.get("PADDLEOCR_MODEL", "PaddleOCR-VL-1.6"),
            max_iterations=_env_int("MAX_ITERATIONS", 5),
            max_retries=_env_int("MAX_RETRIES", 3),
            llm_timeout_sec=_env_float("LLM_TIMEOUT_SEC", 60.0),
            max_format_retries=_env_int("MAX_FORMAT_RETRIES", 2),
            source_ingest_concurrency=_env_int("SOURCE_INGEST_CONCURRENCY", 16),
            source_ocr_concurrency=_env_int("SOURCE_OCR_CONCURRENCY", 16),
            source_ocr_upload_interval_sec=_env_float("SOURCE_OCR_UPLOAD_INTERVAL_SEC", 5.0),
            source_archivist_concurrency=_env_int("SOURCE_ARCHIVIST_CONCURRENCY", 16),
            source_embedding_concurrency=_env_int("SOURCE_EMBEDDING_CONCURRENCY", 1),
            source_archivist_chunk_chars=_env_int("SOURCE_ARCHIVIST_CHUNK_CHARS", 24000),
            pro_degradation_threshold=_env_int("PRO_DEGRADATION_THRESHOLD", 3),
            pro_degradation_cooldown_sec=_env_int("PRO_DEGRADATION_COOLDOWN_SEC", 600),
            max_active_branch_runs=_env_int("MAX_ACTIVE_BRANCH_RUNS", 2),
            project_root=root,
        )


def _role_config(
    role: str,
    default_key: str,
    default_url: str,
    default_model: str,
) -> RoleConfig:
    """Build RoleConfig with role-specific overrides."""
    return RoleConfig(
        api_key=os.environ.get(f"{role}_API_KEY", default_key),
        base_url=os.environ.get(f"{role}_BASE_URL", default_url),
        model=os.environ.get(f"{role}_MODEL", default_model),
    )


def _env_int(name: str, default: int) -> int:
    value = os.environ.get(name)
    if not value:
        return default
    value = value.split("#", 1)[0].strip()
    return int(value) if value else default


def _env_float(name: str, default: float) -> float:
    value = os.environ.get(name)
    if not value:
        return default
    value = value.split("#", 1)[0].strip()
    return float(value) if value else default
