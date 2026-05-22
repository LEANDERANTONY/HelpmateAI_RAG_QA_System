"""Centralized Sentry + PostHog bootstrap for the FastAPI service.

Two reasons this lives in its own module:

1. **Single import order.** Sentry's FastAPI integration must be
   initialized BEFORE ``FastAPI()`` is constructed so that the ASGI
   middleware wraps the app instance. Doing that inline in
   ``backend/main.py`` would mean spreading SDK-config concerns over
   the top of a 1200-line module. Pulling everything into
   ``initialize_observability(settings)`` keeps the call site one
   line.

2. **Quiet no-op path.** Both clients silently degrade when their
   credentials are missing — there is NO production assert that says
   "Sentry must be configured". Local dev, CI, and the test suite
   should never have to set ``SENTRY_DSN`` to use the app. The
   helpers below check the relevant env / settings field and bail
   when unset.

PII posture
-----------
``send_default_pii`` defaults to False. We deliberately do NOT ship
user request bodies or query params to Sentry. The /qa payloads can
contain whatever the user has typed into the textarea, including
private snippets from their uploaded document. Setting
``SENTRY_SEND_DEFAULT_PII=true`` is an explicit opt-in for ops who
have decided this is acceptable for their deploy.

PostHog identification happens AFTER auth resolves (so we get the
Supabase user id), via ``capture_event_for_user`` below — never via
default SDK behavior that would auto-grab cookies / IPs.
"""
from __future__ import annotations

import logging
from contextlib import suppress
from typing import Any

from src.config import Settings


logger = logging.getLogger(__name__)


# Module-level PostHog client handle. Set once in
# ``initialize_observability`` and read by ``capture_event``. Kept on
# the module — rather than passed through every route — because
# PostHog's Python client is itself thread-safe and meant to be
# instantiated once per process.
_posthog_client: Any | None = None


def initialize_observability(settings: Settings) -> None:
    """Initialize Sentry + PostHog using values from ``Settings``.

    Safe to call once at import time. Calling twice is a no-op for
    Sentry (the SDK detects already-initialized state) and reuses the
    existing PostHog client.
    """
    _init_sentry(settings)
    _init_posthog(settings)


def _running_under_pytest() -> bool:
    """True when the current process was launched by pytest.

    The flag matters because the local ``.env`` carries a real
    SENTRY_DSN for dev work; without this guard every `uv run pytest`
    invocation fires test-only crashes (mock RuntimeError, fake-401s,
    HTTPExceptions for the "feature not configured" paths) into the
    production Sentry project, drowning real issues in test noise.

    ``PYTEST_CURRENT_TEST`` is the canonical signal — pytest sets it
    before each test and unsets it after. ``"pytest" in sys.modules``
    is the secondary check that catches the import-time bootstrap
    window before the env var lands. Either positive → bail out of
    Sentry init.
    """
    import os
    import sys

    if os.environ.get("PYTEST_CURRENT_TEST"):
        return True
    if "pytest" in sys.modules:
        return True
    return False


def _drop_expected_http_exceptions(event, hint):
    """``before_send`` filter — drop FastAPI HTTPException events.

    FastAPI uses HTTPException as a structured-flow-control mechanism:
    every 4xx response (auth failure, validation reject, quota cap) and
    several intentional 5xx ones ("Feature not configured", "Service
    unavailable") raise HTTPException, which then becomes the response.
    These are NOT bugs — they're the contract. Without this filter,
    every rejected upload, every disabled-feature ping, and every quota
    cap fills the Sentry issue feed.

    We let through:
      • Bare ``HTTPException`` with status_code >= 500 that ISN'T a
        clean 503 from one of our "not configured" guards — those still
        usually represent a backend problem worth seeing.
      • Every non-HTTPException error (RuntimeError, IntegrityError,
        OpenAI APIError, etc.) — those are the high-signal ones.

    Returning None drops the event; returning ``event`` keeps it.
    """
    exc_info = hint.get("exc_info") if hint else None
    if not exc_info:
        return event
    exc_type = exc_info[0]
    if exc_type is None:
        return event
    try:
        from fastapi import HTTPException
    except Exception:
        return event
    if not issubclass(exc_type, HTTPException):
        return event
    # Drop everything < 500. Those are intentional client errors.
    exc_value = exc_info[1]
    status_code = getattr(exc_value, "status_code", None)
    if status_code is None or status_code < 500:
        return None
    # For 5xx HTTPException, keep them — they usually indicate an
    # upstream failure we want to know about (Supabase outage, OpenAI
    # 5xx, etc.). The exception to that rule is the "not configured"
    # 503 family which is intentional in dev/preview deploys.
    detail = getattr(exc_value, "detail", "") or ""
    if isinstance(detail, str):
        lowered = detail.lower()
        if "not configured" in lowered or "temporarily unavailable" in lowered:
            return None
    return event


def _init_sentry(settings: Settings) -> None:
    if not settings.sentry_dsn:
        logger.debug("SENTRY_DSN not configured; skipping Sentry init.")
        return
    if _running_under_pytest():
        logger.debug("Pytest detected; skipping Sentry init to avoid polluting prod issues with test fixtures.")
        return
    try:
        import sentry_sdk
        from sentry_sdk.integrations.fastapi import FastApiIntegration
        from sentry_sdk.integrations.starlette import StarletteIntegration
        from sentry_sdk.integrations.logging import LoggingIntegration
    except Exception as exc:  # pragma: no cover — defensive
        logger.warning("sentry_sdk import failed (%s); Sentry disabled.", exc)
        return

    # OpenAI auto-instrumentation. The SDK ships a first-class
    # OpenAIIntegration that wraps the client's HTTP calls and emits
    # AI-aware spans (token count, model, latency, total cost). Critical
    # for a RAG product: every /qa response becomes a parent span with
    # the LLM call as a child, so a slow answer can be attributed to
    # OpenAI or to retrieval. The integration is opt-in below; if the
    # SDK rev doesn't ship it (older versions) we silently skip.
    integrations: list = [
        FastApiIntegration(transaction_style="endpoint"),
        StarletteIntegration(transaction_style="endpoint"),
        LoggingIntegration(
            level=logging.INFO,        # breadcrumb threshold
            event_level=logging.ERROR, # event threshold — only ERROR+ becomes a Sentry issue
        ),
    ]
    try:
        from sentry_sdk.integrations.openai import OpenAIIntegration

        integrations.append(
            OpenAIIntegration(
                include_prompts=False,  # don't ship user PII to Sentry
            )
        )
    except Exception:
        # SDK doesn't ship the OpenAI integration; not fatal — the
        # rest of Sentry still works, we just lose the AI-aware
        # span attribution.
        pass

    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        environment=settings.observability_environment,
        release=settings.observability_release or settings.retrieval_version,
        send_default_pii=settings.sentry_send_default_pii,
        traces_sample_rate=settings.sentry_traces_sample_rate,
        profiles_sample_rate=settings.sentry_profiles_sample_rate,
        # Enable the new Sentry Logs product (separate from breadcrumbs).
        # Requires sentry-sdk>=2.35.0 — we pin >=2.18 in pyproject but
        # the latest release fulfills the >=2.35 requirement; if a
        # downstream env happens to install older, the flag is just
        # ignored.
        enable_logs=True,
        integrations=integrations,
        # Drop expected HTTPException events (intentional 4xx + dev
        # 5xx "not configured" guards) before they leave the process.
        # Keeps the issue feed focused on actual bugs.
        before_send=_drop_expected_http_exceptions,
    )
    logger.info(
        "Sentry initialized (environment=%s, traces=%.2f, profiles=%.2f, integrations=%d).",
        settings.observability_environment,
        settings.sentry_traces_sample_rate,
        settings.sentry_profiles_sample_rate,
        len(integrations),
    )


def _init_posthog(settings: Settings) -> None:
    global _posthog_client
    if not settings.posthog_api_key:
        logger.debug("POSTHOG_API_KEY not configured; skipping PostHog init.")
        _posthog_client = None
        return
    if _running_under_pytest():
        # The local .env carries a real POSTHOG_API_KEY for dev work;
        # without this guard every test that exercises an instrumented
        # route (upload, index, /qa, /feedback, a quota reject) would
        # ship events into the production analytics project. Mirrors
        # the _init_sentry pytest guard.
        logger.debug("Pytest detected; skipping PostHog init.")
        _posthog_client = None
        return
    try:
        from posthog import Posthog
    except Exception as exc:  # pragma: no cover — defensive
        logger.warning("posthog import failed (%s); PostHog disabled.", exc)
        _posthog_client = None
        return

    # Posthog client buffers events and flushes on its own schedule
    # (default 10s or 100 events). For long-running FastAPI workers
    # this is the right behavior — we don't want a synchronous network
    # round-trip on every /qa response. The atexit handler the SDK
    # registers takes care of flushing at process shutdown.
    _posthog_client = Posthog(
        project_api_key=settings.posthog_api_key,
        host=settings.posthog_host,
    )
    logger.info("PostHog initialized (host=%s).", settings.posthog_host)


# Every server-side event auto-tags with ``product: "helpmate"`` so
# the shared PostHog project (free-tier 1-project limit) can split
# HelpmateAI's events from AI Job Agent's via a simple insight filter
# (``where event.product = 'helpmate'``). AI Job Agent's capture_event
# does the same with ``product: "jobagent"``. Keeps the two products
# on the same free-tier quota while still giving us product-scoped
# dashboards.
_PRODUCT_TAG = "helpmate"


def capture_event(
    distinct_id: str,
    event: str,
    properties: dict[str, Any] | None = None,
) -> None:
    """Send a server-side analytics event to PostHog.

    No-op when PostHog is not configured (the module-level client is
    None). All exceptions are swallowed — analytics failures must
    never break a /qa response. The most common failure mode (PostHog
    rate limit on the free tier) just means the event is dropped on
    the floor, which is correct: telemetry is best-effort.

    ``distinct_id`` is the Supabase user id from the AuthenticatedUser
    on the request scope — never a session token or anything that
    could leak credentials.

    All events automatically include ``product: "helpmate"`` so the
    shared PostHog project can split events by product on the
    dashboards. Caller-supplied ``properties`` win on conflict, but
    a caller would have no reason to override the product tag.
    """
    if _posthog_client is None:
        return
    merged: dict[str, Any] = {"product": _PRODUCT_TAG}
    if properties:
        merged.update(properties)
    with suppress(Exception):
        _posthog_client.capture(
            distinct_id=distinct_id,
            event=event,
            properties=merged,
        )


def shutdown_observability() -> None:
    """Flush + close the PostHog client on process shutdown.

    PostHog buffers events; calling ``shutdown`` synchronously drains
    that buffer to the API. The SDK registers an atexit handler that
    does the same thing — we expose this as an explicit hook so the
    FastAPI lifespan can call it on graceful termination and not rely
    on interpreter atexit timing alone.
    """
    global _posthog_client
    if _posthog_client is None:
        return
    with suppress(Exception):
        _posthog_client.shutdown()
    _posthog_client = None
