"""Lightweight in-process token-bucket rate limiting (M1).

The product spec deliberately avoided a rate-limit MIDDLEWARE that runs before
route dispatch (see backend/quota.py + docs/tier-enforcement-flags.md). This is
the narrower, in-route version: a per-user token bucket applied inside specific
handlers. It exists to bound abuse of the unmetered /transcribe endpoint, whose
OpenAI Whisper calls have no monthly quota — without it a single authenticated
user could loop the endpoint for unbounded OpenAI spend.

Scope + limitations:
  * Per-process (per uvicorn worker), not shared across workers/instances. A
    sufficient back-stop on a single-VPS deployment; NOT a distributed limiter.
  * Disabled when HELPMATE_RATE_LIMIT_ENABLED is falsey (load tests / local).
"""
from __future__ import annotations

import os
import threading
import time

from fastapi import HTTPException

_buckets: dict[str, tuple[float, float]] = {}
_lock = threading.Lock()


def _enabled() -> bool:
    return os.getenv("HELPMATE_RATE_LIMIT_ENABLED", "true").strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }


def enforce_rate_limit(key: str, *, capacity: float, refill_per_sec: float) -> None:
    """Consume one token from ``key``'s bucket; raise HTTP 429 when empty.

    ``capacity`` is the burst size; ``refill_per_sec`` the sustained rate
    (e.g. capacity=30, refill_per_sec=0.5 => ~30 requests/minute, burst 30).
    """
    if not _enabled():
        return
    now = time.monotonic()
    with _lock:
        tokens, last = _buckets.get(key, (capacity, now))
        tokens = min(capacity, tokens + (now - last) * refill_per_sec)
        if tokens < 1.0:
            raise HTTPException(
                status_code=429,
                detail="Too many requests — please wait a moment and try again.",
            )
        _buckets[key] = (tokens - 1.0, now)
