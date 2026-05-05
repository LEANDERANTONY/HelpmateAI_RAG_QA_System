from src.query_analysis import QueryAnalyzer
from src.retrieval.hybrid import HybridRetriever
from src.schemas import ChunkRecord


def test_numeric_queries_prefer_table_artifacts():
    profile = QueryAnalyzer.analyze("What rate and limit are listed in the table?")

    assert "table" in profile.preferred_content_types
    assert profile.query_type == "numeric_lookup"


def test_front_matter_queries_prefer_front_matter_artifacts():
    profile = QueryAnalyzer.analyze("Who supervised the dissertation on the title page?")

    assert "front_matter" in profile.preferred_content_types


def test_table_artifact_gets_targeted_score_boost():
    chunk = ChunkRecord(
        chunk_id="table-1",
        document_id="doc",
        text="Table 1\nFacility Rate\nRepo 3.75%",
        chunk_index=0,
        page_label="Page 1",
        metadata={
            "content_type": "table",
            "artifact_entry": True,
            "artifact_type": "table",
            "body_evidence_score": 0.72,
            "page_label": "Page 1",
            "source_file": "doc.pdf",
        },
    )

    retriever = object.__new__(HybridRetriever)
    targeted = retriever._score_chunk(
        "What rate is listed in the table?",
        chunk,
        {},
        {},
        0.1,
        ["table", "results", "general"],
        [],
        query_type="numeric_lookup",
    )
    untargeted = retriever._score_chunk(
        "Summarize the document.",
        chunk,
        {},
        {},
        0.1,
        ["general", "results"],
        [],
        query_type="summary_lookup",
    )

    assert targeted.fused_score > untargeted.fused_score


def test_semantic_table_evidence_beats_table_fragment():
    table_evidence = ChunkRecord(
        chunk_id="table-evidence",
        document_id="doc",
        text="Extracted table:\n| Investment | PES | 1.5C Scenario |\n| --- | --- | --- |\n| Cumulative investment | USD 103 trillion | USD 150 trillion |",
        chunk_index=0,
        page_label="Page 1",
        metadata={
            "content_type": "table",
            "artifact_entry": True,
            "artifact_type": "table",
            "body_evidence_score": 0.72,
            "semantic_chunk_role": "table_evidence",
            "semantic_chunk_confidence": 0.9,
            "semantic_body_evidence_score": 0.9,
            "page_label": "Page 1",
            "source_file": "doc.pdf",
        },
    )
    table_fragment = ChunkRecord(
        chunk_id="table-fragment",
        document_id="doc",
        text="TABLE 3.1 Required average annual investments under the Planned Energy Scenario and 1.5C Scenario",
        chunk_index=1,
        page_label="Page 2",
        metadata={
            "content_type": "table",
            "artifact_entry": True,
            "artifact_type": "table",
            "body_evidence_score": 0.72,
            "semantic_chunk_role": "table_fragment",
            "semantic_chunk_confidence": 0.9,
            "semantic_body_evidence_score": 0.2,
            "page_label": "Page 2",
            "source_file": "doc.pdf",
        },
    )

    retriever = object.__new__(HybridRetriever)
    question = "How much cumulative investment does the 1.5C Scenario require by 2050 compared with the Planned Energy Scenario?"
    complete = retriever._score_chunk(question, table_evidence, {}, {}, 0.1, ["table", "general"], [], query_type="numeric_lookup")
    incomplete = retriever._score_chunk(question, table_fragment, {}, {}, 0.1, ["table", "general"], [], query_type="numeric_lookup")

    assert complete.fused_score > incomplete.fused_score


def test_semantic_definition_evidence_gets_definition_query_boost():
    definition_chunk = ChunkRecord(
        chunk_id="definition-1",
        document_id="doc",
        text="Definition: PINO = Physics-Informed Neural Operator\nSource text: Physics-Informed Neural Operator (PINO) adds a PDE residual term.",
        chunk_index=0,
        page_label="Page 2",
        metadata={
            "content_type": "definition",
            "artifact_entry": True,
            "artifact_type": "definition",
            "body_evidence_score": 0.68,
            "semantic_chunk_role": "definition_evidence",
            "semantic_chunk_confidence": 0.9,
            "semantic_body_evidence_score": 0.9,
            "page_label": "Page 2",
            "source_file": "doc.pdf",
        },
    )
    body_chunk = ChunkRecord(
        chunk_id="body-1",
        document_id="doc",
        text="Neural operators are used as climate surrogate models in several case studies.",
        chunk_index=1,
        page_label="Page 3",
        metadata={
            "content_type": "general",
            "chunk_role_prior": "body",
            "body_evidence_score": 0.88,
            "page_label": "Page 3",
            "source_file": "doc.pdf",
        },
    )

    retriever = object.__new__(HybridRetriever)
    question = "What does PINO stand for?"
    definition = retriever._score_chunk(question, definition_chunk, {}, {}, 0.1, ["definition", "general"], [], query_type="definition_lookup")
    body = retriever._score_chunk(question, body_chunk, {}, {}, 0.1, ["definition", "general"], [], query_type="definition_lookup")

    assert definition.fused_score > body.fused_score
