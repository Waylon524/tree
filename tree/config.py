"""Configuration: per-role LLM provider settings with fallback defaults."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv


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
    paddleocr_model: str = "PaddleOCR-VL-1.5"

    # Pipeline
    max_iterations: int = 5
    max_retries: int = 3
    max_format_retries: int = 2
    pro_degradation_threshold: int = 3
    pro_degradation_cooldown_sec: int = 600
    project_root: Path = field(default_factory=lambda: Path.cwd())

    @classmethod
    def from_env(cls, project_root: Path | None = None, require_llm: bool = True) -> Settings:
        root = project_root or Path.cwd()
        load_dotenv(root / ".env", override=True)

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
                    "Set LLM_API_KEY (default) or EXAMINER_API_KEY/STUDENT_API_KEY/WRITER_API_KEY/ARCHIVIST_API_KEY in .env"
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
            paddleocr_model=os.environ.get("PADDLEOCR_MODEL", "PaddleOCR-VL-1.5"),
            max_iterations=_env_int("MAX_ITERATIONS", 5),
            max_retries=_env_int("MAX_RETRIES", 3),
            max_format_retries=_env_int("MAX_FORMAT_RETRIES", 2),
            pro_degradation_threshold=_env_int("PRO_DEGRADATION_THRESHOLD", 3),
            pro_degradation_cooldown_sec=_env_int("PRO_DEGRADATION_COOLDOWN_SEC", 600),
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
