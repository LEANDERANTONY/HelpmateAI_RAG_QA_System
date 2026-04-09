from src.config import Settings
from src.query_analysis import QueryAnalyzer
from src.retrieval.planner import RetrievalPlanner
from src.schemas import SectionSynopsisRecord


def _synopsis(section_id: str, title: str, region_kind: str) -> SectionSynopsisRecord:
    return SectionSynopsisRecord(
        section_id=section_id,
        document_id="doc1",
        title=title,
        synopsis=f"{title} synopsis",
        region_kind=region_kind,
        page_labels=["Page 1"],
        key_terms=[title.lower()],
        metadata={"section_path": [title], "source_file": "doc.pdf"},
    )


def test_planner_prefers_hard_region_for_explicit_page_queries():
    planner = RetrievalPlanner(Settings(router_llm_enabled=False))
    profile = QueryAnalyzer().analyze("What does page 20 say about exclusions?")
    plan = planner.plan(
        question="What does page 20 say about exclusions?",
        query_profile=profile,
        metadata_filters={"page_labels": ["Page 20"], "section_terms": [], "clause_terms": []},
        synopses=[],
    )

    assert plan.constraint_mode == "hard_region"
    assert plan.preferred_route == "chunk_first"
    assert plan.hard_filters["page_labels"] == ["Page 20"]


def test_planner_prefers_synopsis_first_for_global_summary_questions():
    planner = RetrievalPlanner(Settings(router_llm_enabled=False))
    profile = QueryAnalyzer().analyze("What is the main focus of this paper?")
    plan = planner.plan(
        question="What is the main focus of this paper?",
        query_profile=profile,
        metadata_filters={"page_labels": [], "section_terms": [], "clause_terms": []},
        synopses=[
            _synopsis("abstract", "Abstract", "overview"),
            _synopsis("discussion", "Discussion", "discussion"),
        ],
    )

    assert plan.preferred_route == "synopsis_first"
    assert plan.evidence_spread == "global"
    assert plan.constraint_mode == "soft_multi_region"
    assert plan.use_global_fallback is True


def test_planner_uses_soft_multi_region_for_distributed_questions():
    planner = RetrievalPlanner(Settings(router_llm_enabled=False))
    profile = QueryAnalyzer().analyze("What exclusions apply in this policy?")
    plan = planner.plan(
        question="What exclusions apply in this policy?",
        query_profile=profile,
        metadata_filters={"page_labels": [], "section_terms": [], "clause_terms": []},
        synopses=[
            _synopsis("overview", "Overview", "overview"),
            _synopsis("terms", "Terms and Conditions", "rules"),
            _synopsis("exclusions", "Exclusions", "rules"),
        ],
    )

    assert plan.evidence_spread == "distributed"
    assert plan.constraint_mode == "soft_multi_region"
    assert plan.use_global_fallback is True
    assert plan.target_region_ids


def test_planner_prefers_hybrid_both_for_specific_implementation_detail_questions():
    planner = RetrievalPlanner(Settings(router_llm_enabled=False))
    profile = QueryAnalyzer().analyze("How was multimodal fusion implemented in the thesis?")
    plan = planner.plan(
        question="How was multimodal fusion implemented in the thesis?",
        query_profile=profile,
        metadata_filters={"page_labels": [], "section_terms": [], "clause_terms": []},
        synopses=[
            _synopsis("methods", "Methodology", "procedure"),
            _synopsis("fusion", "Multimodal Fusion Implementation", "procedure"),
            _synopsis("results", "Results", "evidence"),
        ],
    )

    assert plan.intent_type == "procedure"
    assert plan.preferred_route == "hybrid_both"
