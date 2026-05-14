"""Monthly question quota — gate, store, and /qa integration.

Step 3 of the tier-enforcement series. Three layers of coverage:

  • Unit tests on `check_question_quota` (pure function, no I/O).
  • Unit tests on `LocalQuotaStore` — read/write/atomic increment
    semantics, including the month rollover behavior (the
    period_start key changes when the calendar month flips).
  • Integration test on /qa via TestClient that exercises:
      - 50th question succeeds (200) — increment happens
      - 51st question fails (402) — pipeline NOT called
      - Pipeline failure does NOT consume quota
"""
from __future__ import annotations

import json
from datetime import date, datetime, timezone
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from backend.auth import AuthenticatedUser, require_authenticated_user
from backend.main import app
from backend.quota import UPGRADE_URL, check_question_quota
from backend.quota_store import (
    LocalQuotaStore,
    QuotaCounter,
    current_period_start,
)
from backend.tiers import TIER_LIMITS


FREE_Q_CAP = TIER_LIMITS["free"]["questions_per_month"]


# ─── check_question_quota (pure) ────────────────────────────────────────


def test_question_quota_under_cap_passes():
    response = check_question_quota(questions_used=FREE_Q_CAP - 1, tier="free")
    assert response is None


def test_question_quota_one_below_cap_passes():
    """At cap - 1, the NEXT question would land at cap — still allowed
    because cap means "up to and including this many"."""
    response = check_question_quota(questions_used=FREE_Q_CAP - 1, tier="free")
    assert response is None


def test_question_quota_at_cap_returns_402():
    """At cap, no more questions allowed (the next would push past).
    Brief's "free user at 50: 51st returns 402" depends on this."""
    response = check_question_quota(questions_used=FREE_Q_CAP, tier="free")
    assert response is not None
    assert response.status_code == 402
    body = json.loads(response.body)
    assert body["code"] == "question_quota_exhausted"
    assert body["tier"] == "free"
    assert body["limit"] == FREE_Q_CAP
    assert body["current"] == FREE_Q_CAP
    assert body["upgrade_url"] == UPGRADE_URL


def test_question_quota_over_cap_returns_402():
    """Defensive: if we somehow drift past the cap (race won at the
    Supabase level), the next call still rejects."""
    response = check_question_quota(questions_used=FREE_Q_CAP + 5, tier="free")
    assert response is not None
    assert response.status_code == 402


# ─── LocalQuotaStore (read / write / atomic increment) ───────────────────


@pytest.fixture
def local_store(monkeypatch, tmp_path):
    """Fresh LocalQuotaStore rooted at a per-test tmp dir, so two tests
    in the same session don't bleed counters between each other."""
    from src.config import Settings

    monkeypatch.setenv("HELPMATE_DATA_DIR", str(tmp_path / "data"))
    settings = Settings()
    return LocalQuotaStore(settings)


def test_local_store_empty_user_returns_zero(local_store):
    counter = local_store.get_counter("user-a")
    assert counter == QuotaCounter(questions=0, premium=0)


def test_local_store_increment_questions_returns_new_count(local_store):
    assert local_store.increment_questions("user-a") == 1
    assert local_store.increment_questions("user-a") == 2
    assert local_store.increment_questions("user-a") == 3


def test_local_store_increment_isolated_per_user(local_store):
    local_store.increment_questions("user-a")
    local_store.increment_questions("user-a")
    local_store.increment_questions("user-b")
    assert local_store.get_counter("user-a").questions == 2
    assert local_store.get_counter("user-b").questions == 1


def test_local_store_increment_premium_independent_of_questions(local_store):
    """Premium and questions live on the same row but increment
    independently. Step 5 wires premium; the store handles both fields
    from day one."""
    local_store.increment_questions("user-a")
    local_store.increment_premium("user-a")
    local_store.increment_premium("user-a")
    counter = local_store.get_counter("user-a")
    assert counter.questions == 1
    assert counter.premium == 2


def test_current_period_start_is_first_of_month_utc():
    period = current_period_start(now=datetime(2026, 5, 15, 23, 30, tzinfo=timezone.utc))
    assert period == date(2026, 5, 1)


def test_local_store_month_rollover_creates_new_period(local_store, monkeypatch):
    """Brief's "month rollover: a new period_start row is created on
    Nov 1". The store auto-rolls because get_counter / increment use
    current_period_start() at call time."""
    from backend import quota_store as qs

    # Inject a controllable "now" that we'll bump forward.
    fake_now = datetime(2026, 5, 28, tzinfo=timezone.utc)
    monkeypatch.setattr(
        qs,
        "current_period_start",
        lambda now=None: qs.current_period_start.__wrapped__(now)
        if hasattr(qs.current_period_start, "__wrapped__")
        else fake_now.date().replace(day=1),
    )
    local_store.increment_questions("user-a")
    assert local_store.get_counter("user-a").questions == 1

    # Roll the clock to June 1.
    fake_now = datetime(2026, 6, 1, 0, 1, tzinfo=timezone.utc)
    monkeypatch.setattr(
        qs,
        "current_period_start",
        lambda now=None: fake_now.date().replace(day=1),
    )
    # New period — counter starts at 0 + 1 from this increment.
    assert local_store.increment_questions("user-a") == 1
    # And the old period's row still has its 1.
    raw = json.loads(local_store._path.read_text(encoding="utf-8"))
    assert raw["user-a"]["2026-05-01"]["questions"] == 1
    assert raw["user-a"]["2026-06-01"]["questions"] == 1


# ─── /qa integration via TestClient ──────────────────────────────────────


_TEST_USER_ID = "00000000-0000-4000-8000-test-question-quota"


@pytest.fixture
def authed_client():
    def fake_user() -> AuthenticatedUser:
        return AuthenticatedUser(id=_TEST_USER_ID, email="qa@example.com")

    app.dependency_overrides[require_authenticated_user] = fake_user
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(require_authenticated_user, None)


@pytest.fixture
def fake_pipeline_and_doc(monkeypatch):
    """Replace _require_document_for_user, _require_index, and _pipeline
    with stubs so /qa runs without touching Supabase or OpenAI. The
    stubs cover the happy path — pipeline-failure tests override the
    `answer_question` mock to raise.
    """
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
    fake_answer = AnswerResult(
        question="q",
        answer="a",
        citations=[],
        evidence=[],
        cache_status=CacheStatus(),
    )

    monkeypatch.setattr(backend_main, "_require_document_for_user", lambda _id, _u: fake_doc)
    monkeypatch.setattr(backend_main, "_require_index", lambda _id: fake_index)
    monkeypatch.setattr(backend_main, "_save_touched_document", lambda doc, _u: doc)

    pipeline_mock = MagicMock()
    pipeline_mock.answer_question.return_value = fake_answer
    monkeypatch.setattr(backend_main, "_pipeline", lambda: pipeline_mock)
    return pipeline_mock


@pytest.fixture
def isolated_quota_store(monkeypatch, tmp_path):
    """Per-test quota store at a fresh tmp dir. backend.main caches the
    store via lru_cache — we clear that cache so the override sticks."""
    from backend import main as backend_main
    from src.config import Settings

    monkeypatch.setenv("HELPMATE_DATA_DIR", str(tmp_path / "data"))
    backend_main._quota_store.cache_clear()
    backend_main._settings.cache_clear()
    store = LocalQuotaStore(Settings())
    monkeypatch.setattr(backend_main, "_quota_store", lambda: store)
    return store


def test_qa_at_cap_returns_402_without_running_pipeline(
    authed_client, fake_pipeline_and_doc, isolated_quota_store
):
    # Seed the user at the free-tier cap.
    for _ in range(FREE_Q_CAP):
        isolated_quota_store.increment_questions(_TEST_USER_ID)
    assert isolated_quota_store.get_counter(_TEST_USER_ID).questions == FREE_Q_CAP

    response = authed_client.post(
        "/qa",
        json={"document_id": "doc-fake", "question": "anything"},
    )
    assert response.status_code == 402
    body = response.json()
    assert body["code"] == "question_quota_exhausted"
    assert body["tier"] == "free"
    assert body["limit"] == FREE_Q_CAP
    assert body["current"] == FREE_Q_CAP
    # Pipeline must NOT have been called — the gate fires first.
    fake_pipeline_and_doc.answer_question.assert_not_called()


def test_qa_succeeds_at_cap_minus_one_and_increments(
    authed_client, fake_pipeline_and_doc, isolated_quota_store
):
    # Seed at cap-1 — the next question is the last allowed (the 50th).
    for _ in range(FREE_Q_CAP - 1):
        isolated_quota_store.increment_questions(_TEST_USER_ID)

    response = authed_client.post(
        "/qa",
        json={"document_id": "doc-fake", "question": "last allowed"},
    )
    assert response.status_code == 200
    # Counter now at cap — next call rejects.
    assert isolated_quota_store.get_counter(_TEST_USER_ID).questions == FREE_Q_CAP
    fake_pipeline_and_doc.answer_question.assert_called_once()


def test_qa_pipeline_failure_does_not_consume_quota(
    fake_pipeline_and_doc, isolated_quota_store
):
    """Brief: 'DO NOT increment if the answer pipeline fails.' Pipeline
    raises → no increment → user can retry without losing a question.

    Uses raise_server_exceptions=False so TestClient returns the
    converted-to-HTTP 500 response instead of propagating the raw
    RuntimeError up through the test runner. The counter assertion is
    what we actually care about — the status code is incidental.
    """

    def fake_user() -> AuthenticatedUser:
        return AuthenticatedUser(id=_TEST_USER_ID, email="qa@example.com")

    app.dependency_overrides[require_authenticated_user] = fake_user
    try:
        client = TestClient(app, raise_server_exceptions=False)
        isolated_quota_store.increment_questions(_TEST_USER_ID)  # 1 used
        fake_pipeline_and_doc.answer_question.side_effect = RuntimeError("boom")
        response = client.post(
            "/qa",
            json={"document_id": "doc-fake", "question": "will fail"},
        )
    finally:
        app.dependency_overrides.pop(require_authenticated_user, None)

    # FastAPI converts unhandled exceptions to 500. The important
    # invariant is that the counter is still 1 — the increment that
    # would have happened on success never ran.
    assert response.status_code == 500
    assert isolated_quota_store.get_counter(_TEST_USER_ID).questions == 1
