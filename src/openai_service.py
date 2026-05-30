"""Central OpenAI client wrapper with schema-strict structured outputs.

Until the Production Safety Pack landed, the codebase called the
OpenAI Python SDK directly from a dozen places — every call site
wrote its own JSON-mode prompt, parsed the result, and recovered (or
didn't) from validation failures. That works fine until the upstream
model returns a JSON object that's *valid* but has the wrong shape:
silent drift makes its way through to /qa, where it surfaces as a
broken answer instead of a model-level rejection.

This module centralizes two patterns:

1. ``run_json_prompt(...)`` — the legacy path. Keeps the existing
   ``response_format={"type": "json_object"}`` behavior and adds
   ``expected_keys`` validation. Useful when the downstream code
   tolerates a missing key and applies a default (see
   ``query_router``).
2. ``run_structured_prompt(..., response_model=...)`` — schema-strict
   path. Builds an OpenAI ``json_schema`` ``response_format`` from a
   Pydantic model so the API guarantees the result parses cleanly,
   and validates the parsed payload through ``response_model`` before
   returning. Any drift surfaces as a ``StructuredOutputError`` the
   caller can either retry or fall back from — never silently.

Both methods also report token usage and computed USD cost back to a
caller-supplied callback. This is what feeds the cost-tracking column
in ``helpmate_run_traces`` (Item 3 of the Production Safety Pack).
The callback is optional so unit tests don't have to construct a
trace store.

We deliberately keep this thin — it's not a full SDK shim. Call sites
that still want raw access to ``client.chat.completions.create`` (the
ragas judge wrapper, the file-search benchmark, the embedding model
calls) can keep doing that. The goal is to migrate the *high-value*
schema-bearing calls (answer generation, query routing, support
verification, planner LLM) onto the strict path, not every call
across the codebase.

Note on package location: this module lives under ``backend/`` rather
than ``src/`` because the original implementation session could not
write to ``src/`` from the agent harness. Logical home is ``src/`` —
the next refactor pass should ``mv backend/openai_service.py
src/openai_service.py`` and update the imports. The ``schemas.py``
sibling alongside it lists the migration recipe for the high-value
call sites (answer generation, router, support verifier).
"""
from __future__ import annotations

import contextvars
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, TypeVar

from pydantic import BaseModel, ValidationError

from src.config import Settings


logger = logging.getLogger(__name__)


# Output-token ceilings (L7). Reasoning models (gpt-5.x) emit unbounded
# reasoning tokens when uncapped — a cost risk, not a truncation one — so
# every call sets max_completion_tokens above its realistic payload size.
# Parsers return full JSON answers/plans (generous 6000); verifiers return a
# tiny (supported, reason)-style verdict (2000). Sized to clear real payloads
# while still bounding a runaway reasoning loop.
DEFAULT_PARSER_MAX_COMPLETION_TOKENS = 6000
DEFAULT_VERIFIER_MAX_COMPLETION_TOKENS = 2000


# ContextVar holding the *current request's* CostCollector. The pipeline
# binds a fresh collector here at the start of each ``/qa`` request and
# resets it in a ``finally`` after ``_build_run_trace`` reads totals.
#
# Why a ContextVar (not a pipeline-instance attribute):
#   ``HelpmatePipeline`` is constructed by ``@lru_cache`` in
#   ``backend/main.py:_pipeline()`` and reused across requests. Anything
#   we attached to ``self`` would be process-scoped — under concurrent
#   ``/qa`` calls, records from request A and B interleave on the same
#   list and one request's ``records.clear()`` wipes the other's
#   in-flight telemetry. A ContextVar inherits the natural per-task
#   isolation FastAPI gives async coroutines, so each request sees its
#   own collector without any synchronization.
#
# Subsystems (AnswerGenerator, QueryRouter) still accept an explicit
# ``cost_collector`` for tests + eval scripts that want a captured
# reference; when omitted, the wrapper reads from this ContextVar at
# record time. That preserves test injection while making production
# automatically request-scoped.
_current_cost_collector: contextvars.ContextVar["CostCollector | None"] = contextvars.ContextVar(
    "helpmate_current_cost_collector",
    default=None,
)


def bind_cost_collector(collector: "CostCollector | None") -> contextvars.Token:
    """Bind ``collector`` as the current-request CostCollector and
    return a Token the caller must ``reset_cost_collector`` with once
    the request is done. Pair the two in a ``try`` / ``finally`` so a
    failed request still leaves the contextvar in a clean state."""
    return _current_cost_collector.set(collector)


def reset_cost_collector(token: contextvars.Token) -> None:
    _current_cost_collector.reset(token)


def get_current_cost_collector() -> "CostCollector | None":
    """Read the current request's CostCollector, or None when no
    request has bound one (eval scripts, background jobs, tests that
    don't care about cost telemetry)."""
    return _current_cost_collector.get()


# OpenAI prices in USD per 1M tokens. Updated alongside the model list
# in src/config.py. Keeping the table close to the wrapper means a
# model rename + price change is a one-line edit. The keys match the
# strings stored in ``settings.answer_model`` / ``settings.router_model``
# etc.; lookups fall back to a zero-cost row so a new model just
# silently records $0 instead of crashing the pipeline.
PRICING_PER_1M_TOKENS: dict[str, dict[str, float]] = {
    "gpt-5.4-nano": {"prompt": 0.10, "completion": 0.40},
    "gpt-5.4-mini": {"prompt": 0.75, "completion": 4.50},
    "gpt-5.4": {"prompt": 2.00, "completion": 10.00},
    "gpt-5.5": {"prompt": 5.00, "completion": 30.00},
}


T = TypeVar("T", bound=BaseModel)


class StructuredOutputError(RuntimeError):
    """Raised when the model returns content that the Pydantic schema
    cannot validate. Callers typically fall back to a deterministic
    answer or a retry with a stricter system prompt rather than
    bubbling this up to the user."""


@dataclass
class TokenUsage:
    prompt_tokens: int = 0
    completion_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    def to_dict(self) -> dict[str, int]:
        return {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
        }


@dataclass
class LLMCallRecord:
    """A single LLM call's outcome — what we record into the run trace
    so the cost-per-query column has something to read.

    The pipeline aggregates these across the retrieval → generation →
    verification chain into a single per-query row before persisting.
    """

    task_name: str
    model_name: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0
    raw_response_id: str = ""
    raw_finish_reason: str = ""
    response_format: str = "json_object"
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_name": self.task_name,
            "model_name": self.model_name,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.prompt_tokens + self.completion_tokens,
            "cost_usd": round(self.cost_usd, 6),
            "raw_response_id": self.raw_response_id,
            "raw_finish_reason": self.raw_finish_reason,
            "response_format": self.response_format,
            "error": self.error,
        }


# Callback type the wrapper uses to report per-call telemetry. The
# pipeline registers a callback that appends to a list it then folds
# into the run trace payload before persistence.
CostRecorder = Callable[[LLMCallRecord], None]


def compute_cost_usd(model_name: str, prompt_tokens: int, completion_tokens: int) -> float:
    """Translate token counts into USD using the pricing table above.
    Unknown models record as $0 so a missing-row doesn't crash — the
    user will see a 0 cost in the trace and can fix the table once
    they notice."""
    pricing = PRICING_PER_1M_TOKENS.get(model_name)
    if pricing is None:
        # Try a relaxed lookup: ``gpt-5.4-mini-2026-04-01`` should match
        # ``gpt-5.4-mini``. We pick the longest prefix that matches an
        # entry in the table to handle date-suffixed model snapshots.
        candidates = [key for key in PRICING_PER_1M_TOKENS if model_name.startswith(key)]
        if not candidates:
            logger.debug("compute_cost_usd: no pricing row for model %s", model_name)
            return 0.0
        pricing = PRICING_PER_1M_TOKENS[max(candidates, key=len)]
    prompt_cost = prompt_tokens * pricing["prompt"] / 1_000_000
    completion_cost = completion_tokens * pricing["completion"] / 1_000_000
    return float(prompt_cost + completion_cost)


def _extract_token_usage(response: Any) -> TokenUsage:
    """OpenAI returns usage as either a dataclass-like object or a dict
    depending on the SDK version. Handle both — we don't want a SDK
    upgrade to silently zero out the cost-tracking column."""
    usage = getattr(response, "usage", None)
    if usage is None:
        return TokenUsage()
    prompt = getattr(usage, "prompt_tokens", None)
    if prompt is None and isinstance(usage, dict):
        prompt = usage.get("prompt_tokens")
    completion = getattr(usage, "completion_tokens", None)
    if completion is None and isinstance(usage, dict):
        completion = usage.get("completion_tokens")
    return TokenUsage(
        prompt_tokens=int(prompt or 0),
        completion_tokens=int(completion or 0),
    )


def _build_json_schema_response_format(response_model: type[BaseModel]) -> dict[str, Any]:
    """Translate a Pydantic model into an OpenAI ``response_format`` blob
    that the API can enforce at generation time.

    OpenAI's structured-output spec requires ``strict: true`` so the
    model is constrained to produce content matching the schema. The
    ``name`` field has to match a tightly limited character set —
    we slug the model's class name.
    """
    schema = response_model.model_json_schema()
    # OpenAI's ``strict`` mode rejects schemas with ``additionalProperties``
    # not explicitly set to false. Pydantic emits it implicitly so we
    # patch it in. We also pin every property as ``required`` because
    # strict mode demands required ≡ properties.
    _enforce_strict_schema(schema)
    name = "".join(ch if ch.isalnum() or ch == "_" else "_" for ch in response_model.__name__)
    return {
        "type": "json_schema",
        "json_schema": {
            "name": name[:64] or "structured_output",
            "schema": schema,
            "strict": True,
        },
    }


def _supports_temperature(model: str | None) -> bool:
    """Return False for OpenAI reasoning models that reject a custom
    ``temperature`` parameter.

    Sentry HELPMATE-BACKEND-B (2026-05-27): the answer-generation path
    was passing ``temperature=0`` to ``gpt-5.5`` and the OpenAI API
    400-ed with::

        Unsupported value: 'temperature' does not support 0 with this
        model. Only the default (1) value is supported.

    The o-series (o1/o3/o4) and the gpt-5.x family all behave this way
    — they're reasoning models that ignore sampling temperature and
    refuse non-default values at the API layer. Our default
    ``temperature=0.0`` was set when the codebase still ran on
    gpt-4-class models; every configured model in ``Settings`` now
    points at a reasoning model, so the default is universally wrong.

    Detection by name prefix because OpenAI also rolls these out as
    ``gpt-5-mini``, ``gpt-5.4-nano``, ``gpt-5.5`` etc. — anything
    starting with ``gpt-5`` is in the family.
    """
    lower = (model or "").strip().lower()
    if not lower:
        return True
    if lower.startswith(("o1", "o3", "o4")):
        return False
    if lower.startswith("gpt-5"):
        return False
    return True


def _enforce_strict_schema(schema: dict[str, Any]) -> None:
    """Walk the JSON schema in-place and tighten it for OpenAI strict
    mode. The two constraints we care about:

      1. Every object must have ``additionalProperties: false`` so the
         model can't slip in stray keys.
      2. Every object must list every property name in ``required``.
         OpenAI strict mode requires required-equals-properties; for
         truly optional fields, callers should use a union with
         ``None`` and a default of ``None`` so the schema permits null.
    """
    if not isinstance(schema, dict):
        return
    if schema.get("type") == "object" and isinstance(schema.get("properties"), dict):
        schema.setdefault("additionalProperties", False)
        if "required" not in schema:
            schema["required"] = list(schema["properties"].keys())
        else:
            existing = set(schema["required"])
            for name in schema["properties"].keys():
                if name not in existing:
                    schema["required"].append(name)
    for value in schema.values():
        if isinstance(value, dict):
            _enforce_strict_schema(value)
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    _enforce_strict_schema(item)


class OpenAIService:
    """Thin wrapper over the OpenAI SDK chat-completions endpoint with
    schema-strict structured-output support and built-in cost
    telemetry.

    Construction is forgiving: a missing API key leaves ``client``
    as None so call sites that already handle no-network paths
    (fallback answer, heuristic router decision) keep working
    without code changes. Real call sites do their own ``client is None``
    check before invoking ``run_json_prompt`` / ``run_structured_prompt``.
    """

    def __init__(
        self,
        settings: Settings,
        *,
        cost_recorder: CostRecorder | None = None,
        client: Any | None = None,
    ):
        self.settings = settings
        self.cost_recorder = cost_recorder
        self.client = client
        if self.client is None and settings.openai_api_key:
            try:
                from openai import OpenAI  # local import keeps the test suite cheap

                self.client = OpenAI(api_key=settings.openai_api_key)
            except Exception as exc:  # pragma: no cover — exercised via failure paths
                logger.warning(
                    "OpenAIService client setup failed (%s); falling back to None.",
                    exc.__class__.__name__,
                )
                self.client = None

    # ─── legacy JSON-mode entry point ──────────────────────────────────

    def run_json_prompt(
        self,
        *,
        system: str,
        user: str,
        task_name: str,
        model: str,
        expected_keys: tuple[str, ...] = (),
        temperature: float = 0.0,
        max_completion_tokens: int | None = DEFAULT_PARSER_MAX_COMPLETION_TOKENS,
    ) -> dict[str, Any]:
        """Legacy JSON-mode call. The model returns a JSON object that
        is parsed and lightly shape-checked via ``expected_keys`` but
        is NOT schema-constrained at generation time. Prefer
        ``run_structured_prompt`` for anything user-visible.

        Returns the parsed dict. On parse errors we return an empty
        dict so call sites can apply their existing defaults without
        a try/except dance — they were doing this anyway via the
        inline ``json.loads`` blocks we replaced.
        """
        if self.client is None:
            raise RuntimeError(f"OpenAI client is not available for task {task_name!r}.")
        kwargs = self._completion_kwargs(model=model, max_completion_tokens=max_completion_tokens)
        # Omit ``temperature`` entirely for reasoning models (gpt-5.x,
        # o-series) — they reject any non-default value at the API
        # layer. See ``_supports_temperature`` above for the Sentry
        # incident that motivated this guard.
        if _supports_temperature(model):
            kwargs["temperature"] = temperature
        response = self.client.chat.completions.create(
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            response_format={"type": "json_object"},
            **kwargs,
        )
        content = response.choices[0].message.content or "{}"
        usage = _extract_token_usage(response)
        self._record_cost(
            task_name=task_name,
            model_name=model,
            usage=usage,
            response=response,
            response_format="json_object",
            error=None,
        )
        try:
            payload = json.loads(content)
        except json.JSONDecodeError:
            logger.warning("run_json_prompt: invalid JSON for task=%s", task_name)
            return {}
        if expected_keys:
            missing = [key for key in expected_keys if key not in payload]
            if missing:
                logger.warning(
                    "run_json_prompt: task=%s missing expected keys %s",
                    task_name,
                    missing,
                )
        return payload

    # ─── schema-strict entry point ─────────────────────────────────────

    def run_structured_prompt(
        self,
        *,
        system: str,
        user: str,
        task_name: str,
        model: str,
        response_model: type[T],
        temperature: float = 0.0,
        max_completion_tokens: int | None = DEFAULT_PARSER_MAX_COMPLETION_TOKENS,
    ) -> T:
        """Schema-strict call. The OpenAI API is configured to constrain
        the generated content to a JSON shape matching ``response_model``
        and we validate it through Pydantic before returning the
        instance.

        Raises ``StructuredOutputError`` if validation fails — call
        sites either retry with a stricter system prompt or fall back
        to a deterministic local answer. Either is better than
        returning a stale/wrong payload to the user.
        """
        if self.client is None:
            raise RuntimeError(f"OpenAI client is not available for task {task_name!r}.")
        response_format = _build_json_schema_response_format(response_model)
        kwargs = self._completion_kwargs(model=model, max_completion_tokens=max_completion_tokens)
        # Omit ``temperature`` entirely for reasoning models (gpt-5.x,
        # o-series) — they reject any non-default value at the API
        # layer. See ``_supports_temperature`` above for the Sentry
        # incident (HELPMATE-BACKEND-B, 2026-05-27) that motivated
        # this guard.
        if _supports_temperature(model):
            kwargs["temperature"] = temperature
        response = self.client.chat.completions.create(
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            response_format=response_format,
            **kwargs,
        )
        content = response.choices[0].message.content or ""
        usage = _extract_token_usage(response)
        try:
            if not content.strip():
                raise StructuredOutputError(
                    f"task={task_name}: structured-output response was empty."
                )
            payload = json.loads(content)
            validated = response_model.model_validate(payload)
        except StructuredOutputError as exc:
            self._record_cost(
                task_name=task_name,
                model_name=model,
                usage=usage,
                response=response,
                response_format="json_schema",
                error=str(exc),
            )
            raise
        except json.JSONDecodeError as exc:
            error_message = f"task={task_name}: invalid JSON from model ({exc.msg})"
            self._record_cost(
                task_name=task_name,
                model_name=model,
                usage=usage,
                response=response,
                response_format="json_schema",
                error=error_message,
            )
            raise StructuredOutputError(error_message) from exc
        except ValidationError as exc:
            error_message = f"task={task_name}: Pydantic validation failed ({exc.error_count()} errors)"
            self._record_cost(
                task_name=task_name,
                model_name=model,
                usage=usage,
                response=response,
                response_format="json_schema",
                error=error_message,
            )
            raise StructuredOutputError(error_message) from exc
        self._record_cost(
            task_name=task_name,
            model_name=model,
            usage=usage,
            response=response,
            response_format="json_schema",
            error=None,
        )
        return validated

    # ─── helpers ───────────────────────────────────────────────────────

    @staticmethod
    def _completion_kwargs(model: str, *, max_completion_tokens: int | None) -> dict[str, Any]:
        kwargs: dict[str, Any] = {"model": model}
        if max_completion_tokens is not None:
            kwargs["max_completion_tokens"] = max_completion_tokens
        return kwargs

    def _record_cost(
        self,
        *,
        task_name: str,
        model_name: str,
        usage: TokenUsage,
        response: Any,
        response_format: str,
        error: str | None,
    ) -> None:
        # Recorder resolution order:
        #   1. Explicit ``self.cost_recorder`` captured at __init__
        #      (tests + eval scripts that want a known reference).
        #   2. Current-request CostCollector from the ContextVar
        #      (production hot path — bound by the pipeline at the
        #      start of each /qa call).
        #   3. None: telemetry is silently dropped.
        recorder = self.cost_recorder
        if recorder is None:
            recorder = get_current_cost_collector()
        if recorder is None:
            return
        try:
            response_id = str(getattr(response, "id", "") or "")
            finish_reason = ""
            choices = getattr(response, "choices", None)
            if choices:
                finish_reason = str(getattr(choices[0], "finish_reason", "") or "")
            record = LLMCallRecord(
                task_name=task_name,
                model_name=model_name,
                prompt_tokens=usage.prompt_tokens,
                completion_tokens=usage.completion_tokens,
                cost_usd=compute_cost_usd(model_name, usage.prompt_tokens, usage.completion_tokens),
                raw_response_id=response_id,
                raw_finish_reason=finish_reason,
                response_format=response_format,
                error=error,
            )
            recorder(record)
        except Exception:  # pragma: no cover — telemetry must never crash a request
            logger.exception("cost_recorder raised while recording task=%s", task_name)


@dataclass
class CostCollector:
    """In-memory aggregator for LLM call records over the lifetime of
    a single ``/qa`` request. The pipeline registers one of these as
    the ``cost_recorder`` and folds the contents into the run trace
    before persisting.

    The collector exposes ``totals()`` for the trace store's per-row
    summary (which is what populates the new ``prompt_tokens`` /
    ``completion_tokens`` / ``cost_usd`` columns) plus the per-call
    breakdown in the payload JSON for debugging.
    """

    records: list[LLMCallRecord] = field(default_factory=list)

    def __call__(self, record: LLMCallRecord) -> None:
        self.records.append(record)

    def totals(self) -> dict[str, Any]:
        prompt_tokens = sum(record.prompt_tokens for record in self.records)
        completion_tokens = sum(record.completion_tokens for record in self.records)
        cost_usd = sum(record.cost_usd for record in self.records)
        # The "primary" model is the highest-cost call's model — usually
        # the answer-generation call. Used as the single trace-row
        # ``model_name`` value so dashboards can group by it without
        # needing to read every per-call breakdown.
        primary_model = ""
        if self.records:
            primary = max(self.records, key=lambda record: record.cost_usd)
            primary_model = primary.model_name
        return {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
            "cost_usd": round(cost_usd, 6),
            "model_name": primary_model,
            "call_count": len(self.records),
        }

    def to_payload(self) -> dict[str, Any]:
        return {
            "totals": self.totals(),
            "calls": [record.to_dict() for record in self.records],
        }
