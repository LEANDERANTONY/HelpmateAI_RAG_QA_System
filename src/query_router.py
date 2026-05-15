from __future__ import annotations

import json
from dataclasses import dataclass, field

from src.config import Settings
from src.openai_service import CostCollector, OpenAIService, StructuredOutputError
from src.query_analysis import QueryProfile
from src.schemas_llm_outputs import QueryRouterOutput


VALID_ROUTES = {"chunk_first", "section_first", "hybrid_both", "synopsis_first"}


@dataclass(frozen=True)
class RoutingDecision:
    route: str
    confidence: float
    reasons: list[str] = field(default_factory=list)
    source: str = "heuristic"


class QueryRouter:
    SYNOPSIS_HINTS = (
        "future work",
        "key takeaway",
        "main conclusion",
        "main focus",
        "overview",
        "summary",
        "summarize",
        "what did the thesis conclude",
        "what does the paper say about",
    )
    CHUNK_HINTS = ("clause ", "define", "exact", "page ", "quote", "what is", "what does")

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        cost_collector: CostCollector | None = None,
    ):
        # cost_collector is provided by the pipeline so a single
        # CostCollector aggregates LLM calls from every subsystem
        # (generator + router) into one per-request total. When omitted
        # (eval scripts, unit tests, anything that doesn't care about
        # cost tracking), the router builds its own local collector.
        self.settings = settings
        self.cost_collector = cost_collector or CostCollector()
        self.client = None
        if settings and settings.openai_api_key and settings.router_llm_enabled:
            try:
                from openai import OpenAI

                self.client = OpenAI(api_key=settings.openai_api_key)
            except Exception:
                self.client = None

    def _wrapper(self) -> OpenAIService | None:
        """Build an OpenAIService around the current ``self.client``,
        or return None when the router doesn't have a live client.

        Constructed per-call rather than once in __init__ for the same
        reason as the generator: tests monkeypatch ``router.client``
        after construction and we want those fakes to propagate.
        """
        if self.settings is None or self.client is None:
            return None
        return OpenAIService(
            self.settings,
            cost_recorder=self.cost_collector,
            client=self.client,
        )

    def _heuristic_route(self, question: str, query_profile: QueryProfile) -> RoutingDecision:
        lowered = question.lower()
        reasons: list[str] = []

        if query_profile.has_explicit_constraint and any(term in lowered for term in self.CHUNK_HINTS):
            return RoutingDecision(
                route="chunk_first",
                confidence=0.9,
                reasons=["Explicit page/clause-style cues favor direct chunk grounding."],
            )

        if query_profile.evidence_spread == "atomic":
            return RoutingDecision(
                route="chunk_first",
                confidence=0.84,
                reasons=["Atomic evidence spread favors exact chunk retrieval."],
            )

        if query_profile.evidence_spread == "global":
            return RoutingDecision(
                route="synopsis_first",
                confidence=0.84,
                reasons=["Global synthesis questions benefit from synopsis-first planning."],
            )

        if query_profile.evidence_spread == "sectional":
            confidence = 0.78 if any(term in lowered for term in self.SYNOPSIS_HINTS) else 0.7
            return RoutingDecision(
                route="synopsis_first",
                confidence=confidence,
                reasons=["Sectional questions should narrow regions before chunk search."],
            )

        if query_profile.evidence_spread == "distributed":
            return RoutingDecision(
                route="hybrid_both",
                confidence=0.62,
                reasons=["Distributed questions benefit from region guidance plus a global fallback pool."],
            )

        reasons.append("Signals remained mixed after deterministic planning.")
        return RoutingDecision(route="hybrid_both", confidence=0.55, reasons=reasons)

    def _llm_route(self, question: str, query_profile: QueryProfile, current: RoutingDecision) -> RoutingDecision:
        if self.client is None or self.settings is None:
            return current
        if current.confidence > self.settings.router_confidence_threshold:
            return current

        prompt = (
            "Choose the best retrieval route for a long-document QA system.\n"
            "Routes:\n"
            "- chunk_first: exact fact, explicit page/clause, definition, or tightly localized lookup\n"
            "- section_first: a legacy section-scoped retrieval mode when synopsis-first is unnecessary\n"
            "- hybrid_both: distributed evidence or genuinely mixed intent\n"
            "- synopsis_first: broad synthesis or section-level narrowing before chunk retrieval\n\n"
            "Return compact JSON with keys route and reason.\n\n"
            f"Intent: {query_profile.intent_type}\n"
            f"Evidence spread: {query_profile.evidence_spread}\n"
            f"Question: {question}"
        )
        # Schema-strict path: the QueryRouterOutput Pydantic model
        # constrains the LLM to produce {route, reason}; an unknown
        # route surfaces here as a StructuredOutputError (when the
        # validator rejects) or — more often — as a valid-but-wrong
        # route string that the post-validation check below catches.
        # In either case we keep the heuristic decision, so a drifted
        # LLM cannot poison routing.
        wrapper = self._wrapper()
        if wrapper is None:
            return current
        try:
            output = wrapper.run_structured_prompt(
                system="You classify retrieval routes for a document QA pipeline.",
                user=prompt,
                task_name="query_router",
                model=self.settings.router_model,
                response_model=QueryRouterOutput,
            )
        except StructuredOutputError:
            return current
        except Exception:
            return current
        if output.route not in VALID_ROUTES:
            return current
        reason = output.reason.strip() or "LLM fallback refined the retrieval route."
        return RoutingDecision(
            route=output.route,
            confidence=0.72,
            reasons=[*current.reasons, reason],
            source="llm_fallback",
        )

    def route(self, question: str, query_profile: QueryProfile) -> RoutingDecision:
        heuristic = self._heuristic_route(question, query_profile)
        return self._llm_route(question, query_profile, heuristic)
