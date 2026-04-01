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
        metadata={"pages": [{"page_label": "Page 1", "text": "A" * 1500}]},
        extracted_text="A" * 1500,
    )

    chunks = chunk_document(document, chunk_size=1000, chunk_overlap=100)
    assert len(chunks) == 2
    assert chunks[0].chunk_index == 0
    assert chunks[1].chunk_index == 1
    assert chunks[0].page_label == "Page 1"
    assert len(chunks[1].text) >= 500
