import json

from src.chunking.chunk_semantics import ChunkSemanticsService
from src.config import Settings
from src.schemas import ChunkRecord, DocumentRecord


class _FakeMessage:
    def __init__(self, content: str):
        self.content = content


class _FakeChoice:
    def __init__(self, content: str):
        self.message = _FakeMessage(content)


class _FakeResponse:
    def __init__(self, content: str):
        self.choices = [_FakeChoice(content)]


class _FakeCompletions:
    def __init__(self, content: str):
        self._content = content

    def create(self, **_: object):
        return _FakeResponse(self._content)


class _FakeChat:
    def __init__(self, content: str):
        self.completions = _FakeCompletions(content)


class _FakeClient:
    def __init__(self, content: str):
        self.chat = _FakeChat(content)


def test_chunk_semantics_service_annotates_suspicious_chunks_only():
    document = DocumentRecord(
        document_id="doc1",
        file_name="report.pdf",
        file_type="pdf",
        source_path="report.pdf",
        fingerprint="abc",
        char_count=400,
        page_count=1,
        metadata={"document_style": "research_paper"},
        extracted_text="",
    )
    chunks = [
        ChunkRecord(
            chunk_id="c1",
            document_id="doc1",
            text="6.2 EXPERIMENTAL RESULTS",
            chunk_index=0,
            page_label="Page 30",
            metadata={
                "page_label": "Page 30",
                "section_heading": "Experimental Results",
                "section_kind": "results",
                "chunk_role_prior": "heading_stub",
                "body_evidence_score": 0.12,
                "front_matter_kind": "body",
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
                "section_heading": "Experimental Results",
                "section_kind": "results",
                "chunk_role_prior": "body",
                "body_evidence_score": 0.92,
                "front_matter_kind": "body",
            },
        ),
    ]
    payload = json.dumps(
        {
            "chunks": [
                {
                    "chunk_id": "c1",
                    "role": "heading_stub",
                    "confidence": 0.88,
                    "body_evidence_score": 0.1,
                }
            ]
        }
    )
    service = ChunkSemanticsService(
        Settings(
            chunk_semantics_enabled=True,
            openai_api_key="test-key",
            chunk_semantics_max_review_chunks=4,
        )
    )
    service.client = _FakeClient(payload)

    annotated = service.annotate_chunks(document, chunks)

    assert annotated[0].metadata["semantic_chunk_role"] == "heading_stub"
    assert annotated[0].metadata["semantic_chunk_confidence"] == 0.88
    assert annotated[0].metadata["semantic_body_evidence_score"] == 0.1
    assert "semantic_chunk_role" not in annotated[1].metadata


def test_chunk_semantics_service_classifies_artifact_candidates():
    document = DocumentRecord(
        document_id="doc2",
        file_name="paper.pdf",
        file_type="pdf",
        source_path="paper.pdf",
        fingerprint="def",
        char_count=400,
        page_count=1,
        metadata={"document_style": "research_paper"},
        extracted_text="",
    )
    chunks = [
        ChunkRecord(
            chunk_id="artifact-definition",
            document_id="doc2",
            text="Definition: PINO = Physics-Informed Neural Operator\nSource text: Physics-Informed Neural Operator (PINO) adds a PDE residual term.",
            chunk_index=0,
            page_label="Page 16",
            metadata={
                "page_label": "Page 16",
                "section_heading": "Neural operators",
                "section_kind": "body",
                "artifact_entry": True,
                "artifact_type": "definition",
                "content_type": "definition",
                "body_evidence_score": 0.68,
            },
        )
    ]
    payload = json.dumps(
        {
            "chunks": [
                {
                    "chunk_id": "artifact-definition",
                    "role": "definition_evidence",
                    "confidence": 0.91,
                    "body_evidence_score": 0.9,
                }
            ]
        }
    )
    service = ChunkSemanticsService(
        Settings(
            chunk_semantics_enabled=True,
            openai_api_key="test-key",
            chunk_semantics_max_review_chunks=4,
        )
    )
    service.client = _FakeClient(payload)

    annotated = service.annotate_chunks(document, chunks)

    assert annotated[0].metadata["semantic_chunk_role"] == "definition_evidence"
    assert annotated[0].metadata["semantic_chunk_confidence"] == 0.91
    assert annotated[0].metadata["semantic_body_evidence_score"] == 0.9
