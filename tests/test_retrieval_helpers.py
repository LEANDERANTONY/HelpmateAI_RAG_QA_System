from src.config import Settings
from src.retrieval.hybrid import HybridRetriever
from src.schemas import ChunkRecord
from src.schemas import RetrievalCandidate
from src.schemas import RetrievalPlan
from src.schemas import RetrievalResult


def test_extract_metadata_filters_detects_page_reference():
    retriever = HybridRetriever(store=None, settings=Settings())  # type: ignore[arg-type]
    filters = retriever._extract_metadata_filters('What does page 20 say about the "free look" period?')

    assert filters["page_labels"] == ["Page 20"]
    assert filters["section_terms"] == ["free look"]


def test_extract_metadata_filters_detects_clause_reference():
    retriever = HybridRetriever(store=None, settings=Settings())  # type: ignore[arg-type]
    filters = retriever._extract_metadata_filters("What does clause 4.1 say about waiting periods?")

    assert filters["clause_terms"] == ["4.1"]


def test_extract_metadata_filters_extracts_named_section_from_in_the_phrase():
    retriever = HybridRetriever(store=None, settings=Settings())  # type: ignore[arg-type]
    filters = retriever._extract_metadata_filters(
        "ok tell me in the literature review what was the section summary?"
    )

    assert filters["section_terms"] == ["literature review"]


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


def test_global_summary_section_selection_prefers_overview_and_findings_sections():
    retriever = HybridRetriever(store=None, settings=Settings())  # type: ignore[arg-type]
    ranked_synopses = [
        RetrievalCandidate(chunk_id="s1", text="Overview synopsis", metadata={"section_id": "s1", "section_kind": "abstract"}, fused_score=0.9),
        RetrievalCandidate(chunk_id="s2", text="Results synopsis", metadata={"section_id": "s2", "section_kind": "results"}, fused_score=0.82),
        RetrievalCandidate(chunk_id="s3", text="References synopsis", metadata={"section_id": "s3", "section_kind": "references"}, fused_score=0.99),
    ]

    section_ids, notes = retriever._choose_global_summary_sections(
        "What is this paper about?",
        ranked_synopses,
        plan=type("Plan", (), {"target_region_ids": [], "target_region_kinds": []})(),
    )

    assert section_ids[:2] == ["s1", "s2"]
    assert any("Global-summary routing assembled" in note for note in notes)


def test_content_overlap_handles_simple_singular_plural_variants():
    retriever = HybridRetriever(store=None, settings=Settings())  # type: ignore[arg-type]
    candidates = [
        RetrievalCandidate(
            chunk_id="c1",
            text="Our main contributions are a unified framework and a concept-alignment method.",
            metadata={"section_heading": "Main contributions"},
            fused_score=0.1,
        )
    ]

    overlap = retriever._content_overlap_ratio("What is the main contribution of this paper?", candidates)

    assert overlap > 0.0


def test_scope_compliance_removes_candidates_outside_hard_orchestrated_scope():
    notes: list[str] = []
    candidates = [
        RetrievalCandidate(chunk_id="c1", text="Implementation evidence", metadata={"section_id": "chapter-4"}),
        RetrievalCandidate(chunk_id="c2", text="Conclusion evidence", metadata={"section_id": "chapter-6"}),
    ]
    plan = RetrievalPlan(
        intent_type="summary",
        evidence_spread="sectional",
        constraint_mode="hard_region",
        preferred_route="chunk_first",
        target_region_ids=["chapter-4"],
        allowed_section_ids=["chapter-4"],
        scope_strictness="hard",
    )

    filtered = HybridRetriever._scope_compliant_candidates(candidates, plan, notes)

    assert [candidate.chunk_id for candidate in filtered] == ["c1"]
    assert any("outside the orchestrated hard scope" in note for note in notes)


def test_chunk_candidates_promote_continuation_of_heading_stub():
    class FakeStore:
        @staticmethod
        def load_chunks(_fingerprint: str):
            return [
                ChunkRecord(
                    chunk_id="c1",
                    document_id="doc1",
                    text="6.2 EXPERIMENTAL RESULTS",
                    chunk_index=0,
                    page_label="Page 30",
                    metadata={
                        "page_label": "Page 30",
                        "section_id": "results",
                        "section_heading": "Experimental Results",
                        "section_kind": "results",
                        "content_type": "results",
                        "chunk_role_prior": "heading_stub",
                        "heading_only_flag": True,
                        "body_evidence_score": 0.12,
                        "low_value_prior": 0.88,
                        "continuation_chunk_id": "c2",
                    },
                ),
                ChunkRecord(
                    chunk_id="c2",
                    document_id="doc1",
                    text="Temperature rise coefficient decreases with Reynolds number and perforated configurations outperform the base configuration.",
                    chunk_index=1,
                    page_label="Page 30",
                    metadata={
                        "page_label": "Page 30",
                        "section_id": "results",
                        "section_heading": "Experimental Results",
                        "section_kind": "results",
                        "content_type": "results",
                        "chunk_role_prior": "body",
                        "heading_only_flag": False,
                        "body_evidence_score": 0.92,
                        "low_value_prior": 0.08,
                    },
                ),
                ChunkRecord(
                    chunk_id="c3",
                    document_id="doc1",
                    text="TABLE OF CONTENTS\n6.2 EXPERIMENTAL RESULTS ............ 22",
                    chunk_index=2,
                    page_label="Page 5",
                    metadata={
                        "page_label": "Page 5",
                        "section_id": "contents",
                        "section_heading": "Contents",
                        "section_kind": "general",
                        "content_type": "general",
                        "chunk_role_prior": "navigation_like",
                        "heading_only_flag": False,
                        "body_evidence_score": 0.02,
                        "low_value_prior": 0.98,
                    },
                ),
            ]

        @staticmethod
        def dense_query(_fingerprint: str, _question: str, top_k: int):
            items = [
                {"chunk_id": "c1", "text": "6.2 EXPERIMENTAL RESULTS", "metadata": {}, "distance": 0.1},
                {"chunk_id": "c3", "text": "TABLE OF CONTENTS", "metadata": {}, "distance": 0.12},
            ]
            return items[:top_k]

    retriever = HybridRetriever(store=FakeStore(), settings=Settings(reranker_enabled=False))  # type: ignore[arg-type]

    candidates = retriever._chunk_candidates(
        "fingerprint",
        "What results or performance trends are reported in the experimental analysis?",
        metadata_filters={"page_labels": [], "section_terms": [], "clause_terms": []},
        preferred_content_types=["results"],
        clause_terms=[],
        query_type="summary_lookup",
    )

    by_id = {candidate.chunk_id: candidate for candidate in candidates}

    assert "c2" in by_id
    assert by_id["c2"].fused_score > by_id["c3"].fused_score


def test_finalize_candidates_prefers_body_continuation_over_heading_stub():
    retriever = HybridRetriever(store=None, settings=Settings(reranker_enabled=False, final_top_k=2))  # type: ignore[arg-type]
    heading = RetrievalCandidate(
        chunk_id="heading",
        text="LITERATURE REVIEW",
        metadata={
            "heading_only_flag": True,
            "chunk_role_prior": "heading_stub",
            "continuation_chunk_id": "body",
        },
        fused_score=0.9,
    )
    body = RetrievalCandidate(
        chunk_id="body",
        text="The literature review surveys pancreatic cancer diagnosis, medical imaging AI, biomarkers, multimodal fusion, and dataset bias.",
        metadata={"chunk_role_prior": "body", "heading_only_flag": False},
        fused_score=0.4,
    )
    other = RetrievalCandidate(
        chunk_id="other",
        text="The final chapter summarizes key findings and future work.",
        metadata={"chunk_role_prior": "body", "heading_only_flag": False},
        fused_score=0.7,
    )

    final = retriever._finalize_candidates("What does the literature review cover?", [heading, other, body])

    assert [candidate.chunk_id for candidate in final] == ["body", "other"]


def test_score_chunk_penalizes_front_matter_for_results_queries():
    retriever = HybridRetriever(store=None, settings=Settings())  # type: ignore[arg-type]
    body_chunk = ChunkRecord(
        chunk_id="body",
        document_id="doc1",
        text="Experimental results show the perforated configuration reduces the temperature rise coefficient as Reynolds number increases.",
        chunk_index=0,
        page_label="Page 30",
        metadata={
            "page_label": "Page 30",
            "section_id": "results",
            "section_heading": "Experimental Results",
            "section_kind": "results",
            "content_type": "results",
            "chunk_role_prior": "body",
            "body_evidence_score": 0.92,
            "low_value_prior": 0.08,
            "front_matter_kind": "body",
            "front_matter_score": 0.0,
        },
    )
    title_page_chunk = ChunkRecord(
        chunk_id="title",
        document_id="doc1",
        text="Project report submitted in partial fulfillment of the degree requirements for Heat Transfer Analysis.",
        chunk_index=1,
        page_label="Page 1",
        metadata={
            "page_label": "Page 1",
            "section_id": "title",
            "section_heading": "Project Report",
            "section_kind": "general",
            "content_type": "general",
            "chunk_role_prior": "body",
            "body_evidence_score": 0.88,
            "low_value_prior": 0.12,
            "front_matter_kind": "title_page",
            "front_matter_score": 0.68,
        },
    )

    body_candidate = retriever._score_chunk(
        "What results or performance trends are reported in the experimental analysis?",
        body_chunk,
        {},
        {},
        0.1,
        ["results"],
        [],
        query_type="summary_lookup",
    )
    title_candidate = retriever._score_chunk(
        "What results or performance trends are reported in the experimental analysis?",
        title_page_chunk,
        {},
        {},
        0.1,
        ["results"],
        [],
        query_type="summary_lookup",
    )

    assert body_candidate.fused_score > title_candidate.fused_score


def test_score_chunk_keeps_title_page_more_viable_for_overview_queries():
    retriever = HybridRetriever(store=None, settings=Settings())  # type: ignore[arg-type]
    title_page_chunk = ChunkRecord(
        chunk_id="title",
        document_id="doc1",
        text="Project report submitted in partial fulfillment of the degree requirements for Heat Transfer Analysis.",
        chunk_index=1,
        page_label="Page 1",
        metadata={
            "page_label": "Page 1",
            "section_id": "title",
            "section_heading": "Project Report",
            "section_kind": "general",
            "content_type": "general",
            "chunk_role_prior": "body",
            "body_evidence_score": 0.88,
            "low_value_prior": 0.12,
            "front_matter_kind": "title_page",
            "front_matter_score": 0.68,
        },
    )

    overview_candidate = retriever._score_chunk(
        "What is this project report about?",
        title_page_chunk,
        {},
        {},
        0.1,
        ["general"],
        [],
        query_type="summary_lookup",
    )
    results_candidate = retriever._score_chunk(
        "What results or performance trends are reported in the experimental analysis?",
        title_page_chunk,
        {},
        {},
        0.1,
        ["results"],
        [],
        query_type="summary_lookup",
    )

    assert overview_candidate.fused_score > results_candidate.fused_score


def test_score_chunk_uses_semantic_chunk_role_as_a_moderate_hint():
    retriever = HybridRetriever(store=None, settings=Settings())  # type: ignore[arg-type]
    body_chunk = ChunkRecord(
        chunk_id="body",
        document_id="doc1",
        text="Experimental results show the perforated configuration reduces temperature rise as Reynolds number increases.",
        chunk_index=0,
        page_label="Page 30",
        metadata={
            "page_label": "Page 30",
            "section_id": "results",
            "section_heading": "Experimental Results",
            "section_kind": "results",
            "content_type": "results",
            "chunk_role_prior": "body",
            "body_evidence_score": 0.7,
            "semantic_chunk_role": "body_evidence",
            "semantic_chunk_confidence": 0.9,
            "semantic_body_evidence_score": 0.95,
        },
    )
    noisy_chunk = ChunkRecord(
        chunk_id="noise",
        document_id="doc1",
        text="6.2 EXPERIMENTAL RESULTS ............ 22",
        chunk_index=1,
        page_label="Page 5",
        metadata={
            "page_label": "Page 5",
            "section_id": "contents",
            "section_heading": "Contents",
            "section_kind": "general",
            "content_type": "general",
            "chunk_role_prior": "heading_stub",
            "body_evidence_score": 0.2,
            "semantic_chunk_role": "navigation_noise",
            "semantic_chunk_confidence": 0.9,
            "semantic_body_evidence_score": 0.1,
        },
    )

    body_candidate = retriever._score_chunk(
        "What results or performance trends are reported in the experimental analysis?",
        body_chunk,
        {},
        {},
        0.1,
        ["results"],
        [],
        query_type="summary_lookup",
    )
    noisy_candidate = retriever._score_chunk(
        "What results or performance trends are reported in the experimental analysis?",
        noisy_chunk,
        {},
        {},
        0.1,
        ["results"],
        [],
        query_type="summary_lookup",
    )

    assert body_candidate.fused_score > noisy_candidate.fused_score


def test_recovery_is_limited_to_atomic_detail_queries():
    retriever = HybridRetriever(store=None, settings=Settings())  # type: ignore[arg-type]
    result = RetrievalResult(question="What are the main findings?", candidates=[], retrieval_plan={})

    assert retriever.should_recover_after_abstention("When is the next meeting scheduled?", result)
    assert not retriever.should_recover_after_abstention("What are the main findings of this report?", result)


def test_abstention_recovery_promotes_front_matter_evidence():
    class FakeStore:
        @staticmethod
        def load_chunks(_fingerprint: str):
            return [
                ChunkRecord(
                    chunk_id="body",
                    document_id="doc1",
                    text="The report discusses risk management practices for AI systems.",
                    chunk_index=0,
                    page_label="Page 12",
                    metadata={
                        "page_label": "Page 12",
                        "section_id": "body",
                        "section_heading": "Risk Management",
                        "section_kind": "body",
                        "content_type": "general",
                        "body_evidence_score": 0.8,
                    },
                ),
                ChunkRecord(
                    chunk_id="front",
                    document_id="doc1",
                    text="Foreword signed by Francesco La Camera, Director-General of IRENA.",
                    chunk_index=1,
                    page_label="Page 5",
                    metadata={
                        "page_label": "Page 5",
                        "section_id": "foreword",
                        "section_heading": "Foreword",
                        "section_kind": "front_matter",
                        "content_type": "general",
                        "artifact_entry": True,
                        "artifact_type": "front_matter",
                        "front_matter_kind": "preface",
                        "front_matter_score": 0.9,
                        "body_evidence_score": 0.75,
                    },
                ),
            ]

    retriever = HybridRetriever(store=FakeStore(), settings=Settings(reranker_enabled=False))  # type: ignore[arg-type]
    initial = RetrievalResult(
        question="Who is identified as the Director-General of IRENA in the foreword?",
        candidates=[
            RetrievalCandidate(
                chunk_id="body",
                text="The report discusses risk management practices for AI systems.",
                metadata={"page_label": "Page 12"},
                fused_score=0.02,
                lexical_score=0.01,
            )
        ],
        retrieval_plan={},
        metadata_filters={"page_labels": [], "section_terms": [], "clause_terms": []},
    )

    recovered = retriever.recover_after_abstention("fingerprint", initial.question, initial)

    assert recovered.retrieval_plan["abstention_recovery_applied"]
    assert recovered.candidates[0].chunk_id == "front"
    assert recovered.evidence_status in {"weak", "strong"}


def test_abstention_recovery_adds_next_chunk_neighbor():
    chunks = [
        ChunkRecord(
            chunk_id="seed",
            document_id="doc1",
            text="Two members dissented from the policy decision.",
            chunk_index=0,
            page_label="Page 13",
            metadata={
                "page_label": "Page 13",
                "section_id": "actions",
                "section_heading": "Committee Policy Actions",
                "section_kind": "body",
                "content_type": "general",
                "body_evidence_score": 0.8,
            },
        ),
        ChunkRecord(
            chunk_id="neighbor",
            document_id="doc1",
            text="Stephen I. Miran and Christopher J. Waller dissented because they preferred to lower the target range by one quarter percentage point.",
            chunk_index=1,
            page_label="Page 14",
            metadata={
                "page_label": "Page 14",
                "section_id": "actions",
                "section_heading": "Committee Policy Actions",
                "section_kind": "body",
                "content_type": "general",
                "body_evidence_score": 0.9,
            },
        ),
    ]
    retriever = HybridRetriever(store=None, settings=Settings(reranker_enabled=False))  # type: ignore[arg-type]
    seed = RetrievalCandidate(
        chunk_id="seed",
        text=chunks[0].text,
        metadata=chunks[0].metadata,
        fused_score=0.08,
        lexical_score=0.03,
    )

    neighbors = retriever._neighbor_recovery_candidates(
        "Which members dissented from the policy decision, and what alternative did they prefer?",
        chunks,
        [seed],
        preferred_content_types=["general"],
        clause_terms=[],
        query_type="general_lookup",
    )

    assert [candidate.chunk_id for candidate in neighbors] == ["neighbor"]
    assert neighbors[0].metadata["abstention_recovery_reason"] == "neighbor_chunk"


def test_required_fact_signal_uses_generic_answer_shapes_not_fixture_terms():
    definition_score = HybridRetriever._required_fact_signal_score(
        "How does this framework define an AI system?",
        "The framework refers to an AI system as an engineered or machine-based system that can generate predictions.",
        "definition_lookup",
    )
    date_score = HybridRetriever._required_fact_signal_score(
        "When is the next meeting scheduled?",
        "The next regular meeting is scheduled for Tuesday-Wednesday, March 17-18, 2026.",
        "general_lookup",
    )
    person_score = HybridRetriever._required_fact_signal_score(
        "Which members dissented from the policy decision?",
        "Voting against this action: Jane A. Smith and Robert Jones.",
        "general_lookup",
    )

    assert definition_score >= 0.30
    assert date_score >= 0.30
    assert person_score >= 0.18
