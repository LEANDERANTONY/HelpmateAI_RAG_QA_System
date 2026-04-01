from src.query_analysis import QueryAnalyzer


def test_query_analyzer_classifies_waiting_period_queries():
    profile = QueryAnalyzer.analyze("What is the named ailment waiting period under clause 4.1?")

    assert profile.query_type == "waiting_period_lookup"
    assert "waiting_period" in profile.preferred_content_types
    assert "4.1" in profile.clause_terms
