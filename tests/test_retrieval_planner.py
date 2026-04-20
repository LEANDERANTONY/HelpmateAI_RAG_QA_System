from src.config import Settings
from src.query_analysis import QueryAnalyzer
from src.retrieval.planner import RetrievalPlanner
from src.schemas import SectionSynopsisRecord


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


def test_llm_planner_can_override_taxonomy_and_route_together():
    planner = RetrievalPlanner(
        Settings(
            openai_api_key="test-key",
            planner_llm_enabled=True,
            router_llm_enabled=False,
        )
    )
    planner.client = _FakeClient(
        (
            '{"intent_type":"cross_cutting","query_type":"cross_cutting_lookup",'
            '"evidence_spread":"distributed","preferred_route":"hybrid_both",'
            '"constraint_mode":"soft_multi_region","target_region_kinds":["procedure","evidence"],'
            '"preferred_content_types":["methodology","results"],"use_global_fallback":true,"confidence":0.88}'
        )
    )

    query_profile, plan = planner.analyze_and_plan(
        question="How does the project describe its methodology or experimental setup?",
        metadata_filters={"page_labels": [], "section_terms": [], "clause_terms": []},
        synopses=[
            _synopsis("ack", "Acknowledgements", "general"),
            _synopsis("methods", "Methodology", "procedure"),
            _synopsis("results", "Experimental Results", "evidence"),
        ],
    )

    assert query_profile.evidence_spread == "distributed"
    assert query_profile.query_type == "cross_cutting_lookup"
    assert query_profile.preferred_content_types == ["methodology", "results"]
    assert plan.preferred_route == "hybrid_both"
    assert plan.constraint_mode == "soft_multi_region"
    assert plan.target_region_kinds == ["procedure", "evidence"]
    assert plan.planner_source == "llm_structured"


def test_llm_planner_respects_explicit_hard_constraints():
    planner = RetrievalPlanner(
        Settings(
            openai_api_key="test-key",
            planner_llm_enabled=True,
            router_llm_enabled=False,
        )
    )
    planner.client = _FakeClient(
        (
            '{"intent_type":"summary","query_type":"summary_lookup","evidence_spread":"global",'
            '"preferred_route":"synopsis_first","constraint_mode":"soft_multi_region",'
            '"target_region_kinds":["overview"],"use_global_fallback":true,"confidence":0.9}'
        )
    )

    query_profile, plan = planner.analyze_and_plan(
        question="What does page 20 say about exclusions?",
        metadata_filters={"page_labels": ["Page 20"], "section_terms": [], "clause_terms": []},
        synopses=[_synopsis("rules", "Exclusions", "rules")],
    )

    assert query_profile.query_type == "summary_lookup"
    assert plan.constraint_mode == "hard_region"
    assert plan.preferred_route == "chunk_first"
    assert plan.target_region_kinds == []
    assert plan.hard_filters["page_labels"] == ["Page 20"]


def test_llm_planner_falls_back_to_deterministic_when_payload_is_invalid():
    planner = RetrievalPlanner(
        Settings(
            openai_api_key="test-key",
            planner_llm_enabled=True,
            router_llm_enabled=False,
        )
    )
    planner.client = _FakeClient('{"intent_type":"wild","preferred_route":"teleport"}')

    query_profile, plan = planner.analyze_and_plan(
        question="Summarize the paper's main conclusion and future work.",
        metadata_filters={"page_labels": [], "section_terms": [], "clause_terms": []},
        synopses=[
            _synopsis("abstract", "Abstract", "overview"),
            _synopsis("discussion", "Discussion", "discussion"),
        ],
    )

    assert query_profile.evidence_spread == "global"
    assert plan.preferred_route == "synopsis_first"
    assert plan.planner_source == "deterministic"
