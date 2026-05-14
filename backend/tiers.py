"""Tier resolution + limits for HelpmateAI's quota gates.

This module is the single source of truth for which subscription tier
a user is on and what limits apply at that tier. Every quota gate
(file size, doc count, monthly questions, premium answers, retention
TTL) reads from `TIER_LIMITS` keyed by the resolved tier.

Tier resolution intentionally returns "free" for every user in the
initial enforcement PRs. When payment integration ships, swap the
implementation of `resolve_user_tier` to look up the user's active
subscription (Stripe, Supabase subscriptions table — TBD). NO other
call site needs to change because all gates already route through
this function. That's the whole point of the indirection.

Source of truth for the numbers is
`frontend/src/components/landing/pricing.tsx`. If you change a value
here, change the pricing UI in the same PR — drift between the two
is how customers get angry. The matrix:

    TIER       DOCS   FILE CAP   Q/MO   PREMIUM/MO   RETENTION   MODEL
    free       3      25 MB      50     0            30 days     gpt-5.4-nano
    pro        ∞*     150 MB     500    25           1 year      gpt-5.4-mini
    business   ∞*     500 MB     2,000  100          ∞           gpt-5.4-mini

    * Soft ceiling of 1000 docs applied where the UI says "∞" so
      abuse detection has a clean number to alert on. The UI still
      reads "Unlimited" — the cap is for our safety, not the user's.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal, TypedDict

from backend.auth import AuthenticatedUser


Tier = Literal["free", "pro", "business"]


# Subscription statuses that still grant tier access while
# `current_period_end > now`. "expired" / "paused" / unknown statuses
# always resolve to Free.
_PAID_STATUSES_DURING_PERIOD: frozenset[str] = frozenset(
    {"active", "cancelled", "past_due"}
)
# Tier values we accept from the subscription row. Anything else
# (typo, future tier we haven't shipped yet) resolves to Free
# defensively.
_PAID_TIERS: frozenset[str] = frozenset({"pro", "business"})


class TierLimits(TypedDict):
    """Per-tier caps consumed by the upload, /qa, and retention gates.

    Field semantics:
        doc_cap                    — max active documents per user. The
                                     gate counts via store.list_documents
                                     filtered by owner.
        file_size_cap_bytes        — Content-Length must be <= this.
                                     Comparison is in bytes so the gate
                                     can read the header directly.
        questions_per_month        — standard /qa calls within the
                                     calendar month (UTC, period_start =
                                     first of month). Atomic counter in
                                     the helpmate_quota_counters table.
        premium_answers_per_month  — gpt-5.5 calls separately metered.
                                     0 means the feature isn't available
                                     at this tier (free).
        retention_days             — workspace TTL beyond the 24h
                                     ephemeral timer. -1 = never auto-
                                     delete.
        answer_model               — default model for /qa at this tier.
        premium_model              — model used when premium=true on
                                     /qa, or None when not available.
    """

    doc_cap: int
    file_size_cap_bytes: int
    questions_per_month: int
    premium_answers_per_month: int
    retention_days: int
    answer_model: str
    premium_model: str | None


_MB = 1024 * 1024

# Soft ceiling applied where the pricing UI says "∞". Picked to be high
# enough that no legitimate user hits it, low enough that runaway upload
# loops cap before they exhaust the bucket. If a paying user genuinely
# needs more than 1000 docs, raise this — don't paper over with a hack.
_SOFT_DOC_CEILING = 1000

# Sentinel for "never auto-delete". Retention sweeper treats this as
# infinity. Stored as -1 (rather than None) so the TypedDict stays
# uniformly typed as int — call sites do a single `< 0` check.
RETENTION_UNBOUNDED = -1

TIER_LIMITS: dict[Tier, TierLimits] = {
    "free": {
        "doc_cap": 3,
        "file_size_cap_bytes": 25 * _MB,
        "questions_per_month": 50,
        "premium_answers_per_month": 0,
        "retention_days": 30,
        "answer_model": "gpt-5.4-nano",
        "premium_model": None,
    },
    "pro": {
        "doc_cap": _SOFT_DOC_CEILING,
        "file_size_cap_bytes": 150 * _MB,
        "questions_per_month": 500,
        "premium_answers_per_month": 25,
        "retention_days": 365,
        "answer_model": "gpt-5.4-mini",
        "premium_model": "gpt-5.5",
    },
    "business": {
        "doc_cap": _SOFT_DOC_CEILING,
        "file_size_cap_bytes": 500 * _MB,
        "questions_per_month": 2000,
        "premium_answers_per_month": 100,
        "retention_days": RETENTION_UNBOUNDED,
        "answer_model": "gpt-5.4-mini",
        "premium_model": "gpt-5.5",
    },
}


def resolve_user_tier(user: AuthenticatedUser) -> Tier:
    """Resolve the active subscription tier for an authenticated user.

    Consults `backend.subscriptions.get_active_subscription`, which
    reads from the Supabase ``subscriptions`` table populated by the
    Lemon Squeezy webhook handler. The read is LRU-cached for up to
    60 seconds so this function never blocks on a network round-trip
    -- it sits on every quota gate's hot path (/qa, /documents/upload,
    /workspace/quota).

    Tier resolution rules:

      * No user / no user.id: "free".
      * No subscription row: "free".
      * subscription row with status in {"active", "cancelled",
        "past_due"} AND current_period_end > now: return the
        subscription's tier. "cancelled" still grants access during
        the paid period; "past_due" is the LS dunning window.
      * Anything else (status="expired" / "paused",
        current_period_end in the past, unknown tier value): "free".

    Lazy import of `backend.subscriptions` so anything importing
    `backend.tiers` (e.g. quota.py) doesn't drag the subscription
    backend in if it's never actually resolving a user. Same pattern
    AI Job Agent uses for symmetry.
    """
    if user is None:
        return "free"
    user_id = getattr(user, "id", None)
    if not user_id:
        return "free"

    # Local import avoids a hard cycle in test collection.
    from backend.subscriptions import get_active_subscription

    sub = get_active_subscription(str(user_id))
    if sub is None:
        return "free"

    if sub.tier not in _PAID_TIERS:
        # Defensive: the table has a CHECK constraint that limits
        # this to {"pro", "business"}, but if the constraint is ever
        # relaxed or a future migration adds a tier we haven't
        # shipped frontend support for, fall back to Free rather
        # than letting an unrecognized string flow into TIER_LIMITS as
        # a KeyError at gate-check time.
        return "free"

    if sub.status not in _PAID_STATUSES_DURING_PERIOD:
        return "free"

    period_end = sub.current_period_end
    if period_end is None:
        # No period boundary on the row -- conservatively downgrade.
        # An active subscription should always have a
        # current_period_end set by the webhook; missing values
        # indicate a bug we'd rather catch on the free side than the
        # paid side.
        return "free"

    if period_end <= datetime.now(timezone.utc):
        return "free"

    return "pro" if sub.tier == "pro" else "business"


__all__ = [
    "TIER_LIMITS",
    "RETENTION_UNBOUNDED",
    "Tier",
    "TierLimits",
    "resolve_user_tier",
]
