"""Multi-role LLM client: one AsyncOpenAI per role + Examiner degradation.

Roles: examiner, student, writer, archivist, dagger.
"""

from __future__ import annotations

import asyncio

from openai import AsyncOpenAI

from tree.config import ROLES, Settings
from tree.observability.retry import (
    AdaptiveConcurrencyLimiter,
    DegradationTracker,
    MalformedLLMResponseError,
    retry_with_backoff,
)

_JSON_RESPONSE_ROLES = {"archivist", "dagger"}
_THINKING_BY_ROLE = {
    "archivist": "disabled",
    "dagger": "enabled",
    "examiner": "enabled",
    "writer": "enabled",
    "student": "disabled",
}


class LLMClient:
    def __init__(self, settings: Settings):
        self._clients: dict[str, AsyncOpenAI] = {}
        self._models: dict[str, str] = {}
        self._provider_keys: dict[str, tuple[str, str]] = {}
        self._limiters: dict[tuple[str, str], AdaptiveConcurrencyLimiter] = {}
        self._degradation = DegradationTracker(
            threshold=settings.pro_degradation_threshold,
            cooldown_sec=settings.pro_degradation_cooldown_sec,
        )
        self._max_retries = settings.max_retries
        self._timeout_sec = settings.llm_timeout_sec

        for role_name in ROLES:
            config = settings.role(role_name)
            self._clients[role_name] = AsyncOpenAI(
                api_key=config.api_key,
                base_url=config.base_url,
                timeout=settings.llm_timeout_sec,
                max_retries=0,
            )
            self._models[role_name] = config.model
            provider_key = (config.base_url.rstrip("/"), config.api_key)
            self._provider_keys[role_name] = provider_key
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
        timeout_sec: float | None = None,
    ) -> str:
        client = self._clients[role]
        model = self._models[role]
        effective_timeout = timeout_sec or self._timeout_sec
        effective_role = role

        # Examiner degrades to the student model after repeated failures.
        if role == "examiner" and self._degradation.is_degraded:
            client = self._clients["student"]
            model = self._models["student"]
            effective_role = "student"

        limiter = self._limiters[self._provider_keys[effective_role]]

        async def _call() -> str:
            async with limiter.slot():
                resp = await asyncio.wait_for(
                    client.chat.completions.create(
                        model=model,
                        messages=[
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_prompt},
                        ],
                        timeout=effective_timeout,
                        **_request_options(role),
                    ),
                    timeout=effective_timeout,
                )
            await limiter.record_success()
            return _extract_chat_content(resp)

        async def _record_provider_pressure(exc: Exception, _attempt: int, _delay: float) -> None:
            if getattr(exc, "status_code", None) in {429, 503}:
                await limiter.record_pressure()

        if role == "examiner":
            try:
                result = await retry_with_backoff(
                    _call,
                    max_retries=self._max_retries,
                    on_retry=_record_provider_pressure,
                )
                self._degradation.record_success()
                return result
            except Exception:
                self._degradation.record_failure()
                raise

        return await retry_with_backoff(
            _call,
            max_retries=self._max_retries,
            on_retry=_record_provider_pressure,
        )

    async def close(self) -> None:
        for client in self._clients.values():
            await client.close()


def _request_options(role: str) -> dict[str, object]:
    options: dict[str, object] = {
        "extra_body": {"thinking": {"type": _THINKING_BY_ROLE.get(role, "disabled")}},
    }
    if role in _JSON_RESPONSE_ROLES:
        options["response_format"] = {"type": "json_object"}
    if role == "dagger":
        options["reasoning_effort"] = "high"
    return options


def _extract_chat_content(resp: object) -> str:
    choices = getattr(resp, "choices", None)
    if not choices:
        raise MalformedLLMResponseError("LLM response missing choices")
    message = getattr(choices[0], "message", None)
    if message is None:
        raise MalformedLLMResponseError("LLM response choice missing message")
    return getattr(message, "content", None) or ""
