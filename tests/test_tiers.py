"""Tier resolution shim + TIER_LIMITS matrix.

Step 1 of the tier-enforcement series. These tests pin three invariants:

  1. `resolve_user_tier` returns "free" for every user today. When
     payment integration ships, this test gets updated alongside the
     resolver — the test is the canary that signals tier behavior has
     changed.

  2. TIER_LIMITS contains exactly the three tiers we advertise. Adding
     or removing a tier without updating the pricing UI is a drift
     bug.

  3. Each tier's values match the pricing matrix in
     `frontend/src/components/landing/pricing.tsx`. If the pricing
     page advertises 50 questions and the backend lets you ask 500,
     someone's getting a refund. These per-value assertions catch a
     copy-paste mistake at PR time, not in production.
"""
from __future__ import annotations

import pytest

from backend.auth import AuthenticatedUser
from backend.tiers import (
    RETENTION_UNBOUNDED,
    TIER_LIMITS,
    Tier,
    resolve_user_tier,
)


_MB = 1024 * 1024


@pytest.fixture
def authenticated_user() -> AuthenticatedUser:
    """Plain authenticated user — Supabase UUID + email shape.

    The id matches Supabase's UUIDv4 format so anything downstream
    that string-parses it won't break in tests.
    """
    return AuthenticatedUser(
        id="00000000-0000-4000-8000-000000000001",
        email="someone@example.com",
    )


# ─── resolve_user_tier ───────────────────────────────────────────────────


def test_every_user_resolves_to_free_today(authenticated_user):
    assert resolve_user_tier(authenticated_user) == "free"


def test_resolver_works_without_email():
    """email is optional on AuthenticatedUser — resolver must not crash
    when it's None. Real callers can hit this for users that signed
    up via providers that don't expose email (rare but possible)."""
    user = AuthenticatedUser(id="abc-123", email=None)
    assert resolve_user_tier(user) == "free"


# ─── TIER_LIMITS shape ───────────────────────────────────────────────────


def test_all_advertised_tiers_present():
    assert set(TIER_LIMITS.keys()) == {"free", "pro", "business"}


def test_every_tier_exposes_full_limits_set():
    """Every tier must have every field — partial dicts would cause
    KeyError at gate-check time. TypedDict catches this at static
    analysis but not at runtime, so we double-check here."""
    expected_keys = {
        "doc_cap",
        "file_size_cap_bytes",
        "questions_per_month",
        "premium_answers_per_month",
        "retention_days",
        "answer_model",
        "premium_model",
    }
    for tier_name, limits in TIER_LIMITS.items():
        assert set(limits.keys()) == expected_keys, f"tier {tier_name} missing fields"


# ─── per-tier values match pricing.tsx ───────────────────────────────────


def test_free_tier_matches_pricing_matrix():
    free = TIER_LIMITS["free"]
    assert free["doc_cap"] == 3
    assert free["file_size_cap_bytes"] == 25 * _MB
    assert free["questions_per_month"] == 50
    assert free["premium_answers_per_month"] == 0
    assert free["retention_days"] == 30
    # Intentionally bumped nano -> mini: nano tripped the schema-strict
    # gate + support verifier into spurious abstentions on the default
    # path. See backend/tiers.py free-tier answer_model comment.
    assert free["answer_model"] == "gpt-5.4-mini"
    # Premium answers aren't available on free — None signals
    # "no model to call" to the /qa handler and the frontend toggle.
    assert free["premium_model"] is None


def test_pro_tier_matches_pricing_matrix():
    pro = TIER_LIMITS["pro"]
    # "Unlimited documents" on the UI maps to a 1000-doc soft ceiling
    # in the backend — high enough that nobody hits it under normal use.
    assert pro["doc_cap"] == 1000
    assert pro["file_size_cap_bytes"] == 150 * _MB
    assert pro["questions_per_month"] == 500
    assert pro["premium_answers_per_month"] == 25
    assert pro["retention_days"] == 365
    assert pro["answer_model"] == "gpt-5.4-mini"
    assert pro["premium_model"] == "gpt-5.5"


def test_business_tier_matches_pricing_matrix():
    business = TIER_LIMITS["business"]
    assert business["doc_cap"] == 1000
    assert business["file_size_cap_bytes"] == 500 * _MB
    assert business["questions_per_month"] == 2000
    assert business["premium_answers_per_month"] == 100
    # Business is "Unlimited history retention" → sentinel.
    assert business["retention_days"] == RETENTION_UNBOUNDED
    assert business["answer_model"] == "gpt-5.4-mini"
    assert business["premium_model"] == "gpt-5.5"


# ─── invariants across tiers ─────────────────────────────────────────────


def test_paid_tiers_have_premium_model_free_does_not():
    """Pro and Business advertise premium answers; free does not. The
    /qa handler will key off premium_model being non-None when deciding
    whether to allow premium=true requests."""
    assert TIER_LIMITS["free"]["premium_model"] is None
    assert TIER_LIMITS["pro"]["premium_model"] is not None
    assert TIER_LIMITS["business"]["premium_model"] is not None


def test_higher_tier_never_has_smaller_cap():
    """Sanity: a paid tier should never have a SMALLER cap than the
    free tier for the same dimension. Catches the easy copy-paste bug
    where someone shuffles the dict and gets the wrong numbers in the
    wrong tier."""
    tiers: list[Tier] = ["free", "pro", "business"]
    monotonic_fields = (
        "doc_cap",
        "file_size_cap_bytes",
        "questions_per_month",
        "premium_answers_per_month",
    )
    for field in monotonic_fields:
        values = [TIER_LIMITS[t][field] for t in tiers]
        assert values == sorted(values), f"{field} not monotonically non-decreasing across tiers"
