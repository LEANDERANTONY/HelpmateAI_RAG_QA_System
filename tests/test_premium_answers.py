"""Premium answers (gpt-5.5) opt-in + /workspace/quota endpoint.

Step 5 of the tier-enforcement series. Coverage:

  • check_premium_quota — unit tests for the two failure modes
    (tier doesn't support premium / quota exhausted) and the pass
    case.

  • /qa premium branch via TestClient:
      - free user with premium=true → 402 premium_unavailable, no
        pipeline call
      - pro user under both caps → 200, model_override=premium_model,
        BOTH counters increment
      - pro user at premium cap → 402 premium_quota_exhausted, no
        pipeline call
      - pro user at standard cap (premium fine) → 402
        question_quota_exhausted (premium counter NOT incremented)

  • /workspace/quota — shape + values match the tier and counter
    state. Read-only, no side effects.
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from backend.auth import AuthenticatedUser, require_authenticated_user
from backend.main import app
from backend.quota import UPGRADE_URL, check_premium_quota
from backend.quota_store import LocalQuotaStore
from backend.tiers import TIER_LIMITS
from src.config import Settings


FREE_Q_CAP = TIER_LIMITS["free"]["questions_per_month"]
PRO_Q_CAP = TIER_LIMITS["pro"]["questions_per_month"]
PRO_PREMIUM_CAP = TIER_LIMITS["pro"]["premium_answers_per_month"]


# ─── check_premium_quota ──────────────────────────────────────────────────


def test_premium_quota_rejects_free_with_premium_unavailable():
    """Free tier has premium_model=None, so any premium request is
    structurally invalid — return premium_unavailable regardless of
    counter state."""
    response = check_premium_quota(premium_used=0, tier="free")
    assert response is not None
    assert response.status_code == 402
    body = json.loads(response.body)
    assert body["code"] == "premium_unavailable"
    assert body["tier"] == "free"


def test_premium_quota_pro_under_cap_passes():
    response = check_premium_quota(premium_used=PRO_PREMIUM_CAP - 1, tier="pro")
    assert response is None


def test_premium_quota_pro_at_cap_returns_402_exhausted():
    response = check_premium_quota(premium_used=PRO_PREMIUM_CAP, tier="pro")
    assert response is not None
    assert response.status_code == 402
    body = json.loads(response.body)
    assert body["code"] == "premium_quota_exhausted"
    assert body["tier"] == "pro"
    assert body["limit"] == PRO_PREMIUM_CAP
    assert body["current"] == PRO_PREMIUM_CAP


def test_premium_quota_business_at_cap_returns_402():
    """Business has a higher cap but the same exhaustion path."""
    business_cap = TIER_LIMITS["business"]["premium_answers_per_month"]
    response = check_premium_quota(premium_used=business_cap, tier="business")
    assert response is not None
    assert response.status_code == 402
    body = json.loads(response.body)
    assert body["code"] == "premium_quota_exhausted"
    assert body["tier"] == "business"


# ─── /qa premium branch via TestClient ────────────────────────────────────


# Must be a syntactically valid UUID: the /workspace/quota path now scopes the
# document read with .eq("user_id", user.id) (H3), and the Supabase user_id
# column is a uuid, so a non-uuid placeholder is rejected by postgrest (22P02).
# Production user ids are always Supabase-auth uuids.
_TEST_USER_ID = "00000000-0000-4000-8000-000000000002"


@pytest.fixture
def authed_client():
    def fake_user() -> AuthenticatedUser:
        return AuthenticatedUser(id=_TEST_USER_ID, email="premium@example.com")

    app.dependency_overrides[require_authenticated_user] = fake_user
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(require_authenticated_user, None)


@pytest.fixture
def fake_pipeline_and_doc(monkeypatch):
    from backend import main as backend_main
    from src.schemas import AnswerResult, CacheStatus, DocumentRecord, IndexRecord

    fake_doc = DocumentRecord(
        document_id="doc-fake",
        file_name="fake.pdf",
        file_type="pdf",
        source_path="/tmp/fake.pdf",
        fingerprint="ff",
        char_count=0,
        page_count=1,
    )
    fake_index = IndexRecord(
        document_id="doc-fake",
        fingerprint="ff",
        collection_name="fake",
        storage_path="/tmp/fake-idx",
        chunk_count=1,
        section_count=1,
        embedding_model="fake",
        chunk_size=1,
        chunk_overlap=0,
        created_at="2026-01-01T00:00:00+00:00",
    )

    def make_answer(model_name: str) -> AnswerResult:
        return AnswerResult(
            question="q",
            answer="a",
            citations=[],
            evidence=[],
            cache_status=CacheStatus(),
            model_name=model_name,
        )

    monkeypatch.setattr(backend_main, "_require_document_for_user", lambda _id, _u: fake_doc)
    monkeypatch.setattr(backend_main, "_require_index", lambda _id: fake_index)
    monkeypatch.setattr(backend_main, "_save_touched_document", lambda doc, _u: doc)

    pipeline_mock = MagicMock()
    pipeline_mock.answer_question.side_effect = (
        lambda _doc, _idx, _q, *, model_override=None: make_answer(
            model_override or "default-from-settings"
        )
    )
    monkeypatch.setattr(backend_main, "_pipeline", lambda: pipeline_mock)
    return pipeline_mock


@pytest.fixture
def isolated_quota_store(monkeypatch, tmp_path):
    from backend import main as backend_main

    monkeypatch.setenv("HELPMATE_DATA_DIR", str(tmp_path / "data"))
    backend_main._quota_store.cache_clear()
    backend_main._settings.cache_clear()
    store = LocalQuotaStore(Settings())
    monkeypatch.setattr(backend_main, "_quota_store", lambda: store)
    return store


def test_qa_free_with_premium_returns_premium_unavailable(
    authed_client, fake_pipeline_and_doc, isolated_quota_store
):
    """Free user opting into premium → 402, no pipeline call. The
    backend never trusts the client flag — premium=true from a free
    user is always rejected regardless of counter state."""
    response = authed_client.post(
        "/qa",
        json={"document_id": "doc-fake", "question": "any", "premium": True},
    )
    assert response.status_code == 402
    body = response.json()
    assert body["code"] == "premium_unavailable"
    assert body["tier"] == "free"
    fake_pipeline_and_doc.answer_question.assert_not_called()
    # Counters untouched.
    counter = isolated_quota_store.get_counter(_TEST_USER_ID)
    assert counter.questions == 0
    assert counter.premium == 0


def test_qa_pro_with_premium_under_both_caps_increments_both_counters(
    authed_client, fake_pipeline_and_doc, isolated_quota_store, monkeypatch
):
    """Pro tier + premium=true + room in both caps → 200, premium_model
    used, BOTH counters tick (per spec: 'BOTH count toward standard AND
    charge a premium credit')."""
    from backend import main as backend_main

    monkeypatch.setattr(backend_main, "resolve_user_tier", lambda _u: "pro")

    response = authed_client.post(
        "/qa",
        json={"document_id": "doc-fake", "question": "any", "premium": True},
    )
    assert response.status_code == 200
    body = response.json()
    # Premium model from pro tier (gpt-5.5) was used.
    assert body["answer"]["model_name"] == TIER_LIMITS["pro"]["premium_model"]

    call = fake_pipeline_and_doc.answer_question.call_args
    assert call.kwargs["model_override"] == "gpt-5.5"

    counter = isolated_quota_store.get_counter(_TEST_USER_ID)
    assert counter.questions == 1
    assert counter.premium == 1


def test_qa_pro_with_premium_at_premium_cap_returns_exhausted(
    authed_client, fake_pipeline_and_doc, isolated_quota_store, monkeypatch
):
    """Pro user has burned through their 25 premium credits → 402, no
    pipeline call, NEITHER counter increments (pre-check fires before
    pipeline runs)."""
    from backend import main as backend_main

    monkeypatch.setattr(backend_main, "resolve_user_tier", lambda _u: "pro")
    for _ in range(PRO_PREMIUM_CAP):
        isolated_quota_store.increment_premium(_TEST_USER_ID)

    response = authed_client.post(
        "/qa",
        json={"document_id": "doc-fake", "question": "any", "premium": True},
    )
    assert response.status_code == 402
    body = response.json()
    assert body["code"] == "premium_quota_exhausted"
    assert body["tier"] == "pro"
    fake_pipeline_and_doc.answer_question.assert_not_called()
    # Premium counter unchanged at cap; question counter unchanged.
    counter = isolated_quota_store.get_counter(_TEST_USER_ID)
    assert counter.premium == PRO_PREMIUM_CAP
    assert counter.questions == 0


def test_qa_pro_with_premium_at_question_cap_returns_question_exhausted(
    authed_client, fake_pipeline_and_doc, isolated_quota_store, monkeypatch
):
    """Pro user has room for premium (premium counter low) but has
    exhausted the standard question quota. Standard check still fires
    second — gate by gate, premium passes, then question quota fails.
    Result: 402 question_quota_exhausted (NOT premium_quota_exhausted).
    Premium counter stays where it was (the premium pre-check passed
    but no increment happens because pipeline never ran)."""
    from backend import main as backend_main

    monkeypatch.setattr(backend_main, "resolve_user_tier", lambda _u: "pro")
    for _ in range(PRO_Q_CAP):
        isolated_quota_store.increment_questions(_TEST_USER_ID)

    response = authed_client.post(
        "/qa",
        json={"document_id": "doc-fake", "question": "any", "premium": True},
    )
    assert response.status_code == 402
    body = response.json()
    assert body["code"] == "question_quota_exhausted"
    fake_pipeline_and_doc.answer_question.assert_not_called()
    # Question counter unchanged at cap; premium counter unchanged.
    counter = isolated_quota_store.get_counter(_TEST_USER_ID)
    assert counter.questions == PRO_Q_CAP
    assert counter.premium == 0


def test_qa_premium_false_uses_default_model_no_premium_increment(
    authed_client, fake_pipeline_and_doc, isolated_quota_store, monkeypatch
):
    """Pro user without premium=true → standard answer_model, ONLY
    question counter increments. Premium counter is untouched."""
    from backend import main as backend_main

    monkeypatch.setattr(backend_main, "resolve_user_tier", lambda _u: "pro")

    response = authed_client.post(
        "/qa",
        json={"document_id": "doc-fake", "question": "any"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["answer"]["model_name"] == TIER_LIMITS["pro"]["answer_model"]

    counter = isolated_quota_store.get_counter(_TEST_USER_ID)
    assert counter.questions == 1
    assert counter.premium == 0


# ─── /workspace/quota endpoint ────────────────────────────────────────────


def test_workspace_quota_free_user_shape(authed_client, isolated_quota_store):
    """Free user with no usage: tier=free, premium_available=False,
    upgrade_url present, all counters at 0."""
    response = authed_client.get("/workspace/quota")
    assert response.status_code == 200
    body = response.json()
    assert body["tier"] == "free"
    assert body["premium_available"] is False
    assert body["questions"]["used"] == 0
    assert body["questions"]["limit"] == FREE_Q_CAP
    assert body["premium"]["used"] == 0
    assert body["premium"]["limit"] == 0  # free has no premium credits
    assert body["documents"]["used"] == 0
    assert body["documents"]["limit"] == TIER_LIMITS["free"]["doc_cap"]
    assert body["upgrade_url"] == UPGRADE_URL
    # period_start is a parseable ISO date.
    from datetime import date
    parsed = date.fromisoformat(body["period_start"])
    assert parsed.day == 1


def test_workspace_quota_pro_user_reports_premium_available(
    authed_client, isolated_quota_store, monkeypatch
):
    from backend import main as backend_main

    monkeypatch.setattr(backend_main, "resolve_user_tier", lambda _u: "pro")
    isolated_quota_store.increment_questions(_TEST_USER_ID)
    isolated_quota_store.increment_premium(_TEST_USER_ID)
    isolated_quota_store.increment_premium(_TEST_USER_ID)

    response = authed_client.get("/workspace/quota")
    assert response.status_code == 200
    body = response.json()
    assert body["tier"] == "pro"
    assert body["premium_available"] is True
    assert body["questions"]["used"] == 1
    assert body["questions"]["limit"] == PRO_Q_CAP
    assert body["premium"]["used"] == 2
    assert body["premium"]["limit"] == PRO_PREMIUM_CAP


def test_workspace_quota_no_side_effects(authed_client, isolated_quota_store):
    """GET /workspace/quota must not change any counter. Smoke-test by
    asserting both counters stay at 0 after the call."""
    isolated_quota_store.get_counter(_TEST_USER_ID)  # baseline read
    authed_client.get("/workspace/quota")
    counter = isolated_quota_store.get_counter(_TEST_USER_ID)
    assert counter.questions == 0
    assert counter.premium == 0
