from src.config import Settings
from src.retrieval.hybrid import HybridRetriever


def test_extract_metadata_filters_detects_page_reference():
    retriever = HybridRetriever(store=None, settings=Settings())  # type: ignore[arg-type]
    filters = retriever._extract_metadata_filters('What does page 20 say about the "free look" period?')

    assert filters["page_labels"] == ["Page 20"]
    assert filters["section_terms"] == ["free look"]


def test_extract_metadata_filters_detects_clause_reference():
    retriever = HybridRetriever(store=None, settings=Settings())  # type: ignore[arg-type]
    filters = retriever._extract_metadata_filters("What does clause 4.1 say about waiting periods?")

    assert filters["clause_terms"] == ["4.1"]
