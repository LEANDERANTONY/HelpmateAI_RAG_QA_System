from __future__ import annotations

import json
import logging
from dataclasses import replace

from src.config import Settings
from src.schemas import RetrievalCandidate, RetrievalResult


logger = logging.getLogger(__name__)


class EvidenceSelector:
    SUMMARY_SUPPORT_SECTION_KINDS = {
        "overview",
        "abstract",
        "introduction",
        "background",
        "results",
        "discussion",
        "conclusion",
        "conclusions",
        "future work",
        "future directions",
    }
    LOW_VALUE_SECTION_KINDS = {"references", "appendix"}

    def __init__(self, settings: Settings):
        self.settings = settings
        self.client = None
        if settings.openai_api_key and settings.evidence_selector_enabled:
            try:
                from openai import OpenAI

                self.client = OpenAI(api_key=settings.openai_api_key)
            except Exception as exc:
                logger.warning("Evidence selector client setup failed (%s)", exc.__class__.__name__)
                self.client = None

    @staticmethod
    def _candidate_score(candidate: RetrievalCandidate) -> float:
        # Use fused retrieval strength as the stable prior; reranker logits are
        # only used to sort candidates and are not comparable across queries.
        return candidate.fused_score

    def _selection_decision(self, retrieval_result: RetrievalResult) -> dict[str, object]:
        base_decision = {
            "should_select": False,
            "spread": "",
            "score_gap": None,
            "weak_evidence_trigger": False,
            "spread_trigger": False,
            "ambiguity_trigger": False,
        }
        if retrieval_result.evidence_status == "unsupported":
            return base_decision
        if self.client is None or not self.settings.evidence_selector_enabled:
            return base_decision
        if len(retrieval_result.candidates) < 2:
            return base_decision
        plan = retrieval_result.retrieval_plan or {}
        spread = str(plan.get("evidence_spread", ""))
        top_scores = [self._candidate_score(candidate) for candidate in retrieval_result.candidates[:2]]
        score_gap = abs(top_scores[0] - top_scores[1]) if len(top_scores) == 2 else 1.0
        gap_threshold = self.settings.evidence_selector_gap_threshold
        ambiguous = gap_threshold >= 1.0 or score_gap <= gap_threshold
        weak_evidence_trigger = self.settings.evidence_selector_trigger_weak_evidence and retrieval_result.weak_evidence
        spread_trigger = self.settings.evidence_selector_trigger_spread and spread in {"global", "sectional"}
        ambiguity_trigger = self.settings.evidence_selector_trigger_ambiguity and ambiguous
        return {
            "should_select": weak_evidence_trigger or spread_trigger or ambiguity_trigger,
            "spread": spread,
            "score_gap": score_gap,
            "weak_evidence_trigger": weak_evidence_trigger,
            "spread_trigger": spread_trigger,
            "ambiguity_trigger": ambiguity_trigger,
        }

    def _should_select(self, retrieval_result: RetrievalResult) -> bool:
        return bool(self._selection_decision(retrieval_result)["should_select"])

    @staticmethod
    def _selector_context(retrieval_result: RetrievalResult) -> dict[str, object]:
        plan = retrieval_result.retrieval_plan or {}
        return {
            "route_used": retrieval_result.route_used,
            "evidence_status": retrieval_result.evidence_status,
            "evidence_spread": plan.get("evidence_spread", ""),
            "constraint_mode": plan.get("constraint_mode", ""),
            "scope_strictness": plan.get("scope_strictness", "none"),
            "scope_query": plan.get("scope_query", ""),
            "allowed_section_ids": plan.get("allowed_section_ids", []),
            "target_region_ids": plan.get("target_region_ids", []),
            "answer_focus": plan.get("answer_focus", []),
            "planner_source": plan.get("planner_source", ""),
            "orchestrator_reason": plan.get("orchestrator_reason", ""),
            "global_fallback_used": plan.get("global_fallback_used", False),
        }

    def _selection_prompt(
        self,
        question: str,
        candidates: list[RetrievalCandidate],
        retrieval_result: RetrievalResult,
    ) -> str:
        selector_context = self._selector_context(retrieval_result)
        lines = [
            "Pick the best evidence chunks for answering the question.",
            "Bias toward higher-ranked candidates unless a lower-ranked one is clearly more direct and specific.",
            "Only score the supplied candidates. Do not invent evidence.",
            "Use the orchestration context to judge whether evidence should be local, broad, summary-focused, findings-focused, or limitations-focused.",
            "If scope_strictness is hard, prefer evidence inside the allowed scope that best matches answer_focus.",
            "If scope_strictness is none, do not over-focus on one local chapter unless the question asks for one.",
            "Return compact JSON with keys candidate_scores and selected_ids.",
            "",
            f"Question: {question}",
            f"Orchestration context: {json.dumps(selector_context, ensure_ascii=True)}",
            "",
        ]
        for index, candidate in enumerate(candidates, start=1):
            score = self._candidate_score(candidate)
            scope_labels = candidate.metadata.get("document_scope_labels", [])
            if not isinstance(scope_labels, list):
                scope_labels = [str(scope_labels)] if scope_labels else []
            lines.extend(
                [
                    f"Candidate {candidate.chunk_id}",
                    f"- rank: {index}",
                    f"- retrieval_score: {score:.4f}",
                    f"- page: {candidate.metadata.get('page_label', 'Document')}",
                    f"- section_id: {candidate.metadata.get('section_id', '')}",
                    f"- section: {candidate.metadata.get('section_heading', '')}",
                    f"- chapter: {candidate.metadata.get('chapter_number', '')} {candidate.metadata.get('chapter_title', '')}".strip(),
                    f"- section_role: {candidate.metadata.get('document_section_role', '')}",
                    f"- scope_labels: {', '.join(str(label) for label in scope_labels[:6])}",
                    f"- text: {candidate.text[:700].replace(chr(10), ' ')}",
                    "",
                ]
            )
        lines.append(
            "candidate_scores must be an object mapping candidate ids to values between 0 and 1. selected_ids should contain the best one or two ids."
        )
        return "\n".join(lines)

    @staticmethod
    def _candidate_context_text(candidate: RetrievalCandidate) -> str:
        scope_labels = candidate.metadata.get("document_scope_labels", [])
        if isinstance(scope_labels, list):
            scope_text = " ".join(str(label) for label in scope_labels)
        else:
            scope_text = str(scope_labels)
        path = candidate.metadata.get("section_path", [])
        if isinstance(path, list):
            path_text = " ".join(str(item) for item in path)
        else:
            path_text = str(path)
        return " ".join(
            [
                str(candidate.metadata.get("section_id", "")),
                str(candidate.metadata.get("section_heading", "")),
                str(candidate.metadata.get("section_kind", "")),
                str(candidate.metadata.get("content_type", "")),
                str(candidate.metadata.get("document_section_role", "")),
                str(candidate.metadata.get("chapter_title", "")),
                scope_text,
                path_text,
            ]
        ).lower()

    def _contextual_adjustment(self, candidate: RetrievalCandidate, retrieval_result: RetrievalResult) -> float:
        plan = retrieval_result.retrieval_plan or {}
        answer_focus = {str(item).lower() for item in plan.get("answer_focus", []) if str(item).strip()}
        scope_strictness = str(plan.get("scope_strictness", "none")).lower()
        context_text = self._candidate_context_text(candidate)
        adjustment = 0.0

        if scope_strictness == "hard":
            allowed_section_ids = {str(item) for item in plan.get("allowed_section_ids", [])}
            if allowed_section_ids and str(candidate.metadata.get("section_id", "")) in allowed_section_ids:
                adjustment += 0.06
            if answer_focus & {"summary", "conclusions"}:
                if any(term in context_text for term in ("summary", "overview", "introduction", "conclusion")):
                    adjustment += 0.14
            if answer_focus & {"implementation", "methodology"}:
                if any(term in context_text for term in answer_focus):
                    adjustment += 0.05

        if answer_focus & {"findings", "results"}:
            if any(term in context_text for term in ("finding", "findings", "result", "results", "discussion", "evidence")):
                adjustment += 0.08
        if answer_focus & {"limitations", "challenges"}:
            if any(term in context_text for term in ("challenge", "challenges", "limitation", "limitations", "constraint")):
                adjustment += 0.10
        return adjustment

    @staticmethod
    def _normalize(values: list[float]) -> list[float]:
        if not values:
            return []
        lower = min(values)
        upper = max(values)
        if upper - lower <= 1e-9:
            return [1.0 for _ in values]
        return [(value - lower) / (upper - lower) for value in values]

    def select(self, question: str, retrieval_result: RetrievalResult) -> RetrievalResult:
        if not self._should_select(retrieval_result):
            return retrieval_result

        original_candidates = retrieval_result.candidates
        candidates = original_candidates[: self.settings.evidence_selector_top_k]
        prompt = self._selection_prompt(question, candidates, retrieval_result)
        try:
            response = self.client.chat.completions.create(
                model=self.settings.evidence_selector_model,
                messages=[
                    {"role": "system", "content": "You select the most direct evidence chunks for grounded document QA."},
                    {"role": "user", "content": prompt},
                ],
                response_format={"type": "json_object"},
            )
            payload = json.loads(response.choices[0].message.content or "{}")
        except Exception as exc:
            logger.warning("Evidence selector call failed; keeping retrieval order (%s)", exc.__class__.__name__)
            return retrieval_result

        llm_scores_raw = payload.get("candidate_scores", {})
        if not isinstance(llm_scores_raw, dict):
            return retrieval_result

        retrieval_scores = [self._candidate_score(candidate) for candidate in candidates]
        normalized_retrieval = self._normalize(retrieval_scores)
        spread = str((retrieval_result.retrieval_plan or {}).get("evidence_spread", ""))
        is_global_summary = spread == "global"

        ranked: list[tuple[float, RetrievalCandidate]] = []
        for candidate, prior_score in zip(candidates, normalized_retrieval):
            llm_score = float(llm_scores_raw.get(candidate.chunk_id, 0.0) or 0.0)
            final_score = (
                self.settings.evidence_selector_rank_weight * prior_score
                + self.settings.evidence_selector_llm_weight * llm_score
            )
            section_kind = str(candidate.metadata.get("section_kind", "")).lower()
            if is_global_summary and section_kind in self.SUMMARY_SUPPORT_SECTION_KINDS:
                final_score += 0.12
            if is_global_summary and section_kind in self.LOW_VALUE_SECTION_KINDS:
                final_score -= 0.28
            final_score += self._contextual_adjustment(candidate, retrieval_result)
            ranked.append((final_score, candidate))

        ranked.sort(key=lambda item: item[0], reverse=True)
        selected_ids = payload.get("selected_ids", [])
        if not isinstance(selected_ids, list):
            selected_ids = []

        selected_lookup = {candidate.chunk_id: candidate for _, candidate in ranked}
        chosen: list[RetrievalCandidate] = []
        for chunk_id in selected_ids:
            if chunk_id in selected_lookup and all(existing.chunk_id != chunk_id for existing in chosen):
                chosen.append(selected_lookup[chunk_id])
            if len(chosen) >= self.settings.evidence_selector_max_evidence:
                break

        max_evidence = self.settings.evidence_selector_max_evidence + 1 if is_global_summary else self.settings.evidence_selector_max_evidence
        preferred_global_candidate = None
        if is_global_summary:
            for _, candidate in ranked:
                section_kind = str(candidate.metadata.get("section_kind", "")).lower()
                if section_kind in self.SUMMARY_SUPPORT_SECTION_KINDS:
                    preferred_global_candidate = candidate
                    break
            if preferred_global_candidate is not None and all(existing.chunk_id != preferred_global_candidate.chunk_id for existing in chosen):
                chosen.insert(0, preferred_global_candidate)

        seen_pages = {candidate.metadata.get("page_label", "") for candidate in chosen}
        for _, candidate in ranked:
            if len(chosen) >= max_evidence:
                break
            if is_global_summary:
                page_label = candidate.metadata.get("page_label", "")
                if page_label in seen_pages and len(seen_pages) < max_evidence:
                    continue
            if all(existing.chunk_id != candidate.chunk_id for existing in chosen):
                chosen.append(candidate)
                if is_global_summary:
                    seen_pages.add(candidate.metadata.get("page_label", ""))

        if self.settings.evidence_selector_prune:
            final_candidates = chosen
            note = (
                f"Evidence selector reviewed top {len(candidates)} chunks and chose "
                f"{', '.join(candidate.metadata.get('page_label', candidate.chunk_id) for candidate in chosen)}."
            )
        else:
            selected_chunk_ids = {candidate.chunk_id for candidate in chosen}
            prioritized = list(chosen)
            for candidate in original_candidates:
                if candidate.chunk_id in selected_chunk_ids:
                    continue
                prioritized.append(candidate)
            final_candidates = prioritized
            note = (
                f"Evidence selector reviewed top {len(candidates)} chunks and reordered evidence to start with "
                f"{', '.join(candidate.metadata.get('page_label', candidate.chunk_id) for candidate in chosen)}."
            )
        return replace(
            retrieval_result,
            candidates=final_candidates,
            strategy_notes=[*retrieval_result.strategy_notes, note],
        )
