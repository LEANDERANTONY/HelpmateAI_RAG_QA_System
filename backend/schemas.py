"""Pydantic LLM output models for schema-strict structured outputs.

These ride on top of ``backend.openai_service.run_structured_prompt``
and double as:

  1. The shape we hand to OpenAI's ``response_format`` so the API
     constrains generation to match.
  2. The Pydantic validator we run on the parsed payload before
     returning to the caller.

Strict mode caveats (see ``_enforce_strict_schema`` in
``openai_service.py``):
  - Every object must list every property in ``required``.
  - ``additionalProperties: false`` is forced on every object.

So fields that are truly optional should be typed ``X | None`` with
a default of ``None`` rather than dropped from ``required`` — strict
mode lets a property be null, just not absent.

Note on package location: this module belongs in ``src/schemas.py``
alongside the existing dataclasses there. The original Production
Safety Pack session couldn't write under ``src/`` from the agent
harness, so the new Pydantic models landed under ``backend/`` instead.
The next refactor pass should move them into the existing
``src/schemas.py`` file. The non-LLM schemas (DocumentRecord,
RetrievalCandidate, …) already live there and we don't want two
parallel schema modules long-term.
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class StructuredLLMModel(BaseModel):
    """Shared base — pins the strict-output settings in one place.

    ``extra="forbid"`` mirrors OpenAI strict mode's
    ``additionalProperties: false``: a model that sneaks in an extra
    key will fail validation rather than silently passing through.
    """

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        frozen=False,
    )


class AnswerOutput(StructuredLLMModel):
    """Schema-strict payload returned by the answer-generation prompt.

    The current free-form ``build_grounded_prompt`` already asks the
    model to return these keys; the strict path turns "asks" into
    "enforced at generation time" so a future prompt drift can't slip
    through silently to /qa.
    """

    supported: bool = Field(
        description="True only when every required fact is grounded in the evidence.",
    )
    support_status: str = Field(
        description='One of "supported", "partial", or "unsupported".',
    )
    answer: str = Field(
        description="Final grounded answer text. May include [Source N] citations inline.",
    )
    reason: str | None = Field(
        default=None,
        description="Optional rationale; surfaces in the trace as the 'note' field.",
    )
    support_summary: str | None = Field(
        default=None,
        description="Short chip-friendly tagline (≤5 words). Auto-defaulted downstream if empty.",
    )


class QueryRouterOutput(StructuredLLMModel):
    """Schema-strict payload for the router's LLM fallback decision.

    The router falls through to this LLM only when the heuristic
    decision was below ``router_confidence_threshold``. The strict
    schema ensures the LLM cannot return a route label outside the
    allowed set — invalid labels surface as a ``StructuredOutputError``
    that the router catches to keep the heuristic answer.

    We deliberately keep the route as a plain string rather than a
    Literal so the schema-build path stays simple; the router code
    re-validates against ``VALID_ROUTES`` (defined in ``query_router``)
    before applying the LLM's choice.
    """

    route: str = Field(
        description=(
            'One of "chunk_first", "section_first", "hybrid_both", "synopsis_first".'
        ),
    )
    reason: str = Field(description="One-line explanation of the chosen route.")


class SupportStatusVerifierOutput(StructuredLLMModel):
    """Schema-strict payload for the support-status verifier prompt.

    Today's free-form prompt asks the model for five keys
    (``support_status``, ``answer_acknowledges_gap``, ``supported_facts``,
    ``missing_or_ambiguous_facts``, ``reason``); the strict schema
    matches that shape. Lists are typed as ``list[str]`` so the
    JSON-schema generator emits ``type: array, items: {type: string}``
    — string-only is enough for the downstream consumer.
    """

    support_status: str = Field(
        description='One of "supported", "partial", "unsupported".',
    )
    answer_acknowledges_gap: bool = Field(
        description="True when the answer text visibly names the missing/ambiguous fact.",
    )
    supported_facts: list[str] = Field(
        default_factory=list,
        description="Facts the evidence clearly supports.",
    )
    missing_or_ambiguous_facts: list[str] = Field(
        default_factory=list,
        description="Facts the question asked for that the evidence does not clearly support.",
    )
    reason: str = Field(description="Short justification for the verifier's classification.")


class SupportVerifierOutput(StructuredLLMModel):
    """Schema-strict payload for the legacy support-verifier prompt
    (the simpler supported/reason pair used during abstention recovery).
    """

    supported: bool = Field(description="True when the recovered answer is fully grounded.")
    reason: str = Field(description="Justification for the supported flag.")
