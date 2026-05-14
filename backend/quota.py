"""Structured quota error responses + per-gate check helpers.

Section 4 of the tier-enforcement brief specifies a single JSON shape
for every quota error the backend returns, so the frontend toast can
render it without per-endpoint special cases:

    {
      "detail":      "<one-sentence explanation>",
      "code":        "quota_exceeded" | "file_too_large" | "doc_count_cap",
      "tier":        "free" | "pro" | "business",
      "limit":       <int>,
      "current":     <int>,
      "upgrade_url": "https://helpmateai.xyz/landing#pricing"
    }

`quota_error_response` is the only way these errors get built in
backend code. Gates call it; the response is returned from the route
directly. We use JSONResponse (not HTTPException) because
HTTPException wraps the body as `{"detail": <body>}` which would
nest our shape one level too deep.

The check helpers (`check_file_size_cap`, `check_doc_count_cap`)
are pure functions: inputs in, optional JSONResponse out. They have
no dependencies on FastAPI state, so unit tests can call them
directly without a TestClient. The route handlers call them and
short-circuit if a response comes back.
"""
from __future__ import annotations

import logging
from typing import Literal

from fastapi.responses import JSONResponse

from backend.tiers import TIER_LIMITS, Tier, TierLimits


logger = logging.getLogger(__name__)


QuotaErrorCode = Literal[
    "quota_exceeded",
    "file_too_large",
    "doc_count_cap",
    "question_quota_exhausted",
    "premium_unavailable",
    "premium_quota_exhausted",
]


UPGRADE_URL = "https://helpmateai.xyz/landing#pricing"


def quota_error_response(
    *,
    status_code: int,
    code: QuotaErrorCode,
    detail: str,
    tier: Tier,
    limit: int,
    current: int,
) -> JSONResponse:
    """Build the canonical quota-error response.

    `status_code` is 402 (Payment Required) for quota exhaustion or
    413 (Payload Too Large) for file-size rejection. The Link header
    points to the pricing page so clients with link-header preview
    (curl with --location, some toast UIs) can offer upgrade UX.
    """
    return JSONResponse(
        status_code=status_code,
        content={
            "detail": detail,
            "code": code,
            "tier": tier,
            "limit": limit,
            "current": current,
            "upgrade_url": UPGRADE_URL,
        },
        headers={"Link": f'<{UPGRADE_URL}>; rel="related"'},
    )


def check_content_length_present(
    *,
    content_length: int | None,
    tier: Tier,
    limits: TierLimits | None = None,
) -> JSONResponse | None:
    """411 reject when the upload request has no Content-Length header.

    Real browsers always send Content-Length for file uploads; clients
    that omit it are typically using chunked transfer-encoding which
    bypasses the simple length-based gate. Reject up-front so size
    enforcement isn't trivially circumvented.

    Per-spec rejection code: 411 Length Required. Body uses the same
    "file_too_large" code as a 413 so the frontend toast can group
    "your upload was rejected on size" into a single UI branch.
    """
    if limits is None:
        limits = TIER_LIMITS[tier]
    if content_length is not None:
        return None
    return quota_error_response(
        status_code=411,
        code="file_too_large",
        detail="Length Required. Re-upload with a Content-Length header.",
        tier=tier,
        limit=limits["file_size_cap_bytes"],
        current=0,
    )


def check_file_size_cap(
    *,
    file_size: int,
    tier: Tier,
    limits: TierLimits | None = None,
) -> JSONResponse | None:
    """413 reject when the uploaded file body exceeds the tier's cap.

    Compares against `file_size` (Starlette's `UploadFile.size`) rather
    than the request's Content-Length header. Content-Length includes
    multipart envelope overhead (~few hundred bytes per part), which
    means a user's 25 MB file would fail a 25 MB cap because the wire
    bytes are 25 MB + envelope. file.size is the actual file body
    size after multipart parsing, matching what the user thinks they
    uploaded. The brief's "file at exactly cap → 200 OK" only holds
    against this measurement.

    "Doesn't this defeat the early-reject benefit?" — partially. By
    the time we're inside the route handler, FastAPI has already
    parsed the multipart body (UploadFile dependency runs first). The
    bytes are on the server. What we save by checking here is the
    pipeline work (DOCX→PDF conversion, chunking, embedding, storage
    upload). Truly-early rejection would need ASGI middleware reading
    Content-Length before route dispatch — out of scope per spec
    Section 4 ("Don't add a rate-limit middleware that runs before
    the route"). See docs/tier-enforcement-flags.md.
    """
    if limits is None:
        limits = TIER_LIMITS[tier]
    cap = limits["file_size_cap_bytes"]
    if file_size <= cap:
        return None
    cap_mb = cap // (1024 * 1024)
    return quota_error_response(
        status_code=413,
        code="file_too_large",
        detail=f"File exceeds your {cap_mb} MB tier cap. Upgrade to lift this limit.",
        tier=tier,
        limit=cap,
        current=file_size,
    )


def check_question_quota(
    *,
    questions_used: int,
    tier: Tier,
    limits: TierLimits | None = None,
) -> JSONResponse | None:
    """402 reject when the user has consumed their monthly question quota.

    `questions_used` is the user's current question count for the
    calendar month (UTC). Reject when used >= cap — at cap means the
    next question would push past, so we don't run the pipeline.

    Quota windows reset at the first of the month (UTC). The
    helpmate_quota_counters table's (user_id, period_start) primary
    key gives us a fresh row on Nov 1 / Dec 1 / etc. — no application
    code needs to schedule a "reset".
    """
    if limits is None:
        limits = TIER_LIMITS[tier]
    cap = limits["questions_per_month"]
    if questions_used < cap:
        return None
    return quota_error_response(
        status_code=402,
        code="question_quota_exhausted",
        detail=(
            f"You've used all {cap} questions in this month's quota. "
            "Quota resets on the 1st (UTC) — or upgrade for more."
        ),
        tier=tier,
        limit=cap,
        current=questions_used,
    )


def check_premium_quota(
    *,
    premium_used: int,
    tier: Tier,
    limits: TierLimits | None = None,
) -> JSONResponse | None:
    """402 reject when the user can't request a premium answer this turn.

    Two distinct failure modes share this helper, both returning 402
    but with different `code` fields so the frontend toast can render
    different copy:

      premium_unavailable        — tier has no premium model at all
                                   (free). Trying to use premium=true
                                   here is a hard "upgrade to unlock".
      premium_quota_exhausted    — tier has the feature, but the user
                                   has burned through this month's
                                   premium credits.

    The check runs BEFORE the standard question-quota check in /qa so
    a premium-only failure is reported clearly. If the standard quota
    is ALSO exhausted, the standard check fires after this one passes;
    each gate has its own structured response.
    """
    if limits is None:
        limits = TIER_LIMITS[tier]
    premium_model = limits["premium_model"]
    cap = limits["premium_answers_per_month"]

    if premium_model is None:
        return quota_error_response(
            status_code=402,
            code="premium_unavailable",
            detail=(
                "Premium answers (GPT-5.5) aren't available on your tier. "
                "Upgrade to Pro to unlock."
            ),
            tier=tier,
            limit=0,
            current=premium_used,
        )

    if premium_used >= cap:
        return quota_error_response(
            status_code=402,
            code="premium_quota_exhausted",
            detail=(
                f"You've used all {cap} premium answers this month. "
                "Quota resets on the 1st (UTC) — or upgrade for more."
            ),
            tier=tier,
            limit=cap,
            current=premium_used,
        )

    return None


def check_doc_count_cap(
    *,
    active_count: int,
    tier: Tier,
    limits: TierLimits | None = None,
) -> JSONResponse | None:
    """Validate that the user is under their tier's active-doc cap.

    Returns None when there's room for one more document, OR a 402
    JSONResponse when the user is already at or past their cap. The
    >= comparison is deliberate: cap of 3 means "3 active documents
    allowed", so attempting to upload when active_count == 3 should
    reject (the upload would make it the 4th).

    Note: today's upload flow deletes the existing workspace document
    before ingest, so active_count is effectively always 0 or 1 in
    practice — the gate is structurally present but unreachable until
    multi-doc workspaces ship. See docs/tier-enforcement-flags.md.
    """
    if limits is None:
        limits = TIER_LIMITS[tier]
    cap = limits["doc_cap"]

    if active_count >= cap:
        return quota_error_response(
            status_code=402,
            code="doc_count_cap",
            detail=f"You've reached your {cap}-document limit. Upgrade for more.",
            tier=tier,
            limit=cap,
            current=active_count,
        )

    return None
