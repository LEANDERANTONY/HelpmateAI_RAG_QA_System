from src.config import Settings
from src.retrieval.hybrid import HybridRetriever
from src.schemas import RetrievalCandidate


def test_extract_metadata_filters_detects_page_reference():
    retriever = HybridRetriever(store=None, settings=Settings())  # type: ignore[arg-type]
    filters = retriever._extract_metadata_filters('What does page 20 say about the "free look" period?')

    assert filters["page_labels"] == ["Page 20"]
    assert filters["section_terms"] == ["free look"]


def test_extract_metadata_filters_detects_clause_reference():
    retriever = HybridRetriever(store=None, settings=Settings())  # type: ignore[arg-type]
    filters = retriever._extract_metadata_filters("What does clause 4.1 say about waiting periods?")

    assert filters["clause_terms"] == ["4.1"]


def test_assess_evidence_status_marks_unsupported_for_very_low_signal():
    retriever = HybridRetriever(store=None, settings=Settings())  # type: ignore[arg-type]
    candidates = [
        RetrievalCandidate(
            chunk_id="c1",
            text="irrelevant policy clause language",
            metadata={},
            lexical_score=0.001,
            fused_score=0.005,
        )
    ]

    status, best_score, max_lexical, content_overlap = retriever._assess_evidence_status(
        "What does this policy say about the capital of France?",
        candidates,
    )

    assert status == "unsupported"
    assert best_score == 0.005
    assert max_lexical == 0.001
    assert content_overlap == 0.0


def test_assess_evidence_status_marks_weak_for_borderline_signal():
    retriever = HybridRetriever(store=None, settings=Settings())  # type: ignore[arg-type]
    candidates = [
        RetrievalCandidate(
            chunk_id="c1",
            text="future work recommendations and next steps for the thesis are discussed here",
            metadata={},
            lexical_score=0.01,
            fused_score=0.02,
        )
    ]

    status, _, _, content_overlap = retriever._assess_evidence_status(
        "What kinds of future work or next steps does the thesis suggest?",
        candidates,
    )

    assert status == "weak"
    assert content_overlap > 0.0


def test_assess_evidence_status_keeps_summary_queries_retryable_when_abstract_is_present():
    retriever = HybridRetriever(store=None, settings=Settings())  # type: ignore[arg-type]
    candidates = [
        RetrievalCandidate(
            chunk_id="c1",
            text="Multimodal data fusion for cancer biomarker discovery with deep learning.",
            metadata={"section_kind": "abstract"},
            lexical_score=0.0,
            fused_score=0.02,
        )
    ]

    status, _, _, _ = retriever._assess_evidence_status(
        "What is the main focus of the pancreas8 paper?",
        candidates,
        query_type="summary_lookup",
    )

    assert status == "weak"
