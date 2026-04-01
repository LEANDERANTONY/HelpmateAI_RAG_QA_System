from src.structure.service import enrich_pages_with_structure


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
