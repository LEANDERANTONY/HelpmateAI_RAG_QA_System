from __future__ import annotations
import logging

from src.config import Settings
from src.generation.prompts import (
    build_grounded_prompt,
    build_support_status_verification_prompt,
    build_support_verification_prompt,
)
from src.openai_service import (
    CostCollector,
    OpenAIService,
    StructuredOutputError,
)
from src.schemas import AnswerResult, CacheStatus, RetrievalCandidate, RetrievalResult
from src.schemas_llm_outputs import (
    AnswerOutput,
    SupportStatusVerifierOutput,
    SupportVerifierOutput,
)


logger = logging.getLogger(__name__)


INFERENTIAL_SUPPORTED_MARKERS = (
    "implied",
    "implies",
    "suggests",
    "appears",
    "likely",
    "could mean",
    "could indicate",
    "may indicate",
    "can be interpreted",
)

SUPPORT_GAP_MARKERS = (
    "cannot be fully answered",
    "does not fully support",
    "does not clearly",
    "does not explicitly",
    "does not provide",
    "does not provide a complete",
    "does not provide the requested",
    "does not provide the required",
    "does not state",
    "evidence does not provide",
    "evidence is partial",
    "incomplete",
    "inconsistent",
    "missing",
    "not explicit",
    "not explicitly provided",
    "not fully answerable",
    "not stated",
    "only partially",
    "partial comparison",
    "partially supported",
    "required fact is missing",
    "required facts are missing",
)

PARTIAL_ANSWER_MARKERS = (
    "but does not",
    "but the evidence does not",
    "cannot fully answer",
    "does not identify",
    "does not include",
    "does not provide",
    "does not state",
    "missing",
    "not provided",
    "not stated",
)


def _uses_inferential_supported_language(answer_text: str) -> bool:
    lowered = answer_text.lower()
    return any(marker in lowered for marker in INFERENTIAL_SUPPORTED_MARKERS)


def _reason_reports_support_gap(reason_text: str) -> bool:
    lowered = reason_text.lower()
    return any(marker in lowered for marker in SUPPORT_GAP_MARKERS)


def _answer_reports_support_gap(answer_text: str) -> bool:
    lowered = answer_text.lower()
    return any(marker in lowered for marker in PARTIAL_ANSWER_MARKERS)


def _normalize_support_status(value: object, *, supported: bool) -> str:
    status = str(value or "").strip().lower()
    if status in {"supported", "partial", "unsupported"}:
        return status
    return "supported" if supported else "unsupported"


_SUPPORT_SUMMARY_DEFAULTS = {
    "supported": "Cited evidence",
    "partial": "Partial support",
    "unsupported": "Source is silent",
}


def _normalize_support_summary(value: object, *, support_status: str) -> str:
    """Sanitize the model's chip-friendly tagline. Caps at 5 words and 60 chars,
    strips trailing punctuation, falls back to a status-appropriate default
    when the model returns nothing usable."""
    raw = str(value or "").strip().rstrip(".,;:!?")
    raw = " ".join(raw.split())  # collapse whitespace
    if raw:
        words = raw.split(" ")
        if len(words) > 5:
            raw = " ".join(words[:5])
        if len(raw) > 60:
            raw = raw[:60].rstrip(" -—")
        return raw
    return _SUPPORT_SUMMARY_DEFAULTS.get(support_status, "Cited evidence")


def _as_text_list(value: object) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


class AnswerGenerator:
    def __init__(
        self,
        settings: Settings,
        *,
        cost_collector: CostCollector | None = None,
    ):
        # cost_collector is an OPTIONAL explicit override for tests +
        # eval scripts that want a captured reference. In production
        # the pipeline binds a request-scoped CostCollector to the
        # ContextVar in ``src.openai_service`` and the OpenAIService
        # wrapper reads from it at record time — so leaving this None
        # (the default) is what gives us per-request isolation under
        # FastAPI's concurrent task model.
        self.settings = settings
        self.cost_collector = cost_collector
        self.client = None
        if settings.openai_api_key:
            try:
                from openai import OpenAI

                self.client = OpenAI(api_key=settings.openai_api_key)
            except Exception as exc:
                logger.warning("Answer generator client setup failed (%s)", exc.__class__.__name__)
                self.client = None

    def _wrapper(self) -> OpenAIService:
        """Build an OpenAIService that wraps whatever ``self.client`` is
        currently set to.

        Constructed per-call (rather than once in __init__) so tests
        which monkeypatch ``generator.client = _FakeClient(...)`` after
        construction see their fake propagated through. The wrapper
        itself is cheap — no network or auth — so per-call construction
        is fine.

        We pass ``self.cost_collector`` (may be None) as the explicit
        recorder. When None, the wrapper's ``_record_cost`` falls back
        to ``get_current_cost_collector()`` — the request-scoped
        ContextVar the pipeline binds at the start of each /qa call.
        """
        return OpenAIService(
            self.settings,
            cost_recorder=self.cost_collector,
            client=self.client,
        )

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
        """Build a degraded answer when the live model is unreachable.

        Previously this path stitched ``candidate.text[:220]`` for the
        first two evidence chunks together and stamped ``supported=True``
        via a brittle heuristic ("does the fabricated text contain the
        word 'insufficient'?"). Two failure modes resulted:

          * Out-of-scope abstention break — the heuristic almost always
            said supported=True even when the retrieved evidence was
            obviously unrelated to the question (e.g. asking "price of
            Bitcoin?" against FOMC minutes returned ``SUPPORTED · Best
            local match`` with stitched FOMC chunks). The marketing
            claim of "100% abstention when out-of-scope" was a lie any
            time the model failed.
          * Chunk-stitching glitches — ``text[:220]`` slices mid-word
            so users saw "tee voted" (truncated "committee voted") and
            "Strateg Document landmark" (random concatenation) leak
            into the answer card as if they were real model output.

        The fix: the fallback is a degraded state, period. We never claim
        ``supported=True`` from this path. We never fabricate an answer
        from raw chunk text. We surface the retrieval pointers (citations
        + evidence) so the user can still navigate to the source, but
        the answer body is an honest "live model unavailable" message and
        the support flag is False so the abstention gate holds.
        """
        if not evidence:
            return AnswerResult(
                question=question,
                answer="Unsupported by the retrieved evidence.",
                citations=[],
                evidence=[],
                supported=False,
                support_status="unsupported",
                support_summary="No matching evidence",
                cache_status=CacheStatus(),
                model_name="fallback",
                note="No evidence met the retrieval threshold.",
                citation_details=[],
                retrieval_notes=[],
                query_used=question,
            )

        # Evidence exists but the live model couldn't produce a grounded
        # answer. Abstain cleanly per the abstention contract (L3): an
        # unsupported answer renders an empty evidence rail (the UI's
        # visibleEvidence yields nothing on support_status="unsupported"),
        # so populated citations here advertised sources the rail never
        # showed. Return an honest "model unavailable" message with empty
        # citation lists; we never fabricate a stitched-chunk "best local
        # match" (the earlier path lied about supported status and leaked
        # mid-word chunk slices into the rendered answer). The retrieved
        # evidence stays on the result for tracing but isn't surfaced as
        # support for the absent answer.
        answer = (
            "The live answer model is currently unavailable, so this question "
            "could not be answered from the document right now. Please retry "
            "in a moment."
        )
        return AnswerResult(
            question=question,
            answer=answer,
            citations=[],
            evidence=evidence,
            supported=False,
            support_status="unsupported",
            support_summary="Live model unavailable",
            cache_status=CacheStatus(),
            model_name="fallback",
            note=(
                "Live model response was unavailable; abstained rather than "
                "surfacing unverified retrieval pointers. The fallback path "
                "never claims supported status."
            ),
            citation_details=[],
            retrieval_notes=[],
            query_used=question,
        )

    def generate(
        self,
        question: str,
        retrieval_result: RetrievalResult,
        *,
        model_override: str | None = None,
    ) -> AnswerResult:
        # model_override lets the caller (typically the /qa handler
        # via the pipeline) pick a model based on the requesting user's
        # tier — free → gpt-5.4-nano, pro/business → gpt-5.4-mini.
        # When None, falls back to settings.answer_model so eval
        # scripts and other unauthenticated contexts still work.
        model_name = model_override or self.settings.answer_model
        evidence = retrieval_result.candidates
        if retrieval_result.evidence_status == "unsupported":
            return AnswerResult(
                question=question,
                answer="Unsupported by the retrieved evidence.",
                citations=[],
                evidence=evidence,
                supported=False,
                support_status="unsupported",
                support_summary="Off-topic evidence",
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

        retrieval_plan = retrieval_result.retrieval_plan or {}
        summary_mode = str(retrieval_plan.get("evidence_spread", "")) == "global"
        prompt = build_grounded_prompt(question, evidence, summary_mode=summary_mode)
        # Schema-strict path: OpenAI constrains the response to the
        # AnswerOutput shape and we validate through Pydantic before
        # downstream consumption. A drifted prompt or model version can
        # no longer return content that silently parses as a wrong-shape
        # dict — it surfaces as StructuredOutputError, which we catch
        # and route through the same fallback path as the legacy
        # generic-exception branch.
        try:
            output = self._wrapper().run_structured_prompt(
                system="You answer questions using only supplied document evidence.",
                user=prompt,
                task_name="answer_generation",
                model=model_name,
                response_model=AnswerOutput,
            )
            payload = output.model_dump()
        except StructuredOutputError as exc:
            logger.warning(
                "Answer model returned drifted structured output; falling back (%s)",
                exc,
            )
            answer = self._fallback_answer(question, evidence)
            answer.retrieval_notes = retrieval_result.strategy_notes
            answer.query_used = retrieval_result.query_used
            answer.query_variants = retrieval_result.query_variants
            # Schema-drift means the LLM produced content the validator
            # rejected; we cannot trust the "supported" flag the
            # _fallback_answer heuristic would otherwise stamp on. Mark
            # the answer as unsupported so the trace + UI surfaces the
            # drift instead of presenting a lossy local summary as if
            # it were a verified grounded answer. Codex P1 on PR #6.
            answer.supported = False
            answer.support_status = "unsupported"
            answer.support_summary = "Schema drift"
            answer.note = (
                "Live model returned a structured output that did not match the "
                "expected schema; the answer was downgraded to unsupported so "
                "the run trace surfaces the drift instead of treating the local "
                "fallback as a verified answer."
            )
            return answer
        except Exception as exc:
            logger.warning("Answer model call failed; returning local fallback (%s)", exc.__class__.__name__)
            answer = self._fallback_answer(question, evidence)
            answer.retrieval_notes = retrieval_result.strategy_notes
            answer.query_used = retrieval_result.query_used
            answer.query_variants = retrieval_result.query_variants
            answer.note = f"Live model call failed, so a local grounded fallback was returned instead. ({exc.__class__.__name__})"
            return answer
        citation_details = self._citation_details(evidence)
        references_block = ""
        supported = bool(payload.get("supported", False))
        support_status = _normalize_support_status(payload.get("support_status"), supported=supported)
        support_summary_raw = payload.get("support_summary")
        answer_text = str(payload.get("answer", "")).strip() or "Unsupported by the retrieved evidence."
        reason_text = str(payload.get("reason", "")).strip() or None
        if supported and _uses_inferential_supported_language(answer_text):
            supported = False
            support_status = "unsupported"
            reason_text = (
                (reason_text + " " if reason_text else "")
                + "The answer used inferential wording, so it was downgraded because the evidence did not directly support a grounded claim."
            )
        if supported and reason_text and _reason_reports_support_gap(reason_text):
            supported = False
            support_status = "partial" if _answer_reports_support_gap(answer_text) else "unsupported"
            reason_text = (
                reason_text
                + " The strict supported flag was downgraded because the model's own support reason identified a missing, partial, or inconsistent required fact."
            )
        if self.settings.support_status_verifier_enabled and not supported and evidence:
            support_status, reason_text = self.verify_support_status(
                question=question,
                answer_text=answer_text,
                reason_text=reason_text or "",
                claimed_support_status=support_status,
                evidence=evidence,
            )
            # Regression fix (2/2): trust the verifier's "supported"
            # verdict. verify_support_status only returns "supported"
            # when it found grounded supported_facts AND an EMPTY
            # missing_or_ambiguous_facts set — it has already
            # adjudicated whether any acknowledged gap is real. The old
            # `not _answer_reports_support_gap(answer_text)` clause
            # re-blocked promotion on the raw answer text whenever the
            # answer politely noted a caveat, so `supported` stayed
            # False and the `elif not supported` below re-stamped
            # "unsupported" — abstaining a verifier-confirmed answer.
            # The inferential-language guard stays: if the answer's
            # grounded claims are themselves weaselled ("suggests",
            # "appears"), not trusting "supported" is still correct.
            if support_status == "supported" and not _uses_inferential_supported_language(answer_text):
                supported = True
        if support_status == "partial":
            supported = False
            if not _answer_reports_support_gap(answer_text):
                verifier_allows_partial = reason_text and "Support-status verifier classified the answer as partial" in reason_text
                if not verifier_allows_partial:
                    support_status = "unsupported"
                    reason_text = (
                        (reason_text + " " if reason_text else "")
                        + "The model requested partial support, but the answer did not explicitly name the missing required fact."
                    )
        elif not supported:
            support_status = "unsupported"
        if citation_details and support_status in {"supported", "partial"}:
            references_block = "\n\nReferences:\n" + "\n".join(f"- {item}" for item in citation_details)
        return AnswerResult(
            question=question,
            answer=(answer_text + references_block).strip(),
            citations=self._citations(evidence),
            evidence=evidence,
            supported=supported,
            support_status=support_status,
            support_summary=_normalize_support_summary(support_summary_raw, support_status=support_status),
            cache_status=CacheStatus(),
            model_name=model_name,
            citation_details=citation_details,
            retrieval_notes=retrieval_result.strategy_notes,
            note=reason_text or ("Evidence quality was weak, so the answer should be treated cautiously." if retrieval_result.weak_evidence else None),
            query_used=retrieval_result.query_used,
            query_variants=retrieval_result.query_variants,
        )

    def verify_supported_answer(
        self,
        *,
        question: str,
        answer: AnswerResult,
        evidence: list[RetrievalCandidate],
    ) -> tuple[bool, str]:
        if self.client is None:
            return True, "Support verification skipped because no live model client is available."
        prompt = build_support_verification_prompt(question, answer.answer, evidence)
        # Schema-strict: SupportVerifierOutput is just (supported, reason)
        # so the schema constraint is small but still catches the
        # case where the model returns text instead of JSON entirely.
        try:
            verifier_output = self._wrapper().run_structured_prompt(
                system="You strictly verify whether answers are fully supported by supplied document evidence.",
                user=prompt,
                task_name="support_verifier",
                model=self.settings.answer_model,
                response_model=SupportVerifierOutput,
            )
            supported = verifier_output.supported
            reason = verifier_output.reason.strip()
        except StructuredOutputError as exc:
            logger.warning(
                "Support verifier returned drifted output; keeping original status (%s)",
                exc,
            )
            # Preserve the incoming ``answer.supported`` rather than
            # returning True unconditionally. CodeRabbit caught: if
            # verify_supported_answer is ever called with an already-
            # unsupported AnswerResult, returning True on drift would
            # upgrade it — exactly the silent correctness regression
            # the schema-strict path is meant to prevent.
            return answer.supported, (
                "Support verifier returned a structured output that did not match "
                "the expected schema, so the original answer status was retained."
            )
        except Exception as exc:
            logger.warning("Support verifier failed; keeping original answer status (%s)", exc.__class__.__name__)
            # Same correctness: preserve the original supported flag
            # rather than blindly returning True. A network glitch
            # shouldn't promote an unsupported answer to supported.
            return answer.supported, f"Support verification failed, so the original answer status was retained. ({exc.__class__.__name__})"
        if supported and _reason_reports_support_gap(reason):
            supported = False
            reason = (
                reason
                + " The verifier reason identified a missing, partial, or inconsistent required fact, so the recovered answer was downgraded."
            )
        return supported, reason or "Support verifier did not provide a reason."

    def verify_support_status(
        self,
        *,
        question: str,
        answer_text: str,
        reason_text: str,
        claimed_support_status: str,
        evidence: list[RetrievalCandidate],
    ) -> tuple[str, str]:
        if self.client is None:
            return claimed_support_status, reason_text or "Support-status verification skipped because no live model client is available."
        prompt = build_support_status_verification_prompt(
            question=question,
            answer=answer_text,
            reason=reason_text,
            claimed_support_status=claimed_support_status,
            evidence=evidence,
        )
        # Schema-strict: SupportStatusVerifierOutput matches the existing
        # five-key shape (support_status, answer_acknowledges_gap,
        # supported_facts, missing_or_ambiguous_facts, reason). The
        # downstream normalization stays identical — the schema only
        # changes how the payload is *obtained*, not how it's interpreted.
        try:
            verifier_output = self._wrapper().run_structured_prompt(
                system="You classify answer support status using only supplied document evidence.",
                user=prompt,
                task_name="support_status_verifier",
                model=self.settings.support_status_verifier_model,
                response_model=SupportStatusVerifierOutput,
            )
        except StructuredOutputError as exc:
            logger.warning(
                "Support-status verifier returned drifted output; keeping original status (%s)",
                exc,
            )
            return claimed_support_status, (
                (reason_text + " " if reason_text else "")
                + "Support-status verifier returned a structured output that did not match "
                + "the expected schema, so the original status was retained."
            )
        except Exception as exc:
            logger.warning("Support-status verifier failed; keeping original status (%s)", exc.__class__.__name__)
            return claimed_support_status, (
                (reason_text + " " if reason_text else "")
                + f"Support-status verification failed, so the original status was retained. ({exc.__class__.__name__})"
            )

        verifier_status = _normalize_support_status(verifier_output.support_status, supported=False)
        supported_facts = _as_text_list(verifier_output.supported_facts)
        missing_facts = _as_text_list(verifier_output.missing_or_ambiguous_facts)
        acknowledges_gap = bool(verifier_output.answer_acknowledges_gap)
        verifier_reason = verifier_output.reason.strip()

        if verifier_status == "supported":
            if not supported_facts:
                # Verifier said "supported" but cannot name a single
                # grounded fact — self-contradictory, don't trust it.
                verifier_status = "unsupported"
            elif missing_facts:
                # The verifier itself found a required fact missing or
                # ambiguous — a real gap. Partial only when the answer
                # owns the gap (acknowledges_facts already truthy here),
                # else unsupported. Equivalent to the old
                # "acknowledges_gap and supported_facts and missing_facts"
                # check since both supported_facts and missing_facts are
                # known truthy in this branch.
                verifier_status = "partial" if acknowledges_gap else "unsupported"
            # else: supported_facts present AND the verifier found
            # nothing missing -> the answer is genuinely supported.
            # Regression fix: previously `acknowledges_gap` alone forced
            # "unsupported" here, so a fully-grounded answer that merely
            # *acknowledged a potential gap the verifier did NOT confirm*
            # was wrongly abstained. The verifier's evidence-grounded
            # verdict must win over the answer's self-doubt.
        if verifier_status == "partial" and not (acknowledges_gap and supported_facts and missing_facts):
            verifier_status = "unsupported"

        prefix = f"Support-status verifier classified the answer as {verifier_status}."
        details = []
        if supported_facts:
            details.append("Supported facts: " + "; ".join(supported_facts[:3]))
        if missing_facts:
            details.append("Missing or ambiguous facts: " + "; ".join(missing_facts[:3]))
        if verifier_reason:
            details.append(verifier_reason)
        combined_reason = " ".join(part for part in [reason_text, prefix, " ".join(details)] if part)
        return verifier_status, combined_reason
