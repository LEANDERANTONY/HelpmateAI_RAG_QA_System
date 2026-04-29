from src.chunking.service import chunk_document
from src.schemas import DocumentRecord


def test_chunking_is_deterministic_and_overlap_aware():
    document = DocumentRecord(
        document_id="doc1",
        file_name="sample.pdf",
        file_type="pdf",
        source_path="sample.pdf",
        fingerprint="abc",
        char_count=2400,
        page_count=1,
        metadata={
            "pages": [
                {
                    "page_label": "Page 1",
                    "text": "3.1 Overview\n" + ("alpha " * 250),
                    "section_heading": "Overview",
                    "section_path": ["Overview"],
                    "clause_ids": ["3.1"],
                    "content_type": "benefit",
                }
            ]
        },
        extracted_text="alpha " * 250,
    )

    chunks = chunk_document(document, chunk_size=1000, chunk_overlap=100)
    assert len(chunks) == 2
    assert chunks[0].chunk_index == 0
    assert chunks[1].chunk_index == 1
    assert chunks[0].page_label == "Page 1"
    assert chunks[0].metadata["section_path"] == ["Overview"]
    assert chunks[0].metadata["primary_clause_id"] == "3.1"
    assert chunks[0].metadata["content_type"] == "benefit"
    assert len(chunks[1].text) >= 500


def test_chunking_marks_heading_stubs_and_navigation_noise():
    document = DocumentRecord(
        document_id="doc2",
        file_name="sample.pdf",
        file_type="pdf",
        source_path="sample.pdf",
        fingerprint="def",
        char_count=200,
        page_count=2,
        metadata={
            "pages": [
                {
                    "page_label": "Page 1",
                    "text": "6.2 EXPERIMENTAL RESULTS",
                    "section_heading": "Experimental Results",
                    "section_path": ["Experimental Results"],
                    "section_id": "results",
                    "clause_ids": [],
                    "content_type": "results",
                },
                {
                    "page_label": "Page 2",
                    "text": "TABLE OF CONTENTS\nChapter 1 ............ 12\nChapter 2 ............ 18",
                    "section_heading": "Contents",
                    "section_path": ["Contents"],
                    "section_id": "contents",
                    "clause_ids": [],
                    "content_type": "general",
                },
            ]
        },
        extracted_text="",
    )

    chunks = chunk_document(document, chunk_size=1000, chunk_overlap=100)

    assert chunks[0].metadata["chunk_role_prior"] == "heading_stub"
    assert chunks[0].metadata["heading_only_flag"] is True
    assert chunks[0].metadata["body_evidence_score"] < 0.2
    navigation_chunks = [chunk for chunk in chunks if chunk.page_label == "Page 2" and not chunk.metadata.get("artifact_entry")]
    assert navigation_chunks[0].metadata["chunk_role_prior"] == "navigation_like"
    assert navigation_chunks[0].metadata["low_value_prior"] > 0.9


def test_chunking_propagates_front_matter_metadata():
    document = DocumentRecord(
        document_id="doc3",
        file_name="project.pdf",
        file_type="pdf",
        source_path="project.pdf",
        fingerprint="ghi",
        char_count=240,
        page_count=1,
        metadata={
            "pages": [
                {
                    "page_label": "Page 1",
                    "text": (
                        "CERTIFICATE\n"
                        "This is to certify that the project report titled Heat Transfer Analysis "
                        "was submitted in partial fulfillment of the degree requirements."
                    ),
                    "section_heading": "Certificate",
                    "section_path": ["Certificate"],
                    "section_id": "certificate",
                    "clause_ids": [],
                    "content_type": "general",
                },
            ]
        },
        extracted_text="",
    )

    chunks = chunk_document(document, chunk_size=1000, chunk_overlap=100)

    prose_chunks = [chunk for chunk in chunks if not chunk.metadata.get("artifact_entry")]
    front_matter_artifacts = [chunk for chunk in chunks if chunk.metadata.get("artifact_type") == "front_matter"]
    assert len(prose_chunks) == 1
    assert len(front_matter_artifacts) == 1
    assert prose_chunks[0].metadata["front_matter_kind"] == "certificate"
    assert prose_chunks[0].metadata["front_matter_score"] >= 0.9
    assert prose_chunks[0].metadata["low_value_section_flag"] is True
    assert prose_chunks[0].metadata["low_value_prior"] >= 0.9


def test_chunking_assigns_continuation_to_heading_stub_chunks():
    document = DocumentRecord(
        document_id="doc4",
        file_name="report.pdf",
        file_type="pdf",
        source_path="report.pdf",
        fingerprint="jkl",
        char_count=500,
        page_count=1,
        metadata={
            "pages": [
                {
                    "page_label": "Page 30",
                    "text": (
                        "6.2 EXPERIMENTAL RESULTS\n"
                        "Temperature rise coefficient decreases with Reynolds number and the perforated "
                        "configuration outperforms the base configuration across the measured trials."
                    ),
                    "section_heading": "Experimental Results",
                    "section_path": ["Experimental Results"],
                    "section_id": "results",
                    "clause_ids": [],
                    "content_type": "results",
                },
            ]
        },
        extracted_text="",
    )

    chunks = chunk_document(document, chunk_size=28, chunk_overlap=0)

    assert len(chunks) >= 2
    assert chunks[0].metadata["heading_only_flag"] is True
    assert chunks[0].metadata["continuation_chunk_id"] == chunks[1].chunk_id


def test_chunking_creates_complete_table_artifact_and_page_metadata():
    table_text = (
        "Table 4.1 Operating parameters\n"
        "Facility        Rate       Limit\n"
        "Overnight repo  3.75%      n/a\n"
        "Reverse repo    3.50%      $160 billion per day\n"
        "The directive applies from January 29."
    )
    document = DocumentRecord(
        document_id="doc5",
        file_name="minutes.pdf",
        file_type="pdf",
        source_path="minutes.pdf",
        fingerprint="mno",
        char_count=len(table_text),
        page_count=1,
        metadata={
            "pages": [
                {
                    "page_label": "Page 14",
                    "text": table_text,
                    "section_heading": "Domestic policy directive",
                    "section_path": ["Policy Actions", "Domestic policy directive"],
                    "section_id": "policy-directive",
                    "clause_ids": [],
                    "content_type": "general",
                },
            ]
        },
        extracted_text=table_text,
    )

    chunks = chunk_document(document, chunk_size=80, chunk_overlap=0)
    table_artifacts = [chunk for chunk in chunks if chunk.metadata.get("artifact_type") == "table"]

    assert len(table_artifacts) == 1
    assert table_artifacts[0].metadata["artifact_entry"] is True
    assert table_artifacts[0].metadata["table_complete"] is True
    assert table_artifacts[0].metadata["retrieval_visibility"] == "targeted_or_numeric"
    assert "Reverse repo    3.50%      $160 billion per day" in table_artifacts[0].text
    assert document.metadata["pages"][0]["artifact_counts"]["table"] == 1


def test_chunking_creates_footnote_and_bibliography_artifacts():
    text = (
        "Main text discusses the funding source.\n"
        "1 Supported by the Learning the Earth with Artificial Intelligence and Physics center.\n"
    )
    document = DocumentRecord(
        document_id="doc6",
        file_name="paper.pdf",
        file_type="pdf",
        source_path="paper.pdf",
        fingerprint="pqr",
        char_count=len(text),
        page_count=1,
        metadata={
            "pages": [
                {
                    "page_label": "Page 1",
                    "text": text,
                    "section_heading": "Title page",
                    "section_path": ["Title page"],
                    "section_id": "title",
                    "clause_ids": [],
                    "content_type": "general",
                },
                {
                    "page_label": "Page 20",
                    "text": "References\nSmith J. Climate models. 2024.",
                    "section_heading": "References",
                    "section_path": ["References"],
                    "section_id": "references",
                    "clause_ids": [],
                    "content_type": "general",
                    "section_kind": "references",
                },
            ]
        },
        extracted_text=text,
    )

    chunks = chunk_document(document, chunk_size=1000, chunk_overlap=0)
    artifact_types = {chunk.metadata.get("artifact_type") for chunk in chunks if chunk.metadata.get("artifact_entry")}

    assert "footnote" in artifact_types
    assert "bibliography" in artifact_types
