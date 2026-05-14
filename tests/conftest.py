"""Shared pytest fixtures for the HelpmateAI test suite.

`backend.subscriptions._cached_read` is an lru_cache that lives for
the whole process. A test that upserts a subscription would otherwise
leak that cached row into unrelated tests in the same pytest run. We
clear the cache + in-memory store between every test so tier
resolution starts from a clean slate.
"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _reset_subscriptions_cache():
    """Wipe the subscriptions LRU + in-memory store before/after every test.

    autouse=True so individual tests don't have to opt in. The reset
    runs both before AND after the test so any test that leaves a
    row behind doesn't poison the next one.
    """
    try:
        from backend.subscriptions import (
            invalidate_subscription_cache,
            reset_in_memory_backend,
        )
    except Exception:
        # The subscriptions module is optional in some test paths;
        # if it can't import, there's nothing to reset.
        yield
        return

    invalidate_subscription_cache()
    reset_in_memory_backend()
    yield
    invalidate_subscription_cache()
    reset_in_memory_backend()
