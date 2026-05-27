from src.config import Settings
from src.generation.prompts import build_grounded_prompt
import json

from src.generation.service import AnswerGenerator, _reason_reports_support_gap, _uses_inferential_supported_language
from src.schemas import RetrievalCandidate, RetrievalResult


class _FakeMessage:
    def __init__(self, content: str):
        self.content = content


class _FakeChoice:
    def __init__(self, content: str):
        self.message = _FakeMessage(content)


class _FakeResponse:
    def __init__(self, content: str):
        self.choices = [_FakeChoice(content)]


class _FakeCompletions:
    def __init__(self, content: str | list[str]):
        self._contents = list(content) if isinstance(content, list) else [content]

    def create(self, **_: object):
        content = self._contents.pop(0) if len(self._contents) > 1 else self._contents[0]
        return _FakeResponse(content)


class _FakeChat:
    def __init__(self, content: str | list[str]):
        self.completions = _FakeCompletions(content)


class _FakeClient:
    def __init__(self, content: str | list[str]):
        self.chat = _FakeChat(content)


def test_fallback_generation_surfaces_citations_but_never_claims_support():
    """Fallback path (no API key / live model unreachable) must NEVER
    return supported=True. The old implementation stitched the first
    two retrieved chunks via candidate.text[:220] and stamped
    supported=True via a brittle "does the fabricated text contain
    'insufficient'?" heuristic — which almost always passed and broke
    the abstention guarantee on off-topic questions (e.g. "price of
    Bitcoin?" against FOMC minutes returned SUPPORTED · Best local
    match). The fix: fallback surfaces citations + evidence (so the
    user can navigate) but the answer body is an honest "live model
    unavailable" message and supported=False so the abstention gate
    holds.
    """
    settings = Settings(openai_api_key=None)
    generator = AnswerGenerator(settings)
    retrieval = RetrievalResult(
        question="What is the waiting period?",
        candidates=[
            RetrievalCandidate(
                chunk_id="c1",
                text="The waiting period is thirty days from the policy effective date.",
                metadata={"page_label": "Page 4"},
            )
        ],
    )

    answer = generator.generate("What is the waiting period?", retrieval)

    # Citation pointers still surface so the user can navigate to source.
    assert answer.citations == ["Page 4"]
    # But the answer body NEVER fabricates a grounded summary from
    # stitched chunks, and the support flag is always False on this
    # path so the abstention contract holds.
    assert answer.supported is False
    assert answer.support_status == "unsupported"
    assert answer.support_summary == "Live model unavailable"
    # The old mid-word chunk slice ("text[:220]") leaked things like
    # "tee voted" / "Strateg Document" into the rendered answer. Verify
    # the raw chunk text is no longer in the body.
    assert "thirty days" not in answer.answer.lower()
    assert "unavailable" in answer.answer.lower()


def test_fallback_holds_abstention_contract_on_off_topic_question():
    """Launch-blocker regression (PR readiness review 2026-05-27):
    asking "What is the price of Bitcoin today?" against a FOMC-only
    document returned ``SUPPORTED · Best local match`` with stitched
    FOMC chunks instead of abstaining. The marketing claim of "100%
    abstention when out-of-scope" was being silently violated any
    time the live model failed and the fallback path fired. Pin the
    abstention contract: when the live model is unreachable, NO
    amount of retrieved evidence can flip supported back to True.
    """
    settings = Settings(openai_api_key=None)  # forces fallback path
    generator = AnswerGenerator(settings)
    # Simulate the n8n / Bitcoin scenario: the retriever pulled
    # unrelated-but-keyword-adjacent FOMC chunks. Under the old
    # heuristic these would have stitched into a "Best local match"
    # response with supported=True.
    retrieval = RetrievalResult(
        question="What is the price of Bitcoin today?",
        candidates=[
            RetrievalCandidate(
                chunk_id="c1",
                text="Personal consumption expenditures inflation rose at a 4.2 percent pace.",
                metadata={"page_label": "Page 3"},
            ),
            RetrievalCandidate(
                chunk_id="c2",
                text="The unemployment rate held at 3.6 percent in the quarter.",
                metadata={"page_label": "Page 5"},
            ),
        ],
    )

    answer = generator.generate("What is the price of Bitcoin today?", retrieval)

    assert answer.supported is False, (
        "Off-topic question with unrelated retrieval must NOT be marked supported"
    )
    assert answer.support_status == "unsupported"
    # The fabricated stitched chunks must not leak into the answer body.
    assert "personal consumption" not in answer.answer.lower()
    assert "unemployment" not in answer.answer.lower()
    assert "4.2 percent" not in answer.answer
    # And the misleading "best local match" / "Here is the strongest
    # grounded summary I could find" boilerplate is gone.
    assert "best local match" not in answer.support_summary.lower()
    assert "strongest grounded summary" not in answer.answer.lower()


def test_generation_short_circuits_when_retrieval_is_clearly_unsupported():
    settings = Settings(openai_api_key=None)
    generator = AnswerGenerator(settings)
    retrieval = RetrievalResult(
        question="What is the capital of France?",
        candidates=[],
        evidence_status="unsupported",
    )

    answer = generator.generate("What is the capital of France?", retrieval)

    assert answer.supported is False
    assert answer.model_name == "retrieval_guardrail"
    assert "unsupported" in answer.answer.lower()


def test_grounded_prompt_adds_summary_specific_guidance_for_global_questions():
    prompt = build_grounded_prompt(
        "What is this paper about?",
        [
            RetrievalCandidate(
                chunk_id="c1",
                text="This paper introduces a new multimodal report generation approach.",
                metadata={"page_label": "Page 1"},
            )
        ],
        summary_mode=True,
    )

    assert "broad high-level summary question" in prompt
    assert "what the document is about" in prompt.lower()


def test_generation_marks_schema_drift_fallback_as_unsupported():
    """Locks the Codex P1 fix on PR #6: when the model returns content
    the schema-strict wrapper rejects (StructuredOutputError), we used
    to drop into the heuristic ``_fallback_answer`` which stamps
    ``supported=True`` for any non-empty evidence. That silently
    presented a "best-local-match" summary as if it had been verified
    by the LLM. The fix forces the fallback answer to be marked
    ``supported=False / support_status=unsupported`` so the trace +
    UI surface the drift instead of presenting the fallback as
    grounded."""
    # Return content that's not JSON — the wrapper raises
    # StructuredOutputError on the json.loads step.
    settings = Settings(openai_api_key="test-key")
    generator = AnswerGenerator(settings)
    generator.client = _FakeClient(["this is not json"])

    retrieval = RetrievalResult(
        question="What is the waiting period?",
        candidates=[
            RetrievalCandidate(
                chunk_id="c1",
                text="The waiting period is thirty days from the policy effective date.",
                metadata={"page_label": "Page 4"},
            )
        ],
    )

    answer = generator.generate("What is the waiting period?", retrieval)

    # Even though the heuristic ``_fallback_answer`` would have set
    # supported=True for this non-empty evidence, the schema-drift
    # branch must override it.
    assert answer.supported is False
    assert answer.support_status == "unsupported"
    assert "schema" in (answer.note or "").lower()


def test_grounded_prompt_requires_complete_support_for_multi_part_answers():
    prompt = build_grounded_prompt(
        "Compare the reported GAN, diffusion, and LLM findings.",
        [
            RetrievalCandidate(
                chunk_id="c1",
                text="The paper reports that GANs can generate realistic construction design images.",
                metadata={"page_label": "Page 3"},
            )
        ],
    )

    assert "set supported to true only when the evidence covers every required fact" in prompt
    assert "which required fact is missing" in prompt
    assert "support_status to partial" in prompt


def test_grounded_prompt_bans_inferential_supported_answers():
    prompt = build_grounded_prompt(
        "What does the report conclude?",
        [
            RetrievalCandidate(
                chunk_id="c1",
                text="The report states the trial was small and more evidence is needed.",
                metadata={"page_label": "Page 6"},
            )
        ],
    )

    assert "Do not use inferential wording" in prompt
    assert "downgrade to supported=false" in prompt


def test_inferential_supported_language_is_detected():
    assert _uses_inferential_supported_language("The evidence suggests that the policy changed.") is True
    assert _uses_inferential_supported_language("The policy changed on January 1.") is False


def test_support_gap_reason_detects_missing_explicit_fact():
    reason = (
        "The evidence does not provide a separate explicit 2030 figure, "
        "so the requested comparison is only partially supported."
    )

    assert _reason_reports_support_gap(reason) is True


def test_generation_preserves_partial_support_as_distinct_status():
    answer_payload = json.dumps(
        {
            "supported": False,
            "support_status": "partial",
            "answer": "The evidence states that Jane Smith dissented, but it does not state what alternative she preferred [Source 1].",
            "reason": "One required fact is supported and one required fact is missing.",
        }
    )
    verifier_payload = json.dumps(
        {
            "support_status": "partial",
            "answer_acknowledges_gap": True,
            "supported_facts": ["Jane Smith dissented."],
            "missing_or_ambiguous_facts": ["The preferred alternative is not stated."],
            "reason": "The answer separates the supported dissenter fact from the missing alternative fact.",
        }
    )
    generator = AnswerGenerator(Settings(openai_api_key="test-key"))
    generator.client = _FakeClient([answer_payload, verifier_payload])
    retrieval = RetrievalResult(
        question="Who dissented and what alternative did they prefer?",
        candidates=[
            RetrievalCandidate(
                chunk_id="c1",
                text="Jane Smith dissented from the policy decision.",
                metadata={"page_label": "Page 10"},
            )
        ],
    )

    answer = generator.generate("Who dissented and what alternative did they prefer?", retrieval)

    assert answer.supported is False
    assert answer.support_status == "partial"
    assert answer.citations == ["Page 10"]
    assert "References:" in answer.answer


def test_generation_downgrades_hidden_partial_to_unsupported():
    answer_payload = json.dumps(
        {
            "supported": False,
            "support_status": "partial",
            "answer": "Jane Smith dissented [Source 1].",
            "reason": "The preferred alternative is missing.",
        }
    )
    verifier_payload = json.dumps(
        {
            "support_status": "unsupported",
            "answer_acknowledges_gap": False,
            "supported_facts": ["Jane Smith dissented."],
            "missing_or_ambiguous_facts": ["The preferred alternative is not stated."],
            "reason": "The answer omits the missing required fact, so it hides the gap.",
        }
    )
    generator = AnswerGenerator(Settings(openai_api_key="test-key"))
    generator.client = _FakeClient([answer_payload, verifier_payload])
    retrieval = RetrievalResult(
        question="Who dissented and what alternative did they prefer?",
        candidates=[
            RetrievalCandidate(
                chunk_id="c1",
                text="Jane Smith dissented from the policy decision.",
                metadata={"page_label": "Page 10"},
            )
        ],
    )

    answer = generator.generate("Who dissented and what alternative did they prefer?", retrieval)

    assert answer.support_status == "unsupported"
    assert "Support-status verifier classified the answer as unsupported" in (answer.note or "")


def test_support_status_verifier_recovers_general_gap_phrasing_to_partial():
    answer_payload = json.dumps(
        {
            "supported": False,
            "support_status": "unsupported",
            "answer": (
                "The evidence supports that each vehicle includes a micro-controller board and an inertial measurement unit. "
                "However, it does not clearly state whether the radio module is part of each vehicle's onboard hardware [Source 1]."
            ),
            "reason": "The hardware list is incomplete.",
        }
    )
    verifier_payload = json.dumps(
        {
            "support_status": "partial",
            "answer_acknowledges_gap": True,
            "supported_facts": ["Each vehicle includes a micro-controller board and an inertial measurement unit."],
            "missing_or_ambiguous_facts": ["Whether the radio module is part of each vehicle's onboard hardware is ambiguous."],
            "reason": "The answer states a grounded hardware subset and visibly acknowledges the ambiguous radio-module fact.",
        }
    )
    generator = AnswerGenerator(Settings(openai_api_key="test-key"))
    generator.client = _FakeClient([answer_payload, verifier_payload])
    retrieval = RetrievalResult(
        question="What onboard hardware components are listed for each vehicle?",
        candidates=[
            RetrievalCandidate(
                chunk_id="c1",
                text="Each vehicle has a micro-controller board and an inertial measurement unit.",
                metadata={"page_label": "Page 10"},
            )
        ],
    )

    answer = generator.generate("What onboard hardware components are listed for each vehicle?", retrieval)

    assert answer.supported is False
    assert answer.support_status == "partial"
    assert "Support-status verifier classified the answer as partial" in (answer.note or "")


def test_support_status_verifier_can_recover_fully_supported_atomic_answer():
    answer_payload = json.dumps(
        {
            "supported": False,
            "support_status": "unsupported",
            "answer": "The report was copy-edited by Steven B. Kennedy [Source 1].",
            "reason": "The first pass was uncertain.",
        }
    )
    verifier_payload = json.dumps(
        {
            "support_status": "supported",
            "answer_acknowledges_gap": False,
            "supported_facts": ["The report was copy-edited by Steven B. Kennedy."],
            "missing_or_ambiguous_facts": [],
            "reason": "The evidence directly states the copy editor.",
        }
    )
    generator = AnswerGenerator(Settings(openai_api_key="test-key"))
    generator.client = _FakeClient([answer_payload, verifier_payload])
    retrieval = RetrievalResult(
        question="Who is credited with copy-editing the report?",
        candidates=[
            RetrievalCandidate(
                chunk_id="c1",
                text="The report was copy-edited by Steven B. Kennedy.",
                metadata={"page_label": "Page iv"},
            )
        ],
    )

    answer = generator.generate("Who is credited with copy-editing the report?", retrieval)

    assert answer.supported is True
    assert answer.support_status == "supported"
    assert answer.citations == ["Page iv"]
    assert "Support-status verifier classified the answer as supported" in (answer.note or "")


def test_support_status_verifier_does_not_recover_inferential_atomic_answer():
    answer_payload = json.dumps(
        {
            "supported": True,
            "support_status": "supported",
            "answer": "The report likely means Steven B. Kennedy was the copy editor [Source 1].",
            "reason": "The answer is probably supported.",
        }
    )
    verifier_payload = json.dumps(
        {
            "support_status": "supported",
            "answer_acknowledges_gap": False,
            "supported_facts": ["The report names Steven B. Kennedy near copy-editing metadata."],
            "missing_or_ambiguous_facts": [],
            "reason": "The evidence contains the relevant name.",
        }
    )
    generator = AnswerGenerator(Settings(openai_api_key="test-key"))
    generator.client = _FakeClient([answer_payload, verifier_payload])
    retrieval = RetrievalResult(
        question="Who is credited with copy-editing the report?",
        candidates=[
            RetrievalCandidate(
                chunk_id="c1",
                text="The report was copy-edited by Steven B. Kennedy.",
                metadata={"page_label": "Page iv"},
            )
        ],
    )

    answer = generator.generate("Who is credited with copy-editing the report?", retrieval)

    assert answer.supported is False
    assert answer.support_status == "unsupported"
    assert "inferential wording" in (answer.note or "")


def test_support_status_verifier_does_not_abstain_grounded_answer_that_merely_hedges():
    """Regression for the PR#6 false-abstention bug (verify_support_status).

    The answer is fully grounded and the verifier independently confirms
    it (support_status=supported, supported_facts present, NOTHING in
    missing_or_ambiguous_facts) — but the answer politely volunteered a
    caveat, so answer_acknowledges_gap=True. The old code forced
    "unsupported" here (no path back to "supported" once a gap was
    acknowledged), abstaining a correct answer. The verifier's
    evidence-grounded verdict must win over the answer's self-doubt.
    """
    answer_payload = json.dumps(
        {
            "supported": False,
            "support_status": "unsupported",
            "answer": (
                "By unanimous vote the Committee selected Jerome H. Powell "
                "as Chair [Source 1]. Missing required fact: the evidence "
                "may not exhaustively label every role."
            ),
            "reason": "The first pass hedged on completeness.",
        }
    )
    verifier_payload = json.dumps(
        {
            "support_status": "supported",
            "answer_acknowledges_gap": True,
            "supported_facts": [
                "By unanimous vote, Jerome H. Powell was selected as Chair."
            ],
            "missing_or_ambiguous_facts": [],
            "reason": "The evidence directly supports the named selection; "
            "no required fact is missing for the question as asked.",
        }
    )
    generator = AnswerGenerator(Settings(openai_api_key="test-key"))
    generator.client = _FakeClient([answer_payload, verifier_payload])
    retrieval = RetrievalResult(
        question="Who was selected as Chair by unanimous vote?",
        candidates=[
            RetrievalCandidate(
                chunk_id="c1",
                text="By unanimous vote the Committee selected Jerome H. Powell as Chair.",
                metadata={"page_label": "Page 2"},
            )
        ],
    )

    answer = generator.generate(
        "Who was selected as Chair by unanimous vote?", retrieval
    )

    # Pre-fix: this was support_status == "unsupported" (ABSTAINED).
    assert answer.support_status == "supported"
    assert answer.supported is True


def test_support_status_verifier_still_downgrades_when_verifier_finds_real_gap():
    """Guard against over-correcting the fix: when the verifier itself
    reports a missing/ambiguous required fact, an acknowledged gap is a
    *real* partial (or unsupported if unowned) — NOT a free pass to
    "supported"."""
    answer_payload = json.dumps(
        {
            "supported": False,
            "support_status": "partial",
            "answer": "Powell is Chair [Source 1]; the deputy is not stated.",
            "reason": "Only part of the question is covered.",
        }
    )
    verifier_payload = json.dumps(
        {
            "support_status": "supported",
            "answer_acknowledges_gap": True,
            "supported_facts": ["Powell is Chair."],
            "missing_or_ambiguous_facts": ["The deputy manager is not named."],
            "reason": "Chair is grounded; the deputy is genuinely absent.",
        }
    )
    generator = AnswerGenerator(Settings(openai_api_key="test-key"))
    generator.client = _FakeClient([answer_payload, verifier_payload])
    retrieval = RetrievalResult(
        question="Who are the Chair and deputy?",
        candidates=[
            RetrievalCandidate(
                chunk_id="c1",
                text="Powell is Chair.",
                metadata={"page_label": "Page 2"},
            )
        ],
    )

    answer = generator.generate("Who are the Chair and deputy?", retrieval)

    # Verifier found a real missing fact + answer owns it -> partial,
    # not silently promoted to supported by the fix.
    assert answer.support_status == "partial"
    assert answer.supported is False
