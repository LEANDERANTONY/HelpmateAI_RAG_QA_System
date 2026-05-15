"""Tests for the /transcribe route — Whisper-backed voice input.

Covers six high-value paths:

  • Happy path returns text + duration from a mocked Whisper response.
  • Auth required (401 without bearer).
  • Empty body returns 400 (no Whisper call).
  • Unsupported content-type returns 400 (no Whisper call).
  • Over-cap audio returns 413 (no Whisper call).
  • Upstream Whisper failure surfaces as 502 (mapped, not raw).
  • Missing OPENAI_API_KEY surfaces as 503 (route doesn't crash).

The OpenAI client is monkeypatched at import time so the route runs
end-to-end without touching the network. Auth is overridden via the
existing dependency-override pattern used by test_question_quota.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from backend.auth import AuthenticatedUser, require_authenticated_user
from backend.main import app


_TEST_USER_ID = "11111111-2222-3333-4444-555555555555"


def _fake_user() -> AuthenticatedUser:
    return AuthenticatedUser(id=_TEST_USER_ID, email="voice@example.com")


@pytest.fixture
def authed_client():
    """Client with require_authenticated_user pinned to a fake user.

    Mirrors the pattern in tests/test_question_quota.py — the dep
    override stays scoped to the fixture so a parallel test that
    relies on the real auth dep doesn't see this leak through.
    """
    app.dependency_overrides[require_authenticated_user] = _fake_user
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(require_authenticated_user, None)


@pytest.fixture
def fake_openai_client(monkeypatch):
    """Patch ``openai.OpenAI`` to return a stub client whose
    ``audio.transcriptions.create`` is a MagicMock we can configure
    per-test. The route does ``from openai import OpenAI`` inside the
    function body, so we patch the symbol on the module object.
    """
    import openai

    stub_response = SimpleNamespace(text="What clauses cover stress?", duration=3.5)

    create_mock = MagicMock(return_value=stub_response)
    client_instance = SimpleNamespace(
        audio=SimpleNamespace(
            transcriptions=SimpleNamespace(create=create_mock),
        )
    )

    def _fake_openai(api_key: str | None = None):
        return client_instance

    monkeypatch.setattr(openai, "OpenAI", _fake_openai)
    return SimpleNamespace(create=create_mock, response=stub_response)


@pytest.fixture
def with_api_key(monkeypatch):
    """Force settings.openai_api_key on so the 503-no-key branch
    doesn't fire.

    ``Settings.openai_api_key`` is a class-level default captured at
    import time, so a plain ``monkeypatch.setenv`` doesn't propagate
    into a freshly-constructed Settings instance. We instead override
    the ``_settings()`` helper on backend.main to return a Settings
    object with the key pre-populated.
    """
    from backend import main as backend_main
    from src.config import Settings

    original = backend_main._settings
    backend_main._settings.cache_clear()

    overridden = Settings()
    overridden.openai_api_key = "test-key"

    def _override_settings():
        return overridden

    monkeypatch.setattr(backend_main, "_settings", _override_settings)
    yield
    backend_main._settings = original
    backend_main._settings.cache_clear()


def _audio_file(content: bytes = b"FAKE_WEBM_BYTES", content_type: str = "audio/webm") -> dict:
    """Build the multipart payload `files=` kwarg for TestClient."""
    return {"file": ("clip.webm", content, content_type)}


# ─── happy path ──────────────────────────────────────────────────────────


def test_transcribe_returns_text_and_duration(authed_client, fake_openai_client, with_api_key):
    response = authed_client.post("/transcribe", files=_audio_file())
    assert response.status_code == 200
    body = response.json()
    assert body["text"] == "What clauses cover stress?"
    assert body["duration_seconds"] == pytest.approx(3.5)
    fake_openai_client.create.assert_called_once()
    call_kwargs = fake_openai_client.create.call_args.kwargs
    assert call_kwargs["model"] == "whisper-1"


# ─── auth ────────────────────────────────────────────────────────────────


def test_transcribe_without_auth_returns_401(with_api_key):
    # No dependency override — the real require_authenticated_user runs
    # and rejects without a bearer token.
    client = TestClient(app)
    response = client.post("/transcribe", files=_audio_file())
    assert response.status_code == 401


# ─── validation: body, type, size ────────────────────────────────────────


def test_transcribe_empty_body_returns_400(authed_client, fake_openai_client, with_api_key):
    response = authed_client.post("/transcribe", files=_audio_file(content=b""))
    assert response.status_code == 400
    assert "empty" in response.json()["detail"].lower()
    # The gate must fire BEFORE the Whisper call — we don't want to
    # bill for a zero-byte upload.
    fake_openai_client.create.assert_not_called()


def test_transcribe_unsupported_content_type_returns_400(
    authed_client, fake_openai_client, with_api_key
):
    response = authed_client.post(
        "/transcribe",
        files={"file": ("clip.txt", b"hello", "text/plain")},
    )
    assert response.status_code == 400
    assert "content-type" in response.json()["detail"].lower()
    fake_openai_client.create.assert_not_called()


def test_transcribe_oversized_body_returns_413(authed_client, fake_openai_client, with_api_key):
    # 26 MB > the 25 MB Whisper cap — must reject without calling out.
    oversized = b"\x00" * (26 * 1024 * 1024)
    response = authed_client.post("/transcribe", files=_audio_file(content=oversized))
    assert response.status_code == 413
    fake_openai_client.create.assert_not_called()


# ─── upstream failure modes ──────────────────────────────────────────────


def test_transcribe_whisper_failure_returns_502(authed_client, fake_openai_client, with_api_key):
    fake_openai_client.create.side_effect = RuntimeError("upstream timeout")
    response = authed_client.post("/transcribe", files=_audio_file())
    assert response.status_code == 502
    assert "temporarily" in response.json()["detail"].lower()


def test_transcribe_missing_api_key_returns_503(authed_client, monkeypatch):
    """When the configured Settings has no api key, the route surfaces
    a 503 instead of crashing on a None client. Matches the
    'gracefully degrade' pattern used elsewhere in the app (billing
    routes do the same)."""
    from backend import main as backend_main
    from src.config import Settings

    overridden = Settings()
    overridden.openai_api_key = None  # explicit no-key
    monkeypatch.setattr(backend_main, "_settings", lambda: overridden)
    response = authed_client.post("/transcribe", files=_audio_file())
    assert response.status_code == 503
    assert "not configured" in response.json()["detail"].lower()


def test_transcribe_falls_back_to_wallclock_when_duration_missing(
    authed_client, monkeypatch, with_api_key
):
    """When Whisper returns text but no duration field (older SDK or
    response_format='json' fallback), the route fills duration_seconds
    from a wall-clock measurement so the response shape stays stable."""
    import openai

    stub_response = SimpleNamespace(text="hello")  # NO duration attr
    create_mock = MagicMock(return_value=stub_response)

    def _fake_openai(api_key: str | None = None):
        return SimpleNamespace(
            audio=SimpleNamespace(
                transcriptions=SimpleNamespace(create=create_mock),
            )
        )

    monkeypatch.setattr(openai, "OpenAI", _fake_openai)
    response = authed_client.post("/transcribe", files=_audio_file())
    assert response.status_code == 200
    body = response.json()
    assert body["text"] == "hello"
    # wall-clock fallback is always >= 0
    assert body["duration_seconds"] >= 0.0
