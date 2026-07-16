"""Retry classification and adaptive provider concurrency tests."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from tree.observability import retry


class _StatusError(Exception):
    def __init__(self, status_code: int, retry_after: str | None = None):
        super().__init__(f"HTTP {status_code}")
        self.status_code = status_code
        self.response = SimpleNamespace(headers={"Retry-After": retry_after} if retry_after else {})


async def test_retry_rejects_non_transient_error_without_sleep(monkeypatch):
    calls = 0

    async def fail():
        nonlocal calls
        calls += 1
        raise ValueError("bad input")

    async def unexpected_sleep(_delay):
        pytest.fail("non-transient failures must not be retried")

    monkeypatch.setattr(retry.asyncio, "sleep", unexpected_sleep)
    with pytest.raises(ValueError, match="bad input"):
        await retry.retry_with_backoff(fail, max_retries=3)
    assert calls == 1


def test_malformed_llm_response_is_retryable():
    assert retry.is_retryable_error(retry.MalformedLLMResponseError("empty content")) is True


@pytest.mark.parametrize(
    "error",
    [
        retry.LLMOutputTruncatedError("length"),
        retry.LLMContentFilteredError("filtered"),
        retry.LLMRefusalError("refused"),
        retry.LLMToolCallError("tool call"),
    ],
)
def test_terminal_llm_responses_are_not_retried(error):
    assert retry.is_retryable_error(error) is False


async def test_retry_honors_retry_after_and_reports_attempt(monkeypatch):
    calls = 0
    sleeps: list[float] = []
    retries: list[tuple[int, float]] = []

    async def call():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise _StatusError(429, "7")
        return "ok"

    async def fake_sleep(delay):
        sleeps.append(delay)

    async def on_retry(_exc, attempt, delay):
        retries.append((attempt, delay))

    monkeypatch.setattr(retry.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(retry.random, "uniform", lambda _low, _high: 0.0)

    assert await retry.retry_with_backoff(call, max_retries=1, on_retry=on_retry) == "ok"
    assert calls == 2
    assert sleeps == [7.0]
    assert retries == [(1, 7.0)]


async def test_adaptive_limiter_reduces_then_recovers_gradually():
    limiter = retry.AdaptiveConcurrencyLimiter(4, recovery_successes=2)

    await limiter.record_pressure()
    assert limiter.limit == 2
    await limiter.record_success()
    assert limiter.limit == 2
    await limiter.record_success()
    assert limiter.limit == 3
