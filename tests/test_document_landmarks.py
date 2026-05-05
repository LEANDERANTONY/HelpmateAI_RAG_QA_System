import json

from src.config import Settings
from src.chunking.service import chunk_document
from src.landmarks import DocumentLandmarkService
from src.schemas import DocumentRecord


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


def test_document_landmarks_add_page_linked_metadata_evidence_chunks():
    document = DocumentRecord(
        document_id="doc-landmark",
        file_name="report.pdf",
        file_type=".pdf",
        source_path="report.pdf",
        fingerprint="abc",
        char_count=300,
        page_count=2,
        metadata={
            "pages": [
                {
                    "page_label": "Page 1",
                    "text": "FOREWORD\nFrancesco La Camera\nDirector-General, IRENA\nThis report tracks transition progress.",
                    "section_heading": "Foreword",
                    "section_path": ["Foreword"],
                    "section_id": "foreword",
                    "clause_ids": [],
                    "content_type": "general",
                    "section_kind": "foreword",
                },
                {
                    "page_label": "Page 2",
                    "text": "Chapter 1 introduces the scenarios.",
                    "section_heading": "Chapter 1",
                    "section_path": ["Chapter 1"],
                    "section_id": "chapter-1",
                    "clause_ids": [],
                    "content_type": "general",
                },
            ]
        },
        extracted_text="",
    )
    chunks = chunk_document(document, chunk_size=1000, chunk_overlap=0)
    payload = json.dumps(
        {
            "landmarks": [
                {
                    "landmark_type": "foreword",
                    "title": "Foreword",
                    "page_label": "Page 1",
                    "summary": "Foreword signed by the IRENA Director-General.",
                    "key_value_facts": ["Director-General: Francesco La Camera"],
                    "source_snippet": "Francesco La Camera Director-General, IRENA",
                    "confidence": 0.93,
                }
            ]
        }
    )
    service = DocumentLandmarkService(
        Settings(openai_api_key="test-key", document_landmarks_enabled=True, document_landmarks_min_confidence=0.5)
    )
    service.client = _FakeClient(payload)

    enriched = service.annotate_chunks(document, chunks)
    landmark_chunks = [chunk for chunk in enriched if chunk.metadata.get("landmark_entry")]

    assert len(landmark_chunks) == 1
    landmark = landmark_chunks[0]
    assert landmark.page_label == "Page 1"
    assert landmark.metadata["artifact_type"] == "landmark"
    assert landmark.metadata["landmark_type"] == "foreword"
    assert landmark.metadata["semantic_chunk_role"] == "metadata_evidence"
    assert "Director-General: Francesco La Camera" in landmark.text
