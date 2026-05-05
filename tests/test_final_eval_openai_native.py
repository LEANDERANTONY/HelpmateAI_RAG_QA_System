from pathlib import Path

from src.config import get_settings
from src.evals import final_eval_suite
from src.evals.final_eval_suite import EvalDocument, EvalQuestion, EvalSuite


class _FakeScorer:
    judge_info = {"reason": "disabled"}
    available = False

    def score(self, *, question: str, answer_text: str, contexts: list[str]) -> dict:
        return {}


class _FakeOpenAIFileSearch:
    calls: list[dict] = []

    def answer(self, document_path, question, *, model, max_num_results, max_context_chars):
        self.calls.append(
            {
                "document_path": document_path,
                "question": question,
                "model": model,
                "max_num_results": max_num_results,
                "max_context_chars": max_context_chars,
            }
        )
        return {
            "answer": "The native OpenAI file_search answer.",
            "supported": True,
            "contexts": [
                {
                    "text": "Native file_search context.",
                    "metadata": {
                        "page_label": "OpenAI File Search Result 1",
                        "source": "openai_file_search_native",
                    },
                }
            ],
            "response_id": "resp_test",
            "model": model,
        }


def test_openai_file_search_uses_native_answer_path(monkeypatch, tmp_path: Path):
    fake_provider = _FakeOpenAIFileSearch()
    monkeypatch.setattr(final_eval_suite, "OpenAIFileSearchBenchmark", lambda: fake_provider)
    document_path = tmp_path / "sample.pdf"
    document_path.write_bytes(b"%PDF-1.4\n")
    suite = EvalSuite(
        suite_id="native-openai-test",
        description="",
        frozen=False,
        context_top_k=5,
        max_context_chars=600,
        documents=(
            EvalDocument(
                document_id="doc",
                path=document_path,
                document_type="pdf",
            ),
        ),
        questions=(
            EvalQuestion(
                question_id="q1",
                document_id="doc",
                question="What does the file say?",
                intent_type="lookup",
                answerable=True,
            ),
        ),
    )
    rows = final_eval_suite._run_openai_file_search(
        suite,
        {"doc": suite.documents[0]},
        [suite.questions[0]],
        get_settings(),
        _FakeScorer(),
    )

    assert fake_provider.calls == [
        {
            "document_path": document_path,
            "question": "What does the file say?",
            "model": get_settings().answer_model,
            "max_num_results": 5,
            "max_context_chars": None,
        }
    ]
    assert rows[0]["system"] == "openai_file_search"
    assert rows[0]["supported"] is True
    assert rows[0]["answer_preview"] == "The native OpenAI file_search answer."
    assert rows[0]["context_count"] == 1


def test_openai_retrieval_helpmate_answer_is_not_default():
    assert "openai_retrieval_helpmate_answer" not in final_eval_suite.DEFAULT_SYSTEMS
    assert "openai_retrieval_helpmate_answer" in final_eval_suite.ALLOWED_SYSTEMS


class _FakeVectaraNative:
    available = True
    calls: list[dict] = []

    def answer(self, document_path, question, *, limit):
        self.calls.append(
            {
                "document_path": document_path,
                "question": question,
                "limit": limit,
            }
        )
        return {
            "answer": "The native Vectara answer.",
            "supported": True,
            "contexts": [
                {
                    "text": "Native Vectara context.",
                    "metadata": {
                        "page_label": "Vectara Result 1",
                        "source": "vectara_native",
                    },
                }
            ],
            "generation_preset_name": "mockingbird-2.0",
            "factual_consistency_score": 0.99,
        }


def test_vectara_uses_native_answer_path(monkeypatch, tmp_path: Path):
    fake_provider = _FakeVectaraNative()
    monkeypatch.setattr(final_eval_suite, "VectaraBenchmark", lambda: fake_provider)
    document_path = tmp_path / "sample.pdf"
    document_path.write_bytes(b"%PDF-1.4\n")
    suite = EvalSuite(
        suite_id="native-vectara-test",
        description="",
        frozen=False,
        context_top_k=5,
        max_context_chars=600,
        documents=(
            EvalDocument(
                document_id="doc",
                path=document_path,
                document_type="pdf",
            ),
        ),
        questions=(
            EvalQuestion(
                question_id="q1",
                document_id="doc",
                question="What does the file say?",
                intent_type="lookup",
                answerable=True,
            ),
        ),
    )
    rows = final_eval_suite._run_vectara(
        suite,
        {"doc": suite.documents[0]},
        [suite.questions[0]],
        get_settings(),
        _FakeScorer(),
    )

    assert fake_provider.calls == [
        {
            "document_path": document_path,
            "question": "What does the file say?",
            "limit": 5,
        }
    ]
    assert rows[0]["system"] == "vectara"
    assert rows[0]["supported"] is True
    assert rows[0]["answer_preview"] == "The native Vectara answer."
    assert rows[0]["context_count"] == 1
