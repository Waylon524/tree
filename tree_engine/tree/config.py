"""Configuration: per-role LLM provider settings + pipeline knobs.

Roles: examiner, student, writer, archivist, dagger.
Load order: ~/.tree/config.env  ->  ./.env  ->  ./.tree/config.env
Blank values never override an already-set key.
Workspace files (./.env, ./.tree/config.env) may only override *_BASE_URL /
PADDLEOCR_API_URL when the same file also sets the matching API key/token.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import dotenv_values

from tree.io import paths

logger = logging.getLogger(__name__)

ROLES = ("examiner", "student", "writer", "archivist", "dagger")

DEFAULT_LLAMA_SERVER_CTX = 22_000
DEFAULT_SOURCE_MTU_CHUNK_TOKENS = 20_000
DEFAULT_EMBED_REQUEST_TIMEOUT_SEC = 300.0
DEFAULT_LLM_CONTEXT_WINDOW = 1_000_000
DEFAULT_LLM_MAX_OUTPUT_TOKENS = 131_072
DEFAULT_LLM_PROMPT_SAFETY_TOKENS = 1_024
LLAMA_SERVER_CTX_MIN = 1_024
LLAMA_SERVER_CTX_MAX = 32_768
SOURCE_MTU_CHUNK_TOKENS_MIN = 500
SOURCE_MTU_CHUNK_TOKENS_MAX = 32_768
NODE_RUN_MODES = ("standard", "fast")

# Endpoint keys a workspace-level (untrusted, cwd-controlled) env file may only set
# when the same file also provides the credential that would be sent to that endpoint.
# Otherwise a malicious workspace could redirect globally-configured API keys.
_ENDPOINT_CREDENTIAL_KEYS: dict[str, tuple[str, ...]] = {
    "LLM_BASE_URL": ("LLM_API_KEY",),
    "PADDLEOCR_API_URL": ("PADDLEOCR_API_TOKEN",),
    **{f"{role.upper()}_BASE_URL": (f"{role.upper()}_API_KEY", "LLM_API_KEY") for role in ROLES},
}


class ConfigurationError(Exception):
    pass


@dataclass(frozen=True)
class RoleConfig:
    api_key: str
    base_url: str
    model: str
    provider_profile: str = "auto"
    context_window: int = DEFAULT_LLM_CONTEXT_WINDOW
    max_output_tokens: int = DEFAULT_LLM_MAX_OUTPUT_TOKENS


@dataclass(frozen=True)
class Settings:
    # Per-role LLM configs
    examiner: RoleConfig
    student: RoleConfig
    writer: RoleConfig
    archivist: RoleConfig
    dagger: RoleConfig

    # PaddleOCR (interface unchanged)
    paddleocr_api_url: str = ""
    paddleocr_api_token: str = ""
    paddleocr_model: str = "PaddleOCR-VL-1.6"

    # LLM behaviour
    max_iterations: int = 5
    max_retries: int = 3
    max_format_retries: int = 2
    llm_timeout_sec: float = 480.0
    llm_provider_concurrency: int = 4
    llm_prompt_safety_tokens: int = DEFAULT_LLM_PROMPT_SAFETY_TOKENS
    pro_degradation_threshold: int = 3
    pro_degradation_cooldown_sec: int = 600

    # Source ingest / OCR
    source_ingest_concurrency: int = 4
    source_ocr_concurrency: int = 5
    source_ocr_pdf_max_pages_per_job: int = 99
    source_ocr_upload_interval_sec: float = 5.0
    source_embedding_concurrency: int = 1
    source_mtu_chunk_tokens: int = DEFAULT_SOURCE_MTU_CHUNK_TOKENS
    embed_request_timeout_sec: float = DEFAULT_EMBED_REQUEST_TIMEOUT_SEC

    # Archivist (MTU cutting)
    archivist_mtu_cut_timeout_sec: float = 480.0
    archivist_mtu_repair_attempts: int = 8
    archivist_chunk_concurrency: int = 2

    # Dagger (DAG build)
    dagger_build_timeout_sec: float = 480.0
    dagger_repair_attempts: int = 3
    dagger_prerequisite_concurrency: int = 3
    dagger_max_nodes_per_call: int = 400  # above this, fall back to per-collection batching
    dagger_embed_cluster_enabled: bool = True
    dagger_cluster_similarity_threshold: float = 0.80
    dagger_cluster_top_k: int = 5
    dagger_cluster_max_size: int = 8
    dagger_cluster_auto_accept_singleton: bool = True
    dagger_cluster_auto_accept_same_collection: bool = False
    dagger_max_unassigned_ratio: float = 0.10

    # NodeRun loop
    node_run_mode: str = "standard"
    max_active_node_runs: int = 3
    max_examiner_span_nodes: int = 3

    project_root: Path = field(default_factory=Path.cwd)

    @classmethod
    def from_env(cls, project_root: Path | None = None, require_llm: bool = True) -> "Settings":
        root = project_root or Path.cwd()
        load_runtime_env(root)

        default_key = os.environ.get("LLM_API_KEY", "")
        default_url = os.environ.get("LLM_BASE_URL", "https://api.deepseek.com")
        default_model = os.environ.get("LLM_MODEL", "deepseek-v4-flash")
        default_provider_profile = os.environ.get("LLM_PROVIDER_PROFILE", "auto")
        default_context_window = _env_int("LLM_CONTEXT_WINDOW", DEFAULT_LLM_CONTEXT_WINDOW)
        default_max_output_tokens = _env_int(
            "LLM_MAX_OUTPUT_TOKENS", DEFAULT_LLM_MAX_OUTPUT_TOKENS
        )

        if require_llm and not default_key:
            any_key = any(os.environ.get(f"{r.upper()}_API_KEY") for r in ROLES)
            if not any_key:
                raise ConfigurationError(
                    "No LLM_API_KEY or role-specific API key found. Set LLM_API_KEY or "
                    "EXAMINER_API_KEY/STUDENT_API_KEY/WRITER_API_KEY/ARCHIVIST_API_KEY/DAGGER_API_KEY."
                )

        roles = {
            name: _role_config(
                name.upper(),
                default_key,
                default_url,
                default_model,
                default_provider_profile,
                default_context_window,
                default_max_output_tokens,
            )
            for name in ROLES
        }

        return cls(
            examiner=roles["examiner"],
            student=roles["student"],
            writer=roles["writer"],
            archivist=roles["archivist"],
            dagger=roles["dagger"],
            paddleocr_api_url=os.environ.get("PADDLEOCR_API_URL", ""),
            paddleocr_api_token=os.environ.get("PADDLEOCR_API_TOKEN", ""),
            paddleocr_model=os.environ.get("PADDLEOCR_MODEL", "PaddleOCR-VL-1.6"),
            max_iterations=_env_int("MAX_ITERATIONS", 5),
            max_retries=_env_int("MAX_RETRIES", 3),
            max_format_retries=_env_int("MAX_FORMAT_RETRIES", 2),
            llm_timeout_sec=_env_float("LLM_TIMEOUT_SEC", 480.0),
            llm_provider_concurrency=max(1, _env_int("LLM_PROVIDER_CONCURRENCY", 4)),
            llm_prompt_safety_tokens=max(
                0, _env_int("LLM_PROMPT_SAFETY_TOKENS", DEFAULT_LLM_PROMPT_SAFETY_TOKENS)
            ),
            pro_degradation_threshold=_env_int("PRO_DEGRADATION_THRESHOLD", 3),
            pro_degradation_cooldown_sec=_env_int("PRO_DEGRADATION_COOLDOWN_SEC", 600),
            source_ingest_concurrency=_env_int("SOURCE_INGEST_CONCURRENCY", 4),
            source_ocr_concurrency=_env_int("SOURCE_OCR_CONCURRENCY", 5),
            source_ocr_pdf_max_pages_per_job=_env_int("SOURCE_OCR_PDF_MAX_PAGES_PER_JOB", 99),
            source_ocr_upload_interval_sec=_env_float("SOURCE_OCR_UPLOAD_INTERVAL_SEC", 5.0),
            source_embedding_concurrency=_env_int("SOURCE_EMBEDDING_CONCURRENCY", 1),
            source_mtu_chunk_tokens=_env_int(
                "SOURCE_MTU_CHUNK_TOKENS", DEFAULT_SOURCE_MTU_CHUNK_TOKENS
            ),
            embed_request_timeout_sec=_env_float(
                "EMBED_REQUEST_TIMEOUT_SEC", DEFAULT_EMBED_REQUEST_TIMEOUT_SEC
            ),
            archivist_mtu_cut_timeout_sec=_env_float("ARCHIVIST_MTU_CUT_TIMEOUT_SEC", 480.0),
            archivist_mtu_repair_attempts=_env_int("ARCHIVIST_MTU_REPAIR_ATTEMPTS", 8),
            archivist_chunk_concurrency=max(1, _env_int("ARCHIVIST_CHUNK_CONCURRENCY", 2)),
            dagger_build_timeout_sec=_env_float("DAGGER_BUILD_TIMEOUT_SEC", 480.0),
            dagger_repair_attempts=_env_int("DAGGER_REPAIR_ATTEMPTS", 3),
            dagger_prerequisite_concurrency=max(1, _env_int("DAGGER_PREREQUISITE_CONCURRENCY", 3)),
            dagger_max_nodes_per_call=_env_int("DAGGER_MAX_NODES_PER_CALL", 400),
            dagger_embed_cluster_enabled=_env_bool("DAGGER_EMBED_CLUSTER_ENABLED", True),
            dagger_cluster_similarity_threshold=_env_float("DAGGER_CLUSTER_SIMILARITY_THRESHOLD", 0.80),
            dagger_cluster_top_k=_env_int("DAGGER_CLUSTER_TOP_K", 5),
            dagger_cluster_max_size=_env_int("DAGGER_CLUSTER_MAX_SIZE", 8),
            dagger_cluster_auto_accept_singleton=_env_bool("DAGGER_CLUSTER_AUTO_ACCEPT_SINGLETON", True),
            dagger_cluster_auto_accept_same_collection=_env_bool("DAGGER_CLUSTER_AUTO_ACCEPT_SAME_COLLECTION", False),
            dagger_max_unassigned_ratio=_env_float("DAGGER_MAX_UNASSIGNED_RATIO", 0.10),
            node_run_mode=_node_run_mode(os.environ.get("NODE_RUN_MODE", "standard")),
            max_active_node_runs=_env_int("MAX_ACTIVE_NODE_RUNS", 3),
            max_examiner_span_nodes=_env_int("MAX_EXAMINER_SPAN_NODES", 3),
            project_root=root,
        )

    def role(self, name: str) -> RoleConfig:
        return getattr(self, name)


def load_runtime_env(project_root: Path | None = None) -> None:
    """Load TREE runtime env files into ``os.environ`` before starting services."""
    root = project_root or Path.cwd()
    _load_env_file(paths.global_config_path())
    _load_env_file(paths.legacy_workspace_env_path(root), trusted=False)
    _load_env_file(paths.workspace_config_path(root), trusted=False)


def configured_node_run_mode(project_root: Path, *, fallback: str = "standard") -> str:
    """Read the latest effective mode without mutating the running process environment."""
    mode = _node_run_mode(fallback)
    for path in (
        paths.global_config_path(),
        paths.legacy_workspace_env_path(project_root),
        paths.workspace_config_path(project_root),
    ):
        if not path.exists():
            continue
        value = dotenv_values(path).get("NODE_RUN_MODE")
        if value:
            mode = _node_run_mode(value)
    return mode


def _role_config(
    role: str,
    default_key: str,
    default_url: str,
    default_model: str,
    default_provider_profile: str,
    default_context_window: int,
    default_max_output_tokens: int,
) -> RoleConfig:
    profile = os.environ.get(f"{role}_PROVIDER_PROFILE", default_provider_profile).strip().lower()
    if profile not in {"auto", "deepseek", "openai", "generic"}:
        raise ConfigurationError(
            f"{role}_PROVIDER_PROFILE must be auto, deepseek, openai, or generic; got {profile!r}."
        )
    context_window = _env_int(f"{role}_CONTEXT_WINDOW", default_context_window)
    max_output_tokens = _env_int(f"{role}_MAX_OUTPUT_TOKENS", default_max_output_tokens)
    if context_window < 1_024:
        raise ConfigurationError(f"{role}_CONTEXT_WINDOW must be at least 1024.")
    if max_output_tokens < 1:
        raise ConfigurationError(f"{role}_MAX_OUTPUT_TOKENS must be positive.")
    if max_output_tokens >= context_window:
        raise ConfigurationError(
            f"{role}_MAX_OUTPUT_TOKENS must be smaller than {role}_CONTEXT_WINDOW."
        )
    return RoleConfig(
        api_key=os.environ.get(f"{role}_API_KEY", default_key),
        base_url=os.environ.get(f"{role}_BASE_URL", default_url),
        model=os.environ.get(f"{role}_MODEL", default_model),
        provider_profile=profile,
        context_window=context_window,
        max_output_tokens=max_output_tokens,
    )


def _load_env_file(path: Path, *, override: bool = True, trusted: bool = True) -> None:
    if not path.exists():
        return
    values = {
        key: value for key, value in dotenv_values(path).items() if value is not None and value != ""
    }
    for key, value in values.items():
        if not trusted and key in _ENDPOINT_CREDENTIAL_KEYS:
            required = _ENDPOINT_CREDENTIAL_KEYS[key]
            if not any(values.get(credential) for credential in required):
                logger.warning(
                    "Ignoring %s from %s: workspace config may only set it together with %s "
                    "in the same file.",
                    key,
                    path,
                    " or ".join(required),
                )
                continue
        if override or key not in os.environ:
            os.environ[key] = value


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


def _env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if not value:
        return default
    return value.split("#", 1)[0].strip().lower() in {"1", "true", "yes", "on"}


def _node_run_mode(value: str) -> str:
    mode = str(value).split("#", 1)[0].strip().lower()
    if mode not in NODE_RUN_MODES:
        raise ConfigurationError(
            f"NODE_RUN_MODE must be one of {', '.join(NODE_RUN_MODES)}; got {value!r}."
        )
    return mode
