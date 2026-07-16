"""Multi-role LLM client: one AsyncOpenAI per role + Examiner degradation.

Roles: examiner, student, writer, archivist, dagger.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import logging
import time
from urllib.parse import urlparse

from openai import AsyncOpenAI

from tree.config import ROLES, RoleConfig, Settings
from tree.model.budget import PromptBudgetExceededError, estimate_chat_tokens
from tree.model.operations import LLMOperationSpec, resolve_operation_spec
from tree.observability.operation_log import OperationLog
from tree.observability.retry import (
    AdaptiveConcurrencyLimiter,
    DegradationTracker,
    LLMContentFilteredError,
    LLMOutputTruncatedError,
    LLMRefusalError,
    LLMToolCallError,
    MalformedLLMResponseError,
    retry_with_backoff,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ProviderCapabilities:
    name: str
    json_mode: bool
    thinking: bool
    reasoning_effort: bool
    max_output_parameter: str | None


_PROVIDER_PROFILES = {
    "deepseek": ProviderCapabilities(
        name="deepseek",
        json_mode=True,
        thinking=True,
        reasoning_effort=True,
        max_output_parameter="max_tokens",
    ),
    "openai": ProviderCapabilities(
        name="openai",
        json_mode=True,
        thinking=False,
        reasoning_effort=False,
        max_output_parameter="max_completion_tokens",
    ),
    "generic": ProviderCapabilities(
        name="generic",
        json_mode=False,
        thinking=False,
        reasoning_effort=False,
        max_output_parameter="max_tokens",
    ),
}


class LLMClient:
    def __init__(self, settings: Settings):
        self._clients: dict[str, AsyncOpenAI] = {}
        self._models: dict[str, str] = {}
        self._provider_keys: dict[str, tuple[str, str]] = {}
        self._capability_keys: dict[str, tuple[str, str]] = {}
        self._capabilities: dict[str, ProviderCapabilities] = {}
        self._context_windows: dict[str, int] = {}
        self._max_output_tokens: dict[str, int] = {}
        self._disabled_options: dict[tuple[str, str], set[str]] = {}
        self._limiters: dict[tuple[str, str], AdaptiveConcurrencyLimiter] = {}
        self._degradation = DegradationTracker(
            threshold=settings.pro_degradation_threshold,
            cooldown_sec=settings.pro_degradation_cooldown_sec,
        )
        self._max_retries = settings.max_retries
        self._timeout_sec = settings.llm_timeout_sec
        self._prompt_safety_tokens = settings.llm_prompt_safety_tokens
        self._operation_log = OperationLog(settings.project_root)

        for role_name in ROLES:
            config = settings.role(role_name)
            self._clients[role_name] = AsyncOpenAI(
                api_key=config.api_key,
                base_url=config.base_url,
                timeout=settings.llm_timeout_sec,
                max_retries=0,
            )
            self._models[role_name] = config.model
            self._capabilities[role_name] = _provider_capabilities(config)
            self._context_windows[role_name] = config.context_window
            self._max_output_tokens[role_name] = config.max_output_tokens
            provider_key = (config.base_url.rstrip("/"), config.api_key)
            capability_key = (config.base_url.rstrip("/"), config.model)
            self._provider_keys[role_name] = provider_key
            self._capability_keys[role_name] = capability_key
            self._disabled_options.setdefault(capability_key, set())
            self._limiters.setdefault(
                provider_key,
                AdaptiveConcurrencyLimiter(
                    settings.llm_provider_concurrency,
                    maximum_limit=settings.llm_provider_concurrency,
                ),
            )

    async def call(
        self,
        role: str,
        system_prompt: str,
        user_prompt: str,
        *,
        operation: str | None = None,
        timeout_sec: float | None = None,
    ) -> str:
        operation_id, operation_spec = resolve_operation_spec(role, operation)
        client = self._clients[role]
        model = self._models[role]
        requested_timeout = timeout_sec or self._timeout_sec
        effective_timeout = (
            min(requested_timeout, operation_spec.timeout_sec)
            if operation_spec.timeout_sec is not None
            else requested_timeout
        )
        effective_role = role

        # Examiner degrades to the student model after repeated failures.
        if role == "examiner" and self._degradation.is_degraded:
            client = self._clients["student"]
            model = self._models["student"]
            effective_role = "student"

        limiter = self._limiters[self._provider_keys[effective_role]]
        capabilities = self._capabilities[effective_role]
        capability_key = self._capability_keys[effective_role]
        context_window = self._context_windows[effective_role]
        max_output_tokens = min(
            self._max_output_tokens[effective_role], operation_spec.max_output_tokens
        )
        max_retries = (
            min(self._max_retries, operation_spec.max_retries)
            if operation_spec.max_retries is not None
            else self._max_retries
        )
        estimated_input_tokens = estimate_chat_tokens(system_prompt, user_prompt)
        input_budget_tokens = (
            context_window - max_output_tokens - self._prompt_safety_tokens
        )
        if input_budget_tokens < 1 or estimated_input_tokens > input_budget_tokens:
            logger.warning(
                "LLM prompt budget rejected operation=%s role=%s estimated_input=%d "
                "input_budget=%d reserved_output=%d context_window=%d",
                operation_id,
                role,
                estimated_input_tokens,
                max(0, input_budget_tokens),
                max_output_tokens,
                context_window,
            )
            self._operation_log.append(
                {
                    "event": "rejected",
                    "operation": operation_id,
                    "role": role,
                    "effective_role": effective_role,
                    "provider": capabilities.name,
                    "model": model,
                    "estimated_input_tokens": estimated_input_tokens,
                    "requested_output_tokens": max_output_tokens,
                    "input_budget_tokens": max(0, input_budget_tokens),
                    "finish_reason": "prompt_budget",
                    "retries": 0,
                    "degraded": effective_role != role,
                }
            )
            raise PromptBudgetExceededError(
                role=role,
                estimated_input_tokens=estimated_input_tokens,
                input_budget_tokens=max(0, input_budget_tokens),
                context_window=context_window,
                reserved_output_tokens=max_output_tokens,
                safety_tokens=self._prompt_safety_tokens,
            )
        logger.debug(
            "LLM budget operation=%s role=%s provider=%s estimated_input=%d reserved_output=%d "
            "context_window=%d",
            operation_id,
            role,
            capabilities.name,
            estimated_input_tokens,
            max_output_tokens,
            context_window,
        )
        started_at = time.monotonic()
        retry_count = 0

        async def _call() -> str:
            async with limiter.slot():
                options = _request_options(
                    operation_spec,
                    capabilities=capabilities,
                    max_output_tokens=max_output_tokens,
                    disabled=self._disabled_options[capability_key],
                )
                try:
                    resp = await _create_completion(
                        client,
                        model=model,
                        system_prompt=system_prompt,
                        user_prompt=user_prompt,
                        timeout_sec=effective_timeout,
                        options=options,
                    )
                except Exception as exc:
                    unsupported = _unsupported_option(exc, options)
                    if unsupported is None:
                        raise
                    self._disabled_options[capability_key].add(unsupported)
                    logger.warning(
                        "LLM capability downgrade provider=%s model=%s option=%s",
                        capabilities.name,
                        model,
                        unsupported,
                    )
                    self._operation_log.append(
                        {
                            "event": "capability_downgrade",
                            "operation": operation_id,
                            "role": role,
                            "effective_role": effective_role,
                            "provider": capabilities.name,
                            "model": model,
                            "option": unsupported,
                            "estimated_input_tokens": estimated_input_tokens,
                            "requested_output_tokens": max_output_tokens,
                            "retries": retry_count,
                            "degraded": True,
                        }
                    )
                    resp = await _create_completion(
                        client,
                        model=model,
                        system_prompt=system_prompt,
                        user_prompt=user_prompt,
                        timeout_sec=effective_timeout,
                        options=_request_options(
                            operation_spec,
                            capabilities=capabilities,
                            max_output_tokens=max_output_tokens,
                            disabled=self._disabled_options[capability_key],
                        ),
                    )
            content = _extract_chat_content(resp)
            await limiter.record_success()
            usage_input, usage_output = _response_usage(resp)
            finish_reason = _finish_reason(resp) or "unknown"
            latency_ms = int((time.monotonic() - started_at) * 1000)
            logger.info(
                "LLM call complete operation=%s role=%s effective_role=%s provider=%s model=%s "
                "estimated_input=%d requested_output=%d usage_input=%s usage_output=%s "
                "finish_reason=%s retries=%d latency_ms=%d degraded=%s",
                operation_id,
                role,
                effective_role,
                capabilities.name,
                model,
                estimated_input_tokens,
                max_output_tokens,
                usage_input,
                usage_output,
                finish_reason,
                retry_count,
                latency_ms,
                effective_role != role,
            )
            self._operation_log.append(
                {
                    "event": "complete",
                    "operation": operation_id,
                    "role": role,
                    "effective_role": effective_role,
                    "provider": capabilities.name,
                    "model": model,
                    "estimated_input_tokens": estimated_input_tokens,
                    "requested_output_tokens": max_output_tokens,
                    "usage_input_tokens": usage_input,
                    "usage_output_tokens": usage_output,
                    "finish_reason": finish_reason,
                    "retries": retry_count,
                    "latency_ms": latency_ms,
                    "degraded": effective_role != role,
                }
            )
            return content

        async def _record_provider_pressure(exc: Exception, _attempt: int, _delay: float) -> None:
            nonlocal retry_count
            retry_count = _attempt
            if getattr(exc, "status_code", None) in {429, 503}:
                await limiter.record_pressure()
            logger.warning(
                "LLM call retry operation=%s role=%s attempt=%d delay_sec=%.2f reason=%s",
                operation_id,
                role,
                _attempt,
                _delay,
                type(exc).__name__,
            )
            self._operation_log.append(
                {
                    "event": "retry",
                    "operation": operation_id,
                    "role": role,
                    "effective_role": effective_role,
                    "provider": capabilities.name,
                    "model": model,
                    "estimated_input_tokens": estimated_input_tokens,
                    "requested_output_tokens": max_output_tokens,
                    "retry_attempt": _attempt,
                    "retry_delay_sec": round(_delay, 3),
                    "retry_reason": type(exc).__name__,
                    "degraded": effective_role != role,
                }
            )

        try:
            result = await retry_with_backoff(
                _call,
                max_retries=max_retries,
                on_retry=_record_provider_pressure,
            )
            if role == "examiner":
                self._degradation.record_success()
            return result
        except Exception as exc:
            if role == "examiner":
                self._degradation.record_failure()
            logger.warning(
                "LLM call failed operation=%s role=%s effective_role=%s provider=%s model=%s "
                "requested_output=%d retries=%d latency_ms=%d error=%s",
                operation_id,
                role,
                effective_role,
                capabilities.name,
                model,
                max_output_tokens,
                retry_count,
                int((time.monotonic() - started_at) * 1000),
                type(exc).__name__,
            )
            self._operation_log.append(
                {
                    "event": "failed",
                    "operation": operation_id,
                    "role": role,
                    "effective_role": effective_role,
                    "provider": capabilities.name,
                    "model": model,
                    "estimated_input_tokens": estimated_input_tokens,
                    "requested_output_tokens": max_output_tokens,
                    "finish_reason": _exception_finish_reason(exc),
                    "retries": retry_count,
                    "latency_ms": int((time.monotonic() - started_at) * 1000),
                    "error_type": type(exc).__name__,
                    "degraded": effective_role != role,
                }
            )
            if isinstance(exc, LLMOutputTruncatedError):
                raise LLMOutputTruncatedError(
                    f"LLM operation {operation_id} reached its {max_output_tokens}-token output "
                    "limit. Split the operation input or raise the role output ceiling after "
                    "verifying the provider limit."
                ) from exc
            raise

    async def close(self) -> None:
        for client in self._clients.values():
            await client.close()


def _provider_capabilities(config: RoleConfig) -> ProviderCapabilities:
    profile = config.provider_profile.strip().lower()
    if profile == "auto":
        hostname = (urlparse(config.base_url).hostname or "").lower()
        if hostname == "api.deepseek.com" or hostname.endswith(".deepseek.com"):
            profile = "deepseek"
        elif hostname == "api.openai.com" or hostname.endswith(".openai.com"):
            profile = "openai"
        else:
            profile = "generic"
    return _PROVIDER_PROFILES[profile]


def _request_options(
    operation: LLMOperationSpec,
    *,
    capabilities: ProviderCapabilities | None = None,
    max_output_tokens: int = 8_192,
    disabled: set[str] | None = None,
) -> dict[str, object]:
    capabilities = capabilities or _PROVIDER_PROFILES["deepseek"]
    disabled = disabled or set()
    options: dict[str, object] = {}
    if capabilities.thinking and "extra_body" not in disabled:
        options["extra_body"] = {
            "thinking": {"type": operation.thinking}
        }
    if capabilities.json_mode and operation.json_mode and "response_format" not in disabled:
        options["response_format"] = {"type": "json_object"}
    if (
        capabilities.reasoning_effort
        and operation.reasoning_effort
        and "reasoning_effort" not in disabled
    ):
        options["reasoning_effort"] = operation.reasoning_effort
    output_parameter = capabilities.max_output_parameter
    if output_parameter and output_parameter not in disabled:
        options[output_parameter] = max_output_tokens
    return options


async def _create_completion(
    client: AsyncOpenAI,
    *,
    model: str,
    system_prompt: str,
    user_prompt: str,
    timeout_sec: float,
    options: dict[str, object],
) -> object:
    return await asyncio.wait_for(
        client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            timeout=timeout_sec,
            **options,
        ),
        timeout=timeout_sec,
    )


def _unsupported_option(exc: Exception, options: dict[str, object]) -> str | None:
    if getattr(exc, "status_code", None) not in {400, 422}:
        return None
    message = str(exc).lower()
    if not any(
        marker in message
        for marker in (
            "unsupported",
            "not supported",
            "unknown parameter",
            "unrecognized parameter",
            "unexpected parameter",
            "extra inputs are not permitted",
        )
    ):
        return None
    aliases = {
        "extra_body": ("extra_body", "thinking"),
        "response_format": ("response_format", "json mode", "json_object"),
        "reasoning_effort": ("reasoning_effort", "reasoning effort"),
        "max_tokens": ("max_tokens", "max tokens"),
        "max_completion_tokens": ("max_completion_tokens", "max completion tokens"),
    }
    for option in options:
        if any(alias in message for alias in aliases.get(option, (option,))):
            return option
    return None


def _finish_reason(resp: object) -> str:
    choices = getattr(resp, "choices", None)
    if not choices:
        return ""
    return str(getattr(choices[0], "finish_reason", "") or "").strip().lower()


def _response_usage(resp: object) -> tuple[int | None, int | None]:
    usage = getattr(resp, "usage", None)
    if usage is None:
        return None, None

    def read(*names: str) -> int | None:
        for name in names:
            value = usage.get(name) if isinstance(usage, dict) else getattr(usage, name, None)
            if isinstance(value, int):
                return value
        return None

    return read("prompt_tokens", "input_tokens"), read("completion_tokens", "output_tokens")


def _exception_finish_reason(exc: Exception) -> str:
    if isinstance(exc, LLMOutputTruncatedError):
        return "length"
    if isinstance(exc, LLMContentFilteredError):
        return "content_filter"
    if isinstance(exc, LLMRefusalError):
        return "refusal"
    if isinstance(exc, LLMToolCallError):
        return "tool_calls"
    return "error"


def _extract_chat_content(resp: object) -> str:
    choices = getattr(resp, "choices", None)
    if not choices:
        raise MalformedLLMResponseError("LLM response missing choices")
    choice = choices[0]
    finish_reason = str(getattr(choice, "finish_reason", "") or "").strip().lower()
    if finish_reason == "length":
        raise LLMOutputTruncatedError(
            "LLM response reached its output token limit (finish_reason=length)"
        )
    if finish_reason == "content_filter":
        raise LLMContentFilteredError(
            "LLM response was blocked by the provider content filter "
            "(finish_reason=content_filter)"
        )
    message = getattr(choice, "message", None)
    if message is None:
        raise MalformedLLMResponseError("LLM response choice missing message")
    if getattr(message, "refusal", None):
        raise LLMRefusalError("LLM response was refused")
    if finish_reason in {"tool_calls", "function_call"} or getattr(message, "tool_calls", None):
        raise LLMToolCallError(
            f"LLM response requested unsupported tools (finish_reason={finish_reason or 'unknown'})"
        )
    content = getattr(message, "content", None)
    if not isinstance(content, str) or not content.strip():
        raise MalformedLLMResponseError("LLM response content is empty")
    return content
