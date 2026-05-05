from src.ingest.service import (
    _attach_table_artifacts,
    _looks_table_enrichment_candidate,
    _table_text_from_rows,
)


def test_table_text_from_rows_renders_markdown():
    text = _table_text_from_rows(
        [
            ["Facility", "Rate", "Limit"],
            ["Overnight repo", "3.75%", ""],
            ["Reverse repo", "3.50%", "$160 billion"],
        ]
    )

    assert text.startswith("Extracted table:")
    assert "| Facility | Rate | Limit |" in text
    assert "| Reverse repo | 3.50% | $160 billion |" in text


def test_attach_table_artifacts_keeps_original_page_mapping():
    pages = [
        {"page_label": "Page 1", "text": "Front matter"},
        {"page_label": "Page 2", "text": "Table 1\nA B\n1 2"},
    ]
    artifact = {
        "original_page_number": 2,
        "text": "Extracted table:\n| A | B |\n| --- | --- |\n| 1 | 2 |",
    }

    _attach_table_artifacts(pages, [artifact])

    assert "table_artifacts" not in pages[0]
    assert pages[1]["table_artifacts"] == [artifact]


def test_table_candidate_detection_handles_captions_and_dense_numeric_pages():
    caption_page = "Table S1. Scenario indicators\nMetric 2020 2030 2050"
    dense_numeric_page = "\n".join(
        [
            "Investment needs are reported in USD for 2030 and 2050.",
            "Power 100 200 300 400",
            "Grid 5 10 15 20",
            "Hydrogen 1 2 3 4",
        ]
    )
    prose_page = "This page explains the motivation in ordinary prose without numeric tabulation."

    assert _looks_table_enrichment_candidate(caption_page) is True
    assert _looks_table_enrichment_candidate(dense_numeric_page) is True
    assert _looks_table_enrichment_candidate(prose_page) is False
