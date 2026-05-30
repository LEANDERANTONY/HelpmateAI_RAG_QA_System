"""In-process token-bucket rate limiter (M1)."""
from __future__ import annotations

import pytest
from fastapi import HTTPException

from backend import rate_limit


def test_allows_requests_within_capacity(monkeypatch):
    monkeypatch.setenv("HELPMATE_RATE_LIMIT_ENABLED", "true")
    rate_limit._buckets.clear()
    # refill_per_sec=0 makes the bucket deterministic (no replenishment).
    for _ in range(5):
        rate_limit.enforce_rate_limit("user-a", capacity=5, refill_per_sec=0)


def test_blocks_with_429_when_exhausted(monkeypatch):
    monkeypatch.setenv("HELPMATE_RATE_LIMIT_ENABLED", "true")
    rate_limit._buckets.clear()
    for _ in range(3):
        rate_limit.enforce_rate_limit("user-b", capacity=3, refill_per_sec=0)
    with pytest.raises(HTTPException) as exc:
        rate_limit.enforce_rate_limit("user-b", capacity=3, refill_per_sec=0)
    assert exc.value.status_code == 429


def test_disabled_via_env_never_blocks(monkeypatch):
    monkeypatch.setenv("HELPMATE_RATE_LIMIT_ENABLED", "false")
    rate_limit._buckets.clear()
    for _ in range(100):
        rate_limit.enforce_rate_limit("user-c", capacity=1, refill_per_sec=0)
