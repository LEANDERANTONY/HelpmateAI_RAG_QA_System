from src.config import Settings
from src.generation.service import AnswerGenerator
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
