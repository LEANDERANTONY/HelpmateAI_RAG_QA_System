from src.structure.service import enrich_pages_with_structure, infer_document_style


def test_enrich_pages_with_structure_extracts_clause_and_content_type():
    pages = [
        {
            "page_label": "Page 1",
            "text": "3.1.2 Waiting Period\nExpenses related to the treatment shall be excluded until the expiry of 24 months.",
            "section_heading": "",
        }
    ]

    enriched, outline = enrich_pages_with_structure(pages)

    assert enriched[0]["content_type"] == "waiting_period"
    assert enriched[0]["clause_ids"] == ["3.1.2"]
    assert enriched[0]["section_heading"] == "Waiting Period"
    assert outline[0]["clause_id"] == "3.1.2"


def test_enrich_pages_with_structure_detects_abstract_and_ignores_author_noise():
    pages = [
        {
            "page_label": "Page 1",
            "text": "Multimodal data fusion for cancer biomarker discovery with deep learning\nSandra Example1,2 Author2,3\nABSTRACT\nThis paper studies multimodal fusion.",
            "section_heading": "Sandra Example1,2 Author2,3",
        }
    ]

    enriched, outline = enrich_pages_with_structure(pages)

    assert enriched[0]["section_heading"] == "Abstract"
    assert enriched[0]["section_kind"] == "abstract"
    assert outline[0]["title"] == "Abstract"


def test_enrich_pages_with_structure_marks_appendix_pages():
    pages = [
        {
            "page_label": "Page 10",
            "text": "APPENDIX A: RESEARCH PROPOSAL\nDetailed appendix text here.",
            "section_heading": "APPENDIX A: RESEARCH PROPOSAL",
        }
    ]

    enriched, _ = enrich_pages_with_structure(pages)

    assert enriched[0]["section_kind"] == "appendix"
    assert enriched[0]["content_type"] == "appendix"


def test_infer_document_style_for_policy_document():
    pages = [{"text": "Policy wording grace period waiting period cashless network provider", "page_label": "Page 1"}]
    outline = [{"title": "Definitions"}, {"title": "Claims Procedure"}]

    assert infer_document_style(pages, outline) == "policy_document"


def test_infer_document_style_for_research_paper():
    pages = [{"text": "Abstract Introduction references et al. Stanford University", "page_label": "Page 1"}]
    outline = [{"title": "Abstract"}, {"title": "Introduction"}, {"title": "References"}]

    assert infer_document_style(pages, outline) == "research_paper"


def test_infer_document_style_for_thesis_document():
    pages = [{"text": "Final Thesis Report research aim research objectives appendix final remarks", "page_label": "Page 1"}]
    outline = [{"title": "Research Aim and Objectives"}, {"title": "Final Remarks"}, {"title": "Appendix A"}]

    assert infer_document_style(pages, outline) == "thesis_document"
