import json

from src.config import Settings
from src.schemas import DocumentRecord, SectionRecord, SectionSynopsisRecord
from src.topology.synopsis_semantics import SynopsisSemanticsService


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


def test_synopsis_semantics_service_rewrites_low_quality_synopsis():
    document = DocumentRecord(
        document_id="doc1",
        file_name="report.pdf",
        file_type="pdf",
        source_path="report.pdf",
        fingerprint="abc",
        char_count=2000,
        page_count=10,
        metadata={"document_style": "research_paper"},
        extracted_text="",
    )
    section = SectionRecord(
        section_id="results",
        document_id="doc1",
        title="Results",
        summary="The paper compares several report-generation models and highlights robustness findings across different LLMs.",
        text=(
            "The results section compares several report-generation models and describes robustness trends across different "
            "LLMs, with special attention to alignment quality and ranking stability."
        ),
        page_labels=["Page 8"],
        section_path=["Results"],
        clause_ids=[],
        metadata={
            "section_kind": "results",
            "structure_confidence": 0.56,
            "structure_repaired": True,
            "structure_repair_reasons": [
                "Section titles look like running headers instead of semantic headings.",
                "Research-style document has weak canonical heading coverage.",
            ],
        },
    )
    synopsis = SectionSynopsisRecord(
        section_id="results",
        document_id="doc1",
        title="Results",
        synopsis="Results\nResults",
        region_kind="evidence",
        page_labels=["Page 8"],
        key_terms=["results"],
        metadata={"section_path": ["Results"], "topology_low_value": False},
    )
    payload = json.dumps(
        {
            "sections": [
                {
                    "section_id": "results",
                    "synopsis": "Compares report-generation systems and highlights robustness findings across different LLMs.",
                    "key_terms": ["robustness", "LLMs", "ranking", "report generation"],
                }
            ]
        }
    )
    service = SynopsisSemanticsService(
        Settings(
            synopsis_semantics_enabled=True,
            openai_api_key="test-key",
            synopsis_semantics_max_sections=4,
        )
    )
    service.client = _FakeClient(payload)

    updated = service.annotate_synopses(document, [section], [synopsis])

    assert updated[0].synopsis.startswith("Compares report-generation systems")
    assert updated[0].key_terms[:2] == ["robustness", "LLMs"]
    assert updated[0].metadata["semantic_synopsis_written"] is True


def test_synopsis_semantics_gate_skips_healthy_policy_documents():
    service = SynopsisSemanticsService(
        Settings(
            synopsis_semantics_enabled=True,
            synopsis_semantics_gate_mode="targeted",
            openai_api_key="test-key",
        )
    )
    document = DocumentRecord(
        document_id="doc2",
        file_name="policy.pdf",
        file_type="pdf",
        source_path="policy.pdf",
        fingerprint="def",
        char_count=500,
        page_count=20,
        metadata={"document_style": "policy_document"},
        extracted_text="",
    )
    sections = [
        SectionRecord(
            section_id="rules",
            document_id="doc2",
            title="Coverage",
            summary="Coverage terms",
            text="Coverage terms and conditions",
            page_labels=["Page 10"],
            section_path=["Coverage"],
            clause_ids=[],
            metadata={"structure_confidence": 0.9, "structure_repair_reasons": ["Deterministic structure extraction looked healthy."]},
        )
    ]

    assert service._should_run_for_document(document, sections) is False


def test_synopsis_semantics_gate_allows_noisy_repaired_research_documents():
    service = SynopsisSemanticsService(
        Settings(
            synopsis_semantics_enabled=True,
            synopsis_semantics_gate_mode="targeted",
            openai_api_key="test-key",
        )
    )
    document = DocumentRecord(
        document_id="doc3",
        file_name="paper.pdf",
        file_type="pdf",
        source_path="paper.pdf",
        fingerprint="ghi",
        char_count=500,
        page_count=15,
        metadata={"document_style": "research_paper"},
        extracted_text="",
    )
    sections = [
        SectionRecord(
            section_id="results",
            document_id="doc3",
            title="Results",
            summary="Results terms",
            text="Results terms and findings",
            page_labels=["Page 8"],
            section_path=["Results"],
            clause_ids=[],
            metadata={
                "structure_confidence": 0.56,
                "structure_repaired": True,
                "structure_repair_reasons": [
                    "Section titles look like running headers instead of semantic headings.",
                    "Research-style document has weak canonical heading coverage.",
                ],
            },
        )
    ]

    assert service._should_run_for_document(document, sections) is True
