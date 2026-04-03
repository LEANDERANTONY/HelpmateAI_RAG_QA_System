from __future__ import annotations
import json

from src.config import Settings
from src.generation.prompts import build_grounded_prompt
from src.schemas import AnswerResult, CacheStatus, RetrievalCandidate, RetrievalResult


class AnswerGenerator:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.client = None
        if settings.openai_api_key:
            try:
                from openai import OpenAI

                self.client = OpenAI(api_key=settings.openai_api_key)
            except Exception:
                self.client = None

    @staticmethod
    def _citation_details(evidence: list[RetrievalCandidate]) -> list[str]:
        seen: list[str] = []
        for index, candidate in enumerate(evidence, start=1):
            label = candidate.citation_label or candidate.metadata.get("page_label", "Document")
            detail = f"[{index}] {label}"
            if detail not in seen:
                seen.append(detail)
        return seen

    def _citations(self, evidence: list[RetrievalCandidate]) -> list[str]:
        return [detail.split("] ", 1)[1] for detail in self._citation_details(evidence)]

    def _fallback_answer(self, question: str, evidence: list[RetrievalCandidate]) -> AnswerResult:
        citations = self._citations(evidence)
        citation_details = self._citation_details(evidence)
        if not evidence:
            return AnswerResult(
                question=question,
                answer="Unsupported by the retrieved evidence.",
                citations=[],
                evidence=[],
                supported=False,
                cache_status=CacheStatus(),
                model_name="fallback",
                note="No evidence met the retrieval threshold.",
                citation_details=[],
                retrieval_notes=[],
                query_used=question,
            )

        snippets = " ".join(candidate.text[:220].replace("\n", " ") for candidate in evidence[:2])
        answer = (
            f"Here is the strongest grounded summary I could find: {snippets.strip()} "
            "Please review the cited sections for the exact wording."
        )
        return AnswerResult(
            question=question,
            answer=answer,
            citations=citations,
            evidence=evidence,
            supported=not any(
                phrase in answer.lower()
                for phrase in ["could not find enough supporting evidence", "insufficient", "cannot provide an answer", "does not contain"]
            ),
            cache_status=CacheStatus(),
            model_name="fallback",
            note="Returned a local grounded summary because a live model response was unavailable.",
            citation_details=citation_details,
            retrieval_notes=[],
            query_used=question,
        )

    def generate(self, question: str, retrieval_result: RetrievalResult) -> AnswerResult:
        evidence = retrieval_result.candidates
        if retrieval_result.evidence_status == "unsupported":
            return AnswerResult(
                question=question,
                answer="Unsupported by the retrieved evidence.",
                citations=[],
                evidence=evidence,
                supported=False,
                cache_status=CacheStatus(),
                model_name="retrieval_guardrail",
                note="Retrieved evidence was too weak or irrelevant to justify answer generation.",
                citation_details=[],
                retrieval_notes=retrieval_result.strategy_notes,
                query_used=retrieval_result.query_used,
                query_variants=retrieval_result.query_variants,
            )
        if self.client is None:
            answer = self._fallback_answer(question, evidence)
            answer.retrieval_notes = retrieval_result.strategy_notes
            answer.query_used = retrieval_result.query_used
            answer.query_variants = retrieval_result.query_variants
            return answer

        prompt = build_grounded_prompt(question, evidence)
        response = self.client.chat.completions.create(
            model=self.settings.answer_model,
            messages=[
                {"role": "system", "content": "You answer questions using only supplied document evidence."},
                {"role": "user", "content": prompt},
            ],
            response_format={"type": "json_object"},
        )
        content = response.choices[0].message.content or "{}"
        try:
            payload = json.loads(content)
        except json.JSONDecodeError:
            payload = {"supported": False, "answer": "Unsupported by the retrieved evidence.", "reason": "The model did not return valid structured output."}
        citation_details = self._citation_details(evidence)
        references_block = ""
        supported = bool(payload.get("supported", False))
        answer_text = str(payload.get("answer", "")).strip() or "Unsupported by the retrieved evidence."
        reason_text = str(payload.get("reason", "")).strip() or None
        if citation_details and supported:
            references_block = "\n\nReferences:\n" + "\n".join(f"- {item}" for item in citation_details)
        return AnswerResult(
            question=question,
            answer=(answer_text + references_block).strip(),
            citations=self._citations(evidence),
            evidence=evidence,
            supported=supported,
            cache_status=CacheStatus(),
            model_name=self.settings.answer_model,
            citation_details=citation_details,
            retrieval_notes=retrieval_result.strategy_notes,
            note=reason_text or ("Evidence quality was weak, so the answer should be treated cautiously." if retrieval_result.weak_evidence else None),
            query_used=retrieval_result.query_used,
            query_variants=retrieval_result.query_variants,
        )
