from __future__ import annotations

from src.config import Settings
from src.query_analysis import QueryProfile
from src.query_router import QueryRouter
from src.schemas import RetrievalPlan, SectionSynopsisRecord
from src.topology import DocumentTopologyService


class RetrievalPlanner:
    def __init__(self, settings: Settings, router: QueryRouter | None = None):
        self.settings = settings
        self.router = router or QueryRouter(settings)
        self.topology_service = DocumentTopologyService()

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

    def plan(
        self,
        *,
        question: str,
        query_profile: QueryProfile,
        metadata_filters: dict[str, list[str]],
        synopses: list[SectionSynopsisRecord],
    ) -> RetrievalPlan:
        constraint_mode = self._constraint_mode(query_profile, metadata_filters)
        preferred_route = self._preferred_route(query_profile, constraint_mode)
        target_region_kinds = self._target_region_kinds(query_profile)
        target_region_ids = self.topology_service.select_candidate_region_ids(
            question,
            synopses,
            target_region_kinds=target_region_kinds,
            explicit_section_terms=metadata_filters.get("section_terms", []),
            top_k=self.settings.planner_candidate_region_limit,
        )
        hard_filters = {key: value for key, value in metadata_filters.items() if value}
        use_global_fallback = constraint_mode in {"soft_local", "soft_multi_region"}

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

        planner = RetrievalPlan(
            intent_type=query_profile.intent_type,
            evidence_spread=query_profile.evidence_spread,
            constraint_mode=constraint_mode,
            preferred_route=preferred_route,
            target_region_ids=target_region_ids,
            target_region_kinds=target_region_kinds,
            hard_filters=hard_filters,
            use_global_fallback=use_global_fallback,
            planner_confidence=max(planner_confidence, 0.0),
            planner_source="deterministic",
        )

        if planner.planner_confidence >= self.settings.planner_confidence_threshold:
            return planner

        routing = self.router.route(question, query_profile)
        if routing.source != "llm_fallback":
            return planner
        return RetrievalPlan(
            intent_type=planner.intent_type,
            evidence_spread=planner.evidence_spread,
            constraint_mode=planner.constraint_mode,
            preferred_route=routing.route,
            target_region_ids=planner.target_region_ids,
            target_region_kinds=planner.target_region_kinds,
            hard_filters=planner.hard_filters,
            use_global_fallback=planner.use_global_fallback,
            planner_confidence=max(planner.planner_confidence, routing.confidence),
            planner_source=routing.source,
        )
