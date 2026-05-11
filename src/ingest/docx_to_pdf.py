"""LibreOffice-backed DOCX → PDF conversion.

The in-app document viewer (Tier 2) renders the original upload via PDF.js. For
DOCX uploads we need a PDF rendition the viewer can consume; this module wraps
the LibreOffice headless converter so we run it once at ingest and cache the
result as a sibling file alongside the source upload.

Why LibreOffice?
- It's the de-facto open-source converter that preserves layout, fonts, and
  embedded images. Docx2pdf relies on MS Word/COM on Windows or Mac; not
  usable on a Linux VPS. python-docx + reportlab would lose layout entirely.
- A headless `soffice` invocation is a single subprocess call — no daemon to
  babysit. The cost (~2-5s per document) is amortised across every viewer
  open because we cache the PDF on disk and reuse it indefinitely.

Failure modes:
- soffice not installed → `FileNotFoundError`, surface as `DocxConversionError`.
- Conversion takes longer than the configured timeout → kill the process and
  raise `DocxConversionError`. The caller decides whether to surface the
  failure to the user or fall back to a download-only experience.
- soffice exits non-zero (corrupt DOCX, locked file) → raise with the captured
  stderr so the operator can debug.
"""
from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path


logger = logging.getLogger(__name__)


class DocxConversionError(RuntimeError):
    """Raised when LibreOffice fails to convert a DOCX to PDF.

    Carries the original cause as a string so callers can log it without
    needing to chase nested exceptions, but does not expose the full stderr
    in __str__ because it can be very chatty.
    """


def convert_docx_to_pdf(
    source_docx: Path,
    output_pdf: Path,
    *,
    soffice_binary: str = "soffice",
    timeout: int = 60,
) -> Path:
    """Convert `source_docx` to PDF, writing the result to `output_pdf`.

    LibreOffice's CLI doesn't let us pick the exact output filename — it
    derives the PDF name from the source stem and drops it in `--outdir`.
    To get a deterministic target path we:
      1. run the conversion into the target directory
      2. find the file LibreOffice produced (it'll match `<stem>.pdf`)
      3. rename it to `output_pdf` if necessary

    Returns the final PDF path on success; raises `DocxConversionError` on
    any failure.
    """
    if not source_docx.exists() or not source_docx.is_file():
        raise DocxConversionError(f"Source DOCX not found: {source_docx}")

    # Resolve the binary up-front so we fail fast with a clearer error than
    # subprocess's generic FileNotFoundError when soffice is missing.
    resolved = shutil.which(soffice_binary)
    if resolved is None:
        raise DocxConversionError(
            f"LibreOffice binary '{soffice_binary}' not found on PATH; "
            "install libreoffice-core + libreoffice-writer (or set "
            "HELPMATE_DOCX_PDF_SOFFICE) to enable DOCX viewer support."
        )

    output_pdf.parent.mkdir(parents=True, exist_ok=True)

    cmd = [
        resolved,
        "--headless",
        "--norestore",
        "--nologo",
        "--nodefault",
        "--nolockcheck",
        "--convert-to",
        "pdf",
        "--outdir",
        str(output_pdf.parent),
        str(source_docx),
    ]

    try:
        completed = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise DocxConversionError(
            f"LibreOffice conversion timed out after {timeout}s for "
            f"{source_docx.name}"
        ) from exc
    except FileNotFoundError as exc:
        # Race: soffice was on PATH at shutil.which() time but disappeared.
        raise DocxConversionError(
            f"LibreOffice binary disappeared during conversion: {exc}"
        ) from exc

    if completed.returncode != 0:
        stderr_tail = (completed.stderr or "").strip().splitlines()[-3:]
        logger.warning(
            "LibreOffice exit %s for %s: %s",
            completed.returncode,
            source_docx.name,
            " | ".join(stderr_tail),
        )
        raise DocxConversionError(
            f"LibreOffice exited with code {completed.returncode} converting "
            f"{source_docx.name}"
        )

    # LibreOffice names the output `<source_stem>.pdf`. If the caller wants
    # a different filename (e.g. `{document_id}.pdf`) rename it into place.
    expected = output_pdf.parent / f"{source_docx.stem}.pdf"
    if not expected.exists():
        raise DocxConversionError(
            f"LibreOffice reported success but no PDF was produced for "
            f"{source_docx.name}"
        )

    if expected != output_pdf:
        # Replace silently if a stale rendition exists.
        if output_pdf.exists():
            output_pdf.unlink()
        expected.rename(output_pdf)

    return output_pdf


__all__ = ["DocxConversionError", "convert_docx_to_pdf"]
