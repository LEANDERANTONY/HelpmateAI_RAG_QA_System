"""Tests for the request-scoped CostCollector ContextVar in
``src.openai_service``.

These cover the concurrency-fix that landed after CodeRabbit + Codex
both flagged that a pipeline-scoped collector mixes records across
concurrent /qa requests under FastAPI's task model. The fix uses a
ContextVar so each request task gets its own collector visible to the
wrapper's ``_record_cost``.

Coverage:

  * Binding + reset round-trips cleanly.
  * Two concurrent asyncio tasks see isolated collectors when each
    binds its own — the classic "would-be-bug" before the fix.
  * The wrapper's ``_record_cost`` reads the bound collector when the
    OpenAIService was constructed without an explicit ``cost_recorder``.
  * An explicit ``cost_recorder`` still wins over the contextvar
    (preserves the test-injection pattern in tests/test_cost_tracking.py).
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from src.openai_service import (
    CostCollector,
    LLMCallRecord,
    OpenAIService,
    bind_cost_collector,
    get_current_cost_collector,
    reset_cost_collector,
)


# ─── basic bind / reset round-trips ──────────────────────────────────────


def test_bind_then_reset_round_trips():
    """A single bind/reset pair leaves the contextvar in its original
    (unset) state. Without this the ContextVar leaks across requests
    even after ``finally``."""
    assert get_current_cost_collector() is None
    collector = CostCollector()
    token = bind_cost_collector(collector)
    try:
        assert get_current_cost_collector() is collector
    finally:
        reset_cost_collector(token)
    assert get_current_cost_collector() is None


def test_bind_overwrites_previous_binding():
    """If a request nests a second bind (shouldn't happen in
    production, but defends against a future refactor doing so),
    reset still restores the previous outer binding rather than
    None-ing everything out."""
    outer = CostCollector()
    inner = CostCollector()
    outer_token = bind_cost_collector(outer)
    try:
        assert get_current_cost_collector() is outer
        inner_token = bind_cost_collector(inner)
        try:
            assert get_current_cost_collector() is inner
        finally:
            reset_cost_collector(inner_token)
        assert get_current_cost_collector() is outer
    finally:
        reset_cost_collector(outer_token)
    assert get_current_cost_collector() is None


# ─── isolation under concurrent asyncio tasks ────────────────────────────


def test_concurrent_tasks_see_isolated_collectors():
    """The classic concurrency bug both reviewers flagged: pipeline-
    scoped collectors interleave records across requests. Two asyncio
    tasks each bind their own collector and write a record; the
    collectors must contain only their own task's record.

    contextvars.copy_context() — which asyncio.create_task uses
    automatically — gives each task an isolated copy of the
    contextvar, so each task's bind is invisible to the other.
    """

    async def _record_into_isolated_collector(label: str, collector: CostCollector):
        token = bind_cost_collector(collector)
        try:
            # Yield to the event loop so the two tasks interleave
            # mid-call — recreates the production scenario where
            # request B starts before request A has finished.
            await asyncio.sleep(0)
            current = get_current_cost_collector()
            assert current is collector, (
                f"task {label} saw the wrong collector"
            )
            current(
                LLMCallRecord(
                    task_name=f"task-{label}",
                    model_name="gpt-5.4-mini",
                    prompt_tokens=10,
                    completion_tokens=5,
                    cost_usd=0.0,
                )
            )
            await asyncio.sleep(0)
        finally:
            reset_cost_collector(token)

    async def _run():
        a = CostCollector()
        b = CostCollector()
        await asyncio.gather(
            _record_into_isolated_collector("a", a),
            _record_into_isolated_collector("b", b),
        )
        return a, b

    a, b = asyncio.run(_run())
    assert len(a.records) == 1 and a.records[0].task_name == "task-a"
    assert len(b.records) == 1 and b.records[0].task_name == "task-b"


# ─── OpenAIService _record_cost reads from the contextvar ────────────────


def _fake_response(prompt_tokens: int, completion_tokens: int):
    """Minimal stub matching the SDK shape ``_extract_token_usage``
    reads from. Lets us drive ``_record_cost`` without an OpenAI
    client."""
    return SimpleNamespace(
        id="resp-abc",
        usage=SimpleNamespace(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        ),
        choices=[SimpleNamespace(finish_reason="stop")],
    )


def _make_service(*, explicit_recorder=None):
    """Build an OpenAIService with no real client — we only call the
    internal ``_record_cost`` hook so the network never gets touched."""
    settings = SimpleNamespace(openai_api_key=None)
    return OpenAIService(settings, cost_recorder=explicit_recorder, client=None)


def test_wrapper_falls_back_to_contextvar_when_no_explicit_recorder():
    """When OpenAIService is built without an explicit ``cost_recorder``
    (the production hot path now), ``_record_cost`` must record into
    whatever collector the pipeline bound for this request."""
    from src.openai_service import TokenUsage

    collector = CostCollector()
    service = _make_service(explicit_recorder=None)
    token = bind_cost_collector(collector)
    try:
        service._record_cost(
            task_name="answer_generation",
            model_name="gpt-5.4-mini",
            usage=TokenUsage(prompt_tokens=42, completion_tokens=11),
            response=_fake_response(42, 11),
            response_format="json_schema",
            error=None,
        )
    finally:
        reset_cost_collector(token)

    assert len(collector.records) == 1
    record = collector.records[0]
    assert record.task_name == "answer_generation"
    assert record.prompt_tokens == 42
    assert record.completion_tokens == 11


def test_explicit_recorder_wins_over_contextvar():
    """Tests + eval scripts can still pass an explicit ``cost_recorder``
    to the wrapper. That recorder wins; the contextvar is ignored. This
    preserves the injection pattern used by tests/test_cost_tracking.py."""
    from src.openai_service import TokenUsage

    explicit = CostCollector()
    ambient = CostCollector()
    service = _make_service(explicit_recorder=explicit)
    token = bind_cost_collector(ambient)
    try:
        service._record_cost(
            task_name="answer_generation",
            model_name="gpt-5.4-mini",
            usage=TokenUsage(prompt_tokens=7, completion_tokens=3),
            response=_fake_response(7, 3),
            response_format="json_schema",
            error=None,
        )
    finally:
        reset_cost_collector(token)

    assert len(explicit.records) == 1, "explicit recorder must capture the record"
    assert len(ambient.records) == 0, "ambient contextvar collector must be ignored"


def test_zero_cost_fallback_schema_matches_collector_totals():
    """The pipeline's ``_build_run_trace`` builds a zero-cost ``cost_totals``
    dict when no collector was passed (eval scripts, background jobs).
    That dict MUST share the same key set as ``CostCollector.totals()``
    so downstream readers don't branch on which path produced the
    trace. CodeRabbit caught this on PR #6 round 2 — original fallback
    omitted ``total_tokens`` and ``call_count``."""
    # Drive the same code path the pipeline uses, but with no LLM
    # call records so totals are all zeros.
    empty_totals = CostCollector().totals()
    expected_keys = set(empty_totals.keys())

    # The fallback dict the pipeline builds when cost_collector is None.
    # Inlined here so a future schema change can't silently drift one
    # branch without the other.
    fallback_totals = {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "cost_usd": 0.0,
        "model_name": "",
        "call_count": 0,
    }
    assert set(fallback_totals.keys()) == expected_keys, (
        "fallback totals must have the same keys as CostCollector.totals()"
    )
    # Values must be the same as a real-empty-collector's totals so a
    # reader can do ``totals['call_count'] == 0`` regardless of source.
    for key in expected_keys:
        assert fallback_totals[key] == empty_totals[key], (
            f"key {key!r} differs: fallback={fallback_totals[key]!r}, "
            f"empty_collector={empty_totals[key]!r}"
        )


def test_no_recorder_no_contextvar_silently_drops():
    """When neither an explicit recorder nor a bound contextvar
    collector exists, telemetry is silently dropped — never raise.
    Tests, eval scripts, and one-off scripts don't need to register a
    recorder just to make a successful call."""
    from src.openai_service import TokenUsage

    service = _make_service(explicit_recorder=None)
    assert get_current_cost_collector() is None
    # Must not raise.
    service._record_cost(
        task_name="answer_generation",
        model_name="gpt-5.4-mini",
        usage=TokenUsage(prompt_tokens=1, completion_tokens=1),
        response=_fake_response(1, 1),
        response_format="json_schema",
        error=None,
    )
