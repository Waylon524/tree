"""Generic LLM client: per-role AsyncOpenAI instances with degradation support."""

from __future__ import annotations

import asyncio

from openai import AsyncOpenAI

from tree.config import Settings
from tree.observability.retry import (
    DegradationTracker,
    MalformedLLMResponseError,
    retry_with_backoff,
)


class LLMClient:
    """Multi-role LLM client. Each role gets its own AsyncOpenAI instance."""

    def __init__(self, settings: Settings):
        self._clients: dict[str, AsyncOpenAI] = {}
        self._models: dict[str, str] = {}
        self._degradation = DegradationTracker(
            threshold=settings.pro_degradation_threshold,
            cooldown_sec=settings.pro_degradation_cooldown_sec,
        )
        self._max_retries = settings.max_retries
        self._timeout_sec = settings.llm_timeout_sec

        for role_name, config in [
            ("examiner", settings.examiner),
            ("student", settings.student),
            ("writer", settings.writer),
            ("archivist", settings.archivist),
        ]:
            self._clients[role_name] = AsyncOpenAI(
                api_key=config.api_key,
                base_url=config.base_url,
                timeout=settings.llm_timeout_sec,
                max_retries=0,
            )
            self._models[role_name] = config.model

    async def call(
        self,
        role: str,
        system_prompt: str,
        user_prompt: str,
        *,
        timeout_sec: float | None = None,
    ) -> str:
        """Call LLM for a given role. Examiner uses degradation logic."""
        client = self._clients[role]
        model = self._models[role]
        effective_timeout = timeout_sec or self._timeout_sec

        # Examiner: check if degraded to student model
        if role == "examiner" and self._degradation.is_degraded:
            client = self._clients["student"]
            model = self._models["student"]

        async def _call():
            resp = await asyncio.wait_for(
                client.chat.completions.create(
                    model=model,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                ),
                timeout=effective_timeout,
            )
            return _extract_chat_content(resp)

        if role == "examiner":
            try:
                result = await retry_with_backoff(_call, max_retries=self._max_retries)
                self._degradation.record_success()
                return result
            except Exception:
                self._degradation.record_failure()
                raise

        return await retry_with_backoff(_call, max_retries=self._max_retries)

    async def close(self) -> None:
        for client in self._clients.values():
            await client.close()


def _extract_chat_content(resp) -> str:
    choices = getattr(resp, "choices", None)
    if choices is None:
        raise MalformedLLMResponseError("LLM response missing choices")
    if not choices:
        raise MalformedLLMResponseError("LLM response contained no choices")
    message = getattr(choices[0], "message", None)
    if message is None:
        raise MalformedLLMResponseError("LLM response choice missing message")
    return getattr(message, "content", None) or ""
