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
    assert chunks[1].metadata["chunk_role_prior"] == "navigation_like"
    assert chunks[1].metadata["low_value_prior"] > 0.9


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

    assert len(chunks) == 1
    assert chunks[0].metadata["front_matter_kind"] == "certificate"
    assert chunks[0].metadata["front_matter_score"] >= 0.9
    assert chunks[0].metadata["low_value_section_flag"] is True
    assert chunks[0].metadata["low_value_prior"] >= 0.9


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
