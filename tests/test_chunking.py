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
