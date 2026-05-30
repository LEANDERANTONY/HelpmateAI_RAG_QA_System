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
import tempfile
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

    # Convert into a private temp directory rather than the shared uploads dir.
    # LibreOffice derives the output filename from the source stem, so two
    # concurrent conversions of same-named files (e.g. two users' report.docx)
    # would otherwise both write report.pdf into output_pdf.parent and clobber
    # each other before the move into place (M13).
    work_dir = Path(tempfile.mkdtemp(prefix="helpmate-docx-"))
    try:
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
            str(work_dir),
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

        # LibreOffice names the output `<source_stem>.pdf` inside work_dir.
        expected = work_dir / f"{source_docx.stem}.pdf"
        if not expected.exists():
            raise DocxConversionError(
                f"LibreOffice reported success but no PDF was produced for "
                f"{source_docx.name}"
            )

        # Move the single produced PDF to the caller's canonical target
        # (e.g. {document_id}.pdf). shutil.move (not rename) because the temp
        # dir may be on a different filesystem. Replace a stale rendition.
        if output_pdf.exists():
            output_pdf.unlink()
        shutil.move(str(expected), str(output_pdf))
        return output_pdf
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


__all__ = ["DocxConversionError", "convert_docx_to_pdf"]
