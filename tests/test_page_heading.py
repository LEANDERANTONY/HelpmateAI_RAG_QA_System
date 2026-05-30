"""_page_heading section-heading pick (L14).

The helper used to return the arbitrary 4th non-empty line as a page's
section heading; it now returns the first line that passes a lightweight
heading check, falling back to the first non-empty line.
"""
from __future__ import annotations

from src.ingest.service import _looks_like_heading, _page_heading


def test_returns_first_heading_like_line_not_the_fourth():
    text = (
        "Results\n"
        "The study found a strong effect across all cohorts.\n"
        "More body text here.\n"
        "Even more prose follows."
    )
    # Old behavior returned the 4th line ("Even more prose follows."); now the
    # first heading-like line wins.
    assert _page_heading(text) == "Results"


def test_skips_leading_page_number_and_body_to_find_heading():
    text = (
        "12\n"
        "This is a running sentence of body text that ends in a period.\n"
        "Methods"
    )
    assert _page_heading(text) == "Methods"


def test_falls_back_to_first_line_when_nothing_looks_like_a_heading():
    text = (
        "This whole page is one long sentence that never reads like a heading "
        "at all, sadly."
    )
    assert _page_heading(text) == text.strip()


def test_empty_text_returns_empty_string():
    assert _page_heading("") == ""
    assert _page_heading("   \n  \n") == ""


def test_looks_like_heading_rejects_sentences_numbers_and_long_lines():
    assert _looks_like_heading("Introduction") is True
    assert _looks_like_heading("Section: Overview") is True
    assert _looks_like_heading("This ends like a sentence.") is False
    assert _looks_like_heading("12") is False
    assert _looks_like_heading("12.") is False
    assert _looks_like_heading("x" * 81) is False
