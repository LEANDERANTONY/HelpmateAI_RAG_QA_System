from src.schemas import DocumentRecord
from src.sections import build_sections


def test_build_sections_groups_pages_by_section_path():
    document = DocumentRecord(
        document_id="doc123",
        file_name="sample.pdf",
        file_type="pdf",
        source_path="sample.pdf",
        fingerprint="abc123",
        char_count=200,
        page_count=2,
        metadata={
            "pages": [
                {
                    "page_label": "Page 1",
                    "text": "Section intro text.",
                    "section_heading": "Introduction",
                    "section_path": ["Introduction"],
                    "section_id": "Introduction",
                    "clause_ids": [],
                    "content_type": "general",
                },
                {
                    "page_label": "Page 2",
                    "text": "More intro text.",
                    "section_heading": "Introduction",
                    "section_path": ["Introduction"],
                    "section_id": "Introduction",
                    "clause_ids": [],
                    "content_type": "general",
                },
            ]
        },
    )

    sections = build_sections(document)

    assert len(sections) == 1
    assert sections[0].section_id == "Introduction"
    assert sections[0].page_labels == ["Page 1", "Page 2"]
    assert "Section intro text." in sections[0].text


def test_build_sections_prefers_canonical_heading_and_clean_summary():
    document = DocumentRecord(
        document_id="doc456",
        file_name="paper.pdf",
        file_type="pdf",
        source_path="paper.pdf",
        fingerprint="def456",
        char_count=300,
        page_count=1,
        metadata={
            "pages": [
                {
                    "page_label": "Page 1",
                    "text": "Author Manuscript\nABSTRACT\nThis paper studies multimodal fusion for cancer diagnosis. It shows why combining modalities matters.\ncontact@example.com",
                    "section_heading": "Author Manuscript",
                    "section_path": ["Author Manuscript"],
                    "section_id": "Author Manuscript",
                    "clause_ids": [],
                    "content_type": "general",
                }
            ]
        },
    )

    section = build_sections(document)[0]

    assert section.title == "Abstract"
    assert "multimodal fusion for cancer diagnosis" in section.summary


def test_build_sections_adds_document_overview_for_research_style_docs():
    document = DocumentRecord(
        document_id="doc789",
        file_name="paper.pdf",
        file_type="pdf",
        source_path="paper.pdf",
        fingerprint="ghi789",
        char_count=500,
        page_count=2,
        metadata={
            "document_style": "research_paper",
            "pages": [
                {
                    "page_label": "Page 1",
                    "text": "Multimodal data fusion for cancer biomarker discovery\nAbstract\nThis paper studies multimodal fusion in oncology.",
                    "section_heading": "Abstract",
                    "section_path": ["Abstract"],
                    "section_id": "Abstract",
                    "clause_ids": [],
                    "content_type": "abstract",
                    "section_kind": "abstract",
                    "document_style": "research_paper",
                },
                {
                    "page_label": "Page 2",
                    "text": "Introduction\nThe paper explains why multiple modalities are needed.",
                    "section_heading": "Introduction",
                    "section_path": ["Introduction"],
                    "section_id": "Introduction",
                    "clause_ids": [],
                    "content_type": "introduction",
                    "section_kind": "introduction",
                    "document_style": "research_paper",
                },
            ]
        },
    )

    sections = build_sections(document)

    assert sections[0].title == "Document Overview"
    assert sections[0].metadata["section_kind"] == "overview"
    assert "main focus" in " ".join(sections[0].metadata["section_aliases"]).lower()
