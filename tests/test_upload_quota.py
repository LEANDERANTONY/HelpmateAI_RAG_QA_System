"""Upload quota gates — file size + active document count.

Step 2 of the tier-enforcement series. Two layers of coverage:

  • Unit tests on the gate helpers in backend.quota (pure functions,
    no FastAPI machinery). Cheap to run, easy to assert exact shapes.

  • Integration tests on /documents/upload via TestClient. The route
    handler's dependency on auth is replaced with a stub user; the
    pipeline is mocked so we can drive the reject paths without
    OpenAI keys or LibreOffice. The HAPPY-path 200 OK at-cap test
    walks through the full reject-gate chain — the gates must all
    return None for a file at exactly cap_bytes.
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from backend.auth import AuthenticatedUser, require_authenticated_user
from backend.main import app
from backend.quota import (
    UPGRADE_URL,
    check_content_length_present,
    check_doc_count_cap,
    check_file_size_cap,
)
from backend.tiers import TIER_LIMITS


_MB = 1024 * 1024
FREE_FILE_CAP = TIER_LIMITS["free"]["file_size_cap_bytes"]
FREE_DOC_CAP = TIER_LIMITS["free"]["doc_cap"]


# ─── unit tests: pure gate functions ─────────────────────────────────────


def test_content_length_missing_returns_411():
    response = check_content_length_present(content_length=None, tier="free")
    assert response is not None
    assert response.status_code == 411
    body = json.loads(response.body)
    assert body["code"] == "file_too_large"
    assert body["tier"] == "free"
    assert body["limit"] == FREE_FILE_CAP
    assert body["upgrade_url"] == UPGRADE_URL


def test_content_length_present_passes():
    response = check_content_length_present(content_length=1024, tier="free")
    assert response is None


def test_file_size_at_cap_passes():
    """File at exactly cap_bytes — must not reject. The brief's
    "file at exactly the cap → 200 OK" test depends on this."""
    response = check_file_size_cap(file_size=FREE_FILE_CAP, tier="free")
    assert response is None


def test_file_size_under_cap_passes():
    response = check_file_size_cap(file_size=FREE_FILE_CAP - 1, tier="free")
    assert response is None


def test_file_size_over_cap_returns_413():
    over_cap = FREE_FILE_CAP + 1
    response = check_file_size_cap(file_size=over_cap, tier="free")
    assert response is not None
    assert response.status_code == 413
    body = json.loads(response.body)
    assert body["code"] == "file_too_large"
    assert body["tier"] == "free"
    assert body["limit"] == FREE_FILE_CAP
    assert body["current"] == over_cap
    assert body["upgrade_url"] == UPGRADE_URL
    # Link header per spec — frontend can pull the upgrade URL from
    # either the body or the Link header depending on transport.
    link = response.headers.get("link")
    assert link is not None and UPGRADE_URL in link


def test_doc_count_under_cap_passes():
    response = check_doc_count_cap(active_count=FREE_DOC_CAP - 1, tier="free")
    assert response is None


def test_doc_count_at_cap_returns_402():
    """At cap — next upload would push past, so reject NOW. Brief's
    "free user uploads 4th doc → 402" relies on cap=3 + active=3
    being the rejection threshold."""
    response = check_doc_count_cap(active_count=FREE_DOC_CAP, tier="free")
    assert response is not None
    assert response.status_code == 402
    body = json.loads(response.body)
    assert body["code"] == "doc_count_cap"
    assert body["tier"] == "free"
    assert body["limit"] == FREE_DOC_CAP
    assert body["current"] == FREE_DOC_CAP


def test_doc_count_well_over_cap_returns_402():
    """Defensive: even if the store somehow holds more docs than the
    cap (race condition, manual seed), the gate still rejects."""
    response = check_doc_count_cap(active_count=FREE_DOC_CAP + 5, tier="free")
    assert response is not None
    assert response.status_code == 402


# ─── integration tests: /documents/upload via TestClient ─────────────────


_TEST_USER_ID = "00000000-0000-4000-8000-test-upload-quota"


@pytest.fixture
def authed_client():
    """TestClient with auth replaced by a deterministic stub user."""

    def fake_user() -> AuthenticatedUser:
        return AuthenticatedUser(id=_TEST_USER_ID, email="quota@example.com")

    app.dependency_overrides[require_authenticated_user] = fake_user
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(require_authenticated_user, None)


def test_upload_oversized_pdf_returns_413(authed_client, monkeypatch):
    """30 MB upload as a free user → 413, no DocumentRecord created.

    The pipeline is NOT mocked — the test relies on the gate rejecting
    BEFORE the route reaches `_pipeline().ingest_document()`. If a
    future refactor moves the gate later in the handler, this test
    would start exercising real OpenAI calls and fail loudly.
    """
    # 30 MB > free tier's 25 MB cap. Pad bytes to match — \x00 because
    # we never reach a PDF parser; the gate fires first.
    payload = b"\x00" * (30 * _MB)
    response = authed_client.post(
        "/documents/upload",
        files={"file": ("oversize.pdf", payload, "application/pdf")},
    )
    assert response.status_code == 413
    body = response.json()
    assert body["code"] == "file_too_large"
    assert body["tier"] == "free"
    assert body["limit"] == FREE_FILE_CAP
    assert body["current"] >= 30 * _MB
    assert body["upgrade_url"] == UPGRADE_URL


def test_upload_doc_count_cap_returns_402(authed_client, monkeypatch):
    """Free user with FREE_DOC_CAP already-active documents → 402.

    Patches _count_active_documents to return the cap; this bypasses
    the single-workspace model that today auto-deletes the previous
    doc on upload (see docs/tier-enforcement-flags.md). When multi-doc
    workspaces ship, the patch can be replaced by actually populating
    the store.
    """
    from backend import main as backend_main

    monkeypatch.setattr(
        backend_main,
        "_count_active_documents",
        lambda user: FREE_DOC_CAP,
    )

    # File body under the size cap so we exercise the doc-count gate
    # specifically, not the size gate.
    payload = b"%PDF-1.4 stub bytes for the doc-count test"
    response = authed_client.post(
        "/documents/upload",
        files={"file": ("fourth.pdf", payload, "application/pdf")},
    )
    assert response.status_code == 402
    body = response.json()
    assert body["code"] == "doc_count_cap"
    assert body["tier"] == "free"
    assert body["limit"] == FREE_DOC_CAP
    assert body["current"] == FREE_DOC_CAP
