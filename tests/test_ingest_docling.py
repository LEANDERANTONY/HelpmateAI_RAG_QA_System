from pathlib import Path

import pytest

from src.ingest import service


class _FakeDoclingResult:
    def __init__(self, document):
        self.document = document


class _FakeDoclingDocument:
    def __init__(self, pages: dict[int, str]):
        self.pages = pages

    def export_to_markdown(self, page_no=None, compact_tables=True):
        if page_no is None:
            return "\n\n".join(self.pages.values())
        return self.pages.get(page_no, "")


class _FakeDoclingConverter:
    def __init__(self, document):
        self.document = document
        self.converted_path = None

    def convert(self, path: Path):
        self.converted_path = path
        return _FakeDoclingResult(self.document)


def test_pdf_default_uses_pypdf(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    pdf_path = tmp_path / "sample.pdf"
    pdf_path.write_bytes(b"%PDF fake")
    called = {"docling": False}

    def fake_docling(path: Path):
        called["docling"] = True
        raise AssertionError("docling should not be called by default")

    def fake_pypdf(path: Path):
        return (
            "plain text",
            [{"page_label": "Page 1", "text": "plain text", "section_heading": "plain text", "extraction_backend": "pypdf"}],
            1,
            {"extraction_backend": "pypdf"},
        )

    monkeypatch.delenv("HELPMATE_PDF_EXTRACTOR", raising=False)
    monkeypatch.setattr(service, "_extract_pdf_docling", fake_docling)
    monkeypatch.setattr(service, "_extract_pdf_pypdf", fake_pypdf)

    full_text, pages, page_count, metadata = service._extract_pdf(pdf_path)

    assert page_count == 1
    assert full_text == "plain text"
    assert pages[0]["extraction_backend"] == "pypdf"
    assert metadata["extraction_backend"] == "pypdf"
    assert called["docling"] is False


def test_pdf_docling_mode_uses_docling(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
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

    monkeypatch.setenv("HELPMATE_PDF_EXTRACTOR", "docling")
    monkeypatch.setattr(service, "_extract_pdf_docling", fake_docling)

    full_text, pages, page_count, metadata = service._extract_pdf(pdf_path)

    assert page_count == 1
    assert "| 1 | 2 |" in full_text
    assert pages[0]["extraction_backend"] == "docling"
    assert metadata["extraction_backend"] == "docling"


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


def test_pdf_invalid_mode_falls_back_to_pypdf(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
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

    monkeypatch.setenv("HELPMATE_PDF_EXTRACTOR", "google")
    monkeypatch.setattr(service, "_extract_pdf_docling", fake_docling)
    monkeypatch.setattr(service, "_extract_pdf_pypdf", fake_pypdf)

    _, _, _, metadata = service._extract_pdf(pdf_path)

    assert metadata["extraction_backend"] == "pypdf"
    assert called["docling"] is False


def test_docling_extracts_expanded_markdown_pages(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    pdf_path = tmp_path / "sample.pdf"
    pdf_path.write_bytes(b"%PDF fake")
    document = _FakeDoclingDocument(
        {
            1: "# First\n\n| Metric | Value |\n|---|---|\n| A | 1 |",
            2: "# Second\n\nDetails",
        }
    )
    converter = _FakeDoclingConverter(document)

    monkeypatch.setenv("HELPMATE_DOCLING_OCR", "true")
    monkeypatch.setattr(service, "_docling_converter", lambda path: converter)

    full_text, pages, page_count, metadata = service._extract_pdf_docling(pdf_path)

    assert page_count == 2
    assert "| A | 1 |" in full_text
    assert [page["page_label"] for page in pages] == ["Page 1", "Page 2"]
    assert pages[0]["docling_ocr"] == "enabled"
    assert pages[0]["docling_table_mode"] == "expanded_markdown"
    assert metadata == {
        "extraction_backend": "docling",
        "docling_ocr": "enabled",
        "docling_table_mode": "expanded_markdown",
    }


def test_docx_default_uses_python_docx(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    docx_path = tmp_path / "sample.docx"
    docx_path.write_bytes(b"fake docx")
    called = {"docling": False}

    def fake_docling(path: Path):
        called["docling"] = True
        raise AssertionError("docling should not be called by default")

    def fake_python_docx(path: Path):
        return (
            "plain docx text",
            [{"page_label": "Document", "text": "plain docx text", "section_heading": "plain docx text", "extraction_backend": "python-docx"}],
            1,
            {"extraction_backend": "python-docx"},
        )

    monkeypatch.delenv("HELPMATE_DOCX_EXTRACTOR", raising=False)
    monkeypatch.setattr(service, "_extract_docx_docling", fake_docling)
    monkeypatch.setattr(service, "_extract_docx_python_docx", fake_python_docx)

    full_text, pages, page_count, metadata = service._extract_docx(docx_path)

    assert page_count == 1
    assert full_text == "plain docx text"
    assert pages[0]["extraction_backend"] == "python-docx"
    assert metadata["extraction_backend"] == "python-docx"
    assert called["docling"] is False


def test_docx_docling_mode_uses_docling(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
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

    monkeypatch.setenv("HELPMATE_DOCX_EXTRACTOR", "docling")
    monkeypatch.setattr(service, "_extract_docx_docling", fake_docling)

    full_text, pages, page_count, metadata = service._extract_docx(docx_path)

    assert page_count == 1
    assert "| A | 1 |" in full_text
    assert pages[0]["extraction_backend"] == "docling"
    assert metadata["extraction_backend"] == "docling"


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


def test_docx_invalid_mode_falls_back_to_python_docx(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
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

    monkeypatch.setenv("HELPMATE_DOCX_EXTRACTOR", "google")
    monkeypatch.setattr(service, "_extract_docx_docling", fake_docling)
    monkeypatch.setattr(service, "_extract_docx_python_docx", fake_python_docx)

    _, _, _, metadata = service._extract_docx(docx_path)

    assert metadata["extraction_backend"] == "python-docx"
    assert called["docling"] is False


