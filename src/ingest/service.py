from __future__ import annotations

import hashlib
import os
from pathlib import Path

from src.schemas import DocumentRecord
from src.structure import enrich_pages_with_structure, infer_document_style


def _file_fingerprint(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


def _page_heading(text: str) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return lines[3] if len(lines) > 3 else (lines[0] if lines else "")


def _extract_pdf_pypdf(path: Path) -> tuple[str, list[dict[str, str]], int, dict[str, str]]:
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    pages: list[dict[str, str]] = []
    page_count = len(reader.pages)
    for index, page in enumerate(reader.pages, start=1):
        text = (page.extract_text() or "").strip()
        if text:
            pages.append(
                {
                    "page_label": f"Page {index}",
                    "text": text,
                    "section_heading": _page_heading(text),
                    "extraction_backend": "pypdf",
                }
            )
    full_text = "\n\n".join(page["text"] for page in pages)
    return full_text, pages, page_count, {"extraction_backend": "pypdf"}


def _docling_page_count(document) -> int:
    pages = getattr(document, "pages", None)
    if isinstance(pages, dict):
        return len(pages)
    if isinstance(pages, list):
        return len(pages)
    return 0


def _extract_docling(path: Path, *, fallback_page_label: str = "Document") -> tuple[str, list[dict[str, str]], int, dict[str, str]]:
    from docling.document_converter import DocumentConverter

    result = DocumentConverter().convert(path.resolve())
    document = result.document
    page_count = _docling_page_count(document)
    pages: list[dict[str, str]] = []
    if page_count > 0:
        for index in range(1, page_count + 1):
            text = (document.export_to_markdown(page_no=index, compact_tables=False) or "").strip()
            if text:
                pages.append(
                    {
                        "page_label": f"Page {index}",
                        "text": text,
                        "section_heading": _page_heading(text),
                        "extraction_backend": "docling",
                    }
                )
    if not pages:
        text = (document.export_to_markdown(compact_tables=False) or "").strip()
        if text:
            pages.append(
                {
                    "page_label": fallback_page_label,
                    "text": text,
                    "section_heading": _page_heading(text),
                    "extraction_backend": "docling",
                }
            )
            page_count = 1
    full_text = "\n\n".join(page["text"] for page in pages)
    return full_text, pages, page_count, {"extraction_backend": "docling"}


def _extract_pdf_docling(path: Path) -> tuple[str, list[dict[str, str]], int, dict[str, str]]:
    return _extract_docling(path, fallback_page_label="Document")


def _extract_docx_docling(path: Path) -> tuple[str, list[dict[str, str]], int, dict[str, str]]:
    return _extract_docling(path, fallback_page_label="Document")


def _pdf_extractor_mode() -> str:
    mode = os.getenv("HELPMATE_PDF_EXTRACTOR", "auto").strip().lower()
    return mode if mode in {"auto", "docling", "pypdf"} else "auto"


def _docx_extractor_mode() -> str:
    mode = os.getenv("HELPMATE_DOCX_EXTRACTOR", "auto").strip().lower()
    return mode if mode in {"auto", "docling", "python-docx"} else "auto"


def _extract_pdf(path: Path) -> tuple[str, list[dict[str, str]], int, dict[str, str]]:
    mode = _pdf_extractor_mode()
    if mode == "pypdf":
        return _extract_pdf_pypdf(path)
    try:
        return _extract_pdf_docling(path)
    except Exception as exc:
        if mode == "docling":
            raise
        full_text, pages, page_count, metadata = _extract_pdf_pypdf(path)
        metadata.update(
            {
                "extraction_backend": "pypdf",
                "preferred_extraction_backend": "docling",
                "extraction_fallback_reason": f"{exc.__class__.__name__}: {str(exc)[:200]}",
            }
        )
        for page in pages:
            page["preferred_extraction_backend"] = "docling"
            page["extraction_fallback_reason"] = metadata["extraction_fallback_reason"]
        return full_text, pages, page_count, metadata


def _extract_docx_python_docx(path: Path) -> tuple[str, list[dict[str, str]], int, dict[str, str]]:
    from docx import Document as DocxDocument

    document = DocxDocument(str(path))
    paragraphs = [paragraph.text.strip() for paragraph in document.paragraphs if paragraph.text.strip()]
    pages = [
        {
            "page_label": "Document",
            "text": "\n".join(paragraphs),
            "section_heading": paragraphs[0] if paragraphs else "",
            "extraction_backend": "python-docx",
        }
    ] if paragraphs else []
    full_text = "\n".join(paragraphs)
    return full_text, pages, 1 if paragraphs else 0, {"extraction_backend": "python-docx"}


def _extract_docx(path: Path) -> tuple[str, list[dict[str, str]], int, dict[str, str]]:
    mode = _docx_extractor_mode()
    if mode == "python-docx":
        return _extract_docx_python_docx(path)
    try:
        return _extract_docx_docling(path)
    except Exception as exc:
        if mode == "docling":
            raise
        full_text, pages, page_count, metadata = _extract_docx_python_docx(path)
        metadata.update(
            {
                "extraction_backend": "python-docx",
                "preferred_extraction_backend": "docling",
                "extraction_fallback_reason": f"{exc.__class__.__name__}: {str(exc)[:200]}",
            }
        )
        for page in pages:
            page["preferred_extraction_backend"] = "docling"
            page["extraction_fallback_reason"] = metadata["extraction_fallback_reason"]
        return full_text, pages, page_count, metadata


def ingest_document(path: str | Path) -> DocumentRecord:
    file_path = Path(path)
    suffix = file_path.suffix.lower()
    if suffix == ".pdf":
        full_text, pages, page_count, extraction_metadata = _extract_pdf(file_path)
        file_type = "pdf"
    elif suffix == ".docx":
        full_text, pages, page_count, extraction_metadata = _extract_docx(file_path)
        file_type = "docx"
    else:
        raise ValueError(f"Unsupported document type: {file_path.suffix}")

    fingerprint = _file_fingerprint(file_path)
    document_id = fingerprint[:16]
    enriched_pages, outline = enrich_pages_with_structure(pages)
    document_style = infer_document_style(enriched_pages, outline)
    for page in enriched_pages:
        section_path = page.get("section_path", [])
        page["section_id"] = "|".join(section_path) if section_path else page.get("page_label", "Document")
        page["document_style"] = document_style
    return DocumentRecord(
        document_id=document_id,
        file_name=file_path.name,
        file_type=file_type,
        source_path=str(file_path),
        fingerprint=fingerprint,
        char_count=len(full_text),
        page_count=page_count,
        metadata={"pages": enriched_pages, "outline": outline, "document_style": document_style, **extraction_metadata},
        extracted_text=full_text,
    )
