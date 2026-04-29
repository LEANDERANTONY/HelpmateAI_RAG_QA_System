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
