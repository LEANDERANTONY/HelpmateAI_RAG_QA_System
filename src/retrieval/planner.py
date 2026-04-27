from __future__ import annotations

import json
import re
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
    VALID_SCOPE_STRICTNESS = {"none", "soft", "hard"}
    VALID_REGION_KINDS = {"overview", "definitions", "procedure", "evidence", "discussion", "rules", "appendix", "general"}
    LOW_VALUE_FRONT_MATTER_KINDS = {
        "acknowledgements",
        "certificate",
        "contents",
        "declaration",
        "dedication",
        "list_of_figures",
        "list_of_tables",
        "preface",
    }
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
    VALID_FOCUS_TERMS = {
        "answer",
        "background",
        "conclusions",
        "findings",
        "implementation",
        "limitations",
        "methodology",
        "recommendations",
        "results",
        "summary",
    }

    def __init__(self, settings: Settings, router: QueryRouter | None = None):
        self.settings = settings
        self.router = router or QueryRouter(settings)
        self.query_analyzer = QueryAnalyzer()
        self.topology_service = DocumentTopologyService()
        self.client = None
        if settings.openai_api_key and (settings.planner_llm_enabled or settings.retrieval_orchestrator_enabled):
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
    def _is_low_value_synopsis(synopsis: SectionSynopsisRecord) -> bool:
        metadata = synopsis.metadata or {}
        front_matter_kind = str(metadata.get("front_matter_kind", "")).lower()
        return bool(
            metadata.get("topology_low_value")
            or metadata.get("low_value_section_flag")
            or front_matter_kind in RetrievalPlanner.LOW_VALUE_FRONT_MATTER_KINDS
        )

    @staticmethod
    def _document_map_item(synopsis: SectionSynopsisRecord) -> dict[str, Any]:
        metadata = synopsis.metadata or {}
        section_path = metadata.get("section_path", [])
        if not isinstance(section_path, list):
            section_path = [str(section_path)] if section_path else []
        scope_labels = metadata.get("document_scope_labels", [])
        if not isinstance(scope_labels, list):
            scope_labels = [str(scope_labels)] if scope_labels else []
        aliases = metadata.get("section_aliases", [])
        if not isinstance(aliases, list):
            aliases = [str(aliases)] if aliases else []
        return {
            "section_id": synopsis.section_id,
            "title": synopsis.title,
            "region_kind": synopsis.region_kind,
            "page_labels": synopsis.page_labels[:3],
            "section_path": section_path[-4:],
            "chapter_number": metadata.get("chapter_number"),
            "chapter_title": metadata.get("chapter_title", ""),
            "document_section_role": metadata.get("document_section_role", ""),
            "scope_labels": scope_labels[:8],
            "aliases": aliases[:8],
        }

    @staticmethod
    def _token_set(value: str) -> set[str]:
        return {token for token in re.findall(r"[A-Za-z0-9]+", value.lower()) if len(token) > 2}

    def _likely_scope_items(self, question: str, synopses: list[SectionSynopsisRecord]) -> list[dict[str, Any]]:
        question_terms = self._token_set(question)
        if not question_terms:
            return []
        scored: list[tuple[float, SectionSynopsisRecord]] = []
        for synopsis in synopses:
            if self._is_low_value_synopsis(synopsis):
                continue
            item = self._document_map_item(synopsis)
            searchable = " ".join(
                [
                    str(item.get("title", "")),
                    str(item.get("chapter_title", "")),
                    str(item.get("document_section_role", "")),
                    " ".join(item.get("section_path", [])),
                    " ".join(item.get("scope_labels", [])),
                    " ".join(item.get("aliases", [])),
                ]
            )
            item_terms = self._token_set(searchable)
            if not item_terms:
                continue
            overlap = len(question_terms & item_terms)
            if overlap <= 0:
                continue
            scored.append((overlap / max(len(question_terms), 1), synopsis))
        return [
            self._document_map_item(synopsis)
            for _, synopsis in sorted(scored, key=lambda pair: pair[0], reverse=True)[:20]
        ]

    def _orchestrator_prompt(
        self,
        *,
        question: str,
        baseline_profile: QueryProfile,
        deterministic_plan: RetrievalPlan,
        metadata_filters: dict[str, list[str]],
        synopses: list[SectionSynopsisRecord],
        previous_payload: dict[str, Any] | None = None,
    ) -> str:
        document_map = [
            self._document_map_item(synopsis)
            for synopsis in synopses[: self.settings.retrieval_orchestrator_max_sections]
            if not self._is_low_value_synopsis(synopsis)
        ]
        prompt = {
            "task": "Plan retrieval scope for a grounded long-document QA system. Do not answer the question.",
            "question": question,
            "baseline_query_profile": baseline_profile.__dict__,
            "deterministic_plan": deterministic_plan.to_dict(),
            "explicit_metadata_filters": metadata_filters,
            "likely_scope_sections": self._likely_scope_items(question, synopses),
            "available_sections": document_map,
            "return_json_keys": [
                "intent_type",
                "query_type",
                "evidence_spread",
                "preferred_route",
                "scope_strictness",
                "resolved_scope_ids",
                "scope_query",
                "answer_focus",
                "use_global_fallback",
                "confidence",
                "reason",
            ],
            "rules": [
                "Choose resolved_scope_ids only from available_sections.section_id. Never invent IDs.",
                "Use likely_scope_sections as the first place to resolve named local scopes.",
                "If the question asks in/from/within a named chapter, section, appendix, page, or other local part, scope_strictness must be hard.",
                "For a hard scope, the retrieval must stay inside resolved_scope_ids even if words like conclusion, summary, or findings appear.",
                "For a hard scope, return the minimal coherent section IDs for the requested local part, not every globally relevant section.",
                "Treat words such as summary, conclusions, findings, limitations, or recommendations as answer_focus unless the user asks for the global document conclusion.",
                "Use soft only when the user implies a nearby area but does not explicitly bound the answer.",
                "Use none for whole-document questions.",
            ],
        }
        if previous_payload is not None:
            prompt["previous_rejected_output"] = previous_payload
            prompt["retry_instruction"] = (
                "The previous output could not be enforced because it did not produce a coherent non-none scope. "
                "Return a corrected minimal scope using only available section IDs."
            )
        return json.dumps(prompt, ensure_ascii=True)

    def _orchestrator_payload(
        self,
        *,
        question: str,
        baseline_profile: QueryProfile,
        deterministic_plan: RetrievalPlan,
        metadata_filters: dict[str, list[str]],
        synopses: list[SectionSynopsisRecord],
        previous_payload: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        if self.client is None or not self.settings.retrieval_orchestrator_enabled:
            return None
        if not synopses:
            return None

        try:
            response = self.client.chat.completions.create(
                model=self.settings.retrieval_orchestrator_model,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You are a retrieval orchestration manager. "
                            "Return strict JSON only and never answer the user's question."
                        ),
                    },
                    {
                        "role": "user",
                        "content": self._orchestrator_prompt(
                            question=question,
                            baseline_profile=baseline_profile,
                            deterministic_plan=deterministic_plan,
                            metadata_filters=metadata_filters,
                            synopses=synopses,
                            previous_payload=previous_payload,
                        ),
                    },
                ],
                response_format={"type": "json_object"},
            )
            payload = json.loads(response.choices[0].message.content or "{}")
        except Exception:
            return None
        return payload if isinstance(payload, dict) else None

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

    @staticmethod
    def _dedupe_strings(values: list[str]) -> list[str]:
        cleaned: list[str] = []
        for value in values:
            item = str(value).strip()
            if item and item not in cleaned:
                cleaned.append(item)
        return cleaned

    def _valid_section_ids(self, values: Any, synopses: list[SectionSynopsisRecord]) -> list[str]:
        if not isinstance(values, list):
            return []
        known_ids = {synopsis.section_id for synopsis in synopses}
        return self._dedupe_strings([str(value).strip() for value in values if str(value).strip() in known_ids])

    @classmethod
    def _normalize_scope_strictness(cls, value: Any) -> str:
        raw = str(value or "").strip().lower().replace("-", "_")
        if raw in {"hard", "hard_local", "hard_region", "strict", "strict_local"}:
            return "hard"
        if raw in {"soft", "soft_local", "soft_multi_region", "local"}:
            return "soft"
        if raw in cls.VALID_SCOPE_STRICTNESS:
            return raw
        return "none"

    @staticmethod
    def _has_explicit_local_scope(question: str, metadata_filters: dict[str, list[str]]) -> bool:
        if metadata_filters.get("page_labels") or metadata_filters.get("clause_terms") or metadata_filters.get("section_terms"):
            return True
        lowered = question.lower()
        if re.search(r"\b(?:chapter|section|appendix|page|clause)\s+[a-z0-9ivxlcdm]", lowered):
            return True
        if re.search(r"\b(?:in|from|within|inside)\s+the\s+[a-z0-9][a-z0-9 ._-]{2,80}?\s+(?:chapter|section|appendix)\b", lowered):
            return True
        return False

    @staticmethod
    def _answer_focus_from_question(question: str) -> list[str]:
        lowered = question.lower()
        focus: list[str] = []
        for focus_term, patterns in {
            "summary": ("summary", "summarize", "overview"),
            "conclusions": ("conclusion", "conclusions", "takeaway", "takeaways"),
            "findings": ("finding", "findings"),
            "results": ("result", "results", "outcome", "outcomes"),
            "limitations": ("challenge", "challenges", "limitation", "limitations", "constraint", "constraints"),
            "methodology": ("methodology", "method", "methods"),
            "implementation": ("implementation", "implemented"),
            "recommendations": ("recommendation", "recommendations"),
        }.items():
            if any(pattern in lowered for pattern in patterns):
                focus.append(focus_term)
        return focus

    @staticmethod
    def _synopsis_by_id(synopses: list[SectionSynopsisRecord]) -> dict[str, SectionSynopsisRecord]:
        return {synopsis.section_id: synopsis for synopsis in synopses}

    @staticmethod
    def _normalize_scope_text(value: str) -> set[str]:
        return {token for token in value.lower().replace("_", " ").split() if len(token) > 2}

    def _coherent_hard_scope_ids(
        self,
        *,
        scope_ids: list[str],
        synopses: list[SectionSynopsisRecord],
        question: str,
        scope_query: str,
    ) -> list[str]:
        if len(scope_ids) <= 1:
            return scope_ids
        synopsis_by_id = self._synopsis_by_id(synopses)
        groups: dict[tuple[str, str], list[str]] = {}
        for section_id in scope_ids:
            synopsis = synopsis_by_id.get(section_id)
            if synopsis is None:
                continue
            metadata = synopsis.metadata or {}
            chapter_number = str(metadata.get("chapter_number", "") or "")
            chapter_title = str(metadata.get("chapter_title", "") or "")
            if not chapter_number and not chapter_title:
                continue
            groups.setdefault((chapter_number, chapter_title.lower()), []).append(section_id)
        if not groups:
            return scope_ids[:1]

        scope_terms = self._normalize_scope_text(f"{question} {scope_query}")

        def group_score(item: tuple[tuple[str, str], list[str]]) -> tuple[int, int]:
            (chapter_number, chapter_title), ids = item
            title_terms = self._normalize_scope_text(chapter_title)
            number_hit = int(bool(chapter_number and chapter_number in scope_terms))
            title_hits = len(scope_terms & title_terms)
            return title_hits + number_hit, len(ids)

        best_key, best_ids = max(groups.items(), key=group_score)
        best_score, _ = group_score((best_key, best_ids))
        if best_score > 0:
            return best_ids
        if len(groups) == 1:
            return best_ids
        return []

    def _summary_focused_scope_ids(
        self,
        *,
        scope_ids: list[str],
        synopses: list[SectionSynopsisRecord],
        answer_focus: list[str],
    ) -> list[str]:
        if not (set(answer_focus) & {"summary", "conclusions"}):
            return scope_ids
        synopsis_by_id = self._synopsis_by_id(synopses)
        chapter_keys: set[tuple[str, str]] = set()
        for section_id in scope_ids:
            synopsis = synopsis_by_id.get(section_id)
            if synopsis is None:
                continue
            metadata = synopsis.metadata or {}
            chapter_number = str(metadata.get("chapter_number", "") or "")
            chapter_title = str(metadata.get("chapter_title", "") or "").lower()
            if chapter_number or chapter_title:
                chapter_keys.add((chapter_number, chapter_title))
        if len(chapter_keys) != 1:
            return scope_ids
        chapter_number, chapter_title = next(iter(chapter_keys))
        scored: list[tuple[int, SectionSynopsisRecord]] = []
        for synopsis in synopses:
            if self._is_low_value_synopsis(synopsis):
                continue
            metadata = synopsis.metadata or {}
            candidate_chapter_number = str(metadata.get("chapter_number", "") or "")
            candidate_chapter_title = str(metadata.get("chapter_title", "") or "").lower()
            if chapter_number and candidate_chapter_number != chapter_number:
                continue
            if not chapter_number and chapter_title and candidate_chapter_title != chapter_title:
                continue
            section_id = synopsis.section_id
            section_text = " ".join(
                [
                    section_id,
                    synopsis.title,
                    synopsis.region_kind,
                    " ".join(metadata.get("section_path", [])) if isinstance(metadata.get("section_path", []), list) else "",
                    str(metadata.get("document_section_role", "")),
                ]
            ).lower()
            score = 0
            if "summary" in section_text:
                score += 4
            if "chapter" in section_text:
                score += 3
            if synopsis.region_kind in {"overview", "discussion"}:
                score += 2
            if str(metadata.get("document_section_role", "")).lower() in {"overview", "conclusion"}:
                score += 2
            if synopsis.title.lower() in {"introduction", "overview", "summary", "conclusion", "conclusions"}:
                score += 2
            if "|" in section_id:
                score += 1
            if score > 0:
                scored.append((score, synopsis))
        focused_ids = [
            synopsis.section_id
            for _, synopsis in sorted(scored, key=lambda item: item[0], reverse=True)[:3]
        ]
        return focused_ids or scope_ids

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

    def _plan_from_orchestrator_payload(
        self,
        *,
        question: str,
        baseline_profile: QueryProfile,
        payload: dict[str, Any],
        synopses: list[SectionSynopsisRecord],
        metadata_filters: dict[str, list[str]],
        deterministic_plan: RetrievalPlan,
    ) -> tuple[QueryProfile, RetrievalPlan] | None:
        valid_scope_ids = self._valid_section_ids(payload.get("resolved_scope_ids"), synopses)
        low_value_scope_ids = {synopsis.section_id for synopsis in synopses if self._is_low_value_synopsis(synopsis)}
        if not metadata_filters.get("page_labels"):
            valid_scope_ids = [section_id for section_id in valid_scope_ids if section_id not in low_value_scope_ids]
        confidence = self._coerce_float(payload.get("confidence"), 0.0)
        if confidence < self.settings.retrieval_orchestrator_min_confidence:
            return None

        scope_strictness = self._normalize_scope_strictness(payload.get("scope_strictness"))
        if scope_strictness == "none":
            return None
        if not valid_scope_ids:
            return None
        has_explicit_filters = bool(
            metadata_filters.get("page_labels") or metadata_filters.get("clause_terms") or metadata_filters.get("section_terms")
        )
        if has_explicit_filters and valid_scope_ids:
            scope_strictness = "hard"
        scope_query = str(payload.get("scope_query", "")).strip()
        if scope_strictness == "hard" and not self._has_explicit_local_scope(question, metadata_filters):
            return None
        if scope_strictness == "hard":
            valid_scope_ids = self._coherent_hard_scope_ids(
                scope_ids=valid_scope_ids,
                synopses=synopses,
                question=question,
                scope_query=scope_query,
            )
        if scope_strictness == "hard" and not valid_scope_ids:
            return None

        answer_focus = self._dedupe_strings(
            [
                *self._sanitized_list(payload.get("answer_focus"), self.VALID_FOCUS_TERMS),
                *self._answer_focus_from_question(question),
            ]
        )
        valid_scope_ids = self._summary_focused_scope_ids(
            scope_ids=valid_scope_ids,
            synopses=synopses,
            answer_focus=answer_focus,
        )
        query_profile = self._query_profile_from_payload(baseline_profile, payload)
        reason = str(payload.get("reason", "")).strip()
        preferred_route = str(payload.get("preferred_route", "")).strip().lower()
        if preferred_route not in self.VALID_ROUTES:
            preferred_route = deterministic_plan.preferred_route

        if scope_strictness == "hard":
            constraint_mode = "hard_region"
            preferred_route = "chunk_first"
            use_global_fallback = False
            target_region_ids = valid_scope_ids
        elif scope_strictness == "soft" and valid_scope_ids:
            constraint_mode = "soft_local" if len(valid_scope_ids) <= 2 else "soft_multi_region"
            use_global_fallback = self._coerce_bool(payload.get("use_global_fallback"), False)
            target_region_ids = valid_scope_ids
        else:
            constraint_mode = deterministic_plan.constraint_mode
            use_global_fallback = deterministic_plan.use_global_fallback
            target_region_ids = deterministic_plan.target_region_ids
            scope_strictness = "none"

        return query_profile, RetrievalPlan(
            intent_type=query_profile.intent_type,
            evidence_spread=query_profile.evidence_spread,
            constraint_mode=constraint_mode,
            preferred_route=preferred_route,
            target_region_ids=target_region_ids,
            target_region_kinds=[] if scope_strictness == "hard" else deterministic_plan.target_region_kinds,
            allowed_section_ids=valid_scope_ids if scope_strictness in {"hard", "soft"} else [],
            scope_strictness=scope_strictness,
            scope_query=scope_query,
            answer_focus=answer_focus,
            hard_filters={key: value for key, value in metadata_filters.items() if value},
            use_global_fallback=use_global_fallback,
            planner_confidence=max(confidence, deterministic_plan.planner_confidence),
            planner_source="llm_orchestrator",
            orchestrator_reason=reason,
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
        orchestrator_payload = self._orchestrator_payload(
            question=question,
            baseline_profile=baseline_profile,
            deterministic_plan=deterministic_plan,
            metadata_filters=metadata_filters,
            synopses=synopses,
        )
        if orchestrator_payload:
            orchestrated = self._plan_from_orchestrator_payload(
                question=question,
                baseline_profile=baseline_profile,
                payload=orchestrator_payload,
                synopses=synopses,
                metadata_filters=metadata_filters,
                deterministic_plan=deterministic_plan,
            )
            if orchestrated is not None:
                return orchestrated
            retry_payload = self._orchestrator_payload(
                question=question,
                baseline_profile=baseline_profile,
                deterministic_plan=deterministic_plan,
                metadata_filters=metadata_filters,
                synopses=synopses,
                previous_payload=orchestrator_payload,
            )
            if retry_payload:
                orchestrated = self._plan_from_orchestrator_payload(
                    question=question,
                    baseline_profile=baseline_profile,
                    payload=retry_payload,
                    synopses=synopses,
                    metadata_filters=metadata_filters,
                    deterministic_plan=deterministic_plan,
                )
                if orchestrated is not None:
                    return orchestrated

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
