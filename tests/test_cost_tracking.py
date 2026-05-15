"""Unit tests for the per-query cost-tracking pipeline.

The flow ships in two halves:

  1. ``backend/openai_service.py`` (this commit-set) — token capture,
     USD cost computation, and the ``CostCollector`` aggregator that
     folds per-call records into a single per-query row.
  2. ``src/traces/store.py`` (post-merge follow-up — see
     ``docs/safety-pack-migration-recipe.md``) — wires the collector
     totals into the new ``prompt_tokens`` / ``completion_tokens`` /
     ``cost_usd`` / ``model_name`` Supabase columns.

This file covers half #1 plus the *shape contract* of the
trace-store payload that half #2 will plug into. The shape contract
test asserts the keys a future ``SupabaseRunTraceStore.save_trace``
override needs to read — if a refactor renames a key (say
``cost_usd`` → ``usd_cost``) the test fails before the actual write
path drifts out of sync with the SQL columns.
"""
from __future__ import annotations

import pytest

from src.openai_service import (
    CostCollector,
    LLMCallRecord,
    PRICING_PER_1M_TOKENS,
    compute_cost_usd,
)


# ─── cost computation: edge cases for the brief's pricing table ──────


@pytest.mark.parametrize(
    "model_name,prompt,completion,expected",
    [
        # gpt-5.4-nano: $0.10 / $0.40 per 1M
        ("gpt-5.4-nano", 1_000_000, 1_000_000, 0.50),
        ("gpt-5.4-nano", 100, 100, 1e-5 + 4e-5),
        # gpt-5.4-mini: $0.75 / $4.50 per 1M
        ("gpt-5.4-mini", 1_000_000, 1_000_000, 5.25),
        ("gpt-5.4-mini", 1000, 250, 1000 * 0.75 / 1e6 + 250 * 4.50 / 1e6),
        # gpt-5.4: $2 / $10 per 1M
        ("gpt-5.4", 1_000_000, 1_000_000, 12.0),
        # gpt-5.5: $5 / $30 per 1M
        ("gpt-5.5", 1_000_000, 1_000_000, 35.0),
        ("gpt-5.5", 0, 0, 0.0),
    ],
)
def test_compute_cost_matches_brief_pricing(model_name, prompt, completion, expected):
    """Brief specifies exact per-1M-token prices. This locks them in
    so a future pricing-table tweak that drifts from the brief gets
    caught at CI time."""
    assert compute_cost_usd(model_name, prompt, completion) == pytest.approx(expected)


def test_compute_cost_zero_tokens_is_free():
    """Defensive — a failed call that returns no usage shouldn't accrue
    cost on its own. The wrapper's error path still records a 0-cost
    row so the trace at least notes the failure."""
    for model in PRICING_PER_1M_TOKENS:
        assert compute_cost_usd(model, 0, 0) == 0.0


# ─── CostCollector aggregation ────────────────────────────────────────


def test_cost_collector_records_called_in_order():
    collector = CostCollector()
    a = LLMCallRecord(task_name="a", model_name="gpt-5.4-nano", prompt_tokens=1)
    b = LLMCallRecord(task_name="b", model_name="gpt-5.4-mini", prompt_tokens=2)
    collector(a)
    collector(b)
    assert collector.records == [a, b]


def test_cost_collector_totals_sums_tokens_and_cost():
    collector = CostCollector()
    collector(
        LLMCallRecord(
            task_name="planner",
            model_name="gpt-5.4-nano",
            prompt_tokens=300,
            completion_tokens=50,
            cost_usd=compute_cost_usd("gpt-5.4-nano", 300, 50),
        )
    )
    collector(
        LLMCallRecord(
            task_name="answer_generation",
            model_name="gpt-5.4-mini",
            prompt_tokens=1200,
            completion_tokens=400,
            cost_usd=compute_cost_usd("gpt-5.4-mini", 1200, 400),
        )
    )
    totals = collector.totals()
    assert totals["prompt_tokens"] == 1500
    assert totals["completion_tokens"] == 450
    assert totals["total_tokens"] == 1950
    # Six-decimal-place rounding is the SQL precision the migration
    # uses (numeric(10,6)). The collector pre-rounds so the database
    # write doesn't have to.
    expected_total = compute_cost_usd("gpt-5.4-nano", 300, 50) + compute_cost_usd(
        "gpt-5.4-mini", 1200, 400
    )
    assert totals["cost_usd"] == pytest.approx(round(expected_total, 6))


def test_cost_collector_primary_model_is_highest_cost():
    """The trace row's ``model_name`` column reports the *primary*
    model so dashboards can group by it. We pick the call with the
    highest cost, not the most recent — a $0.001 generation call
    matters more than a $0.00001 router decision even when the router
    fires after."""
    collector = CostCollector()
    collector(LLMCallRecord(task_name="planner", model_name="gpt-5.4-nano", cost_usd=1e-5))
    collector(LLMCallRecord(task_name="answer_generation", model_name="gpt-5.4-mini", cost_usd=1e-3))
    collector(LLMCallRecord(task_name="verifier", model_name="gpt-5.4-mini", cost_usd=5e-4))
    assert collector.totals()["model_name"] == "gpt-5.4-mini"


def test_cost_collector_to_payload_includes_call_breakdown():
    """The payload helper is what the run trace persists into the
    JSONB ``payload`` column. The per-call breakdown is preserved so
    a future debugger can see which step blew the cost budget."""
    collector = CostCollector()
    collector(LLMCallRecord(task_name="planner", model_name="gpt-5.4-nano", prompt_tokens=10, completion_tokens=2))
    payload = collector.to_payload()
    assert "totals" in payload
    assert "calls" in payload
    assert len(payload["calls"]) == 1
    assert payload["calls"][0]["task_name"] == "planner"
    assert payload["calls"][0]["model_name"] == "gpt-5.4-nano"
    assert payload["calls"][0]["prompt_tokens"] == 10


def test_cost_collector_to_payload_keys_match_sql_migration():
    """Shape contract for the trace store's planned write path. If
    this test fails after a SQL migration edit, the trace store's
    Python write path will fall out of sync with the columns.

    The migration in docs/sql/supabase-run-traces-cost-columns.sql adds:
      prompt_tokens, completion_tokens, cost_usd, model_name

    Each of those MUST appear in the totals dict — that's the
    contract the SupabaseRunTraceStore upsert will rely on."""
    collector = CostCollector()
    collector(LLMCallRecord(task_name="x", model_name="gpt-5.4-mini", prompt_tokens=1, completion_tokens=1, cost_usd=0.000005))
    totals = collector.totals()
    for required_key in ("prompt_tokens", "completion_tokens", "cost_usd", "model_name"):
        assert required_key in totals, f"Cost-tracking SQL migration expects '{required_key}' in totals"


# ─── LLMCallRecord shape ──────────────────────────────────────────────


def test_llm_call_record_to_dict_preserves_error_field():
    """A failed call still produces a record — the trace shows it
    with ``error`` populated. We assert the field round-trips
    through to_dict so the payload JSON has the breadcrumb."""
    record = LLMCallRecord(
        task_name="answer_generation",
        model_name="gpt-5.4-mini",
        prompt_tokens=100,
        completion_tokens=0,
        cost_usd=0.0,
        error="StructuredOutputError: validation failed",
    )
    payload = record.to_dict()
    assert payload["task_name"] == "answer_generation"
    assert payload["error"] == "StructuredOutputError: validation failed"
    assert payload["total_tokens"] == 100


def test_llm_call_record_default_cost_zero():
    record = LLMCallRecord(task_name="t", model_name="gpt-5.4-mini")
    assert record.cost_usd == 0.0
    assert record.prompt_tokens == 0
    assert record.completion_tokens == 0


# ─── tier-margin smoke (informational) ───────────────────────────────


def test_typical_qa_query_cost_under_one_cent():
    """Informational guard for tier-margin validation. A typical /qa
    query in production runs maybe 4 LLM calls (planner +
    answer_generation + support_verifier + an occasional
    chunk-semantics call). With the gpt-5.4-mini answer model on a
    ~2k prompt + 500 completion this works out to:

        2000 * 0.75 / 1e6 + 500 * 4.50 / 1e6 = 0.00375 USD

    plus the ~$0.0001 across the three nano calls. Total well
    under a cent. If a refactor pushes a typical query past 1¢
    the test fires and the tier-pricing model needs a relook."""
    collector = CostCollector()
    # Planner call
    collector(
        LLMCallRecord(
            task_name="planner",
            model_name="gpt-5.4-nano",
            prompt_tokens=600,
            completion_tokens=80,
            cost_usd=compute_cost_usd("gpt-5.4-nano", 600, 80),
        )
    )
    # Answer generation (the hot path)
    collector(
        LLMCallRecord(
            task_name="answer_generation",
            model_name="gpt-5.4-mini",
            prompt_tokens=2000,
            completion_tokens=500,
            cost_usd=compute_cost_usd("gpt-5.4-mini", 2000, 500),
        )
    )
    # Support verifier (lighter prompt, same model)
    collector(
        LLMCallRecord(
            task_name="support_status_verifier",
            model_name="gpt-5.4-mini",
            prompt_tokens=900,
            completion_tokens=200,
            cost_usd=compute_cost_usd("gpt-5.4-mini", 900, 200),
        )
    )
    totals = collector.totals()
    assert totals["cost_usd"] < 0.01, (
        f"Typical query cost has crept past 1¢: {totals['cost_usd']} — re-check tier pricing."
    )
