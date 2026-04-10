from src.config import Settings
from src.schemas import DocumentRecord, SectionRecord
from src.sections.repair import StructureRepairService


def test_structure_repair_assess_flags_low_confidence_research_doc():
    settings = Settings(openai_api_key=None)
    service = StructureRepairService(settings)
    document = DocumentRecord(
        document_id="doc1",
        file_name="journal.pdf",
        file_type="pdf",
        source_path="journal.pdf",
        fingerprint="abc",
        char_count=5000,
        page_count=20,
        metadata={"document_style": "research_paper"},
    )
    sections = [
        SectionRecord(
            section_id="s1",
            document_id="doc1",
            title="Nature Medicine | Volume 31 | February 2025 | 599-608",
            summary="Front matter summary.",
            text="Nature Medicine | Volume 31 | February 2025 | 599-608\nArticle\nTitle page text.",
            page_labels=["Page 1"],
            section_path=["Nature Medicine | Volume 31 | February 2025 | 599-608"],
            clause_ids=[],
            metadata={"section_kind": "general", "section_aliases": ["Nature Medicine"]},
        ),
        SectionRecord(
            section_id="s2",
            document_id="doc1",
            title="Results",
            summary="Results section summary.",
            text="Results text.",
            page_labels=["Page 2", "Page 3", "Page 4"],
            section_path=["Results"],
            clause_ids=[],
            metadata={"section_kind": "results", "section_aliases": ["Results"]},
        ),
        SectionRecord(
            section_id="s3",
            document_id="doc1",
            title="Nature Medicine | Volume 31 | February 2025 | 599-608",
            summary="Repeated journal header summary.",
            text="Repeated journal header content.",
            page_labels=["Page 5", "Page 6"],
            section_path=["Nature Medicine | Volume 31 | February 2025 | 599-608"],
            clause_ids=[],
            metadata={"section_kind": "general", "section_aliases": ["Nature Medicine"]},
        ),
        SectionRecord(
            section_id="s4",
            document_id="doc1",
            title="Methods",
            summary="Methods summary.",
            text="Methods text.",
            page_labels=["Page 7", "Page 8", "Page 9"],
            section_path=["Methods"],
            clause_ids=[],
            metadata={"section_kind": "methodology", "section_aliases": ["Methods"]},
        ),
    ]

    decision = service.assess(document, sections)

    assert decision.confidence < 0.62
    assert decision.should_repair is True
    assert any("too few sections" in reason.lower() for reason in decision.reasons)


def test_structure_repair_rebuilds_sections_from_page_assignments():
    settings = Settings(openai_api_key="test-key")
    service = StructureRepairService(settings)
    service.client = object()

    document = DocumentRecord(
        document_id="doc2",
        file_name="paper.pdf",
        file_type="pdf",
        source_path="paper.pdf",
        fingerprint="def",
        char_count=3000,
        page_count=6,
        metadata={
            "document_style": "research_paper",
            "pages": [
                {"page_label": "Page 1", "text": "ABSTRACT\nThis paper studies report generation.", "section_heading": "Article"},
                {"page_label": "Page 2", "text": "Introduction\nWe motivate the study.", "section_heading": "Article"},
                {"page_label": "Page 3", "text": "Methods\nWe describe the method.", "section_heading": "Article"},
                {"page_label": "Page 4", "text": "Methods\nWe describe the method details.", "section_heading": "Article"},
                {"page_label": "Page 5", "text": "Results\nThe system improves performance.", "section_heading": "Article"},
                {"page_label": "Page 6", "text": "Discussion\nWe discuss limitations.", "section_heading": "Article"},
            ],
        },
    )
    coarse_sections = [
        SectionRecord(
            section_id="coarse",
            document_id="doc2",
            title="Article",
            summary="Flattened article summary.",
            text=" ".join(str(page["text"]) for page in document.metadata["pages"]),
            page_labels=[f"Page {i}" for i in range(1, 7)],
            section_path=["Article"],
            clause_ids=[],
            metadata={"section_kind": "general", "section_aliases": ["Article"]},
        )
    ]

    def fake_assignments(_document, _pages):
        return [
            {"page_label": "Page 1", "title": "Abstract", "section_kind": "abstract"},
            {"page_label": "Page 2", "title": "Introduction", "section_kind": "introduction"},
            {"page_label": "Page 3", "title": "Methods", "section_kind": "methodology"},
            {"page_label": "Page 4", "title": "Methods", "section_kind": "methodology"},
            {"page_label": "Page 5", "title": "Results", "section_kind": "results"},
            {"page_label": "Page 6", "title": "Discussion", "section_kind": "discussion"},
        ]

    service._llm_assignments = fake_assignments  # type: ignore[method-assign]

    repaired_sections, decision = service.repair_if_needed(document, coarse_sections)

    assert decision.confidence < settings.structure_repair_confidence_threshold
    assert [section.title for section in repaired_sections] == ["Document Overview", "Abstract", "Introduction", "Methods", "Results", "Discussion"]
    assert repaired_sections[3].page_labels == ["Page 3", "Page 4"]
    assert all(section.metadata.get("structure_repaired") for section in repaired_sections)


def test_structure_repair_replaces_noisy_overview_titles_with_canonical_heading():
    settings = Settings(openai_api_key="test-key")
    service = StructureRepairService(settings)
    service.client = object()

    document = DocumentRecord(
        document_id="doc3",
        file_name="paper.pdf",
        file_type="pdf",
        source_path="paper.pdf",
        fingerprint="ghi",
        char_count=1200,
        page_count=2,
        metadata={
            "document_style": "research_paper",
            "pages": [
                {
                    "page_label": "Page 1",
                    "text": "ABSTRACT\nThis paper studies clinician-AI collaboration in report generation.",
                    "section_heading": "Nature Medicine | Volume 31 | February 2025 | 599-608",
                },
                {
                    "page_label": "Page 2",
                    "text": "Results\nThe system improves quality.",
                    "section_heading": "Nature Medicine | Volume 31 | February 2025 | 599-608",
                },
            ],
        },
    )
    coarse_sections = [
        SectionRecord(
            section_id="coarse",
            document_id="doc3",
            title="Nature Medicine | Volume 31 | February 2025 | 599-608",
            summary="Flattened summary.",
            text=" ".join(str(page["text"]) for page in document.metadata["pages"]),
            page_labels=["Page 1", "Page 2"],
            section_path=["Article"],
            clause_ids=[],
            metadata={"section_kind": "general", "section_aliases": ["Article"]},
        )
    ]

    def fake_assignments(_document, _pages):
        return [
            {
                "page_label": "Page 1",
                "title": "Nature Medicine | Volume 31 | February 2025 | 599-608",
                "section_kind": "overview",
            },
            {"page_label": "Page 2", "title": "Results", "section_kind": "results"},
        ]

    service._llm_assignments = fake_assignments  # type: ignore[method-assign]

    repaired_sections, _ = service.repair_if_needed(document, coarse_sections)

    assert repaired_sections[0].title == "Document Overview"
    assert repaired_sections[1].title == "Abstract"
