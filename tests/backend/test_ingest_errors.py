"""Upload-time PDF extraction failures surface as typed errors, not 500s.

H5: an encrypted (password-protected) PDF previously raised pypdf's
FileNotDecryptedError uncaught through the ingest pipeline -> a generic HTTP
500. The extractor now raises a typed EncryptedPdfError that the upload route
maps to 422 with an actionable message.
"""
from __future__ import annotations

import pytest

from src.ingest.service import EncryptedPdfError, _extract_pdf_pypdf


def _write_encrypted_pdf(path) -> None:
    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    writer.encrypt("a-real-user-password")
    with open(path, "wb") as handle:
        writer.write(handle)


def test_encrypted_pdf_raises_encrypted_error(tmp_path):
    pdf_path = tmp_path / "locked.pdf"
    _write_encrypted_pdf(pdf_path)
    with pytest.raises(EncryptedPdfError):
        _extract_pdf_pypdf(pdf_path)


def test_scanned_pdf_with_no_text_raises_unextractable(tmp_path, monkeypatch):
    """H6: a PDF with pages but zero extractable text (scanned / image-only)
    must raise rather than ingest into a silently empty index."""
    from src.ingest import service as ingest_service

    pdf_path = tmp_path / "scanned.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 dummy bytes")

    # page_count > 0 but no text — the scanned-PDF degenerate case.
    monkeypatch.setattr(
        ingest_service,
        "_extract_pdf",
        lambda _p: ("", [], 3, {"extraction_backend": "pypdf"}),
    )
    with pytest.raises(ingest_service.UnextractablePdfError):
        ingest_service.ingest_document(pdf_path)

