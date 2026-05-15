"""Tests for the /feedback route and the LocalFeedbackStore backend.

Covers seven paths:

  • LocalFeedbackStore round-trips a row (save → list).
  • LocalFeedbackStore rejects invalid rating with a clean error.
  • LocalFeedbackStore caps comment length.
  • POST /feedback persists a row with trace_id (happy path).
  • POST /feedback enforces auth (401 without bearer).
  • POST /feedback rejects bad rating with 400.
  • POST /feedback rejects oversized comment with 400.
  • Two users' rows stay isolated (RLS-equivalent on the local backend).
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.auth import AuthenticatedUser, require_authenticated_user
from backend.feedback_store import (
    COMMENT_MAX_LENGTH,
    FeedbackValidationError,
    LocalFeedbackStore,
)
from backend.main import app


_TEST_USER_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


def _fake_user() -> AuthenticatedUser:
    return AuthenticatedUser(id=_TEST_USER_ID, email="fb@example.com")


@pytest.fixture
def isolated_feedback_store(monkeypatch, tmp_path):
    """Per-test feedback store rooted at a fresh tmp dir.

    backend.main caches the store via lru_cache; we clear that cache so
    the override sticks for the duration of the test. Mirrors the
    pattern in test_question_quota's isolated_quota_store fixture.
    """
    from backend import main as backend_main
    from src.config import Settings

    monkeypatch.setenv("HELPMATE_DATA_DIR", str(tmp_path / "data"))
    # Capture the original lru_cache-wrapped function before monkeypatch
    # replaces it; we'll clear its cache after teardown so the next test
    # starts from a clean slate.
    original_feedback_store = backend_main._feedback_store
    original_feedback_store.cache_clear()
    backend_main._settings.cache_clear()
    store = LocalFeedbackStore(Settings())
    monkeypatch.setattr(backend_main, "_feedback_store", lambda: store)
    yield store
    # monkeypatch's teardown restores _feedback_store; clear the
    # original's cache so we don't carry state across tests.
    original_feedback_store.cache_clear()
    backend_main._settings.cache_clear()


@pytest.fixture
def authed_client():
    app.dependency_overrides[require_authenticated_user] = _fake_user
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(require_authenticated_user, None)


# ─── LocalFeedbackStore unit tests ──────────────────────────────────────


def test_local_store_roundtrip(isolated_feedback_store):
    record = isolated_feedback_store.save_feedback(
        user_id="user-1",
        rating="up",
        trace_id="trace-abc",
        comment="great answer",
    )
    assert record.user_id == "user-1"
    assert record.rating == "up"
    assert record.trace_id == "trace-abc"
    assert record.feedback_id  # uuid was generated
    assert record.created_at  # iso timestamp was stamped

    rows = isolated_feedback_store.list_for_user("user-1")
    assert len(rows) == 1
    assert rows[0].feedback_id == record.feedback_id


def test_local_store_rejects_bad_rating(isolated_feedback_store):
    with pytest.raises(FeedbackValidationError):
        isolated_feedback_store.save_feedback(
            user_id="user-1",
            rating="maybe",  # not in {'up', 'down'}
        )


def test_local_store_caps_comment_length(isolated_feedback_store):
    too_long = "x" * (COMMENT_MAX_LENGTH + 1)
    with pytest.raises(FeedbackValidationError):
        isolated_feedback_store.save_feedback(
            user_id="user-1",
            rating="down",
            comment=too_long,
        )


def test_local_store_isolated_per_user(isolated_feedback_store):
    isolated_feedback_store.save_feedback(user_id="user-1", rating="up")
    isolated_feedback_store.save_feedback(user_id="user-2", rating="down")
    isolated_feedback_store.save_feedback(user_id="user-1", rating="up")

    rows_one = isolated_feedback_store.list_for_user("user-1")
    rows_two = isolated_feedback_store.list_for_user("user-2")
    assert len(rows_one) == 2
    assert len(rows_two) == 1
    # most-recent-first
    assert rows_one[0].created_at >= rows_one[1].created_at


# ─── /feedback route integration tests ───────────────────────────────────


def test_post_feedback_persists_row(authed_client, isolated_feedback_store):
    response = authed_client.post(
        "/feedback",
        json={
            "trace_id": "trace-xyz",
            "rating": "up",
            "comment": "spot on",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["feedback_id"]
    assert body["created_at"]
    rows = isolated_feedback_store.list_for_user(_TEST_USER_ID)
    assert len(rows) == 1
    assert rows[0].trace_id == "trace-xyz"
    assert rows[0].rating == "up"
    assert rows[0].comment == "spot on"


def test_post_feedback_defaults_surface(authed_client, isolated_feedback_store):
    """When the client omits surface, it should default to 'answer'."""
    response = authed_client.post(
        "/feedback",
        json={"rating": "down"},
    )
    assert response.status_code == 200
    rows = isolated_feedback_store.list_for_user(_TEST_USER_ID)
    assert rows[0].surface == "answer"
    # No trace_id supplied → stored as None, not a stale empty string.
    assert rows[0].trace_id is None


def test_post_feedback_without_auth_returns_401(isolated_feedback_store):
    client = TestClient(app)
    response = client.post(
        "/feedback",
        json={"rating": "up", "trace_id": "trace-1"},
    )
    assert response.status_code == 401


def test_post_feedback_rejects_bad_rating(authed_client, isolated_feedback_store):
    response = authed_client.post(
        "/feedback",
        json={"rating": "love-it"},
    )
    assert response.status_code == 400
    rows = isolated_feedback_store.list_for_user(_TEST_USER_ID)
    assert len(rows) == 0


def test_post_feedback_rejects_oversized_comment(authed_client, isolated_feedback_store):
    response = authed_client.post(
        "/feedback",
        json={
            "rating": "up",
            "comment": "x" * (COMMENT_MAX_LENGTH + 1),
        },
    )
    assert response.status_code == 400


def test_post_feedback_storage_failure_returns_503(
    authed_client, isolated_feedback_store, monkeypatch
):
    """When the store raises (Supabase outage, disk error), surface a
    503 so the frontend can retry — feedback is the user's gesture
    and a silent drop would feel broken."""

    def raise_storage(**kwargs):
        raise RuntimeError("disk full")

    monkeypatch.setattr(isolated_feedback_store, "save_feedback", raise_storage)
    response = authed_client.post(
        "/feedback",
        json={"rating": "up"},
    )
    assert response.status_code == 503
    assert "again" in response.json()["detail"].lower()
