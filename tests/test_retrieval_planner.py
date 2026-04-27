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


class _FakeSequenceCompletions:
    def __init__(self, contents: list[str]):
        self._contents = list(contents)

    def create(self, **kwargs):
        if not self._contents:
            return _FakeResponse("{}")
        return _FakeResponse(self._contents.pop(0))


class _FakeClient:
    def __init__(self, content: str):
        self.chat = type("Chat", (), {"completions": _FakeCompletions(content)})()


class _FakeSequenceClient:
    def __init__(self, contents: list[str]):
        self.chat = type("Chat", (), {"completions": _FakeSequenceCompletions(contents)})()


def _synopsis(section_id: str, title: str, region_kind: str, metadata: dict | None = None) -> SectionSynopsisRecord:
    return SectionSynopsisRecord(
        section_id=section_id,
        document_id="doc1",
        title=title,
        synopsis=f"{title} synopsis",
        region_kind=region_kind,
        page_labels=["Page 1"],
        key_terms=[title.lower()],
        metadata=metadata or {"section_path": [title], "source_file": "doc.pdf"},
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


def test_orchestrator_enforces_valid_hard_scope_ids():
    planner = RetrievalPlanner(
        Settings(
            openai_api_key="test-key",
            planner_llm_enabled=True,
            router_llm_enabled=False,
            retrieval_orchestrator_enabled=True,
        )
    )
    planner.client = _FakeClient(
        (
            '{"intent_type":"summary","query_type":"summary_lookup","evidence_spread":"sectional",'
            '"preferred_route":"synopsis_first","scope_strictness":"hard",'
            '"resolved_scope_ids":["chapter-4","made-up"],"scope_query":"implementation chapter",'
            '"answer_focus":["summary","conclusions"],"use_global_fallback":true,'
            '"confidence":0.84,"reason":"Question explicitly asks for the implementation chapter."}'
        )
    )

    query_profile, plan = planner.analyze_and_plan(
        question="What were the summary/conclusions from the implementation chapter?",
        metadata_filters={"page_labels": [], "section_terms": [], "clause_terms": []},
        synopses=[
            _synopsis(
                "chapter-4",
                "Implementation",
                "procedure",
                {
                    "section_path": ["CHAPTER 4", "IMPLEMENTATION"],
                    "document_scope_labels": ["Chapter 4 Implementation", "Implementation chapter"],
                    "chapter_number": 4,
                    "chapter_title": "Implementation",
                    "source_file": "doc.pdf",
                },
            ),
            _synopsis("chapter-6", "Conclusion", "discussion"),
        ],
    )

    assert query_profile.query_type == "summary_lookup"
    assert plan.planner_source == "llm_orchestrator"
    assert plan.constraint_mode == "hard_region"
    assert plan.preferred_route == "chunk_first"
    assert plan.target_region_ids == ["chapter-4"]
    assert plan.allowed_section_ids == ["chapter-4"]
    assert plan.scope_strictness == "hard"
    assert plan.answer_focus == ["summary", "conclusions", "implementation"]
    assert plan.use_global_fallback is False


def test_orchestrator_accepts_hard_local_scope_alias():
    planner = RetrievalPlanner(
        Settings(
            openai_api_key="test-key",
            planner_llm_enabled=True,
            router_llm_enabled=False,
            retrieval_orchestrator_enabled=True,
        )
    )
    planner.client = _FakeClient(
        (
            '{"intent_type":"summary","query_type":"summary_lookup","evidence_spread":"sectional",'
            '"preferred_route":"synopsis_first","scope_strictness":"hard_local",'
            '"resolved_scope_ids":["chapter-4"],"scope_query":"implementation chapter",'
            '"confidence":0.84}'
        )
    )

    _, plan = planner.analyze_and_plan(
        question="What were the summary/conclusions from the implementation chapter?",
        metadata_filters={"page_labels": [], "section_terms": [], "clause_terms": []},
        synopses=[
            _synopsis(
                "chapter-4",
                "Implementation",
                "procedure",
                {"chapter_number": "4", "chapter_title": "Implementation"},
            )
        ],
    )

    assert plan.planner_source == "llm_orchestrator"
    assert plan.scope_strictness == "hard"
    assert plan.constraint_mode == "hard_region"


def test_orchestrator_ignores_invented_scope_ids():
    planner = RetrievalPlanner(
        Settings(
            openai_api_key="test-key",
            planner_llm_enabled=True,
            router_llm_enabled=False,
            retrieval_orchestrator_enabled=True,
        )
    )
    planner.client = _FakeSequenceClient(
        [
            (
                '{"intent_type":"summary","query_type":"summary_lookup","evidence_spread":"sectional",'
                '"preferred_route":"chunk_first","scope_strictness":"hard",'
                '"resolved_scope_ids":["invented"],"confidence":0.91}'
            ),
            "{}",
        ]
    )

    query_profile, plan = planner.analyze_and_plan(
        question="What were the summary/conclusions from the implementation chapter?",
        metadata_filters={"page_labels": [], "section_terms": [], "clause_terms": []},
        synopses=[_synopsis("chapter-4", "Implementation", "procedure")],
    )

    assert query_profile.evidence_spread == "sectional"
    assert plan.planner_source == "deterministic"
    assert plan.constraint_mode == "soft_local"
    assert plan.allowed_section_ids == []


def test_orchestrator_rejects_scope_none_even_with_valid_ids():
    planner = RetrievalPlanner(
        Settings(
            openai_api_key="test-key",
            planner_llm_enabled=True,
            router_llm_enabled=False,
            retrieval_orchestrator_enabled=True,
        )
    )
    planner.client = _FakeSequenceClient(
        [
            (
                '{"intent_type":"summary","query_type":"summary_lookup","evidence_spread":"sectional",'
                '"preferred_route":"chunk_first","scope_strictness":"none",'
                '"resolved_scope_ids":["chapter-4"],"confidence":0.91}'
            ),
            "{}",
        ]
    )

    _, plan = planner.analyze_and_plan(
        question="What were the summary/conclusions from the implementation chapter?",
        metadata_filters={"page_labels": [], "section_terms": [], "clause_terms": []},
        synopses=[_synopsis("chapter-4", "Implementation", "procedure")],
    )

    assert plan.planner_source == "deterministic"


def test_orchestrator_retries_rejected_scope_payload_once():
    planner = RetrievalPlanner(
        Settings(
            openai_api_key="test-key",
            planner_llm_enabled=True,
            router_llm_enabled=False,
            retrieval_orchestrator_enabled=True,
        )
    )
    planner.client = _FakeSequenceClient(
        [
            (
                '{"intent_type":"summary","query_type":"summary_lookup","evidence_spread":"sectional",'
                '"preferred_route":"chunk_first","scope_strictness":"none",'
                '"resolved_scope_ids":["chapter-4"],"confidence":0.91}'
            ),
            (
                '{"intent_type":"summary","query_type":"summary_lookup","evidence_spread":"sectional",'
                '"preferred_route":"chunk_first","scope_strictness":"hard",'
                '"resolved_scope_ids":["chapter-4"],"scope_query":"implementation chapter",'
                '"confidence":0.91}'
            ),
        ]
    )

    _, plan = planner.analyze_and_plan(
        question="What were the summary/conclusions from the implementation chapter?",
        metadata_filters={"page_labels": [], "section_terms": [], "clause_terms": []},
        synopses=[_synopsis("chapter-4", "Implementation", "procedure", {"chapter_number": "4", "chapter_title": "Implementation"})],
    )

    assert plan.planner_source == "llm_orchestrator"
    assert plan.constraint_mode == "hard_region"
    assert plan.target_region_ids == ["chapter-4"]


def test_orchestrator_hard_scope_keeps_only_matching_chapter_group():
    planner = RetrievalPlanner(
        Settings(
            openai_api_key="test-key",
            planner_llm_enabled=True,
            router_llm_enabled=False,
            retrieval_orchestrator_enabled=True,
        )
    )
    planner.client = _FakeClient(
        (
            '{"intent_type":"summary","query_type":"summary_lookup","evidence_spread":"sectional",'
            '"preferred_route":"chunk_first","scope_strictness":"hard",'
            '"resolved_scope_ids":["chapter-2","chapter-5","chapter-5-detail"],'
            '"scope_query":"literature review chapter","confidence":0.91}'
        )
    )

    _, plan = planner.analyze_and_plan(
        question="In the literature review, what was the section summary?",
        metadata_filters={"page_labels": [], "section_terms": [], "clause_terms": []},
        synopses=[
            _synopsis("chapter-2", "Literature Review", "overview", {"chapter_number": "2", "chapter_title": "Literature Review"}),
            _synopsis("chapter-5", "Results", "discussion", {"chapter_number": "5", "chapter_title": "Results And Discussion"}),
            _synopsis("chapter-5-detail", "Results Detail", "evidence", {"chapter_number": "5", "chapter_title": "Results And Discussion"}),
        ],
    )

    assert plan.planner_source == "llm_orchestrator"
    assert plan.constraint_mode == "hard_region"
    assert plan.target_region_ids == ["chapter-2"]
    assert plan.allowed_section_ids == ["chapter-2"]


def test_orchestrator_rejects_hard_scope_for_broad_document_question():
    planner = RetrievalPlanner(
        Settings(
            openai_api_key="test-key",
            planner_llm_enabled=True,
            router_llm_enabled=False,
            retrieval_orchestrator_enabled=True,
        )
    )
    planner.client = _FakeSequenceClient(
        [
            (
                '{"intent_type":"cross_cutting","query_type":"cross_cutting_lookup","evidence_spread":"distributed",'
                '"preferred_route":"chunk_first","scope_strictness":"hard",'
                '"resolved_scope_ids":["results","limitations"],"scope_query":"findings across thesis",'
                '"answer_focus":["findings","results"],"confidence":0.91}'
            ),
            "{}",
        ]
    )

    _, plan = planner.analyze_and_plan(
        question="What are the findings of this thesis?",
        metadata_filters={"page_labels": [], "section_terms": [], "clause_terms": []},
        synopses=[
            _synopsis("results", "Results", "evidence", {"chapter_number": "5", "chapter_title": "Results"}),
            _synopsis("limitations", "Limitations", "discussion", {"chapter_number": "5", "chapter_title": "Results"}),
        ],
    )

    assert plan.planner_source == "deterministic"
    assert plan.constraint_mode != "hard_region"


def test_likely_scope_items_prioritizes_profile_labels():
    planner = RetrievalPlanner(Settings(router_llm_enabled=False))
    items = planner._likely_scope_items(
        "What were the summary/conclusions from the implementation chapter?",
        [
            _synopsis(
                "chapter-4",
                "Implementation",
                "procedure",
                {
                    "section_path": ["CHAPTER 4", "IMPLEMENTATION"],
                    "document_scope_labels": ["Chapter 4 Implementation", "Implementation chapter"],
                    "chapter_number": "4",
                    "chapter_title": "Implementation",
                },
            ),
            _synopsis(
                "chapter-6",
                "Conclusion",
                "discussion",
                {
                    "section_path": ["CHAPTER 6", "CONCLUSION"],
                    "document_scope_labels": ["Chapter 6 Conclusion", "Conclusion chapter"],
                    "chapter_number": "6",
                    "chapter_title": "Conclusion",
                },
            ),
        ],
    )

    assert items[0]["section_id"] == "chapter-4"


def test_orchestrator_ignores_low_value_scope_sections():
    planner = RetrievalPlanner(
        Settings(
            openai_api_key="test-key",
            planner_llm_enabled=True,
            router_llm_enabled=False,
            retrieval_orchestrator_enabled=True,
        )
    )
    planner.client = _FakeClient(
        (
            '{"intent_type":"summary","query_type":"summary_lookup","evidence_spread":"sectional",'
            '"preferred_route":"chunk_first","scope_strictness":"hard",'
            '"resolved_scope_ids":["toc-implementation","chapter-4"],'
            '"scope_query":"implementation chapter","answer_focus":["summary","conclusions"],'
            '"confidence":0.91}'
        )
    )

    _, plan = planner.analyze_and_plan(
        question="What were the summary/conclusions from the implementation chapter?",
        metadata_filters={"page_labels": [], "section_terms": [], "clause_terms": []},
        synopses=[
            _synopsis(
                "toc-implementation",
                "Implementation .............................................................................................................. 37",
                "overview",
                {
                    "chapter_number": "4",
                    "chapter_title": "Implementation .............................................................................................................. 37",
                    "section_path": ["Table of Contents", "Implementation"],
                    "front_matter_kind": "contents",
                    "front_matter_score": 0.98,
                    "topology_low_value": True,
                    "document_scope_labels": ["Implementation chapter"],
                },
            ),
            _synopsis(
                "chapter-4",
                "Implementation",
                "procedure",
                {
                    "chapter_number": "4",
                    "chapter_title": "Implementation",
                    "section_path": ["Chapter 4", "Implementation"],
                    "document_scope_labels": ["Implementation chapter"],
                },
            ),
        ],
    )

    assert plan.planner_source == "llm_orchestrator"
    assert plan.target_region_ids == ["chapter-4"]
    assert plan.allowed_section_ids == ["chapter-4"]


def test_summary_focused_scope_prefers_chapter_overview_section():
    planner = RetrievalPlanner(Settings(router_llm_enabled=False))
    synopses = [
        _synopsis(
            "summary",
            "Summary",
            "overview",
            {"chapter_number": "4", "chapter_title": "Implementation ........ 37", "section_path": ["Summary"]},
        ),
        _synopsis(
            "chapter-4-detail",
            "Multimodal Fusion Implementation",
            "procedure",
            {"chapter_number": "4", "chapter_title": "Implementation", "section_path": ["Chapter 4", "Implementation"]},
        ),
        _synopsis(
            "summary|chapter-4",
            "Introduction",
            "overview",
            {"chapter_number": "4", "chapter_title": "Implementation", "section_path": ["Summary", "Chapter 4"]},
        ),
    ]

    focused = planner._summary_focused_scope_ids(
        scope_ids=["summary"],
        synopses=synopses,
        answer_focus=["summary", "conclusions"],
    )

    assert focused[0] == "summary|chapter-4"


def test_answer_focus_falls_back_to_question_terms():
    assert RetrievalPlanner._answer_focus_from_question(
        "What were the summary/conclusions from the implementation chapter?"
    ) == ["summary", "conclusions", "implementation"]
