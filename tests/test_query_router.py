from src.query_analysis import QueryAnalyzer
from src.query_router import QueryRouter


def test_query_router_prefers_chunk_first_for_exact_questions():
    question = "What is the waiting period under clause 4.1 on page 23?"

    decision = QueryRouter().route(question, QueryAnalyzer.analyze(question))

    assert decision.route == "chunk_first"


def test_query_router_prefers_synopsis_first_for_summary_questions():
    question = "Summarize the paper's main conclusion and future work."

    decision = QueryRouter().route(question, QueryAnalyzer.analyze(question))

    assert decision.route == "synopsis_first"
