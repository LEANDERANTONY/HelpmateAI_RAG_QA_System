"""Unit tests for the schema-strict structured-output wrapper.

Covers:
  - Pydantic schema → OpenAI response_format translation (strict mode
    has fiddly invariants: required≡properties, no additional props)
  - Round-trip validation through ``run_structured_prompt`` with a
    mocked OpenAI client
  - Validation failures raise ``StructuredOutputError`` instead of
    silently letting bad payloads through
  - The cost recorder is invoked on success and on failure paths
  - The legacy ``run_json_prompt`` path still parses + reports cost

We deliberately do NOT call the real OpenAI API in this suite — the
client is mocked. The brief sets this expectation: "Tests:
tests/test_structured_outputs.py — Pydantic validation, the
response_format building, and integration with run_structured_prompt.
Mock the OpenAI call."
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
import pytest

from src.openai_service import (
    CostCollector,
    LLMCallRecord,
    OpenAIService,
    PRICING_PER_1M_TOKENS,
    StructuredOutputError,
    TokenUsage,
    _build_json_schema_response_format,
    _enforce_strict_schema,
    _supports_temperature,
    compute_cost_usd,
)
from src.schemas_llm_outputs import (
    AnswerOutput,
    QueryRouterOutput,
    SupportStatusVerifierOutput,
)
from src.config import Settings


# ─── fake OpenAI client plumbing ──────────────────────────────────────


@dataclass
class _FakeUsage:
    prompt_tokens: int = 0
    completion_tokens: int = 0


class _FakeMessage:
    def __init__(self, content: str):
        self.content = content


class _FakeChoice:
    def __init__(self, content: str, finish_reason: str = "stop"):
        self.message = _FakeMessage(content)
        self.finish_reason = finish_reason


class _FakeResponse:
    def __init__(
        self,
        content: str,
        *,
        usage: _FakeUsage | None = None,
        response_id: str = "resp_test",
        finish_reason: str = "stop",
    ):
        self.choices = [_FakeChoice(content, finish_reason=finish_reason)]
        self.usage = usage or _FakeUsage()
        self.id = response_id


class _FakeCompletions:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.responses: list[_FakeResponse] = []

    def queue(self, response: _FakeResponse) -> None:
        self.responses.append(response)

    def create(self, **kwargs: Any) -> _FakeResponse:
        self.calls.append(kwargs)
        if not self.responses:
            raise AssertionError("No fake response queued; check the test setup.")
        return self.responses.pop(0)


class _FakeChat:
    def __init__(self) -> None:
        self.completions = _FakeCompletions()


class _FakeClient:
    def __init__(self) -> None:
        self.chat = _FakeChat()


@pytest.fixture
def fake_client() -> _FakeClient:
    return _FakeClient()


@pytest.fixture
def settings() -> Settings:
    # ``openai_api_key`` is left set so the service doesn't try its
    # own SDK import — we'll pass in ``client=fake_client`` directly.
    return Settings(openai_api_key="test-key")


# ─── pricing table & cost computation ─────────────────────────────────


def test_pricing_table_covers_all_runtime_models():
    """Brief locks in the four-model pricing list. If a future config
    refactor renames a model, this assert is the canary."""
    assert set(PRICING_PER_1M_TOKENS.keys()) == {
        "gpt-5.4-nano",
        "gpt-5.4-mini",
        "gpt-5.4",
        "gpt-5.5",
    }


def test_compute_cost_usd_matches_table():
    # 1M prompt + 1M completion on gpt-5.4-mini = 0.75 + 4.50 = 5.25
    cost = compute_cost_usd("gpt-5.4-mini", 1_000_000, 1_000_000)
    assert cost == pytest.approx(5.25)


def test_compute_cost_usd_scales_linearly():
    # 1k prompt + 500 completion on gpt-5.4-nano:
    #   prompt   = 1000 * 0.10 / 1e6 = 1e-4
    #   complete =  500 * 0.40 / 1e6 = 2e-4
    cost = compute_cost_usd("gpt-5.4-nano", 1000, 500)
    assert cost == pytest.approx(3e-4)


def test_compute_cost_usd_resolves_date_suffixed_snapshots():
    """gpt-5.4-mini-2026-04-01 should still match the gpt-5.4-mini row.
    Without this prefix match, a SDK upgrade that returns the dated
    snapshot string would silently zero our cost column."""
    cost = compute_cost_usd("gpt-5.4-mini-2026-04-01", 1_000_000, 0)
    assert cost == pytest.approx(0.75)


def test_compute_cost_usd_returns_zero_for_unknown_model():
    # Defensive: a renamed model just records $0 — operator notices
    # the gap in the dashboard and updates the pricing table.
    assert compute_cost_usd("unknown-future-model", 1000, 1000) == 0.0


# ─── reasoning-model temperature suppression ──────────────────────────


def test_supports_temperature_rejects_reasoning_models():
    """Sentry HELPMATE-BACKEND-B regression. The gpt-5.x and o-series
    families are reasoning models and the OpenAI API rejects any
    non-default temperature with::

        Unsupported value: 'temperature' does not support 0 with this
        model. Only the default (1) value is supported.

    The helper must report these as NOT supporting a custom
    temperature so the API call sites can omit the param instead of
    passing the legacy default 0.0 that triggers the rejection.
    """
    # gpt-5 family — every Settings default routes here.
    assert _supports_temperature("gpt-5.5") is False
    assert _supports_temperature("gpt-5.4") is False
    assert _supports_temperature("gpt-5.4-mini") is False
    assert _supports_temperature("gpt-5.4-nano") is False
    assert _supports_temperature("gpt-5-mini") is False
    # o-series — OpenAI's reasoning model family.
    assert _supports_temperature("o1") is False
    assert _supports_temperature("o1-mini") is False
    assert _supports_temperature("o3") is False
    assert _supports_temperature("o3-mini") is False
    assert _supports_temperature("o4-mini") is False
    # Case-insensitivity: OpenAI sometimes returns the dated-suffix
    # snapshot string with mixed case; the helper must still classify it.
    assert _supports_temperature("GPT-5.4") is False
    assert _supports_temperature(" gpt-5.4-2026-04-01 ") is False


def test_supports_temperature_allows_traditional_models():
    """Non-reasoning models DO accept a custom temperature. The helper
    must not over-reject — otherwise gpt-4-class deployments would
    silently lose their sampling control."""
    assert _supports_temperature("gpt-4") is True
    assert _supports_temperature("gpt-4o") is True
    assert _supports_temperature("gpt-4o-mini") is True
    assert _supports_temperature("gpt-4-turbo") is True
    assert _supports_temperature("gpt-3.5-turbo") is True
    # Defensive: unknown / empty model name defaults to "supports" so
    # an unrecognised future model gets the same behavior as the
    # mainline non-reasoning class — pass temperature through, let
    # OpenAI reject if it doesn't like the value. Better than
    # silently stripping a param the new model might actually need.
    assert _supports_temperature("unknown-future-model") is True
    assert _supports_temperature("") is True
    assert _supports_temperature(None) is True


def test_run_structured_prompt_omits_temperature_for_reasoning_models(
    fake_client, settings,
):
    """Pin the wiring: when the model is a reasoning class, the
    chat.completions.create call must NOT contain a ``temperature``
    kwarg. The Sentry-tripping bug was passing
    ``temperature=0`` into ``client.chat.completions.create(...)``
    for ``gpt-5.5``; the API 400-ed before the SDK even returned.
    """
    service = OpenAIService(settings, client=fake_client)
    fake_client.chat.completions.queue(
        _FakeResponse(content='{"supported": true, "support_status": "supported", "answer": "ok", "reason": "evidence", "support_summary": "Cited evidence"}')
    )

    service.run_structured_prompt(
        system="sys",
        user="usr",
        task_name="answer_generation",
        model="gpt-5.5",
        response_model=AnswerOutput,
    )

    call = fake_client.chat.completions.calls[0]
    assert "temperature" not in call, (
        "gpt-5.5 is a reasoning model — temperature must be omitted, "
        f"got: {call.get('temperature')!r}"
    )
    # The other kwargs should still be wired (sanity).
    assert call["model"] == "gpt-5.5"
    assert call["response_format"]["type"] == "json_schema"


def test_run_structured_prompt_passes_temperature_for_traditional_models(
    fake_client, settings,
):
    """The opposite direction: gpt-4-class models should still receive
    the temperature kwarg. The fix is selective by model family —
    not a blanket strip."""
    service = OpenAIService(settings, client=fake_client)
    fake_client.chat.completions.queue(
        _FakeResponse(content='{"supported": true, "support_status": "supported", "answer": "ok", "reason": "evidence", "support_summary": "Cited evidence"}')
    )

    service.run_structured_prompt(
        system="sys",
        user="usr",
        task_name="answer_generation",
        model="gpt-4o-mini",
        response_model=AnswerOutput,
        temperature=0.2,
    )

    call = fake_client.chat.completions.calls[0]
    assert call.get("temperature") == 0.2


# ─── strict-schema JSON construction ──────────────────────────────────


def test_enforce_strict_schema_pins_required_and_no_additional_props():
    """OpenAI strict mode rejects schemas without these tightenings.
    Test the in-place mutator directly so the failure mode is obvious
    if a Pydantic upgrade changes the emitted schema shape."""
    schema = {
        "type": "object",
        "properties": {
            "a": {"type": "string"},
            "b": {"type": "integer"},
        },
    }
    _enforce_strict_schema(schema)
    assert schema["additionalProperties"] is False
    assert sorted(schema["required"]) == ["a", "b"]


def test_enforce_strict_schema_recurses_into_nested_objects():
    schema = {
        "type": "object",
        "properties": {
            "outer": {
                "type": "object",
                "properties": {"inner": {"type": "string"}},
            }
        },
    }
    _enforce_strict_schema(schema)
    assert schema["properties"]["outer"]["additionalProperties"] is False
    assert schema["properties"]["outer"]["required"] == ["inner"]


def test_build_response_format_for_answer_output_is_strict():
    blob = _build_json_schema_response_format(AnswerOutput)
    assert blob["type"] == "json_schema"
    assert blob["json_schema"]["name"] == "AnswerOutput"
    assert blob["json_schema"]["strict"] is True
    schema = blob["json_schema"]["schema"]
    assert schema["type"] == "object"
    assert schema["additionalProperties"] is False
    # AnswerOutput has 5 declared fields — all must appear in required.
    assert sorted(schema["required"]) == sorted(
        ["supported", "support_status", "answer", "reason", "support_summary"]
    )


def test_build_response_format_slug_strips_unsafe_chars():
    """OpenAI's response_format ``name`` is tightly character-restricted.
    Pin the slugging rule so a model class with a slash or dot can't
    leak through as an invalid schema name."""

    class WeirdName(AnswerOutput):
        pass

    WeirdName.__name__ = "Weird/Name.v1"
    blob = _build_json_schema_response_format(WeirdName)
    assert blob["json_schema"]["name"] == "Weird_Name_v1"


# ─── Pydantic model behavior ──────────────────────────────────────────


def test_answer_output_ignores_extra_keys():
    """INTENTIONAL contract change (was test_answer_output_rejects_
    extra_keys, extra='forbid'). Fail-closed against unknown keys is
    now enforced where it actually matters — the OpenAI strict
    response_format (`_enforce_strict_schema` force-sets
    additionalProperties:false server-side, independent of this
    config). Client-side Pydantic uses extra='ignore' so that IF
    strict mode is ever not honoured (non-strict model, truncated
    response), one stray key degrades gracefully instead of nuking the
    whole answer into a 'Schema drift' abstention. Required-field
    safety is unchanged (see the missing-field test below)."""
    out = AnswerOutput.model_validate(
        {
            "supported": True,
            "support_status": "supported",
            "answer": "...",
            "reason": None,
            "support_summary": None,
            "rogue_key": "dropped, not rejected",
        }
    )
    assert out.supported is True
    assert out.support_status == "supported"
    assert not hasattr(out, "rogue_key")


def test_answer_output_round_trips_minimal_required():
    payload = {
        "supported": True,
        "support_status": "supported",
        "answer": "The waiting period is 30 days.",
        "reason": None,
        "support_summary": None,
    }
    parsed = AnswerOutput.model_validate(payload)
    assert parsed.supported is True
    assert parsed.support_status == "supported"
    assert parsed.answer.startswith("The waiting period")


def test_query_router_output_validates_route_string():
    payload = QueryRouterOutput.model_validate(
        {"route": "chunk_first", "reason": "Explicit page hint."}
    )
    assert payload.route == "chunk_first"


def test_support_status_verifier_output_validates_lists():
    payload = SupportStatusVerifierOutput.model_validate(
        {
            "support_status": "partial",
            "answer_acknowledges_gap": True,
            "supported_facts": ["Fact A"],
            "missing_or_ambiguous_facts": ["Fact B"],
            "reason": "Mixed.",
        }
    )
    assert payload.support_status == "partial"
    assert payload.supported_facts == ["Fact A"]


# ─── run_structured_prompt integration ────────────────────────────────


def test_run_structured_prompt_returns_validated_instance(fake_client, settings):
    fake_client.chat.completions.queue(
        _FakeResponse(
            json.dumps(
                {
                    "supported": True,
                    "support_status": "supported",
                    "answer": "Yes.",
                    "reason": "Evidence clearly states it.",
                    "support_summary": "Cited evidence",
                }
            ),
            usage=_FakeUsage(prompt_tokens=120, completion_tokens=40),
        )
    )
    service = OpenAIService(settings, client=fake_client)
    result = service.run_structured_prompt(
        system="You answer questions.",
        user="What does the doc say?",
        task_name="answer_generation",
        model="gpt-5.4-mini",
        response_model=AnswerOutput,
    )

    assert isinstance(result, AnswerOutput)
    assert result.supported is True
    # The wrapper must have sent the strict json_schema response_format —
    # not the legacy json_object — so the API constrains generation.
    call_kwargs = fake_client.chat.completions.calls[0]
    assert call_kwargs["response_format"]["type"] == "json_schema"
    assert call_kwargs["response_format"]["json_schema"]["strict"] is True


def test_run_structured_prompt_raises_on_pydantic_validation_failure(fake_client, settings):
    """The API can technically still return a payload that fails our
    Pydantic validator (e.g. wrong type, missing required field) when
    the model has been wedged out of strict mode by a server-side
    failover. We catch that at the wrapper boundary so the caller can
    fall back."""
    fake_client.chat.completions.queue(
        _FakeResponse(
            # supported is missing — Pydantic must complain.
            json.dumps({"support_status": "supported", "answer": "hi"}),
            usage=_FakeUsage(prompt_tokens=10, completion_tokens=5),
        )
    )
    service = OpenAIService(settings, client=fake_client)
    with pytest.raises(StructuredOutputError):
        service.run_structured_prompt(
            system="sys",
            user="usr",
            task_name="answer_generation",
            model="gpt-5.4-mini",
            response_model=AnswerOutput,
        )


def test_run_structured_prompt_raises_on_invalid_json(fake_client, settings):
    fake_client.chat.completions.queue(
        _FakeResponse(
            "this is not JSON at all",
            usage=_FakeUsage(prompt_tokens=10, completion_tokens=2),
        )
    )
    service = OpenAIService(settings, client=fake_client)
    with pytest.raises(StructuredOutputError):
        service.run_structured_prompt(
            system="sys",
            user="usr",
            task_name="answer_generation",
            model="gpt-5.4-mini",
            response_model=AnswerOutput,
        )


def test_run_structured_prompt_raises_on_empty_content(fake_client, settings):
    fake_client.chat.completions.queue(
        _FakeResponse("", usage=_FakeUsage(prompt_tokens=10, completion_tokens=0))
    )
    service = OpenAIService(settings, client=fake_client)
    with pytest.raises(StructuredOutputError):
        service.run_structured_prompt(
            system="sys",
            user="usr",
            task_name="answer_generation",
            model="gpt-5.4-mini",
            response_model=AnswerOutput,
        )


# ─── run_json_prompt (legacy path) ───────────────────────────────────


def test_run_json_prompt_returns_parsed_payload(fake_client, settings):
    fake_client.chat.completions.queue(
        _FakeResponse(
            json.dumps({"route": "chunk_first", "reason": "page hint"}),
            usage=_FakeUsage(prompt_tokens=80, completion_tokens=20),
        )
    )
    service = OpenAIService(settings, client=fake_client)
    payload = service.run_json_prompt(
        system="sys",
        user="usr",
        task_name="query_router",
        model="gpt-5.4-nano",
        expected_keys=("route", "reason"),
    )
    assert payload == {"route": "chunk_first", "reason": "page hint"}


def test_run_json_prompt_returns_empty_dict_on_bad_json(fake_client, settings):
    fake_client.chat.completions.queue(
        _FakeResponse("not json", usage=_FakeUsage(prompt_tokens=10, completion_tokens=2))
    )
    service = OpenAIService(settings, client=fake_client)
    payload = service.run_json_prompt(
        system="sys",
        user="usr",
        task_name="query_router",
        model="gpt-5.4-nano",
    )
    assert payload == {}


# ─── client-not-available shortcuts ──────────────────────────────────


def test_run_structured_prompt_raises_when_client_absent():
    settings = Settings(openai_api_key=None)
    service = OpenAIService(settings)
    assert service.client is None
    with pytest.raises(RuntimeError):
        service.run_structured_prompt(
            system="sys",
            user="usr",
            task_name="answer_generation",
            model="gpt-5.4-mini",
            response_model=AnswerOutput,
        )


# ─── cost recording ──────────────────────────────────────────────────


def test_cost_recorder_called_on_success(fake_client, settings):
    fake_client.chat.completions.queue(
        _FakeResponse(
            json.dumps(
                {
                    "supported": True,
                    "support_status": "supported",
                    "answer": ".",
                    "reason": None,
                    "support_summary": None,
                }
            ),
            usage=_FakeUsage(prompt_tokens=200, completion_tokens=50),
        )
    )
    collector = CostCollector()
    service = OpenAIService(settings, client=fake_client, cost_recorder=collector)
    service.run_structured_prompt(
        system="sys",
        user="usr",
        task_name="answer_generation",
        model="gpt-5.4-mini",
        response_model=AnswerOutput,
    )

    assert len(collector.records) == 1
    record = collector.records[0]
    assert record.task_name == "answer_generation"
    assert record.model_name == "gpt-5.4-mini"
    assert record.prompt_tokens == 200
    assert record.completion_tokens == 50
    # 200 prompt + 50 completion on gpt-5.4-mini:
    #   prompt   = 200 * 0.75 / 1e6 = 1.5e-4
    #   complete =  50 * 4.50 / 1e6 = 2.25e-4
    assert record.cost_usd == pytest.approx(3.75e-4)
    assert record.error is None
    assert record.response_format == "json_schema"


def test_cost_recorder_called_on_validation_failure(fake_client, settings):
    """Token cost should still be recorded when the schema validation
    fails — we paid for the tokens regardless."""
    fake_client.chat.completions.queue(
        _FakeResponse(
            json.dumps({"only_one_key": "oops"}),
            usage=_FakeUsage(prompt_tokens=10, completion_tokens=3),
        )
    )
    collector = CostCollector()
    service = OpenAIService(settings, client=fake_client, cost_recorder=collector)
    with pytest.raises(StructuredOutputError):
        service.run_structured_prompt(
            system="sys",
            user="usr",
            task_name="answer_generation",
            model="gpt-5.4-mini",
            response_model=AnswerOutput,
        )
    assert len(collector.records) == 1
    assert collector.records[0].error is not None
    assert collector.records[0].prompt_tokens == 10


def test_cost_collector_totals_aggregate_and_pick_primary_model():
    collector = CostCollector()
    collector(LLMCallRecord(task_name="planner", model_name="gpt-5.4-nano", prompt_tokens=50, completion_tokens=10, cost_usd=1e-5))
    collector(LLMCallRecord(task_name="answer_generation", model_name="gpt-5.4-mini", prompt_tokens=500, completion_tokens=200, cost_usd=2e-3))
    collector(LLMCallRecord(task_name="verifier", model_name="gpt-5.4-mini", prompt_tokens=100, completion_tokens=30, cost_usd=2e-4))
    totals = collector.totals()
    assert totals["prompt_tokens"] == 650
    assert totals["completion_tokens"] == 240
    assert totals["total_tokens"] == 890
    assert totals["call_count"] == 3
    # The highest-cost call is the answer generation — its model_name
    # is what shows up in the trace row's primary ``model_name`` column.
    assert totals["model_name"] == "gpt-5.4-mini"
    assert totals["cost_usd"] == pytest.approx(2e-3 + 2e-4 + 1e-5)


def test_cost_collector_totals_for_empty_collector_returns_zeros():
    collector = CostCollector()
    totals = collector.totals()
    assert totals["prompt_tokens"] == 0
    assert totals["completion_tokens"] == 0
    assert totals["cost_usd"] == 0
    assert totals["model_name"] == ""
    assert totals["call_count"] == 0


# ─── token usage extraction ───────────────────────────────────────────


def test_token_usage_extracts_from_object_with_attributes():
    """SDK >=1.x returns a usage object with attributes — the path
    the production code mostly takes."""

    @dataclass
    class _Usage:
        prompt_tokens: int
        completion_tokens: int

    @dataclass
    class _Response:
        usage: _Usage

    from src.openai_service import _extract_token_usage

    usage = _extract_token_usage(_Response(_Usage(prompt_tokens=42, completion_tokens=7)))
    assert usage.prompt_tokens == 42
    assert usage.completion_tokens == 7
    assert usage.total_tokens == 49


def test_token_usage_extracts_from_dict_fallback():
    """Older or wrapped SDK responses sometimes pass usage as a dict.
    The extractor handles both shapes — the cost column would be a lie
    otherwise.

    Use a plain object so attribute access falls through to None and
    the extractor takes the dict branch."""

    class _Response:
        def __init__(self, usage: Any):
            self.usage = usage

    from src.openai_service import _extract_token_usage

    response = _Response({"prompt_tokens": 11, "completion_tokens": 2})
    usage = _extract_token_usage(response)
    assert usage.prompt_tokens == 11
    assert usage.completion_tokens == 2


def test_token_usage_returns_zero_when_response_has_no_usage():
    from src.openai_service import _extract_token_usage

    # A response without a ``usage`` attr should yield zero — most
    # commonly happens when a SDK upgrade restructures the shape.
    response = object()
    usage = _extract_token_usage(response)
    assert usage.prompt_tokens == 0
    assert usage.completion_tokens == 0


# ─── extra="ignore" contract (defence-in-depth for non-strict paths) ────


def test_structured_model_ignores_benign_extra_key_not_abstains():
    """Regression guard: when strict mode is NOT honoured and the model
    returns a valid payload PLUS a stray key, validation must DROP the
    key and succeed — not raise (which the call sites route to a hard
    'Schema drift' / unsupported fallback). Was extra='forbid'."""
    payload = {
        "supported": True,
        "support_status": "supported",
        "support_summary": "Cited evidence",
        "answer": "The editor was Steven B. Kennedy [Source 1].",
        "reason": "Directly stated.",
        "confidence": 0.92,          # stray key a non-strict model added
        "citations": ["Source 1"],   # another stray key
    }
    out = AnswerOutput.model_validate(payload)
    assert out.supported is True
    assert out.support_status == "supported"
    assert out.answer.startswith("The editor was Steven B. Kennedy")
    # Extra keys are dropped, not retained.
    assert not hasattr(out, "confidence")


def test_structured_model_still_rejects_missing_required_field():
    """The fix must NOT become over-permissive: a genuinely malformed
    payload (missing required ``answer``) must still fail validation so
    the call site falls back instead of presenting a hollow answer.
    (Route-value validity is intentionally NOT a Pydantic concern —
    QueryRouterOutput.route is a plain str, re-checked against
    VALID_ROUTES in query_router + enforced by the OpenAI strict
    response_format. extra='ignore' does not touch required/type
    enforcement, which is what this guards.)"""
    with pytest.raises(Exception):  # pydantic ValidationError
        AnswerOutput.model_validate(
            {"supported": True, "support_status": "supported"}  # no answer
        )
