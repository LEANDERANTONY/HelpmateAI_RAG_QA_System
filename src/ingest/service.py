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


def _docling_ocr_enabled() -> bool:
    return os.getenv("HELPMATE_DOCLING_OCR", "false").strip().lower() in {"1", "true", "yes", "on"}


def _docling_converter(path: Path):
    from docling.document_converter import DocumentConverter, PdfFormatOption
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions

    if path.suffix.lower() != ".pdf":
        return DocumentConverter()
    pipeline_options = PdfPipelineOptions()
    pipeline_options.do_ocr = _docling_ocr_enabled()
    return DocumentConverter(format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options)})


def _extract_docling(path: Path, *, fallback_page_label: str = "Document") -> tuple[str, list[dict[str, str]], int, dict[str, str]]:
    result = _docling_converter(path).convert(path.resolve())
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


def _azure_document_intelligence_config() -> tuple[str, str]:
    endpoint = os.getenv("HELPMATE_AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT") or os.getenv("AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT")
    key = os.getenv("HELPMATE_AZURE_DOCUMENT_INTELLIGENCE_KEY") or os.getenv("AZURE_DOCUMENT_INTELLIGENCE_KEY")
    if not endpoint or not key:
        raise RuntimeError(
            "Azure Document Intelligence requires HELPMATE_AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT "
            "and HELPMATE_AZURE_DOCUMENT_INTELLIGENCE_KEY."
        )
    return endpoint, key


def _google_document_ai_config() -> tuple[str, str, str]:
    project_id = os.getenv("HELPMATE_GOOGLE_DOCUMENT_AI_PROJECT_ID") or os.getenv("GOOGLE_CLOUD_PROJECT")
    location = os.getenv("HELPMATE_GOOGLE_DOCUMENT_AI_LOCATION", "us")
    processor_id = os.getenv("HELPMATE_GOOGLE_DOCUMENT_AI_PROCESSOR_ID")
    if not project_id or not processor_id:
        raise RuntimeError(
            "Google Document AI requires HELPMATE_GOOGLE_DOCUMENT_AI_PROJECT_ID "
            "and HELPMATE_GOOGLE_DOCUMENT_AI_PROCESSOR_ID. Authentication uses GOOGLE_APPLICATION_CREDENTIALS."
        )
    return project_id, location, processor_id


def _mime_type_for_path(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return "application/pdf"
    if suffix == ".docx":
        return "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    return "application/octet-stream"


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def _pdf_page_count(path: Path) -> int:
    from pypdf import PdfReader

    return len(PdfReader(str(path)).pages)


def _page_spans_text(content: str, page) -> str:
    spans = getattr(page, "spans", None) or []
    parts = []
    for span in spans:
        offset = int(getattr(span, "offset", 0) or 0)
        length = int(getattr(span, "length", 0) or 0)
        if length > 0:
            parts.append(content[offset : offset + length])
    return "\n".join(part.strip() for part in parts if part and part.strip()).strip()


def _extract_azure_document_intelligence(path: Path) -> tuple[str, list[dict[str, str]], int, dict[str, str]]:
    from azure.ai.documentintelligence import DocumentIntelligenceClient
    from azure.ai.documentintelligence.models import DocumentContentFormat
    from azure.core.credentials import AzureKeyCredential

    endpoint, key = _azure_document_intelligence_config()
    client = DocumentIntelligenceClient(endpoint=endpoint, credential=AzureKeyCredential(key))
    with path.open("rb") as document:
        poller = client.begin_analyze_document(
            "prebuilt-layout",
            body=document,
            output_content_format=DocumentContentFormat.MARKDOWN,
        )
    result = poller.result()
    full_text = (getattr(result, "content", "") or "").strip()
    pages = []
    result_pages = getattr(result, "pages", None) or []
    for index, page in enumerate(result_pages, start=1):
        text = _page_spans_text(full_text, page)
        if text:
            page_number = getattr(page, "page_number", None) or index
            pages.append(
                {
                    "page_label": f"Page {page_number}",
                    "text": text,
                    "section_heading": _page_heading(text),
                    "extraction_backend": "azure_document_intelligence",
                }
            )
    if not pages and full_text:
        pages.append(
            {
                "page_label": "Document",
                "text": full_text,
                "section_heading": _page_heading(full_text),
                "extraction_backend": "azure_document_intelligence",
            }
        )
    return full_text, pages, len(result_pages) or len(pages), {"extraction_backend": "azure_document_intelligence"}


def _google_text_anchor_text(content: str, text_anchor) -> str:
    segments = getattr(text_anchor, "text_segments", None) or []
    parts = []
    for segment in segments:
        start = int(getattr(segment, "start_index", 0) or 0)
        end = int(getattr(segment, "end_index", 0) or 0)
        if end > start:
            parts.append(content[start:end])
    return "\n".join(part.strip() for part in parts if part and part.strip()).strip()


def _google_page_text(content: str, page) -> str:
    layout = getattr(page, "layout", None)
    if layout and getattr(layout, "text_anchor", None):
        return _google_text_anchor_text(content, layout.text_anchor)
    blocks = getattr(page, "blocks", None) or []
    parts = []
    for block in blocks:
        block_layout = getattr(block, "layout", None)
        if block_layout and getattr(block_layout, "text_anchor", None):
            text = _google_text_anchor_text(content, block_layout.text_anchor)
            if text:
                parts.append(text)
    return "\n".join(parts).strip()


def _google_layout_page_span(block) -> tuple[int, int]:
    page_span = getattr(block, "page_span", None)
    start = int(getattr(page_span, "page_start", 0) or 0)
    end = int(getattr(page_span, "page_end", 0) or start or 0)
    return start, end


def _google_layout_block_text(block) -> str:
    text_block = getattr(block, "text_block", None)
    if text_block and getattr(text_block, "text", None):
        return str(text_block.text).strip()
    table_block = getattr(block, "table_block", None)
    if table_block and getattr(table_block, "body_rows", None):
        rows = []
        for row in list(getattr(table_block, "header_rows", []) or []) + list(getattr(table_block, "body_rows", []) or []):
            cells = [str(getattr(cell, "text", "") or "").strip() for cell in getattr(row, "cells", []) or []]
            if any(cells):
                rows.append(" | ".join(cells))
        return "\n".join(rows).strip()
    return ""


def _collect_google_layout_pages(blocks, page_texts: dict[int, list[str]]) -> None:
    for block in blocks or []:
        text = _google_layout_block_text(block)
        start, _ = _google_layout_page_span(block)
        if text and start > 0:
            page_texts.setdefault(start, []).append(text)
        children = []
        text_block = getattr(block, "text_block", None)
        if text_block:
            children.extend(getattr(text_block, "blocks", []) or [])
        table_block = getattr(block, "table_block", None)
        if table_block:
            children.extend(getattr(table_block, "blocks", []) or [])
        children.extend(getattr(block, "blocks", []) or [])
        _collect_google_layout_pages(children, page_texts)


def _google_document_layout_pages(document) -> list[dict[str, str]]:
    layout = getattr(document, "document_layout", None)
    blocks = getattr(layout, "blocks", None) if layout else None
    page_texts: dict[int, list[str]] = {}
    _collect_google_layout_pages(blocks, page_texts)
    pages = []
    for page_number in sorted(page_texts):
        text = "\n".join(part for part in page_texts[page_number] if part).strip()
        if text:
            pages.append(
                {
                    "page_label": f"Page {page_number}",
                    "text": text,
                    "section_heading": _page_heading(text),
                    "extraction_backend": "google_document_ai",
                }
            )
    return pages


def _google_chunked_text(document) -> str:
    chunked = getattr(document, "chunked_document", None)
    chunks = getattr(chunked, "chunks", None) if chunked else None
    return "\n\n".join(str(getattr(chunk, "content", "") or "").strip() for chunk in chunks or [] if getattr(chunk, "content", None)).strip()


def _google_layout_process_options(page_numbers: list[int] | None = None):
    from google.cloud import documentai

    options = documentai.ProcessOptions(
        layout_config=documentai.ProcessOptions.LayoutConfig(
            chunking_config=documentai.ProcessOptions.LayoutConfig.ChunkingConfig(
                chunk_size=500,
                include_ancestor_headings=True
            )
        )
    )
    if page_numbers:
        options.individual_page_selector = documentai.ProcessOptions.IndividualPageSelector(pages=page_numbers)
    return options


def _extract_google_response(document) -> tuple[str, list[dict[str, str]], int]:
    full_text = (getattr(document, "text", "") or "").strip()
    pages = _google_document_layout_pages(document)
    if not full_text:
        full_text = "\n\n".join(page["text"] for page in pages).strip() or _google_chunked_text(document)
    result_pages = getattr(document, "pages", None) or []
    if not pages:
        for index, page in enumerate(result_pages, start=1):
            text = _google_page_text(full_text, page)
            if text:
                page_number = getattr(page, "page_number", None) or index
                pages.append(
                    {
                        "page_label": f"Page {page_number}",
                        "text": text,
                        "section_heading": _page_heading(text),
                        "extraction_backend": "google_document_ai",
                    }
                )
    if not pages and full_text:
        pages.append(
            {
                "page_label": "Document",
                "text": full_text,
                "section_heading": _page_heading(full_text),
                "extraction_backend": "google_document_ai",
            }
        )
    layout = getattr(document, "document_layout", None)
    layout_blocks = getattr(layout, "blocks", None) if layout else None
    layout_page_count = max(((_google_layout_page_span(block)[1]) for block in layout_blocks or []), default=0)
    return full_text, pages, len(result_pages) or layout_page_count or len(pages)


def _dedupe_google_pages(pages: list[dict[str, str]]) -> list[dict[str, str]]:
    by_label: dict[str, dict[str, str]] = {}
    for page in pages:
        label = page.get("page_label", "Document")
        text = page.get("text", "")
        existing = by_label.get(label)
        if existing is None or len(text) > len(existing.get("text", "")):
            by_label[label] = page

    def sort_key(item: tuple[str, dict[str, str]]) -> tuple[int, str]:
        label = item[0]
        if label.startswith("Page "):
            try:
                return int(label.split()[1]), label
            except (IndexError, ValueError):
                return 10**9, label
        return 10**9, label

    return [page for _, page in sorted(by_label.items(), key=sort_key)]


def _extract_google_document_ai(path: Path) -> tuple[str, list[dict[str, str]], int, dict[str, str]]:
    from google.api_core.retry import Retry
    from google.api_core.client_options import ClientOptions
    from google.cloud import documentai

    project_id, location, processor_id = _google_document_ai_config()
    client = documentai.DocumentProcessorServiceClient(
        client_options=ClientOptions(api_endpoint=f"{location}-documentai.googleapis.com")
    )
    name = client.processor_path(project_id, location, processor_id)
    raw_document = documentai.RawDocument(content=path.read_bytes(), mime_type=_mime_type_for_path(path))
    page_count = _pdf_page_count(path) if path.suffix.lower() == ".pdf" else 0
    request_timeout = _env_int("HELPMATE_GOOGLE_DOCUMENT_AI_TIMEOUT_SECONDS", 600)
    request_retry = Retry(deadline=_env_int("HELPMATE_GOOGLE_DOCUMENT_AI_RETRY_DEADLINE_SECONDS", 900))
    batch_size = max(1, min(30, _env_int("HELPMATE_GOOGLE_DOCUMENT_AI_BATCH_PAGES", 15)))
    if page_count <= batch_size:
        result = client.process_document(
            request=documentai.ProcessRequest(
                name=name,
                raw_document=raw_document,
                process_options=_google_layout_process_options(),
            ),
            retry=request_retry,
            timeout=request_timeout,
        )
        full_text, pages, detected_pages = _extract_google_response(result.document)
        return full_text, pages, detected_pages, {"extraction_backend": "google_document_ai"}

    all_pages: list[dict[str, str]] = []
    text_parts = []
    for start in range(1, page_count + 1, batch_size):
        end = min(start + batch_size - 1, page_count)
        page_numbers = list(range(start, end + 1))
        result = client.process_document(
            request=documentai.ProcessRequest(
                name=name,
                raw_document=raw_document,
                process_options=_google_layout_process_options(page_numbers),
            ),
            retry=request_retry,
            timeout=request_timeout,
        )
        full_text, pages, _ = _extract_google_response(result.document)
        if full_text:
            text_parts.append(full_text)
        all_pages.extend(pages)
    all_pages = _dedupe_google_pages(all_pages)
    full_text = "\n\n".join(page["text"] for page in all_pages).strip() or "\n\n".join(text_parts).strip()
    return full_text, all_pages, page_count, {"extraction_backend": "google_document_ai"}


def _pdf_extractor_mode() -> str:
    mode = os.getenv("HELPMATE_PDF_EXTRACTOR", "pypdf").strip().lower()
    return mode if mode in {"azure", "docling", "google", "pypdf"} else "pypdf"


def _docx_extractor_mode() -> str:
    mode = os.getenv("HELPMATE_DOCX_EXTRACTOR", "python-docx").strip().lower()
    return mode if mode in {"azure", "docling", "google", "python-docx"} else "python-docx"


def _extract_pdf(path: Path) -> tuple[str, list[dict[str, str]], int, dict[str, str]]:
    mode = _pdf_extractor_mode()
    if mode == "pypdf":
        return _extract_pdf_pypdf(path)
    if mode == "azure":
        return _extract_azure_document_intelligence(path)
    if mode == "google":
        return _extract_google_document_ai(path)
    if mode == "docling":
        return _extract_pdf_docling(path)
    return _extract_pdf_pypdf(path)


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
    if mode == "azure":
        return _extract_azure_document_intelligence(path)
    if mode == "google":
        return _extract_google_document_ai(path)
    if mode == "docling":
        return _extract_docx_docling(path)
    return _extract_docx_python_docx(path)


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
