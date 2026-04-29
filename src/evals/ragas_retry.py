from __future__ import annotations

import random
import time
from collections.abc import Callable
from typing import TypeVar


T = TypeVar("T")


TRANSIENT_ERROR_MARKERS = (
    "429",
    "503",
    "rate limit",
    "rate_limit",
    "resource exhausted",
    "temporarily unavailable",
    "service unavailable",
    "model overloaded",
    "overloaded",
    "timeout",
    "timed out",
    "connection",
)


def is_transient_ragas_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return any(marker in message for marker in TRANSIENT_ERROR_MARKERS)


def call_with_ragas_retry(
    fn: Callable[[], T],
    *,
    max_attempts: int = 4,
    initial_delay_seconds: float = 4.0,
    max_delay_seconds: float = 30.0,
    jitter_seconds: float = 1.0,
) -> T:
    """Retry transient judge/API failures while leaving deterministic errors visible."""
    attempts = max(1, max_attempts)
    delay = max(0.0, initial_delay_seconds)
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except Exception as exc:
            if attempt >= attempts or not is_transient_ragas_error(exc):
                raise
            sleep_for = min(delay, max_delay_seconds)
            if jitter_seconds > 0:
                sleep_for += random.uniform(0, jitter_seconds)
            time.sleep(sleep_for)
            delay = min(max(delay * 2, initial_delay_seconds), max_delay_seconds)
    raise RuntimeError("RAGAS retry loop exhausted unexpectedly.")
