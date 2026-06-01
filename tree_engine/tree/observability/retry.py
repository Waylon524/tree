"""LLM retry/backoff + Examiner degradation tracking."""

from __future__ import annotations

import asyncio
import random
import time
from typing import Awaitable, Callable, TypeVar

T = TypeVar("T")


class MalformedLLMResponseError(Exception):
    """Raised when an LLM response cannot be parsed into the expected shape."""


async def retry_with_backoff(
    func: Callable[[], Awaitable[T]],
    *,
    max_retries: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
) -> T:
    """Retry an async call with exponential backoff + jitter."""
    attempt = 0
    while True:
        try:
            return await func()
        except Exception:
            attempt += 1
            if attempt > max_retries:
                raise
            delay = min(max_delay, base_delay * (2 ** (attempt - 1)))
            delay += random.uniform(0, delay * 0.25)
            await asyncio.sleep(delay)


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
