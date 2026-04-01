from __future__ import annotations

import hashlib
from pathlib import Path

from src.schemas import DocumentRecord
from src.structure import enrich_pages_with_structure


def _file_fingerprint(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


def _extract_pdf(path: Path) -> tuple[str, list[dict[str, str]], int]:
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    pages: list[dict[str, str]] = []
    page_count = len(reader.pages)
    for index, page in enumerate(reader.pages, start=1):
        text = (page.extract_text() or "").strip()
        if text:
            lines = [line.strip() for line in text.splitlines() if line.strip()]
            section_heading = lines[3] if len(lines) > 3 else (lines[0] if lines else "")
            pages.append({"page_label": f"Page {index}", "text": text, "section_heading": section_heading})
    full_text = "\n\n".join(page["text"] for page in pages)
    return full_text, pages, page_count


def _extract_docx(path: Path) -> tuple[str, list[dict[str, str]], int]:
    from docx import Document as DocxDocument

    document = DocxDocument(str(path))
    paragraphs = [paragraph.text.strip() for paragraph in document.paragraphs if paragraph.text.strip()]
    pages = [{"page_label": "Document", "text": "\n".join(paragraphs), "section_heading": paragraphs[0] if paragraphs else ""}] if paragraphs else []
    full_text = "\n".join(paragraphs)
    return full_text, pages, 1 if paragraphs else 0


def ingest_document(path: str | Path) -> DocumentRecord:
    file_path = Path(path)
    suffix = file_path.suffix.lower()
    if suffix == ".pdf":
        full_text, pages, page_count = _extract_pdf(file_path)
        file_type = "pdf"
    elif suffix == ".docx":
        full_text, pages, page_count = _extract_docx(file_path)
        file_type = "docx"
    else:
        raise ValueError(f"Unsupported document type: {file_path.suffix}")

    fingerprint = _file_fingerprint(file_path)
    document_id = fingerprint[:16]
    enriched_pages, outline = enrich_pages_with_structure(pages)
    return DocumentRecord(
        document_id=document_id,
        file_name=file_path.name,
        file_type=file_type,
        source_path=str(file_path),
        fingerprint=fingerprint,
        char_count=len(full_text),
        page_count=page_count,
        metadata={"pages": enriched_pages, "outline": outline},
        extracted_text=full_text,
    )
