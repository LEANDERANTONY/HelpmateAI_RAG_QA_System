from __future__ import annotations

import json
from dataclasses import replace
from typing import Any

from src.config import Settings
from src.query_analysis import QueryAnalyzer, QueryProfile
from src.query_router import QueryRouter
from src.schemas import RetrievalPlan, SectionSynopsisRecord
from src.topology import DocumentTopologyService


class RetrievalPlanner:
    VALID_INTENT_TYPES = {"lookup", "summary", "procedure", "numeric", "comparison", "cross_cutting"}
    VALID_QUERY_TYPES = {
        "general_lookup",
        "summary_lookup",
        "definition_lookup",
        "process_lookup",
        "numeric_lookup",
        "comparison_lookup",
        "cross_cutting_lookup",
    }
    VALID_EVIDENCE_SPREADS = {"atomic", "sectional", "distributed", "global"}
    VALID_ROUTES = {"chunk_first", "section_first", "hybrid_both", "synopsis_first"}
    VALID_CONSTRAINT_MODES = {"none", "soft_local", "soft_multi_region", "hard_region"}
    VALID_REGION_KINDS = {"overview", "definitions", "procedure", "evidence", "discussion", "rules", "appendix", "general"}
    VALID_CONTENT_TYPES = {
        "general",
        "definition",
        "definitions",
        "methodology",
        "process",
        "procedure",
        "results",
        "discussion",
        "overview",
        "rules",
        "evidence",
    }

    def __init__(self, settings: Settings, router: QueryRouter | None = None):
        self.settings = settings
        self.router = router or QueryRouter(settings)
        self.query_analyzer = QueryAnalyzer()
        self.topology_service = DocumentTopologyService()
        self.client = None
        if settings.openai_api_key and settings.planner_llm_enabled:
            try:
                from openai import OpenAI

                self.client = OpenAI(api_key=settings.openai_api_key)
            except Exception:
                self.client = None

    @staticmethod
    def _target_region_kinds(query_profile: QueryProfile) -> list[str]:
        if query_profile.has_explicit_constraint:
            return []
        if query_profile.asks_for_definition:
            return ["definitions", "general"]
        if query_profile.intent_type == "procedure":
            return ["procedure", "evidence", "general"]
        if query_profile.intent_type == "numeric":
            return ["evidence", "discussion", "general"]
        if query_profile.intent_type == "comparison":
            return ["evidence", "discussion", "overview"]
        if query_profile.intent_type == "summary":
            return ["overview", "discussion", "evidence"]
        if query_profile.intent_type == "cross_cutting":
            return ["rules", "discussion", "evidence", "general"]
        return ["general", "rules", "definitions"]

    @staticmethod
    def _constraint_mode(query_profile: QueryProfile, metadata_filters: dict[str, list[str]]) -> str:
        if metadata_filters.get("page_labels") or metadata_filters.get("clause_terms") or metadata_filters.get("section_terms"):
            return "hard_region"
        if query_profile.evidence_spread == "sectional":
            return "soft_local"
        if query_profile.evidence_spread in {"distributed", "global"}:
            return "soft_multi_region"
        return "none"

    @staticmethod
    def _preferred_route(query_profile: QueryProfile, constraint_mode: str) -> str:
        if constraint_mode == "hard_region":
            return "chunk_first"
        if query_profile.evidence_spread == "atomic":
            return "chunk_first"
        if query_profile.intent_type == "procedure" and query_profile.asks_for_specific_detail:
            return "hybrid_both"
        if query_profile.evidence_spread in {"sectional", "global"}:
            return "synopsis_first"
        if query_profile.evidence_spread == "distributed":
            return "synopsis_first"
        return "hybrid_both"

    def _deterministic_confidence(
        self,
        query_profile: QueryProfile,
        metadata_filters: dict[str, list[str]],
        target_region_ids: list[str],
        constraint_mode: str,
    ) -> float:
        if constraint_mode == "hard_region":
            planner_confidence = 0.92
        elif query_profile.evidence_spread == "atomic":
            planner_confidence = 0.86
        elif query_profile.evidence_spread == "global":
            planner_confidence = 0.78
        elif query_profile.evidence_spread == "sectional":
            planner_confidence = 0.74
        else:
            planner_confidence = 0.66

        if constraint_mode in {"soft_local", "soft_multi_region"} and not target_region_ids:
            planner_confidence -= 0.08
        if metadata_filters.get("section_terms") and not target_region_ids:
            planner_confidence -= 0.08
        return max(planner_confidence, 0.0)

    def _build_plan(
        self,
        *,
        question: str,
        query_profile: QueryProfile,
        metadata_filters: dict[str, list[str]],
        synopses: list[SectionSynopsisRecord],
        preferred_route: str | None = None,
        constraint_mode: str | None = None,
        target_region_kinds: list[str] | None = None,
        use_global_fallback: bool | None = None,
        planner_confidence: float | None = None,
        planner_source: str = "deterministic",
    ) -> RetrievalPlan:
        resolved_constraint_mode = constraint_mode or self._constraint_mode(query_profile, metadata_filters)
        resolved_preferred_route = preferred_route or self._preferred_route(query_profile, resolved_constraint_mode)
        resolved_target_region_kinds = target_region_kinds if target_region_kinds is not None else self._target_region_kinds(query_profile)
        target_region_ids = self.topology_service.select_candidate_region_ids(
            question,
            synopses,
            target_region_kinds=resolved_target_region_kinds,
            explicit_section_terms=metadata_filters.get("section_terms", []),
            top_k=self.settings.planner_candidate_region_limit,
        )
        hard_filters = {key: value for key, value in metadata_filters.items() if value}
        resolved_use_global_fallback = (
            use_global_fallback
            if use_global_fallback is not None
            else resolved_constraint_mode in {"soft_local", "soft_multi_region"}
        )
        resolved_confidence = (
            planner_confidence
            if planner_confidence is not None
            else self._deterministic_confidence(query_profile, metadata_filters, target_region_ids, resolved_constraint_mode)
        )
        return RetrievalPlan(
            intent_type=query_profile.intent_type,
            evidence_spread=query_profile.evidence_spread,
            constraint_mode=resolved_constraint_mode,
            preferred_route=resolved_preferred_route,
            target_region_ids=target_region_ids,
            target_region_kinds=resolved_target_region_kinds,
            hard_filters=hard_filters,
            use_global_fallback=resolved_use_global_fallback,
            planner_confidence=max(resolved_confidence, 0.0),
            planner_source=planner_source,
        )

    def _planning_prompt(
        self,
        *,
        question: str,
        baseline_profile: QueryProfile,
        deterministic_plan: RetrievalPlan,
        metadata_filters: dict[str, list[str]],
        synopses: list[SectionSynopsisRecord],
    ) -> str:
        synopsis_lines = []
        for synopsis in synopses[:8]:
            synopsis_lines.append(
                f"- {synopsis.section_id}: {synopsis.title} [{synopsis.region_kind}] "
                f"pages={', '.join(synopsis.page_labels[:2]) or 'n/a'}"
            )

        prompt_lines = [
            "Plan retrieval for a grounded long-document QA system.",
            "Return compact JSON only.",
            "Choose both the query taxonomy and the bounded retrieval schema.",
            "You may correct the baseline if the question is broader, more distributed, or more summary-oriented than the lexical hints suggest.",
            "",
            "Return keys:",
            "- intent_type",
            "- query_type",
            "- evidence_spread",
            "- preferred_route",
            "- constraint_mode",
            "- target_region_kinds",
            "- preferred_content_types",
            "- use_global_fallback",
            "- confidence",
            "- reason",
            "",
            f"Valid intent_type values: {sorted(self.VALID_INTENT_TYPES)}",
            f"Valid query_type values: {sorted(self.VALID_QUERY_TYPES)}",
            f"Valid evidence_spread values: {sorted(self.VALID_EVIDENCE_SPREADS)}",
            f"Valid preferred_route values: {sorted(self.VALID_ROUTES)}",
            f"Valid constraint_mode values: {sorted(self.VALID_CONSTRAINT_MODES)}",
            f"Valid target_region_kinds values: {sorted(self.VALID_REGION_KINDS)}",
            "",
            "Planning guidance:",
            "- Use chunk_first for exact localized lookup, page/clause-grounded facts, or narrow definitions.",
            "- Use synopsis_first for broad section-level narrowing or synthesis before chunk retrieval.",
            "- Use hybrid_both for distributed evidence, mixed-intent, or broad methodology/results questions spanning multiple sections.",
            "- Use hard_region only when explicit page/clause/section filters are present.",
            "- Use soft_local for bounded section-level questions.",
            "- Use soft_multi_region for broad or distributed questions.",
            "",
            f"Question: {question}",
            f"Explicit metadata filters: {json.dumps(metadata_filters)}",
            f"Baseline query profile: {json.dumps(baseline_profile.__dict__)}",
            f"Deterministic plan: {json.dumps(deterministic_plan.to_dict())}",
            "Available synopsis regions:",
        ]
        prompt_lines.extend(synopsis_lines if synopsis_lines else ["- none"])
        return "\n".join(prompt_lines)

    @staticmethod
    def _coerce_float(value: Any, default: float) -> float:
        try:
            return float(value)
        except Exception:
            return default

    @staticmethod
    def _coerce_bool(value: Any, default: bool) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            lowered = value.strip().lower()
            if lowered in {"1", "true", "yes", "on"}:
                return True
            if lowered in {"0", "false", "no", "off"}:
                return False
        return default

    def _sanitized_list(self, values: Any, valid_values: set[str]) -> list[str]:
        if not isinstance(values, list):
            return []
        cleaned: list[str] = []
        for value in values:
            item = str(value).strip().lower()
            if item and item in valid_values and item not in cleaned:
                cleaned.append(item)
        return cleaned

    def _llm_plan_payload(
        self,
        *,
        question: str,
        baseline_profile: QueryProfile,
        deterministic_plan: RetrievalPlan,
        metadata_filters: dict[str, list[str]],
        synopses: list[SectionSynopsisRecord],
    ) -> dict[str, Any] | None:
        if self.client is None or not self.settings.planner_llm_enabled:
            return None

        prompt = self._planning_prompt(
            question=question,
            baseline_profile=baseline_profile,
            deterministic_plan=deterministic_plan,
            metadata_filters=metadata_filters,
            synopses=synopses,
        )
        try:
            response = self.client.chat.completions.create(
                model=self.settings.planner_model,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You produce structured retrieval plans for a grounded long-document QA system. "
                            "Reply with strict JSON only."
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
                response_format={"type": "json_object"},
            )
            payload = json.loads(response.choices[0].message.content or "{}")
        except Exception:
            return None
        return payload if isinstance(payload, dict) else None

    def _query_profile_from_payload(self, baseline_profile: QueryProfile, payload: dict[str, Any]) -> QueryProfile:
        intent_type = str(payload.get("intent_type", "")).strip().lower()
        query_type = str(payload.get("query_type", "")).strip().lower()
        evidence_spread = str(payload.get("evidence_spread", "")).strip().lower()
        preferred_content_types = self._sanitized_list(payload.get("preferred_content_types"), self.VALID_CONTENT_TYPES)

        return replace(
            baseline_profile,
            intent_type=intent_type if intent_type in self.VALID_INTENT_TYPES else baseline_profile.intent_type,
            query_type=query_type if query_type in self.VALID_QUERY_TYPES else baseline_profile.query_type,
            evidence_spread=evidence_spread if evidence_spread in self.VALID_EVIDENCE_SPREADS else baseline_profile.evidence_spread,
            preferred_content_types=preferred_content_types or baseline_profile.preferred_content_types,
        )

    def _plan_from_payload(
        self,
        *,
        question: str,
        query_profile: QueryProfile,
        metadata_filters: dict[str, list[str]],
        synopses: list[SectionSynopsisRecord],
        payload: dict[str, Any],
        deterministic_plan: RetrievalPlan,
    ) -> RetrievalPlan:
        constraint_mode = str(payload.get("constraint_mode", "")).strip().lower()
        preferred_route = str(payload.get("preferred_route", "")).strip().lower()
        target_region_kinds = self._sanitized_list(payload.get("target_region_kinds"), self.VALID_REGION_KINDS)
        use_global_fallback = self._coerce_bool(payload.get("use_global_fallback"), deterministic_plan.use_global_fallback)
        confidence = self._coerce_float(payload.get("confidence"), 0.82)

        resolved_constraint_mode = deterministic_plan.constraint_mode
        if metadata_filters.get("page_labels") or metadata_filters.get("clause_terms") or metadata_filters.get("section_terms"):
            resolved_constraint_mode = "hard_region"
        elif constraint_mode in self.VALID_CONSTRAINT_MODES and constraint_mode != "hard_region":
            resolved_constraint_mode = constraint_mode

        resolved_preferred_route = deterministic_plan.preferred_route
        if preferred_route in self.VALID_ROUTES:
            resolved_preferred_route = preferred_route
        if resolved_constraint_mode == "hard_region":
            resolved_preferred_route = "chunk_first"

        resolved_target_region_kinds = target_region_kinds or deterministic_plan.target_region_kinds
        resolved_use_global_fallback = (
            use_global_fallback if resolved_constraint_mode in {"soft_local", "soft_multi_region"} else False
        )
        if resolved_constraint_mode == "hard_region":
            resolved_target_region_kinds = []

        return self._build_plan(
            question=question,
            query_profile=query_profile,
            metadata_filters=metadata_filters,
            synopses=synopses,
            preferred_route=resolved_preferred_route,
            constraint_mode=resolved_constraint_mode,
            target_region_kinds=resolved_target_region_kinds,
            use_global_fallback=resolved_use_global_fallback,
            planner_confidence=max(confidence, deterministic_plan.planner_confidence),
            planner_source="llm_structured",
        )

    def analyze_and_plan(
        self,
        *,
        question: str,
        metadata_filters: dict[str, list[str]],
        synopses: list[SectionSynopsisRecord],
    ) -> tuple[QueryProfile, RetrievalPlan]:
        baseline_profile = self.query_analyzer.analyze(question)
        deterministic_plan = self.plan(
            question=question,
            query_profile=baseline_profile,
            metadata_filters=metadata_filters,
            synopses=synopses,
        )
        payload = self._llm_plan_payload(
            question=question,
            baseline_profile=baseline_profile,
            deterministic_plan=deterministic_plan,
            metadata_filters=metadata_filters,
            synopses=synopses,
        )
        if not payload:
            return baseline_profile, deterministic_plan

        query_profile = self._query_profile_from_payload(baseline_profile, payload)
        plan = self._plan_from_payload(
            question=question,
            query_profile=query_profile,
            metadata_filters=metadata_filters,
            synopses=synopses,
            payload=payload,
            deterministic_plan=deterministic_plan,
        )
        if (
            query_profile == baseline_profile
            and plan.preferred_route == deterministic_plan.preferred_route
            and plan.constraint_mode == deterministic_plan.constraint_mode
            and plan.target_region_kinds == deterministic_plan.target_region_kinds
            and plan.use_global_fallback == deterministic_plan.use_global_fallback
        ):
            return baseline_profile, deterministic_plan
        return query_profile, plan

    def plan(
        self,
        *,
        question: str,
        query_profile: QueryProfile,
        metadata_filters: dict[str, list[str]],
        synopses: list[SectionSynopsisRecord],
    ) -> RetrievalPlan:
        planner = self._build_plan(
            question=question,
            query_profile=query_profile,
            metadata_filters=metadata_filters,
            synopses=synopses,
        )
        if planner.planner_confidence >= self.settings.planner_confidence_threshold:
            return planner

        routing = self.router.route(question, query_profile)
        if routing.source != "llm_fallback":
            return planner
        return self._build_plan(
            question=question,
            query_profile=query_profile,
            metadata_filters=metadata_filters,
            synopses=synopses,
            preferred_route=routing.route,
            constraint_mode=planner.constraint_mode,
            target_region_kinds=planner.target_region_kinds,
            use_global_fallback=planner.use_global_fallback,
            planner_confidence=max(planner.planner_confidence, routing.confidence),
            planner_source=routing.source,
        )
