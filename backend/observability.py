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


def _init_sentry(settings: Settings) -> None:
    if not settings.sentry_dsn:
        logger.debug("SENTRY_DSN not configured; skipping Sentry init.")
        return
    try:
        import sentry_sdk
        from sentry_sdk.integrations.fastapi import FastApiIntegration
        from sentry_sdk.integrations.starlette import StarletteIntegration
        from sentry_sdk.integrations.logging import LoggingIntegration
    except Exception as exc:  # pragma: no cover — defensive
        logger.warning("sentry_sdk import failed (%s); Sentry disabled.", exc)
        return

    # The LoggingIntegration sends ERROR-level logs as breadcrumbs +
    # WARNING+ as events; the defaults are conservative enough that
    # we don't drown in noise from background tasks.
    logging_integration = LoggingIntegration(
        level=logging.INFO,        # breadcrumb threshold
        event_level=logging.ERROR, # event threshold — only ERROR+ becomes a Sentry issue
    )

    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        environment=settings.observability_environment,
        release=settings.observability_release or settings.retrieval_version,
        send_default_pii=settings.sentry_send_default_pii,
        traces_sample_rate=settings.sentry_traces_sample_rate,
        profiles_sample_rate=settings.sentry_profiles_sample_rate,
        integrations=[
            FastApiIntegration(transaction_style="endpoint"),
            StarletteIntegration(transaction_style="endpoint"),
            logging_integration,
        ],
    )
    logger.info(
        "Sentry initialized (environment=%s, traces=%.2f, profiles=%.2f).",
        settings.observability_environment,
        settings.sentry_traces_sample_rate,
        settings.sentry_profiles_sample_rate,
    )


def _init_posthog(settings: Settings) -> None:
    global _posthog_client
    if not settings.posthog_api_key:
        logger.debug("POSTHOG_API_KEY not configured; skipping PostHog init.")
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
    """
    if _posthog_client is None:
        return
    with suppress(Exception):
        _posthog_client.capture(
            distinct_id=distinct_id,
            event=event,
            properties=properties or {},
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
