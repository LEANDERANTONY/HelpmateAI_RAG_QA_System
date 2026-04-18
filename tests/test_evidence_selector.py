from src.config import Settings
from src.generation.evidence_selector import EvidenceSelector
from src.schemas import RetrievalCandidate, RetrievalResult


class _FakeResponse:
    def __init__(self, content: str):
        self.choices = [type("Choice", (), {"message": type("Message", (), {"content": content})()})()]


class _FakeCompletions:
    def __init__(self, content: str):
        self._content = content

    def create(self, **kwargs):
        return _FakeResponse(self._content)


class _FakeClient:
    def __init__(self, content: str):
        self.chat = type("Chat", (), {"completions": _FakeCompletions(content)})()


def test_evidence_selector_skips_unsupported_results():
    selector = EvidenceSelector(Settings(openai_api_key=None, evidence_selector_enabled=True))
    retrieval = RetrievalResult(
        question="What is the capital of France?",
        candidates=[],
        evidence_status="unsupported",
    )

    selected = selector.select("What is the capital of France?", retrieval)

    assert selected is retrieval


def test_evidence_selector_can_promote_lower_ranked_chunk_when_llm_prefers_it():
    selector = EvidenceSelector(
        Settings(
            openai_api_key="test-key",
            evidence_selector_enabled=True,
            evidence_selector_top_k=4,
            evidence_selector_max_evidence=2,
            evidence_selector_prune=True,
        )
    )
    selector.client = _FakeClient(
        '{"candidate_scores":{"c1":0.15,"c2":0.95,"c3":0.05},"selected_ids":["c2","c1"]}'
    )
    retrieval = RetrievalResult(
        question="What was the baseline bias AUC and what did extreme standardization reduce it to?",
        candidates=[
            RetrievalCandidate(chunk_id="c1", text="Near miss summary.", metadata={"page_label": "Page 43"}, rerank_score=0.92),
            RetrievalCandidate(chunk_id="c2", text="The baseline bias AUC was 0.866 and extreme standardization reduced it to 0.569.", metadata={"page_label": "Page 73"}, rerank_score=0.88),
            RetrievalCandidate(chunk_id="c3", text="Less relevant discussion.", metadata={"page_label": "Page 20"}, rerank_score=0.61),
        ],
        evidence_status="strong",
        retrieval_plan={"evidence_spread": "sectional"},
    )

    selected = selector.select(retrieval.question, retrieval)

    assert [candidate.chunk_id for candidate in selected.candidates] == ["c2", "c1"]
    assert any("Evidence selector reviewed top" in note for note in selected.strategy_notes)


def test_evidence_selector_skips_when_no_client_is_available():
    selector = EvidenceSelector(Settings(openai_api_key=None, evidence_selector_enabled=True))
    retrieval = RetrievalResult(
        question="What is the main focus of the paper?",
        candidates=[
            RetrievalCandidate(chunk_id="c1", text="Overview.", metadata={"page_label": "Page 1"}, rerank_score=0.8),
            RetrievalCandidate(chunk_id="c2", text="Discussion.", metadata={"page_label": "Page 8"}, rerank_score=0.78),
        ],
        evidence_status="weak",
        retrieval_plan={"evidence_spread": "global"},
    )

    selected = selector.select(retrieval.question, retrieval)

    assert selected is retrieval


def test_evidence_selector_can_reorder_without_pruning():
    selector = EvidenceSelector(
        Settings(
            openai_api_key="test-key",
            evidence_selector_enabled=True,
            evidence_selector_top_k=4,
            evidence_selector_max_evidence=2,
            evidence_selector_prune=False,
        )
    )
    selector.client = _FakeClient(
        '{"candidate_scores":{"c1":0.15,"c2":0.95,"c3":0.05},"selected_ids":["c2","c1"]}'
    )
    retrieval = RetrievalResult(
        question="What was the baseline bias AUC and what did extreme standardization reduce it to?",
        candidates=[
            RetrievalCandidate(chunk_id="c1", text="Near miss summary.", metadata={"page_label": "Page 43"}, rerank_score=0.92),
            RetrievalCandidate(chunk_id="c2", text="The baseline bias AUC was 0.866 and extreme standardization reduced it to 0.569.", metadata={"page_label": "Page 73"}, rerank_score=0.88),
            RetrievalCandidate(chunk_id="c3", text="Less relevant discussion.", metadata={"page_label": "Page 20"}, rerank_score=0.61),
        ],
        evidence_status="strong",
        retrieval_plan={"evidence_spread": "sectional"},
    )

    selected = selector.select(retrieval.question, retrieval)

    assert [candidate.chunk_id for candidate in selected.candidates] == ["c2", "c1", "c3"]
    assert any("reordered evidence to start with" in note for note in selected.strategy_notes)
