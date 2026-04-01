from __future__ import annotations

import json
from dataclasses import dataclass, field

from src.config import Settings
from src.query_analysis import QueryProfile


@dataclass(frozen=True)
class RoutingDecision:
    route: str
    confidence: float
    reasons: list[str] = field(default_factory=list)


class QueryRouter:
    SECTION_HINTS = (
        "summary",
        "summarize",
        "overview",
        "main focus",
        "main idea",
        "main argument",
        "what does the paper argue",
        "conclusion",
        "limitations",
        "future work",
        "future directions",
        "key takeaway",
        "compare",
        "challenge",
        "argue",
        "main aim",
        "research objectives",
        "next steps",
    )
    STRONG_SECTION_HINTS = (
        "main focus",
        "main idea",
        "main argument",
        "what does the paper argue",
        "conclusion",
        "limitations",
        "future work",
        "future directions",
        "key takeaway",
        "clinical adoption",
        "main finding",
        "main aim",
        "research objectives",
        "next steps",
    )
    FACTUAL_HINTS = (
        "what is",
        "how many",
        "when",
        "which",
        "define",
        "exact",
        "page ",
        "clause ",
        "auc",
        "accuracy",
        "split",
        "how was",
    )

    def __init__(self, settings: Settings | None = None):
        self.settings = settings
        self.client = None
        if settings and settings.openai_api_key and settings.router_llm_enabled:
            try:
                from openai import OpenAI

                self.client = OpenAI(api_key=settings.openai_api_key)
            except Exception:
                self.client = None

    def _heuristic_route(self, question: str, query_profile: QueryProfile) -> RoutingDecision:
        lowered = question.lower()
        section_score = sum(1 for term in self.SECTION_HINTS if term in lowered)
        section_score += sum(2 for term in self.STRONG_SECTION_HINTS if term in lowered)
        factual_score = sum(1 for term in self.FACTUAL_HINTS if term in lowered)
        reasons: list[str] = []

        if query_profile.query_type == "summary_lookup":
            section_score += 3
            reasons.append("Summary-style query type prefers section-level routing.")

        if query_profile.query_type in {"definition_lookup", "waiting_period_lookup", "benefit_lookup", "exclusion_lookup", "process_lookup"}:
            factual_score += 2
            reasons.append(f"Query type {query_profile.query_type} prefers chunk-level grounding.")

        if any(term in lowered for term in ("abstract", "conclusion", "future work", "discussion", "results", "main finding", "challenge")):
            section_score += 2
            reasons.append("High-level narrative cues suggest section-first retrieval.")

        if "paper" in lowered and any(term in lowered for term in ("main focus", "main idea", "main conclusion", "argue", "challenge")):
            section_score += 2
            reasons.append("Paper-level synthesis phrasing favors section-first retrieval.")

        if section_score >= factual_score + 2:
            confidence = min(0.95, 0.6 + 0.1 * (section_score - factual_score))
            return RoutingDecision("section_first", confidence, reasons or ["Broad summary cues dominated the query."])
        if factual_score >= section_score + 2:
            confidence = min(0.95, 0.6 + 0.1 * (factual_score - section_score))
            return RoutingDecision("chunk_first", confidence, reasons or ["Exact factual cues dominated the query."])
        return RoutingDecision("hybrid_both", 0.55, reasons or ["Signals were mixed, so both retrieval paths will run."])

    def _llm_route(self, question: str, query_profile: QueryProfile, current: RoutingDecision) -> RoutingDecision:
        if self.client is None or self.settings is None:
            return current
        if current.confidence > self.settings.router_confidence_threshold or current.route != "hybrid_both":
            return current

        prompt = (
            "Classify the best retrieval route for this question in a long-document QA system.\n"
            "Routes:\n"
            "- chunk_first: exact fact, number, clause, definition, metric, split, page-specific lookup\n"
            "- section_first: broad summary, main aim, conclusion, future work, cross-section synthesis\n"
            "- hybrid_both: meaningfully needs both or is genuinely mixed\n\n"
            "Return compact JSON with keys route and reason.\n\n"
            f"Query type: {query_profile.query_type}\n"
            f"Question: {question}"
        )
        try:
            response = self.client.chat.completions.create(
                model=self.settings.router_model,
                messages=[
                    {"role": "system", "content": "You classify retrieval routes for a document QA pipeline."},
                    {"role": "user", "content": prompt},
                ],
                response_format={"type": "json_object"},
            )
            payload = json.loads(response.choices[0].message.content or "{}")
            route = payload.get("route")
            if route not in {"chunk_first", "section_first", "hybrid_both"}:
                return current
            reason = str(payload.get("reason", "")).strip() or "LLM router resolved the mixed query."
            return RoutingDecision(route=route, confidence=0.7, reasons=[*current.reasons, reason])
        except Exception:
            return current

    def route(self, question: str, query_profile: QueryProfile) -> RoutingDecision:
        heuristic = self._heuristic_route(question, query_profile)
        return self._llm_route(question, query_profile, heuristic)
