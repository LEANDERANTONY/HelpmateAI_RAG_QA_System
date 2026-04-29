from src.config import Settings
from src.generation.prompts import build_grounded_prompt
from src.generation.service import AnswerGenerator, _uses_inferential_supported_language
from src.schemas import RetrievalCandidate, RetrievalResult


def test_fallback_generation_uses_evidence_when_api_key_missing():
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

    assert "waiting period" in answer.answer.lower()
    assert answer.citations == ["Page 4"]
    assert answer.supported is True


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
    assert "only answers it partially" in prompt


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
