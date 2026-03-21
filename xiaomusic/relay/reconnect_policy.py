"""Deterministic reconnect policy for relay streams."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class ReconnectPolicy:
    base_delay_seconds: int = 1
    max_delay_seconds: int = 30
    max_retries: int = 3

    def delay_for_attempt(self, attempt: int) -> int | None:
        if attempt <= 0:
            raise ValueError("attempt must be >= 1")
        if attempt > self.max_retries:
            return None
        delay = self.base_delay_seconds * (2 ** (attempt - 1))
        return min(delay, self.max_delay_seconds)
