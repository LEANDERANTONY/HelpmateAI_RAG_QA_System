from src.query_analysis import QueryAnalyzer


def test_query_analyzer_classifies_waiting_period_queries():
    profile = QueryAnalyzer.analyze("What is the named ailment waiting period under clause 4.1?")

    assert profile.query_type == "definition_lookup"
    assert profile.evidence_spread == "atomic"
    assert "4.1" in profile.clause_terms


def test_query_analyzer_classifies_summary_questions():
    profile = QueryAnalyzer.analyze("What is the main aim of this thesis?")

    assert profile.query_type == "summary_lookup"


def test_query_analyzer_marks_feature_list_questions_as_specific_detail_procedure():
    profile = QueryAnalyzer.analyze("What seven urinary biomarker features were used for the biomarker model?")

    assert profile.query_type == "process_lookup"
    assert profile.intent_type == "procedure"
    assert profile.asks_for_specific_detail is True


def test_query_analyzer_treats_future_work_as_global_summary():
    profile = QueryAnalyzer.analyze("What kinds of future work or next steps does the thesis suggest?")

    assert profile.query_type == "summary_lookup"
    assert profile.evidence_spread == "global"


def test_query_analyzer_treats_main_contribution_as_global_summary():
    profile = QueryAnalyzer.analyze("What is the main contribution of this paper?")

    assert profile.query_type == "summary_lookup"
    assert profile.evidence_spread == "global"


def test_query_analyzer_treats_paper_about_question_as_global_summary():
    profile = QueryAnalyzer.analyze("What is this paper about?")

    assert profile.query_type == "summary_lookup"
    assert profile.evidence_spread == "global"
