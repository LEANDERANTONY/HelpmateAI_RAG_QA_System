from src.config import Settings
from src.retrieval.query_rewriter import QueryRewriter


def test_query_rewriter_fallback_preserves_original_and_adds_variant():
    rewriter = QueryRewriter(Settings(openai_api_key=None))
    variants = rewriter.rewrite("What is the free look period in this policy?")

    assert variants[0] == "What is the free look period in this policy?"
    assert any("right to examine policy" in variant.lower() for variant in variants)
