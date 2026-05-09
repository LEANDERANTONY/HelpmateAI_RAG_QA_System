from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path
from typing import Any

from src.schemas import DocumentRecord
from src.structure import enrich_pages_with_structure, infer_document_style


def _file_fingerprint(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


def _strip_invalid_storage_chars(value: Any) -> Any:
    if isinstance(value, str):
        return value.replace("\x00", "")
    if isinstance(value, list):
        return [_strip_invalid_storage_chars(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_strip_invalid_storage_chars(item) for item in value)
    if isinstance(value, dict):
        return {
            _strip_invalid_storage_chars(key): _strip_invalid_storage_chars(item)
            for key, item in value.items()
        }
    return value


def _page_heading(text: str) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return lines[3] if len(lines) > 3 else (lines[0] if lines else "")


def _table_extractor_mode() -> str:
    mode = os.getenv("HELPMATE_TABLE_EXTRACTOR", "pdfplumber").strip().lower()
    return mode if mode in {"off", "pdfplumber"} else "pdfplumber"


def _table_extractor_max_pages() -> int:
    value = os.getenv("HELPMATE_TABLE_EXTRACTOR_MAX_PAGES")
    if not value:
        return 40
    try:
        return max(0, int(value))
    except ValueError:
        return 40


def _looks_table_enrichment_candidate(text: str) -> bool:
    lowered = text.lower()
    if re.search(r"\b(?:table|exhibit)\s+[a-z0-9.:-]+", text, flags=re.IGNORECASE):
        return True
    if any(token in lowered for token in ("scenario", "indicators", "investment", "2030", "2050", "usd", "%", "gtco", "ej")):
        numeric_count = len(re.findall(r"\b\d+(?:[.,]\d+)?\b", text))
        return numeric_count >= 8
    dense_numeric_lines = 0
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        numeric_ratio = sum(char.isdigit() for char in stripped) / max(len(stripped), 1)
        if numeric_ratio >= 0.12 and (re.search(r"\s{2,}|\t|[|]", stripped) or len(stripped.split()) >= 5):
            dense_numeric_lines += 1
    return dense_numeric_lines >= 3


def _markdown_table(rows: list[list[str]]) -> str:
    if not rows:
        return ""
    width = max(len(row) for row in rows)
    normalized = [[(cell or "").strip().replace("\n", " ") for cell in row] + [""] * (width - len(row)) for row in rows]
    header = normalized[0]
    separator = ["---"] * width
    body = normalized[1:]

    def render(row: list[str]) -> str:
        return "| " + " | ".join(cell.replace("|", "\\|") for cell in row) + " |"

    return "\n".join([render(header), render(separator), *(render(row) for row in body)])


def _table_text_from_rows(rows: list[list[str]]) -> str:
    markdown = _markdown_table(rows)
    if not markdown:
        return ""
    return "Extracted table:\n" + markdown


def _extract_pdfplumber_table_artifacts(path: Path, pages: list[dict[str, str]]) -> list[dict[str, object]]:
    if _table_extractor_mode() == "off":
        return []
    try:
        import pdfplumber
    except ImportError:
        return []

    candidate_page_numbers = [
        index
        for index, page in enumerate(pages, start=1)
        if _looks_table_enrichment_candidate(str(page.get("text", "")))
    ][: _table_extractor_max_pages()]
    if not candidate_page_numbers:
        return []

    table_settings = {
        "vertical_strategy": "lines",
        "horizontal_strategy": "lines",
        "snap_tolerance": 3,
        "join_tolerance": 3,
        "intersection_tolerance": 3,
    }
    artifacts: list[dict[str, object]] = []
    with pdfplumber.open(path) as pdf:
        for page_number in candidate_page_numbers:
            if page_number > len(pdf.pages):
                continue
            page = pdf.pages[page_number - 1]
            try:
                tables = page.extract_tables(table_settings=table_settings)
            except Exception:
                continue
            table_index = 0
            for table in tables:
                rows = [
                    [(cell or "").strip() for cell in row]
                    for row in table
                    if any((cell or "").strip() for cell in row)
                ]
                if not rows:
                    continue
                row_count = len(rows)
                column_count = max(len(row) for row in rows)
                populated_cells = sum(1 for row in rows for cell in row if cell)
                if column_count < 2 or populated_cells < 4 or (row_count < 2 and column_count < 4):
                    continue
                table_text = _table_text_from_rows(rows)
                if not table_text:
                    continue
                table_index += 1
                artifacts.append(
                    {
                        "artifact_type": "table",
                        "source_backend": "pdfplumber",
                        "original_page_number": page_number,
                        "original_page_label": f"Page {page_number}",
                        "table_index_on_page": table_index,
                        "row_count": row_count,
                        "column_count": column_count,
                        "text": table_text,
                    }
                )
    return artifacts


def _attach_table_artifacts(pages: list[dict[str, str]], artifacts: list[dict[str, object]]) -> None:
    by_page: dict[int, list[dict[str, object]]] = {}
    for artifact in artifacts:
        page_number = int(artifact.get("original_page_number", 0) or 0)
        by_page.setdefault(page_number, []).append(artifact)
    for page_number, page_artifacts in by_page.items():
        if 1 <= page_number <= len(pages):
            pages[page_number - 1]["table_artifacts"] = page_artifacts  # type: ignore[assignment]


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
    table_artifacts = _extract_pdfplumber_table_artifacts(path, pages)
    _attach_table_artifacts(pages, table_artifacts)
    return full_text, pages, page_count, {
        "extraction_backend": "pypdf",
        "table_extraction_backend": _table_extractor_mode(),
        "table_artifact_count": len(table_artifacts),
    }


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
    try:
        from docling.document_converter import DocumentConverter, PdfFormatOption
        from docling.datamodel.base_models import InputFormat
        from docling.datamodel.pipeline_options import PdfPipelineOptions
    except ImportError as exc:
        raise RuntimeError("Docling extraction requires the optional docling dependency to be installed.") from exc

    if path.suffix.lower() != ".pdf":
        return DocumentConverter()
    pipeline_options = PdfPipelineOptions()
    pipeline_options.do_ocr = _docling_ocr_enabled()
    return DocumentConverter(format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options)})


def _docling_metadata() -> dict[str, str]:
    return {
        "extraction_backend": "docling",
        "docling_ocr": "enabled" if _docling_ocr_enabled() else "disabled",
        "docling_table_mode": "expanded_markdown",
    }


def _extract_docling(path: Path, *, fallback_page_label: str = "Document") -> tuple[str, list[dict[str, str]], int, dict[str, str]]:
    result = _docling_converter(path).convert(path.resolve())
    document = result.document
    page_count = _docling_page_count(document)
    metadata = _docling_metadata()
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
                        "docling_ocr": metadata["docling_ocr"],
                        "docling_table_mode": metadata["docling_table_mode"],
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
                    "docling_ocr": metadata["docling_ocr"],
                    "docling_table_mode": metadata["docling_table_mode"],
                }
            )
            page_count = 1
    full_text = "\n\n".join(page["text"] for page in pages)
    return full_text, pages, page_count, metadata


def _extract_pdf_docling(path: Path) -> tuple[str, list[dict[str, str]], int, dict[str, str]]:
    return _extract_docling(path, fallback_page_label="Document")


def _extract_docx_docling(path: Path) -> tuple[str, list[dict[str, str]], int, dict[str, str]]:
    return _extract_docling(path, fallback_page_label="Document")


def _pdf_extractor_mode() -> str:
    mode = os.getenv("HELPMATE_PDF_EXTRACTOR", "pypdf").strip().lower()
    return mode if mode in {"docling", "pypdf"} else "pypdf"


def _docx_extractor_mode() -> str:
    mode = os.getenv("HELPMATE_DOCX_EXTRACTOR", "python-docx").strip().lower()
    return mode if mode in {"docling", "python-docx"} else "python-docx"


def _extract_pdf(path: Path) -> tuple[str, list[dict[str, str]], int, dict[str, str]]:
    mode = _pdf_extractor_mode()
    if mode == "pypdf":
        return _extract_pdf_pypdf(path)
    if mode == "docling":
        try:
            return _extract_pdf_docling(path)
        except Exception as exc:
            full_text, pages, page_count, metadata = _extract_pdf_pypdf(path)
            metadata = {
                **metadata,
                "requested_extraction_backend": "docling",
                "extraction_fallback_reason": str(exc)[:300],
            }
            return full_text, pages, page_count, metadata
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
    if mode == "docling":
        try:
            return _extract_docx_docling(path)
        except Exception as exc:
            full_text, pages, page_count, metadata = _extract_docx_python_docx(path)
            metadata = {
                **metadata,
                "requested_extraction_backend": "docling",
                "extraction_fallback_reason": str(exc)[:300],
            }
            return full_text, pages, page_count, metadata
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

    full_text = _strip_invalid_storage_chars(full_text)
    pages = _strip_invalid_storage_chars(pages)
    extraction_metadata = _strip_invalid_storage_chars(extraction_metadata)
    fingerprint = _file_fingerprint(file_path)
    document_id = fingerprint[:16]
    enriched_pages, outline = enrich_pages_with_structure(pages)
    enriched_pages = _strip_invalid_storage_chars(enriched_pages)
    outline = _strip_invalid_storage_chars(outline)
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
