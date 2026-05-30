"""GET /documents/{id}/file across its branches, local storage backend (M24).

The route serves the user's actual uploaded bytes (and is hit on every citation
click). It branches on inline-vs-download key selection, a 415 for legacy
DOCX-only records on the inline path, a 404 when the file is missing, and the
local-disk FileResponse — none of which had coverage.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.auth import AuthenticatedUser, require_authenticated_user
from backend.main import app
from backend.store import WORKSPACE_OWNER_KEY
from src.schemas import DocumentRecord

OWNER = "00000000-0000-4000-8000-0000000000f1"


@pytest.fixture
def file_env(monkeypatch, tmp_path):
    from backend import main as backend_main

    monkeypatch.setenv("HELPMATE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("HELPMATE_FILE_STORAGE_BACKEND", "local")
    monkeypatch.delenv("HELPMATE_STATE_STORE_BACKEND", raising=False)
    backend_main._settings.cache_clear()
    backend_main._store.cache_clear()
    backend_main._file_storage.cache_clear()
    settings = backend_main._settings()
    settings.uploads_dir.mkdir(parents=True, exist_ok=True)
    store = backend_main._store()
    yield store, settings
    backend_main._settings.cache_clear()
    backend_main._store.cache_clear()
    backend_main._file_storage.cache_clear()


def _doc(document_id, *, source_path, viewable_pdf_path, file_name="f.pdf") -> DocumentRecord:
    return DocumentRecord(
        document_id=document_id,
        file_name=file_name,
        file_type="pdf",
        source_path=str(source_path),
        fingerprint="ff",
        char_count=1,
        page_count=1,
        viewable_pdf_path=(str(viewable_pdf_path) if viewable_pdf_path else None),
        metadata={WORKSPACE_OWNER_KEY: OWNER},
    )


def _get(path: str):
    app.dependency_overrides[require_authenticated_user] = lambda: AuthenticatedUser(
        id=OWNER, email="o@example.com"
    )
    try:
        return TestClient(app).get(path)
    finally:
        app.dependency_overrides.pop(require_authenticated_user, None)


def test_inline_pdf_returns_200(file_env):
    store, settings = file_env
    pdf = settings.uploads_dir / "doc-ok.pdf"
    pdf.write_bytes(b"%PDF-1.4 test pdf bytes")
    store.save_document(_doc("doc-ok", source_path=pdf, viewable_pdf_path=pdf))
    response = _get("/documents/doc-ok/file")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/pdf")
    assert "inline" in response.headers.get("content-disposition", "").lower()


def test_legacy_docx_inline_returns_415(file_env):
    store, settings = file_env
    docx = settings.uploads_dir / "doc-legacy.docx"
    store.save_document(_doc("doc-legacy", source_path=docx, viewable_pdf_path=None))
    assert _get("/documents/doc-legacy/file").status_code == 415


def test_missing_file_returns_404(file_env):
    store, settings = file_env
    missing = settings.uploads_dir / "doc-missing.pdf"  # never created
    store.save_document(_doc("doc-missing", source_path=missing, viewable_pdf_path=missing))
    assert _get("/documents/doc-missing/file").status_code == 404


def test_download_returns_original_filename(file_env):
    store, settings = file_env
    pdf = settings.uploads_dir / "doc-dl.pdf"
    pdf.write_bytes(b"%PDF-1.4 test pdf bytes")
    store.save_document(
        _doc("doc-dl", source_path=pdf, viewable_pdf_path=pdf, file_name="original-name.pdf")
    )
    response = _get("/documents/doc-dl/file?download=1")
    assert response.status_code == 200
    assert "original-name.pdf" in response.headers.get("content-disposition", "")
