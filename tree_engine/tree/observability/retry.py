"""Exponential backoff retry + Pro→Flash degradation tracker."""

from __future__ import annotations

import asyncio
import time

from openai import APIStatusError, APITimeoutError

RETRYABLE_STATUS_CODES = {429, 500, 502, 503}


class MalformedLLMResponseError(RuntimeError):
    """Raised when an LLM provider returns a transiently malformed response."""


def is_retryable(exc: Exception) -> bool:
    if isinstance(exc, MalformedLLMResponseError):
        return True
    if isinstance(exc, APITimeoutError):
        return True
    if isinstance(exc, APIStatusError):
        return exc.status_code in RETRYABLE_STATUS_CODES
    if isinstance(exc, (ConnectionError, asyncio.TimeoutError)):
        return True
    return False


async def retry_with_backoff(
    fn,
    *args,
    max_retries: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 4.0,
    **kwargs,
):
    last_exc = None
    for attempt in range(max_retries + 1):
        try:
            return await fn(*args, **kwargs)
        except Exception as exc:
            if not is_retryable(exc) or attempt == max_retries:
                raise
            last_exc = exc
            delay = min(base_delay * (2 ** attempt), max_delay)
            await asyncio.sleep(delay)
    raise last_exc  # type: ignore[misc]


class DegradationTracker:
    def __init__(self, threshold: int = 3, cooldown_sec: int = 600):
        self._consecutive_failures = 0
        self._degraded_at: float | None = None
        self._threshold = threshold
        self._cooldown_sec = cooldown_sec

    def record_failure(self) -> None:
        self._consecutive_failures += 1

    def record_success(self) -> None:
        self._consecutive_failures = 0

    @property
    def is_degraded(self) -> bool:
        if self._consecutive_failures < self._threshold:
            return False
        if self._degraded_at is None:
            self._degraded_at = time.monotonic()
            return True
        if time.monotonic() - self._degraded_at > self._cooldown_sec:
            self._consecutive_failures = 0
            self._degraded_at = None
            return False
        return True
