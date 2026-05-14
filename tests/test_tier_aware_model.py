"""Tier-aware answer model selection.

Step 4 of the tier-enforcement series. Three layers of coverage:

  • Unit tests on AnswerGenerator.generate's `model_override` param —
    verify the OpenAI client is called with the override (when set) or
    settings.answer_model (when not).

  • Unit tests on HelpmatePipeline cache-key composition — different
    overrides must produce different keys so free + pro don't share
    cached answers.

  • Integration test on /qa via TestClient that asserts the model name
    on the AnswerResult matches the tier's configured model.
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from backend.auth import AuthenticatedUser, require_authenticated_user
from backend.main import app
from backend.quota_store import LocalQuotaStore
from backend.tiers import TIER_LIMITS
from src.config import Settings
from src.generation.service import AnswerGenerator
from src.pipeline.service import HelpmatePipeline
from src.schemas import RetrievalCandidate, RetrievalResult


# Reuse the fake-OpenAI client shape from test_generation_service.py
class _FakeMessage:
    def __init__(self, content: str):
        self.content = content


class _FakeChoice:
    def __init__(self, content: str):
        self.message = _FakeMessage(content)


class _FakeResponse:
    def __init__(self, content: str):
        self.choices = [_FakeChoice(content)]


class _FakeCompletions:
    """Captures `model=` on each create() call so tests can assert
    which model the generator picked."""

    def __init__(self, content: str):
        self._content = content
        self.calls: list[dict] = []

    def create(self, **kwargs: object):
        self.calls.append(dict(kwargs))
        return _FakeResponse(self._content)


class _FakeChat:
    def __init__(self, content: str):
        self.completions = _FakeCompletions(content)


class _FakeClient:
    def __init__(self, content: str):
        self.chat = _FakeChat(content)


def _supported_payload() -> str:
    """Minimal JSON the generator will accept as a successful answer
    so we exercise the model_name path rather than the fallback."""
    return json.dumps(
        {
            "supported": True,
            "support_status": "supported",
            "support_summary": "Direct extract from the source",
            "answer": "The waiting period is thirty days.",
            "reason": None,
        }
    )


def _build_retrieval() -> RetrievalResult:
    return RetrievalResult(
        question="What is the waiting period?",
        candidates=[
            RetrievalCandidate(
                chunk_id="c1",
                text="The waiting period is thirty days from the policy effective date.",
                metadata={"page_label": "Page 4"},
                dense_score=0.9,
                lexical_score=0.5,
                fused_score=0.8,
                citation_label="Page 4",
            ),
        ],
        evidence_status="strong",
        best_score=0.8,
        max_lexical_score=0.5,
    )


# ─── AnswerGenerator.generate respects model_override ────────────────────


def test_generator_uses_settings_model_when_no_override():
    settings = Settings(openai_api_key="sk-test", answer_model="gpt-5.4-mini")
    generator = AnswerGenerator(settings)
    fake = _FakeClient(_supported_payload())
    generator.client = fake

    answer = generator.generate("What is the waiting period?", _build_retrieval())

    assert len(fake.chat.completions.calls) == 1
    assert fake.chat.completions.calls[0]["model"] == "gpt-5.4-mini"
    assert answer.model_name == "gpt-5.4-mini"


def test_generator_uses_override_when_provided():
    """The override path: regardless of settings.answer_model, the call
    site can force a specific model for this request. Used by /qa to
    pick per tier."""
    settings = Settings(openai_api_key="sk-test", answer_model="gpt-5.4-mini")
    generator = AnswerGenerator(settings)
    fake = _FakeClient(_supported_payload())
    generator.client = fake

    answer = generator.generate(
        "What is the waiting period?",
        _build_retrieval(),
        model_override="gpt-5.4-nano",
    )

    assert fake.chat.completions.calls[0]["model"] == "gpt-5.4-nano"
    # model_name on the result reflects the model actually used —
    # caches, audit logs, and telemetry all key off this.
    assert answer.model_name == "gpt-5.4-nano"


def test_generator_override_none_falls_back_to_settings():
    """Passing None for model_override should be indistinguishable
    from not passing it at all. Eval scripts and other non-tiered
    contexts rely on this fallback."""
    settings = Settings(openai_api_key="sk-test", answer_model="gpt-5.4-mini")
    generator = AnswerGenerator(settings)
    fake = _FakeClient(_supported_payload())
    generator.client = fake

    generator.generate("Q", _build_retrieval(), model_override=None)

    assert fake.chat.completions.calls[0]["model"] == "gpt-5.4-mini"


# ─── HelpmatePipeline cache key changes with model ────────────────────────


def test_pipeline_cache_key_differs_by_model_override(monkeypatch, tmp_path):
    """Free + pro asking the same question must NOT share a cached
    answer — different tiers get different models, and the model_name
    is part of the cache key. This test verifies the cache_key
    composition includes the active model."""
    monkeypatch.setenv("HELPMATE_DATA_DIR", str(tmp_path / "data"))
    settings = Settings(openai_api_key=None, answer_model="gpt-5.4-mini")
    pipeline = HelpmatePipeline(settings)

    free_key = pipeline.answer_cache.build_key(
        fingerprint="ff",
        question="What is the waiting period?",
        retrieval_version=settings.retrieval_version,
        generation_version=settings.generation_version,
        model_name="gpt-5.4-nano",
    )
    pro_key = pipeline.answer_cache.build_key(
        fingerprint="ff",
        question="What is the waiting period?",
        retrieval_version=settings.retrieval_version,
        generation_version=settings.generation_version,
        model_name="gpt-5.4-mini",
    )
    assert free_key != pro_key


# ─── /qa integration: tier picks the model ───────────────────────────────


_TEST_USER_ID = "00000000-0000-4000-8000-test-tier-aware-model"


@pytest.fixture
def authed_client():
    def fake_user() -> AuthenticatedUser:
        return AuthenticatedUser(id=_TEST_USER_ID, email="tier@example.com")

    app.dependency_overrides[require_authenticated_user] = fake_user
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(require_authenticated_user, None)


@pytest.fixture
def fake_pipeline_and_doc(monkeypatch):
    """Same fixture pattern as test_question_quota.py — replace
    document lookups + pipeline so /qa runs without OpenAI/Chroma.
    The pipeline mock records its calls so we can assert what
    model_override was passed in."""
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
    # Echo the model_override back so the test can read it from
    # AnswerResult.model_name — proves the parameter reached
    # the pipeline call.
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


def test_qa_free_tier_passes_nano_as_model_override(
    authed_client, fake_pipeline_and_doc, isolated_quota_store
):
    """The default tier resolver returns 'free'; free's answer_model
    is gpt-5.4-nano per TIER_LIMITS. /qa must pass that down."""
    response = authed_client.post(
        "/qa",
        json={"document_id": "doc-fake", "question": "anything"},
    )
    assert response.status_code == 200
    body = response.json()
    # The fixture's pipeline mock echoes model_override into
    # answer.model_name. If model_override wasn't passed, we'd
    # see "default-from-settings" instead.
    assert body["answer"]["model_name"] == TIER_LIMITS["free"]["answer_model"]
    assert body["answer"]["model_name"] == "gpt-5.4-nano"

    # Verify directly on the mock call too, as a belt-and-braces
    # check against the echo path.
    call = fake_pipeline_and_doc.answer_question.call_args
    assert call.kwargs["model_override"] == "gpt-5.4-nano"


def test_qa_pro_tier_passes_mini_as_model_override(
    authed_client, fake_pipeline_and_doc, isolated_quota_store, monkeypatch
):
    """Monkey-patch the tier resolver to return 'pro' so we can
    exercise the paid-tier path before payment integration ships."""
    from backend import main as backend_main

    monkeypatch.setattr(backend_main, "resolve_user_tier", lambda _u: "pro")

    response = authed_client.post(
        "/qa",
        json={"document_id": "doc-fake", "question": "anything"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["answer"]["model_name"] == TIER_LIMITS["pro"]["answer_model"]
    assert body["answer"]["model_name"] == "gpt-5.4-mini"

    call = fake_pipeline_and_doc.answer_question.call_args
    assert call.kwargs["model_override"] == "gpt-5.4-mini"
