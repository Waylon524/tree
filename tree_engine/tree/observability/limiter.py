"""Iteration limit: max N rounds per file before pausing."""

from __future__ import annotations


class IterationLimitError(Exception):
    pass


class IterationLimiter:
    def __init__(self, max_iterations: int = 5):
        self._max = max_iterations

    def check(self, execution_path: str, file_seq: str, iteration: int) -> None:
        if iteration > self._max:
            raise IterationLimitError(
                f"⚠ ITERATION_LIMIT: {execution_path}/{file_seq} has looped {iteration} rounds without PASS. "
                f"Please check the Bottleneck Report and draft manually."
            )
