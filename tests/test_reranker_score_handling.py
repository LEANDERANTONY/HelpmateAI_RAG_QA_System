from src.generation.evidence_selector import EvidenceSelector
from src.retrieval.hybrid import HybridRetriever
from src.schemas import RetrievalCandidate


def test_hybrid_evidence_score_ignores_raw_reranker_logits():
    candidate = RetrievalCandidate(
        chunk_id="chunk-1",
        text="Pancreatic cancer classifier",
        metadata={},
        fused_score=0.42,
        rerank_score=-10.8,
    )

    assert HybridRetriever._evidence_score(candidate) == 0.42


def test_evidence_selector_prior_uses_fused_score_not_reranker_logit():
    candidate = RetrievalCandidate(
        chunk_id="chunk-1",
        text="Pancreatic cancer classifier",
        metadata={},
        fused_score=0.31,
        rerank_score=-9.7,
    )

    assert EvidenceSelector._candidate_score(candidate) == 0.31
