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
