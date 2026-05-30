"""Cross-user document authorization — the REAL `_require_document_for_user` guard.

H9 / audit a12-1: every existing test that touches a document-scoped route
monkeypatches `_require_document_for_user` to an unconditional stub
(`lambda _id, _u: fake_doc`), so the authenticated-but-not-owner -> 404 denial
branch had ZERO unit or integration coverage. Because the backend runs as the
Supabase service-role key (RLS bypassed), this app-level guard is the *real*
multi-tenant isolation boundary — a silent refactor that flipped or skipped the
`owner_id != user.id` check would leak another user's documents (and, via
`/documents/{id}/file`, their raw PDF bytes) while the whole suite stayed green.

These tests deliberately do NOT stub the guard. They seed a real document in a
per-test LocalApiRecordStore and assert:
  * the owner is allowed through (unit + HTTP 200),
  * a non-owner is denied with 404 (unit + HTTP 404),
  * a legacy record with no owner stamp fails closed (404).
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from backend.auth import AuthenticatedUser, require_authenticated_user
from backend.main import app
from backend.store import WORKSPACE_OWNER_KEY
from src.schemas import DocumentRecord


OWNER_ID = "00000000-0000-4000-8000-owneraaaaaaa"
OTHER_ID = "00000000-0000-4000-8000-otherbbbbbbb"


def _make_document(document_id: str, owner_id: str | None) -> DocumentRecord:
    metadata: dict[str, str] = {}
    if owner_id is not None:
        metadata[WORKSPACE_OWNER_KEY] = owner_id
    return DocumentRecord(
        document_id=document_id,
        file_name="owned.pdf",
        file_type="pdf",
        source_path="/tmp/owned.pdf",
        fingerprint="ffowned",
        char_count=10,
        page_count=1,
        metadata=metadata,
    )


@pytest.fixture
def seeded_store(monkeypatch, tmp_path):
    """A fresh LocalApiRecordStore rooted at a per-test tmp dir, wired into
    backend.main's lru_cached `_store()`/`_settings()` so both the route
    handlers and the guard read the same seeded data. Local backend is forced
    so the test never reaches Supabase regardless of ambient env."""
    from backend import main as backend_main

    monkeypatch.setenv("HELPMATE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("HELPMATE_STATE_STORE_BACKEND", raising=False)
    backend_main._settings.cache_clear()
    backend_main._store.cache_clear()
    store = backend_main._store()
    yield store
    backend_main._settings.cache_clear()
    backend_main._store.cache_clear()


# ── unit: the guard function itself ──────────────────────────────────────


def test_owner_can_access_their_document(seeded_store):
    from backend import main as backend_main

    seeded_store.save_document(_make_document("doc-owned", OWNER_ID))
    owner = AuthenticatedUser(id=OWNER_ID, email="owner@example.com")
    document = backend_main._require_document_for_user("doc-owned", owner)
    assert document.document_id == "doc-owned"


def test_non_owner_is_denied_404(seeded_store):
    from backend import main as backend_main

    seeded_store.save_document(_make_document("doc-owned", OWNER_ID))
    other = AuthenticatedUser(id=OTHER_ID, email="other@example.com")
    with pytest.raises(HTTPException) as exc:
        backend_main._require_document_for_user("doc-owned", other)
    assert exc.value.status_code == 404


def test_legacy_document_without_owner_fails_closed_404(seeded_store):
    """A pre-ownership record (no _workspace_owner_user_id) must deny everyone:
    `_document_owner_id` returns None, and `None != user.id` -> 404."""
    from backend import main as backend_main

    seeded_store.save_document(_make_document("doc-legacy", None))
    someone = AuthenticatedUser(id=OWNER_ID, email="owner@example.com")
    with pytest.raises(HTTPException) as exc:
        backend_main._require_document_for_user("doc-legacy", someone)
    assert exc.value.status_code == 404


# ── integration: a real HTTP request through the UN-stubbed guard ─────────


def test_get_document_as_non_owner_returns_404(seeded_store):
    seeded_store.save_document(_make_document("doc-owned", OWNER_ID))

    def other_user() -> AuthenticatedUser:
        return AuthenticatedUser(id=OTHER_ID, email="other@example.com")

    app.dependency_overrides[require_authenticated_user] = other_user
    try:
        client = TestClient(app)
        response = client.get("/documents/doc-owned")
    finally:
        app.dependency_overrides.pop(require_authenticated_user, None)
    assert response.status_code == 404


def test_get_document_as_owner_returns_200(seeded_store):
    seeded_store.save_document(_make_document("doc-owned", OWNER_ID))

    def owner_user() -> AuthenticatedUser:
        return AuthenticatedUser(id=OWNER_ID, email="owner@example.com")

    app.dependency_overrides[require_authenticated_user] = owner_user
    try:
        client = TestClient(app)
        response = client.get("/documents/doc-owned")
    finally:
        app.dependency_overrides.pop(require_authenticated_user, None)
    assert response.status_code == 200
    assert response.json()["document"]["document_id"] == "doc-owned"
