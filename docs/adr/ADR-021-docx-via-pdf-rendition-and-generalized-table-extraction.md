# ADR-021: DOCX Ingestion Via LibreOffice Rendition + Generalized Table Extraction

Date: 2026-05-17

Status: Shipped (local; behind the ADR-020 eval gate before push)

Supersedes the DOCX-extraction half of [ADR-014](ADR-014-in-app-source-viewer-with-pdfjs-and-docx-rendition.md) (the viewer/rendition decision in ADR-014 stands; how DOCX *text* is extracted changes here).

## Context

Two ingestion defects, same root.

**1. DOCX Read Mode "ring trap" (D3).** `_extract_docx_python_docx` flattened every DOCX into exactly one page with the literal `page_label="Document"`, `page_count=1`, and (because `python-docx` `document.paragraphs` excludes table cells / headers / footers / text boxes) silently dropped all non-paragraph text. Downstream, `parsePageLabel("Document") → 1`, and the Read Mode viewer's `±3` page ring only ever scanned rendered-PDF pages 1-4. A citation that rendered on page ≥5 of the LibreOffice viewer PDF produced a no-match banner while `highlightAll` had painted the real match off-screen. The viewer PDF's pagination (LibreOffice layout engine) had no relationship to the python-docx text extraction, so the page hint was structurally dead for every DOCX.

**2. Eval-fitted table pre-gate.** `_extract_pdfplumber_table_artifacts` only ran on pages flagged by `_looks_table_enrichment_candidate`, whose heuristic was a corpus-word allowlist (`scenario`, `indicators`, `2030`, `2050`, `usd`, `gtco`, `ej`, …) plus a captioned-table regex. Those tokens map directly onto the FinanceBench + climate-scenario eval corpus — the gate was fitted to the benchmark, not built as a general table detector. Any generic table (HR, pricing, schedules) with neither a `Table N` caption nor those words was never scanned. A hard 40-candidate-page cap also dropped tables past page 40 of long reports.

The product already renders a LibreOffice PDF for every DOCX (`viewable_pdf_path`, ADR-014) for the viewer. The native-PDF ingestion path (`_extract_pdf_pypdf` → pypdf text + pdfplumber tables + physical `Page N` labels) is proven and is the path whose page labels already align with the served file.

## Decision

**1. Ingest DOCX from its LibreOffice rendition, not from python-docx.** The pipeline stages the rendition *before* extraction at the exact `<uploads>/<document_id>.pdf` path `normalize_upload_paths` already computes (`_prepare_docx_rendition`), so it is produced once and cache-reused by the viewer step. `ingest_document(path, *, docx_pdf_rendition=...)` then routes a DOCX through the same `_extract_pdf` path native PDFs use. DOCX chunk `page_label`s now equal the physical page of the exact PDF the viewer serves — Read Mode page hints are correct *by construction*, and table/header/footer text is captured for the first time. Fallback: when LibreOffice is unavailable / conversion fails, `_extract_docx_via_pdf_or_text` falls back to python-docx (self-consistent — the viewer is download-only in that case too, so "no page alignment" matches end to end).

**2. Generalize the table pre-gate; widen detection; uncap pages.** `_looks_table_enrichment_candidate` is now vocabulary-free and structural: a captioned `Table/Exhibit N`, **or** ≥N tabular-looking lines (column-gap structure via `\s{2,}|\t|\|`, or numeric-density with a `len(tokens)>=5` proxy — kept because pypdf collapses cell boundaries to single spaces). Detection is `lines`-strategy first with a per-page `text`-strategy fallback only where `lines` found nothing (borderless coverage without paying `text`'s prose-hallucination tax on every page). The shape filter (≥2 cols, ≥4 populated cells, drop slivers) stays as a shared precision guard for both strategies. The 40-page cap default becomes `0` (unlimited); env knobs `HELPMATE_TABLE_PREGATE_MIN_LINES` (default 3) and `HELPMATE_TABLE_EXTRACTOR_MAX_PAGES` (default 0 = unlimited, safety-valve only).

## Consequences

- **Positive.** DOCX Read Mode navigation is correct at the root (no viewer-side workaround needed). DOCX tables/headers/footers are retrievable for the first time. Generic-document tables are no longer silently skipped. Long reports' later-page tables are captured. The rendition is produced exactly once (no double LibreOffice cost).
- **Negative / cost.** DOCX ingest now depends on LibreOffice being present (already true for the viewer; fallback covers absence). `text`-strategy fallback adds pdfplumber cost on borderless-table pages; uncapping pages adds cost on pathological all-table 200-page docs (one-time, pure CPU, acceptable; env cap is the safety valve).
- **Eval-load-bearing.** The old pre-gate was fitted to FinanceBench/the eval corpus. Generalizing it changes which tables are extracted on exactly those docs → it can move FinanceBench / final-eval scores either way. This is part of why the batch is held behind the ADR-020 eval gate.

## Validation

47+ ingest/pipeline/chunking/table tests pass; backward-compatible (positional `ingest_document` callers + native-PDF path unaffected; the corpus-agnostic detection test asserts generic tables are now caught and corpus-words-without-structure are not). End-to-end LibreOffice DOCX path to be exercised in QA + measured by the ADR-020 baseline-vs-HEAD eval before push.
