from __future__ import annotations

import json
from dataclasses import replace

from src.config import Settings
from src.schemas import RetrievalCandidate, RetrievalResult


class EvidenceSelector:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.client = None
        if settings.openai_api_key and settings.evidence_selector_enabled:
            try:
                from openai import OpenAI

                self.client = OpenAI(api_key=settings.openai_api_key)
            except Exception:
                self.client = None

    @staticmethod
    def _candidate_score(candidate: RetrievalCandidate) -> float:
        return candidate.rerank_score if candidate.rerank_score is not None else candidate.fused_score

    def _should_select(self, retrieval_result: RetrievalResult) -> bool:
        if retrieval_result.evidence_status == "unsupported":
            return False
        if self.client is None or not self.settings.evidence_selector_enabled:
            return False
        if len(retrieval_result.candidates) < 2:
            return False
        plan = retrieval_result.retrieval_plan or {}
        spread = str(plan.get("evidence_spread", ""))
        top_scores = [self._candidate_score(candidate) for candidate in retrieval_result.candidates[:2]]
        score_gap = abs(top_scores[0] - top_scores[1]) if len(top_scores) == 2 else 1.0
        ambiguous = score_gap <= self.settings.evidence_selector_gap_threshold
        return retrieval_result.weak_evidence or spread in {"global", "sectional"} or ambiguous

    def _selection_prompt(self, question: str, candidates: list[RetrievalCandidate]) -> str:
        lines = [
            "Pick the best evidence chunks for answering the question.",
            "Bias toward higher-ranked candidates unless a lower-ranked one is clearly more direct and specific.",
            "Only score the supplied candidates. Do not invent evidence.",
            "Return compact JSON with keys candidate_scores and selected_ids.",
            "",
            f"Question: {question}",
            "",
        ]
        for index, candidate in enumerate(candidates, start=1):
            score = self._candidate_score(candidate)
            lines.extend(
                [
                    f"Candidate {candidate.chunk_id}",
                    f"- rank: {index}",
                    f"- retrieval_score: {score:.4f}",
                    f"- page: {candidate.metadata.get('page_label', 'Document')}",
                    f"- section: {candidate.metadata.get('section_heading', '')}",
                    f"- text: {candidate.text[:700].replace(chr(10), ' ')}",
                    "",
                ]
            )
        lines.append(
            "candidate_scores must be an object mapping candidate ids to values between 0 and 1. selected_ids should contain the best one or two ids."
        )
        return "\n".join(lines)

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

        candidates = retrieval_result.candidates[: self.settings.evidence_selector_top_k]
        prompt = self._selection_prompt(question, candidates)
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
        except Exception:
            return retrieval_result

        llm_scores_raw = payload.get("candidate_scores", {})
        if not isinstance(llm_scores_raw, dict):
            return retrieval_result

        retrieval_scores = [self._candidate_score(candidate) for candidate in candidates]
        normalized_retrieval = self._normalize(retrieval_scores)

        ranked: list[tuple[float, RetrievalCandidate]] = []
        for candidate, prior_score in zip(candidates, normalized_retrieval):
            llm_score = float(llm_scores_raw.get(candidate.chunk_id, 0.0) or 0.0)
            final_score = (
                self.settings.evidence_selector_rank_weight * prior_score
                + self.settings.evidence_selector_llm_weight * llm_score
            )
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

        for _, candidate in ranked:
            if len(chosen) >= self.settings.evidence_selector_max_evidence:
                break
            if all(existing.chunk_id != candidate.chunk_id for existing in chosen):
                chosen.append(candidate)

        note = (
            f"Evidence selector reviewed top {len(candidates)} chunks and chose "
            f"{', '.join(candidate.metadata.get('page_label', candidate.chunk_id) for candidate in chosen)}."
        )
        return replace(
            retrieval_result,
            candidates=chosen,
            strategy_notes=[*retrieval_result.strategy_notes, note],
        )
