"""Per-tier retention TTL + sweeper FileStorage routing.

Step 6 of the tier-enforcement series. Coverage:

  • `_retention_delta_for_user` returns the right timedelta for each
    tier (30d/365d) AND None for Business (unbounded).

  • `_touch_document_workspace` writes the right expires_at value for
    each tier, INCLUDING the Business case where the field must be
    REMOVED (not set to a sentinel) so the sweeper's
    `expires_at < now` check never fires.

  • The sweeper deletes expired workspaces AND routes through
    FileStorage.delete so Supabase bucket objects get cleaned (the
    pipeline's local-unlink is a no-op for bucket keys).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from backend.auth import AuthenticatedUser
from backend.main import (
    WORKSPACE_EXPIRES_AT_KEY,
    WORKSPACE_LAST_ACTIVITY_KEY,
    WORKSPACE_OWNER_KEY,
    _retention_delta_for_user,
    _touch_document_workspace,
)
from backend.tiers import RETENTION_UNBOUNDED, TIER_LIMITS
from src.schemas import DocumentRecord


def _user(user_id: str = "00000000-0000-4000-8000-test-retention") -> AuthenticatedUser:
    return AuthenticatedUser(id=user_id, email="retention@example.com")


def _blank_doc() -> DocumentRecord:
    return DocumentRecord(
        document_id="doc-fake",
        file_name="fake.pdf",
        file_type="pdf",
        source_path="/tmp/fake.pdf",
        fingerprint="ff",
        char_count=0,
        page_count=1,
    )


# ─── _retention_delta_for_user ────────────────────────────────────────────


def test_free_user_gets_30_day_retention():
    """resolve_user_tier returns 'free' for every user today, so the
    default user should land on the free tier's 30-day retention."""
    delta = _retention_delta_for_user(_user())
    assert delta == timedelta(days=30)
    # Also matches the source of truth in TIER_LIMITS.
    assert delta == timedelta(days=TIER_LIMITS["free"]["retention_days"])


def test_pro_user_gets_365_day_retention(monkeypatch):
    """Monkey-patch resolve_user_tier until payment integration ships."""
    from backend import main as backend_main

    monkeypatch.setattr(backend_main, "resolve_user_tier", lambda _u: "pro")
    delta = _retention_delta_for_user(_user())
    assert delta == timedelta(days=365)


def test_business_user_gets_none_for_unbounded(monkeypatch):
    """Business tier's retention_days=-1 sentinel maps to None at the
    delta level — callers interpret None as "don't set expires_at"."""
    from backend import main as backend_main

    monkeypatch.setattr(backend_main, "resolve_user_tier", lambda _u: "business")
    delta = _retention_delta_for_user(_user())
    assert delta is None
    assert TIER_LIMITS["business"]["retention_days"] == RETENTION_UNBOUNDED


# ─── _touch_document_workspace ────────────────────────────────────────────


def test_touch_workspace_sets_expires_at_for_free_tier():
    """Free tier touch: expires_at = now + 30 days. Use a wide tolerance
    on the comparison because the test executes between `now = _now()`
    inside the helper and our assertion, so they're a few microseconds
    apart."""
    before = datetime.now(timezone.utc)
    doc = _touch_document_workspace(_blank_doc(), _user())
    after = datetime.now(timezone.utc)

    raw = doc.metadata[WORKSPACE_EXPIRES_AT_KEY]
    expires_at = datetime.fromisoformat(raw)
    assert before + timedelta(days=30) - timedelta(seconds=2) <= expires_at
    assert expires_at <= after + timedelta(days=30) + timedelta(seconds=2)


def test_touch_workspace_sets_owner_and_activity():
    """Regression: the existing owner / last_activity stamps must
    still be written. Step 6 only changes the expires_at clock."""
    user = _user(user_id="abc-123")
    doc = _touch_document_workspace(_blank_doc(), user)
    assert doc.metadata[WORKSPACE_OWNER_KEY] == "abc-123"
    assert doc.metadata[WORKSPACE_LAST_ACTIVITY_KEY]  # ISO timestamp set


def test_touch_workspace_pro_tier_uses_365_days(monkeypatch):
    from backend import main as backend_main

    monkeypatch.setattr(backend_main, "resolve_user_tier", lambda _u: "pro")
    before = datetime.now(timezone.utc)
    doc = _touch_document_workspace(_blank_doc(), _user())
    expires_at = datetime.fromisoformat(doc.metadata[WORKSPACE_EXPIRES_AT_KEY])
    # Within a few seconds of `now + 365 days`.
    expected = before + timedelta(days=365)
    assert abs((expires_at - expected).total_seconds()) < 5


def test_touch_workspace_business_tier_omits_expires_at(monkeypatch):
    """Business tier → expires_at REMOVED from metadata entirely.
    Setting None would also work but the brief specifies the sentinel
    behavior should be 'never auto-delete', and the sweeper's
    `expires_at is None` check (via _parse_timestamp) handles a
    missing key correctly."""
    from backend import main as backend_main

    monkeypatch.setattr(backend_main, "resolve_user_tier", lambda _u: "business")
    doc = _touch_document_workspace(_blank_doc(), _user())
    assert WORKSPACE_EXPIRES_AT_KEY not in doc.metadata
    # Other workspace fields still set.
    assert doc.metadata[WORKSPACE_OWNER_KEY] == _user().id


def test_touch_workspace_business_clears_stale_expires_from_prior_tier(monkeypatch):
    """If a doc had an expires_at set when the user was on a lower
    tier and then upgrades to Business, the next touch must strip
    the stale value. Otherwise the sweeper could delete a Business-
    tier doc on the old (lower) deadline."""
    from backend import main as backend_main

    doc = _blank_doc()
    doc.metadata = {
        WORKSPACE_EXPIRES_AT_KEY: "2025-01-01T00:00:00+00:00",  # stale
    }
    monkeypatch.setattr(backend_main, "resolve_user_tier", lambda _u: "business")
    doc = _touch_document_workspace(doc, _user())
    assert WORKSPACE_EXPIRES_AT_KEY not in doc.metadata


# ─── sweeper deletes via FileStorage ──────────────────────────────────────


def test_sweeper_calls_file_storage_delete_on_expired_workspace(
    monkeypatch, tmp_path
):
    """Expired workspace → FileStorage.delete is called with both
    source_path and viewable_pdf_path. Supabase backend depends on
    this to clean bucket objects after pipeline.delete_workspace's
    local-unlink runs as a no-op for bucket keys."""
    from backend import maintenance

    monkeypatch.setenv("HELPMATE_DATA_DIR", str(tmp_path / "data"))

    # Build an expired DocumentRecord (one second in the past so we
    # don't fight test scheduling) with both source + viewable paths.
    past = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    expired_doc = DocumentRecord(
        document_id="doc-expired",
        file_name="ex.pdf",
        file_type="pdf",
        source_path="bucket/key/source.pdf",
        fingerprint="ff",
        char_count=0,
        page_count=1,
        metadata={WORKSPACE_EXPIRES_AT_KEY: past},
        viewable_pdf_path="bucket/key/viewable.pdf",
    )

    fake_store = MagicMock()
    fake_store.list_documents.return_value = [expired_doc]
    fake_store.get_index.return_value = None

    fake_pipeline = MagicMock()
    fake_storage = MagicMock()
    fake_trace_store = MagicMock()
    fake_trace_store.delete_expired.return_value = 0

    monkeypatch.setattr(maintenance, "build_api_record_store", lambda _s: fake_store)
    monkeypatch.setattr(maintenance, "build_file_storage", lambda _s: fake_storage)
    monkeypatch.setattr(maintenance, "build_run_trace_store", lambda _s: fake_trace_store)
    monkeypatch.setattr(maintenance, "HelpmatePipeline", lambda _s: fake_pipeline)

    summary = maintenance.sweep_local_workspace_storage()
    assert summary.expired_workspaces_deleted == 1

    # Pipeline.delete_workspace was called for the expired doc.
    fake_pipeline.delete_workspace.assert_called_once()
    # FileStorage.delete was called for BOTH paths.
    delete_keys = {call.args[0] for call in fake_storage.delete.call_args_list}
    assert delete_keys == {"bucket/key/source.pdf", "bucket/key/viewable.pdf"}
    # The metadata record was removed too.
    fake_store.delete_document.assert_called_once_with("doc-expired")


def test_sweeper_does_not_delete_unbounded_workspace(monkeypatch, tmp_path):
    """A Business-tier doc has no expires_at — sweeper must skip
    deletion regardless of age."""
    from backend import maintenance

    monkeypatch.setenv("HELPMATE_DATA_DIR", str(tmp_path / "data"))

    business_doc = DocumentRecord(
        document_id="doc-business",
        file_name="biz.pdf",
        file_type="pdf",
        source_path="bucket/key/biz.pdf",
        fingerprint="ff",
        char_count=0,
        page_count=1,
        metadata={},  # no expires_at — Business-tier touch removed it
        viewable_pdf_path=None,
    )

    fake_store = MagicMock()
    fake_store.list_documents.return_value = [business_doc]
    fake_store.get_index.return_value = None

    fake_pipeline = MagicMock()
    fake_storage = MagicMock()
    fake_trace_store = MagicMock()
    fake_trace_store.delete_expired.return_value = 0

    monkeypatch.setattr(maintenance, "build_api_record_store", lambda _s: fake_store)
    monkeypatch.setattr(maintenance, "build_file_storage", lambda _s: fake_storage)
    monkeypatch.setattr(maintenance, "build_run_trace_store", lambda _s: fake_trace_store)
    monkeypatch.setattr(maintenance, "HelpmatePipeline", lambda _s: fake_pipeline)

    summary = maintenance.sweep_local_workspace_storage()
    assert summary.expired_workspaces_deleted == 0
    fake_pipeline.delete_workspace.assert_not_called()
    fake_storage.delete.assert_not_called()
    fake_store.delete_document.assert_not_called()
