"""Tests for the PostHog funnel-event instrumentation in backend.main.

The funnel events (document_uploaded, document_indexed, quota_blocked)
are fire-and-forget capture_event calls. The only piece carrying real
branching logic is the `_quota_blocked` passthrough helper — covered
here. The post-success document_uploaded / document_indexed emits
mirror the already-shipped qa_answered pattern.
"""
from __future__ import annotations

from fastapi.responses import JSONResponse

from backend import main as backend_main
from backend.auth import AuthenticatedUser
from backend.main import _quota_blocked


def _user() -> AuthenticatedUser:
    return AuthenticatedUser(id="user-funnel-1", email="funnel@example.com")


def test_quota_blocked_passes_through_none_without_emitting(monkeypatch):
    """A quota check that allowed the action (returns None) emits no
    event and returns None."""
    events = []
    monkeypatch.setattr(
        backend_main, "capture_event", lambda **kwargs: events.append(kwargs)
    )

    result = _quota_blocked(None, user=_user(), gate="question_quota")

    assert result is None
    assert events == []


def test_quota_blocked_emits_event_on_rejection(monkeypatch):
    """A quota check that rejected (a JSONResponse) emits a
    `quota_blocked` event tagged with the gate + user, and returns the
    response unchanged so the caller can short-circuit."""
    events = []
    monkeypatch.setattr(
        backend_main, "capture_event", lambda **kwargs: events.append(kwargs)
    )
    rejection = JSONResponse(
        status_code=402, content={"code": "question_quota_exhausted"}
    )

    result = _quota_blocked(rejection, user=_user(), gate="question_quota")

    assert result is rejection
    assert len(events) == 1
    assert events[0]["event"] == "quota_blocked"
    assert events[0]["distinct_id"] == "user-funnel-1"
    assert events[0]["properties"]["gate"] == "question_quota"
