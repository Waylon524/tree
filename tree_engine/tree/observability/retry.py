"""LLM retry/backoff + Examiner degradation tracking."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import random
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import TypeVar

T = TypeVar("T")


class MalformedLLMResponseError(Exception):
    """Raised when an LLM response cannot be parsed into the expected shape."""


def is_retryable_error(exc: Exception) -> bool:
    """Return whether retrying ``exc`` can plausibly succeed without changing input."""
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
        return True

    status_code = getattr(exc, "status_code", None)
    if isinstance(status_code, int):
        return status_code in {408, 425, 429} or status_code >= 500

    # Avoid importing provider-specific exception classes here.  OpenAI and httpx
    # expose stable class names for connection/timeout failures, including subclasses.
    retryable_names = {
        "APIConnectionError",
        "APITimeoutError",
        "ConnectError",
        "ConnectTimeout",
        "NetworkError",
        "PoolTimeout",
        "ReadError",
        "ReadTimeout",
        "RemoteProtocolError",
        "WriteError",
        "WriteTimeout",
    }
    return any(cls.__name__ in retryable_names for cls in type(exc).__mro__)


def retry_after_seconds(exc: Exception) -> float | None:
    """Read a provider Retry-After header as seconds, when available."""
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None)
    if headers is None:
        return None
    value = headers.get("retry-after") or headers.get("Retry-After")
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        try:
            retry_at = parsedate_to_datetime(str(value))
            if retry_at.tzinfo is None:
                retry_at = retry_at.replace(tzinfo=timezone.utc)
            return max(0.0, (retry_at - datetime.now(timezone.utc)).total_seconds())
        except (TypeError, ValueError, OverflowError):
            return None


async def retry_with_backoff(
    func: Callable[[], Awaitable[T]],
    *,
    max_retries: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    should_retry: Callable[[Exception], bool] = is_retryable_error,
    on_retry: Callable[[Exception, int, float], Awaitable[None] | None] | None = None,
) -> T:
    """Retry transient failures with Retry-After-aware backoff and jitter."""
    attempt = 0
    while True:
        try:
            return await func()
        except Exception as exc:
            attempt += 1
            if attempt > max_retries or not should_retry(exc):
                raise
            delay = min(max_delay, base_delay * (2 ** (attempt - 1)))
            delay += random.uniform(0, delay * 0.25)
            retry_after = retry_after_seconds(exc)
            if retry_after is not None:
                delay = max(delay, min(max_delay, retry_after))
            if on_retry is not None:
                callback_result = on_retry(exc, attempt, delay)
                if callback_result is not None:
                    await callback_result
            await asyncio.sleep(delay)


class AdaptiveConcurrencyLimiter:
    """A process-local AIMD limiter shared by roles using the same provider."""

    def __init__(
        self,
        initial_limit: int,
        *,
        minimum_limit: int = 1,
        maximum_limit: int | None = None,
        recovery_successes: int = 20,
    ):
        self.minimum_limit = max(1, minimum_limit)
        self.maximum_limit = max(self.minimum_limit, maximum_limit or initial_limit)
        self._limit = min(self.maximum_limit, max(self.minimum_limit, initial_limit))
        self._recovery_successes = max(1, recovery_successes)
        self._successes = 0
        self._active = 0
        self._condition = asyncio.Condition()

    @property
    def limit(self) -> int:
        return self._limit

    @property
    def active(self) -> int:
        return self._active

    @asynccontextmanager
    async def slot(self) -> AsyncIterator[None]:
        async with self._condition:
            await self._condition.wait_for(lambda: self._active < self._limit)
            self._active += 1
        try:
            yield
        finally:
            async with self._condition:
                self._active -= 1
                self._condition.notify_all()

    async def record_pressure(self) -> None:
        """Multiplicatively reduce concurrency after provider pressure."""
        async with self._condition:
            self._limit = max(self.minimum_limit, self._limit // 2)
            self._successes = 0
            self._condition.notify_all()

    async def record_success(self) -> None:
        """Add one slot after a sustained sequence of successful calls."""
        async with self._condition:
            self._successes += 1
            if self._successes >= self._recovery_successes and self._limit < self.maximum_limit:
                self._limit += 1
                self._successes = 0
                self._condition.notify_all()


class DegradationTracker:
    """Track consecutive failures to temporarily degrade the Examiner model."""

    def __init__(self, threshold: int, cooldown_sec: int):
        self.threshold = threshold
        self.cooldown_sec = cooldown_sec
        self._consecutive_failures = 0
        self._degraded_until = 0.0

    @property
    def is_degraded(self) -> bool:
        return time.time() < self._degraded_until

    def record_success(self) -> None:
        self._consecutive_failures = 0

    def record_failure(self) -> None:
        self._consecutive_failures += 1
        if self._consecutive_failures >= self.threshold:
            self._degraded_until = time.time() + self.cooldown_sec
            self._consecutive_failures = 0
