from src.schemas import SectionRecord
from src.sections.profiles import enrich_section_profiles


def _section(section_id: str, title: str, text: str, page: str = "Page 1", path: list[str] | None = None) -> SectionRecord:
    return SectionRecord(
        section_id=section_id,
        document_id="doc",
        title=title,
        summary=text[:200],
        text=text,
        page_labels=[page],
        section_path=path or [title],
        clause_ids=[],
        metadata={
            "section_kind": title.lower(),
            "content_type": "general",
            "section_aliases": [title],
            "front_matter_kind": "body",
        },
    )


def test_enrich_section_profiles_extracts_chapter_scope_and_aliases():
    sections = [
        _section(
            "chapter-4",
            "Introduction",
            "37\nCHAPTER 4\nIMPLEMENTATION\n4.1 Introduction\nThis chapter presents the implementation pipeline.",
            page="Page 52",
            path=["Summary", "CHAPTER 4"],
        )
    ]

    enriched = enrich_section_profiles(sections)
    metadata = enriched[0].metadata

    assert metadata["chapter_number"] == "4"
    assert metadata["chapter_title"] == "Implementation"
    assert metadata["document_section_role"] == "implementation"
    assert "Implementation chapter" in metadata["document_scope_labels"]
    assert "Chapter 4 Implementation" in metadata["section_aliases"]


def test_enrich_section_profiles_infers_chapter_from_numbered_heading():
    sections = [
        _section(
            "prelude",
            "Introduction",
            "CHAPTER 4\nIMPLEMENTATION\n4.1 Introduction\nThe chapter introduces implementation.",
            page="Page 10",
        ),
        _section(
            "pipeline",
            "Training and Evaluation Pipeline",
            "4.5 Training and Evaluation Pipeline\nThe workflow trains and evaluates all models.",
            page="Page 20",
        ),
    ]

    enriched = enrich_section_profiles(sections)
    metadata = enriched[1].metadata

    assert metadata["chapter_number"] == "4"
    assert metadata["chapter_title"] == "Implementation"
    assert metadata["numbered_heading"] == "Training and Evaluation Pipeline"
    assert "Implementation chapter" in metadata["document_scope_labels"]


def test_enrich_section_profiles_marks_page_ranges_and_roles_generically():
    sections = [
        _section(
            "results",
            "Results",
            "5.2 Results\nThe section reports performance metrics and findings.",
            page="Page 30",
        )
    ]

    enriched = enrich_section_profiles(sections)
    metadata = enriched[0].metadata

    assert metadata["document_section_role"] == "results"
    assert metadata["page_range_start"] == 30
    assert metadata["page_range_end"] == 30
    assert "findings" in metadata["section_aliases"]
