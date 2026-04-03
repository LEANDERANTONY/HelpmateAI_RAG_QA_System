from src.query_analysis import QueryProfile
from src.config import Settings
from src.retrieval.query_rewriter import QueryRewriter


def test_query_rewriter_fallback_preserves_original_and_adds_variant():
    rewriter = QueryRewriter(Settings(openai_api_key=None))
    variants = rewriter.rewrite("What is the free look period in this policy?")

    assert variants[0] == "What is the free look period in this policy?"
    assert any("right to examine policy" in variant.lower() for variant in variants)


def test_query_rewriter_adds_section_expansion_for_summary_questions():
    rewriter = QueryRewriter(Settings(openai_api_key=None))

    variants = rewriter.rewrite(
        "What kinds of future work or next steps does the thesis suggest?",
        query_profile=QueryProfile(query_type="summary_lookup"),
    )

    assert variants[0] == "What kinds of future work or next steps does the thesis suggest?"
    assert any("future work recommendations" in variant.lower() for variant in variants[1:])
