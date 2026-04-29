from pathlib import Path

import pytest

from src.ingest import service


def test_pdf_auto_uses_docling_when_available(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    pdf_path = tmp_path / "sample.pdf"
    pdf_path.write_bytes(b"%PDF fake")

    def fake_docling(path: Path):
        return (
            "# Title\n\n| A | B |\n|---|---|\n| 1 | 2 |",
            [
                {
                    "page_label": "Page 1",
                    "text": "# Title\n\n| A | B |\n|---|---|\n| 1 | 2 |",
                    "section_heading": "Title",
                    "extraction_backend": "docling",
                }
            ],
            1,
            {"extraction_backend": "docling"},
        )

    monkeypatch.setenv("HELPMATE_PDF_EXTRACTOR", "auto")
    monkeypatch.setattr(service, "_extract_pdf_docling", fake_docling)

    full_text, pages, page_count, metadata = service._extract_pdf(pdf_path)

    assert page_count == 1
    assert "| 1 | 2 |" in full_text
    assert pages[0]["extraction_backend"] == "docling"
    assert metadata["extraction_backend"] == "docling"


def test_pdf_auto_falls_back_to_pypdf_when_docling_fails(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    pdf_path = tmp_path / "sample.pdf"
    pdf_path.write_bytes(b"%PDF fake")

    def failing_docling(path: Path):
        raise RuntimeError("docling conversion failed")

    def fake_pypdf(path: Path):
        return (
            "plain text",
            [{"page_label": "Page 1", "text": "plain text", "section_heading": "plain text", "extraction_backend": "pypdf"}],
            1,
            {"extraction_backend": "pypdf"},
        )

    monkeypatch.setenv("HELPMATE_PDF_EXTRACTOR", "auto")
    monkeypatch.setattr(service, "_extract_pdf_docling", failing_docling)
    monkeypatch.setattr(service, "_extract_pdf_pypdf", fake_pypdf)

    full_text, pages, page_count, metadata = service._extract_pdf(pdf_path)

    assert full_text == "plain text"
    assert page_count == 1
    assert metadata["extraction_backend"] == "pypdf"
    assert metadata["preferred_extraction_backend"] == "docling"
    assert "RuntimeError" in metadata["extraction_fallback_reason"]
    assert pages[0]["preferred_extraction_backend"] == "docling"


def test_pdf_pypdf_mode_bypasses_docling(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    pdf_path = tmp_path / "sample.pdf"
    pdf_path.write_bytes(b"%PDF fake")
    called = {"docling": False}

    def fake_docling(path: Path):
        called["docling"] = True
        raise AssertionError("docling should not be called")

    def fake_pypdf(path: Path):
        return (
            "plain text",
            [{"page_label": "Page 1", "text": "plain text", "section_heading": "plain text", "extraction_backend": "pypdf"}],
            1,
            {"extraction_backend": "pypdf"},
        )

    monkeypatch.setenv("HELPMATE_PDF_EXTRACTOR", "pypdf")
    monkeypatch.setattr(service, "_extract_pdf_docling", fake_docling)
    monkeypatch.setattr(service, "_extract_pdf_pypdf", fake_pypdf)

    _, _, _, metadata = service._extract_pdf(pdf_path)

    assert metadata["extraction_backend"] == "pypdf"
    assert called["docling"] is False


def test_docx_auto_uses_docling_when_available(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    docx_path = tmp_path / "sample.docx"
    docx_path.write_bytes(b"fake docx")

    def fake_docling(path: Path):
        return (
            "# Title\n\n| Metric | Value |\n|---|---|\n| A | 1 |",
            [
                {
                    "page_label": "Document",
                    "text": "# Title\n\n| Metric | Value |\n|---|---|\n| A | 1 |",
                    "section_heading": "Title",
                    "extraction_backend": "docling",
                }
            ],
            1,
            {"extraction_backend": "docling"},
        )

    monkeypatch.setenv("HELPMATE_DOCX_EXTRACTOR", "auto")
    monkeypatch.setattr(service, "_extract_docx_docling", fake_docling)

    full_text, pages, page_count, metadata = service._extract_docx(docx_path)

    assert page_count == 1
    assert "| A | 1 |" in full_text
    assert pages[0]["extraction_backend"] == "docling"
    assert metadata["extraction_backend"] == "docling"


def test_docx_auto_falls_back_to_python_docx_when_docling_fails(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    docx_path = tmp_path / "sample.docx"
    docx_path.write_bytes(b"fake docx")

    def failing_docling(path: Path):
        raise RuntimeError("docling conversion failed")

    def fake_python_docx(path: Path):
        return (
            "plain docx text",
            [
                {
                    "page_label": "Document",
                    "text": "plain docx text",
                    "section_heading": "plain docx text",
                    "extraction_backend": "python-docx",
                }
            ],
            1,
            {"extraction_backend": "python-docx"},
        )

    monkeypatch.setenv("HELPMATE_DOCX_EXTRACTOR", "auto")
    monkeypatch.setattr(service, "_extract_docx_docling", failing_docling)
    monkeypatch.setattr(service, "_extract_docx_python_docx", fake_python_docx)

    full_text, pages, page_count, metadata = service._extract_docx(docx_path)

    assert full_text == "plain docx text"
    assert page_count == 1
    assert metadata["extraction_backend"] == "python-docx"
    assert metadata["preferred_extraction_backend"] == "docling"
    assert "RuntimeError" in metadata["extraction_fallback_reason"]
    assert pages[0]["preferred_extraction_backend"] == "docling"


def test_docx_python_docx_mode_bypasses_docling(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    docx_path = tmp_path / "sample.docx"
    docx_path.write_bytes(b"fake docx")
    called = {"docling": False}

    def fake_docling(path: Path):
        called["docling"] = True
        raise AssertionError("docling should not be called")

    def fake_python_docx(path: Path):
        return (
            "plain docx text",
            [
                {
                    "page_label": "Document",
                    "text": "plain docx text",
                    "section_heading": "plain docx text",
                    "extraction_backend": "python-docx",
                }
            ],
            1,
            {"extraction_backend": "python-docx"},
        )

    monkeypatch.setenv("HELPMATE_DOCX_EXTRACTOR", "python-docx")
    monkeypatch.setattr(service, "_extract_docx_docling", fake_docling)
    monkeypatch.setattr(service, "_extract_docx_python_docx", fake_python_docx)

    _, _, _, metadata = service._extract_docx(docx_path)

    assert metadata["extraction_backend"] == "python-docx"
    assert called["docling"] is False
