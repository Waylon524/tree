"""Per-file iteration limiter to stop runaway exam/write loops."""

from __future__ import annotations


class IterationLimitExceeded(Exception):
    pass


class IterationLimiter:
    def __init__(self, max_iterations: int):
        self.max_iterations = max_iterations

    def check(self, execution_path: str, file_seq: str, iteration: int) -> None:
        if iteration > self.max_iterations:
            raise IterationLimitExceeded(
                f"{execution_path}/{file_seq} exceeded {self.max_iterations} iterations"
            )
