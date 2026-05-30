from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path
from typing import Any

from src.schemas import DocumentRecord
from src.structure import enrich_pages_with_structure, infer_document_style


class PdfExtractionError(RuntimeError):
    """Upload-time extraction failure that should surface to the user as a
    422 (the file can't be processed) rather than an opaque 500."""


class EncryptedPdfError(PdfExtractionError):
    """The uploaded PDF is password-protected and could not be decrypted."""


class UnextractablePdfError(PdfExtractionError):
    """The document has pages but yielded no extractable text — typically a
    scanned / image-only PDF with no text layer (needs OCR)."""


def _file_fingerprint(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


def file_fingerprint(path: Path) -> str:
    """Public SHA-256 content fingerprint.

    Exposed so the pipeline can derive the same ``document_id`` stem the
    module uses (``file_fingerprint(path)[:16]``) to pre-stage the DOCX
    PDF rendition at the path ``normalize_upload_paths`` expects, without
    reaching into the private helper.
    """
    return _file_fingerprint(path)


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
    """Candidate-page scan cap. ``0`` (the default) = unlimited.

    The old default of 40 silently dropped tables past page 40 of long
    documents — a table on page 150 of a 200-page report must still be
    captured. The env var stays as a safety valve for pathological
    inputs only; unset / 0 / invalid all mean "no cap".
    """
    value = os.getenv("HELPMATE_TABLE_EXTRACTOR_MAX_PAGES")
    if not value:
        return 0
    try:
        return max(0, int(value))
    except ValueError:
        return 0


def _table_pregate_min_lines() -> int:
    """Minimum tabular-looking lines for the structural pre-gate to flag
    a page (env-tunable, default 3 = a header + two data rows). Lower =
    higher recall / more pdfplumber passes."""
    value = os.getenv("HELPMATE_TABLE_PREGATE_MIN_LINES")
    if not value:
        return 3
    try:
        return max(1, int(value))
    except ValueError:
        return 3


def _looks_table_enrichment_candidate(text: str) -> bool:
    """Vocabulary-free, recall-biased pre-gate: does this page show any
    tabular structure worth pdfplumber's geometry pass?

    Replaces the old corpus-word allowlist (``scenario``, ``2050``,
    ``gtco`` …) which was fitted to the eval corpus and silently skipped
    every generic table — HR, pricing, schedules — that didn't use that
    vocabulary. Signals here are purely structural so the gate
    generalises to any document class.

    Deliberately recall-biased: a false positive is cheap (the
    lines/text detection + shape filter discard non-tables); a false
    negative permanently loses a real table. The structural test keeps
    the ``len(tokens) >= 5`` proxy because pypdf collapses table cell
    boundaries to single spaces, so multi-space gaps alone under-detect
    rows on extracted text.
    """
    # A captioned table/exhibit is a universal, high-precision signal —
    # any document class, no vocabulary assumption.
    if re.search(r"\b(?:table|exhibit)\s+[a-z0-9.:\-]+", text, flags=re.IGNORECASE):
        return True
    min_lines = _table_pregate_min_lines()
    tabular_lines = 0
    for line in text.splitlines():
        stripped = line.strip()
        if len(stripped) < 4:
            continue
        tokens = stripped.split()
        has_column_gap = bool(re.search(r"\s{2,}|\t|\|", stripped))
        numeric_ratio = sum(char.isdigit() for char in stripped) / len(stripped)
        # A row is tabular if it shows explicit column structure
        # (aligned/ruled/piped — text tables, glossaries, schedules) OR
        # it's a numeric data row (financial/metrics tables, robust to
        # pypdf single-space cell collapse via the token-count proxy).
        is_tabular = has_column_gap or (numeric_ratio >= 0.12 and len(tokens) >= 5)
        if is_tabular:
            tabular_lines += 1
            if tabular_lines >= min_lines:
                return True
    return False


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


# Stage 2 detection strategies. `lines` = ruled tables (pdfplumber's
# conservative default; near-zero hallucination, blind to borderless).
# `text` = infer columns from text alignment; catches borderless tables
# (incl. Word/DOCX tables LibreOffice renders without visible borders)
# but hallucinates from prose/TOC/multi-column text — so it only runs as
# a per-page fallback where `lines` found nothing, bounding its noise to
# the borderless-table signature.
_LINES_TABLE_SETTINGS = {
    "vertical_strategy": "lines",
    "horizontal_strategy": "lines",
    "snap_tolerance": 3,
    "join_tolerance": 3,
    "intersection_tolerance": 3,
}
_TEXT_TABLE_SETTINGS = {
    "vertical_strategy": "text",
    "horizontal_strategy": "text",
    "snap_tolerance": 3,
    "join_tolerance": 3,
}


def _tables_to_artifacts(
    tables: list, page_number: int, strategy: str
) -> list[dict[str, object]]:
    """Stage 3 precision guard: clean rows + drop pdfplumber's
    degenerate matches (slivers / single-cell / too-sparse), shared by
    both detection strategies so the filter is identical regardless of
    how the table was found."""
    artifacts: list[dict[str, object]] = []
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
                "source_strategy": strategy,
                "original_page_number": page_number,
                "original_page_label": f"Page {page_number}",
                "table_index_on_page": table_index,
                "row_count": row_count,
                "column_count": column_count,
                "text": table_text,
            }
        )
    return artifacts


def _extract_pdfplumber_table_artifacts(path: Path, pages: list[dict[str, str]]) -> list[dict[str, object]]:
    if _table_extractor_mode() == "off":
        return []
    try:
        import pdfplumber
    except ImportError:
        return []

    # Use the TRUE PDF page number (pdf_page_number), not the position in the
    # filtered `pages` list. The two diverge whenever an earlier page had no
    # extractable text and was dropped, which previously made pdfplumber open
    # the wrong physical page and stamp the wrong citation label (H7). Fall
    # back to the filtered position only if the field is somehow absent.
    candidate_page_numbers = [
        int(page.get("pdf_page_number") or filtered_pos)
        for filtered_pos, page in enumerate(pages, start=1)
        if _looks_table_enrichment_candidate(str(page.get("text", "")))
    ]
    max_pages = _table_extractor_max_pages()
    if max_pages > 0:
        candidate_page_numbers = candidate_page_numbers[:max_pages]
    if not candidate_page_numbers:
        return []

    artifacts: list[dict[str, object]] = []
    with pdfplumber.open(path) as pdf:
        for page_number in candidate_page_numbers:
            if page_number > len(pdf.pages):
                continue
            page = pdf.pages[page_number - 1]
            try:
                lines_tables = page.extract_tables(table_settings=_LINES_TABLE_SETTINGS)
            except Exception:
                lines_tables = []
            page_artifacts = _tables_to_artifacts(lines_tables, page_number, "lines")
            if not page_artifacts:
                # No ruled table survived on a page the structural
                # pre-gate flagged → retry borderless via text strategy.
                try:
                    text_tables = page.extract_tables(table_settings=_TEXT_TABLE_SETTINGS)
                except Exception:
                    text_tables = []
                page_artifacts = _tables_to_artifacts(text_tables, page_number, "text")
            artifacts.extend(page_artifacts)
    return artifacts


def _attach_table_artifacts(pages: list[dict[str, str]], artifacts: list[dict[str, object]]) -> None:
    by_page: dict[int, list[dict[str, object]]] = {}
    for artifact in artifacts:
        page_number = int(artifact.get("original_page_number", 0) or 0)
        by_page.setdefault(page_number, []).append(artifact)
    # Attach by matching each artifact's TRUE page number to the page dict
    # carrying the same pdf_page_number, instead of indexing the filtered
    # `pages` list positionally — those diverge once a blank page is dropped
    # (H7). Fall back to positional for inputs without pdf_page_number.
    for filtered_pos, page in enumerate(pages, start=1):
        true_page = int(page.get("pdf_page_number") or filtered_pos)
        page_artifacts = by_page.get(true_page)
        if page_artifacts:
            page["table_artifacts"] = page_artifacts  # type: ignore[assignment]


def _extract_pdf_pypdf(path: Path) -> tuple[str, list[dict[str, str]], int, dict[str, str]]:
    from pypdf import PdfReader
    from pypdf.errors import FileNotDecryptedError

    reader = PdfReader(str(path))
    if reader.is_encrypted:
        # Many "encrypted" PDFs only carry an owner password and decrypt
        # cleanly with an empty user password — try that before giving up.
        try:
            reader.decrypt("")
        except Exception:
            pass
    pages: list[dict[str, str]] = []
    try:
        page_count = len(reader.pages)
        numbered_pages = list(enumerate(reader.pages, start=1))
    except FileNotDecryptedError as exc:
        raise EncryptedPdfError(
            "This PDF is password-protected and can't be read. Remove the "
            "password (or re-save it without one) and upload it again."
        ) from exc
    for index, page in numbered_pages:
        text = (page.extract_text() or "").strip()
        if text:
            pages.append(
                {
                    "page_label": f"Page {index}",
                    # True 1-based PDF page number. `pages` drops blank /
                    # no-text pages, so a position in this filtered list is
                    # NOT the physical page. Carry the real number so table
                    # extraction reads the right page and labels citations
                    # correctly (H7).
                    "pdf_page_number": index,
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


def _extract_docx_via_pdf_or_text(
    docx_path: Path, rendition: str | Path | None
) -> tuple[str, list[dict[str, str]], int, dict[str, str]]:
    """Prefer extracting a DOCX from its LibreOffice-rendered PDF.

    The python-docx path flattens the whole document into one
    ``page_label="Document"`` page and drops tables/headers/footers
    entirely. Extracting instead from the LibreOffice rendition — the
    SAME PDF artifact the viewer serves via ``viewable_pdf_path`` — runs
    the document through the proven native-PDF pipeline (``_extract_pdf``
    → pypdf text + pdfplumber tables + real ``Page N`` labels). Those
    page labels line up exactly with what the user sees in Read Mode,
    which is what fixes the DOCX page-hint / ring-trap defect.

    Falls back to the python-docx extractor when no rendition was
    produced (LibreOffice disabled / unavailable) or the PDF path
    raises, so a missing converter never blocks ingestion. The fallback
    is self-consistent: when there's no rendition the viewer is
    download-only too, so "no page alignment" matches end to end.
    """
    if rendition is not None:
        rendition_path = Path(rendition)
        if rendition_path.is_file():
            try:
                full_text, pages, page_count, metadata = _extract_pdf(rendition_path)
            except Exception:
                full_text, pages = "", []
                page_count, metadata = 0, {}
            if pages:
                return (
                    full_text,
                    pages,
                    page_count,
                    {**metadata, "docx_extraction_source": "libreoffice_pdf"},
                )
    return _extract_docx(docx_path)


def ingest_document(
    path: str | Path,
    *,
    docx_pdf_rendition: str | Path | None = None,
) -> DocumentRecord:
    """Extract a document into a ``DocumentRecord``.

    ``docx_pdf_rendition`` (keyword-only, optional): when the upload is a
    DOCX and the pipeline has already produced its LibreOffice PDF
    rendition, pass that path here to extract from the rendered PDF
    instead of the python-docx paragraph flattener. This yields real
    per-page text + pdfplumber tables + ``Page N`` labels that match the
    PDF the viewer serves. Omitting it (or passing a missing path)
    preserves the legacy python-docx behaviour, so existing callers and
    PDF uploads are unaffected.
    """
    file_path = Path(path)
    suffix = file_path.suffix.lower()
    if suffix == ".pdf":
        full_text, pages, page_count, extraction_metadata = _extract_pdf(file_path)
        file_type = "pdf"
    elif suffix == ".docx":
        full_text, pages, page_count, extraction_metadata = _extract_docx_via_pdf_or_text(
            file_path, docx_pdf_rendition
        )
        file_type = "docx"
    else:
        raise ValueError(f"Unsupported document type: {file_path.suffix}")

    if page_count > 0 and not (full_text or "").strip():
        # The document has pages but no extractable text — almost always a
        # scanned / image-only PDF with no text layer. Without this guard the
        # upload "succeeds" (200) into a silently empty index that abstains on
        # every question, with no signal to the user (H6). Surface a 422 via
        # the upload route's PdfExtractionError handler instead.
        raise UnextractablePdfError(
            "No readable text could be extracted from this document. If it is "
            "a scanned or photographed PDF, it needs OCR before it can be "
            "indexed — try uploading a text-based PDF."
        )

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
