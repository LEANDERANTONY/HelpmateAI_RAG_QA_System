"""One-shot migration: move local PDF/DOCX files into Supabase Storage.

This is the cutover step that flips HelpmateAI's file storage from VPS
disk to Supabase Storage. Run it once on the production VPS AFTER
deploying the file_storage abstraction and BEFORE flipping
HELPMATE_FILE_STORAGE_BACKEND=supabase.

Sequence per document:
  1.  Detect if source_path / viewable_pdf_path are already bucket keys
      (relative paths like "user-uuid/doc-id.pdf"). If yes, skip — that
      record migrated on an earlier run.
  2.  Otherwise, open each local file and upload to the configured
      Supabase bucket under `{owner_id}/{document_id}{ext}`.
  3.  Rewrite the DocumentRecord's source_path / viewable_pdf_path to
      the new bucket keys and persist via store.save_document().

The original local files are PRESERVED by default. They stay on the VPS
disk until you confirm the cutover went well, at which point you can
either re-run with --cleanup-local or just let the workspace sweeper
age them out. Keeping the locals means a partial migration failure
leaves a working fallback — the read endpoint's local-path branch
still serves uploaded bytes from disk.

Usage:

  # Dry run (default) — shows what would happen, doesn't modify anything
  python scripts/migrate_files_to_supabase.py

  # Actually perform the migration
  python scripts/migrate_files_to_supabase.py --apply

  # Delete local copies of files that have been migrated
  python scripts/migrate_files_to_supabase.py --apply --cleanup-local

Idempotent. Safe to re-run.

Required environment when --apply:
  SUPABASE_URL                       Supabase project URL
  SUPABASE_SERVICE_ROLE_KEY          Service-role key (NOT anon)
  HELPMATE_SUPABASE_STORAGE_BUCKET   Bucket name (default: helpmate-documents)
  HELPMATE_FILE_STORAGE_BACKEND      Should be set to "supabase" so the
                                     script's storage factory builds
                                     SupabaseFileStorage. The runtime
                                     env var can stay "local" if you
                                     want to leave the live service on
                                     local until cutover is verified.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Make `backend.*` and `src.*` importable when run from anywhere.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from backend.file_storage import SupabaseFileStorage, build_file_storage  # noqa: E402
from backend.store import build_api_record_store  # noqa: E402
from src.config import get_settings  # noqa: E402
from src.schemas import DocumentRecord  # noqa: E402


logger = logging.getLogger(__name__)

WORKSPACE_OWNER_KEY = "_workspace_owner_user_id"


@dataclass
class MigrationCounters:
    total: int = 0
    already_migrated: int = 0
    migrated: int = 0
    skipped_no_owner: int = 0
    skipped_missing_local: int = 0
    cleaned_locals: int = 0
    errors: int = 0

    def print_summary(self) -> None:
        print()
        print("=" * 60)
        print("Migration summary")
        print("=" * 60)
        print(f"  Documents scanned:        {self.total}")
        print(f"  Already in Supabase:      {self.already_migrated}")
        print(f"  Migrated this run:        {self.migrated}")
        print(f"  Skipped (no owner_id):    {self.skipped_no_owner}")
        print(f"  Skipped (local missing):  {self.skipped_missing_local}")
        print(f"  Local files cleaned up:   {self.cleaned_locals}")
        print(f"  Errors:                   {self.errors}")
        print("=" * 60)


def _owner_id_of(document: DocumentRecord) -> str | None:
    """Pull the workspace owner_id out of the document's metadata.

    The user-id partition is essential — bucket keys are namespaced as
    `{owner_id}/{document_id}{ext}` so per-user cleanup is one prefix-
    list+delete instead of a row-by-row scan. Records without an owner
    are pre-auth-era artifacts; we don't migrate them (safer to leave
    them in the local store until they age out).
    """
    metadata = document.metadata or {}
    raw = metadata.get(WORKSPACE_OWNER_KEY)
    if not raw:
        return None
    owner = str(raw).strip()
    return owner or None


def _is_bucket_key(path_value: str | None) -> bool:
    """True if the string looks like a Supabase bucket key (relative)
    rather than an absolute filesystem path.

    Bucket keys are formatted as `{owner_id}/{document_id}{ext}` —
    relative, no leading slash, no Windows drive letter. We detect
    "absolute" with explicit string-prefix checks rather than
    Path.is_absolute(), because Path.is_absolute() is platform-
    dependent: a POSIX absolute path like `/var/data/helpmate/...`
    reads as "relative" on Windows (no drive letter), which would
    misclassify production paths during a dry-run on a Windows dev
    box. The explicit checks treat both POSIX and Windows absolute
    paths as not-a-bucket-key on every platform.
    """
    if not path_value:
        return False
    # POSIX absolute path.
    if path_value.startswith("/"):
        return False
    # Windows absolute path: "C:\..." or "C:/...".
    if (
        len(path_value) >= 3
        and path_value[1] == ":"
        and path_value[2] in ("/", "\\")
    ):
        return False
    # Anything else (no leading slash, no drive letter) — treat as a
    # bucket-relative key.
    return True


def _upload_local_to_storage(
    *,
    storage: SupabaseFileStorage,
    owner_id: str,
    document_id: str,
    local_path: Path,
) -> str | None:
    """Upload a local file to the bucket and return its key. None means
    the source file wasn't on disk; caller decides what to do.

    Wraps SupabaseFileStorage.save_from_path so the migration script
    has a single place to log per-file outcomes consistently.
    """
    if not local_path.exists() or not local_path.is_file():
        logger.warning(
            "  ! local file missing: %s (will skip this record)",
            local_path,
        )
        return None
    try:
        return storage.save_from_path(
            owner_id=owner_id,
            document_id=document_id,
            source=local_path,
        )
    except Exception as exc:
        logger.error("  ! upload failed for %s: %s", local_path, exc)
        return None


def _canonical_local_paths(
    document: DocumentRecord, uploads_dir: Path
) -> list[Path]:
    """Compute the canonical local file paths for a document.

    After ingest, files were renamed to `{document_id}{ext}` under
    `uploads_dir` (see HelpmatePipeline.normalize_upload_paths). So even
    when a DocumentRecord's source_path / viewable_pdf_path now hold
    Supabase bucket keys, we can still recompute where the original
    local copy lived: extension is whatever's at the end of the bucket
    key (`.pdf` or `.docx`), and the stem is the document_id.

    Returns a deduplicated list of Path candidates (source and viewable
    if they have different suffixes; usually one path for PDF uploads
    and two for DOCX uploads).
    """
    suffixes: set[str] = set()
    for key in (document.source_path, document.viewable_pdf_path):
        if not key:
            continue
        ext = Path(key).suffix.lower()
        if ext:
            suffixes.add(ext)
    return [uploads_dir / f"{document.document_id}{ext}" for ext in suffixes]


def migrate_one(
    *,
    document: DocumentRecord,
    storage: SupabaseFileStorage,
    apply: bool,
    cleanup_local: bool,
    counters: MigrationCounters,
    store: Any,
    uploads_dir: Path,
) -> None:
    """Migrate a single DocumentRecord. Updates counters in place."""
    print(f"\n[{document.document_id}] {document.file_name}")

    # Already-migrated detection. We treat the source as authoritative —
    # if source_path is already a bucket key, the record was migrated on
    # a prior run. The upload step is a no-op; the cleanup step still
    # runs if requested because we can derive the canonical local path
    # from {uploads_dir}/{document_id}{ext}.
    if _is_bucket_key(document.source_path):
        print("  [ok] source_path is already a bucket key — skipping upload.")
        counters.already_migrated += 1
        if cleanup_local and apply:
            for path in _canonical_local_paths(document, uploads_dir):
                if path.exists():
                    try:
                        path.unlink()
                        counters.cleaned_locals += 1
                        print(f"  [del] removed local: {path}")
                    except OSError as exc:
                        logger.warning(
                            "  ! failed to remove local %s: %s", path, exc
                        )
        return

    owner_id = _owner_id_of(document)
    if not owner_id:
        print(
            "  [warn] no workspace owner_id in metadata - cannot partition "
            "bucket key. Skipping."
        )
        counters.skipped_no_owner += 1
        return

    source_local = Path(document.source_path)
    viewable_raw = document.viewable_pdf_path
    viewable_local: Path | None = Path(viewable_raw) if viewable_raw else None

    # The viewable PDF may alias the source on PDF uploads (PDF gets
    # used as both); detect that so we don't double-upload the same
    # bytes under the same key.
    same_viewable = (
        viewable_local is not None
        and viewable_local == source_local
    )

    if not apply:
        # Dry-run mode just prints the plan.
        print(f"  [dry-run] would upload source: {source_local}")
        if viewable_local is not None and not same_viewable:
            print(f"  [dry-run] would upload viewable: {viewable_local}")
        print(f"  [dry-run] would rewrite paths to bucket keys under {owner_id}/")
        counters.migrated += 1
        return

    # Upload source.
    print(f"  -> uploading source: {source_local}")
    source_key = _upload_local_to_storage(
        storage=storage,
        owner_id=owner_id,
        document_id=document.document_id,
        local_path=source_local,
    )
    if source_key is None:
        counters.skipped_missing_local += 1
        return
    print(f"    [ok] source -> {source_key}")

    # Upload viewable if it's a distinct file.
    if viewable_local is not None and not same_viewable:
        print(f"  -> uploading viewable: {viewable_local}")
        viewable_key = _upload_local_to_storage(
            storage=storage,
            owner_id=owner_id,
            document_id=document.document_id,
            local_path=viewable_local,
        )
        if viewable_key is None:
            # Viewable missing isn't fatal — DOCX conversion may have
            # failed at ingest time. We just clear the field so the
            # frontend falls through to the download affordance.
            print("    [warn] viewable missing - clearing viewable_pdf_path")
        else:
            print(f"    [ok] viewable -> {viewable_key}")
    elif same_viewable:
        viewable_key = source_key
    else:
        viewable_key = None

    # Persist the rewritten record.
    document.source_path = source_key
    document.viewable_pdf_path = viewable_key
    try:
        store.save_document(document)
        counters.migrated += 1
        print("  [ok] DocumentRecord updated")
    except Exception as exc:
        counters.errors += 1
        logger.error("  ! failed to save record: %s", exc)
        return

    # Optional: remove the local copies now that Supabase has them.
    if cleanup_local:
        for path in (source_local, viewable_local):
            if path is None:
                continue
            if path.exists():
                try:
                    path.unlink()
                    counters.cleaned_locals += 1
                    print(f"  [del] removed local: {path}")
                except OSError as exc:
                    logger.warning(
                        "  ! failed to remove local %s: %s", path, exc
                    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Migrate local document files into Supabase Storage. "
            "Dry-run by default — pass --apply to actually run."
        ),
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Perform the migration (default is dry-run only).",
    )
    parser.add_argument(
        "--cleanup-local",
        action="store_true",
        help=(
            "Delete local source / viewable files after a successful "
            "upload. Only effective with --apply."
        ),
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s: %(message)s",
    )

    settings = get_settings()

    if args.apply:
        # Force the supabase backend regardless of the current runtime env
        # so we can migrate from a VPS that's still serving the local
        # backend. The runtime can flip to supabase after this script
        # finishes successfully.
        os.environ["HELPMATE_FILE_STORAGE_BACKEND"] = "supabase"
        # Rebuild settings so the override sticks for the storage factory.
        settings = get_settings()
        storage = build_file_storage(settings)
        if not isinstance(storage, SupabaseFileStorage):
            print(
                "ERROR: build_file_storage did not return a SupabaseFileStorage. "
                "Check SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY / "
                "HELPMATE_SUPABASE_STORAGE_BUCKET.",
                file=sys.stderr,
            )
            return 1
        print(
            f"Mode: APPLY ({type(storage).__name__}, bucket="
            f"{settings.supabase_storage_bucket})"
        )
    else:
        # Dry-run uses a placeholder so we don't even attempt to connect.
        storage = None  # type: ignore[assignment]
        print("Mode: DRY RUN (no changes will be made)")

    counters = MigrationCounters()
    store = build_api_record_store(settings)
    documents = list(store.list_documents())
    counters.total = len(documents)
    print(f"\nFound {counters.total} document(s) to consider.\n")

    for document in documents:
        try:
            if args.apply:
                migrate_one(
                    document=document,
                    storage=storage,  # type: ignore[arg-type]
                    apply=True,
                    cleanup_local=args.cleanup_local,
                    counters=counters,
                    store=store,
                    uploads_dir=settings.uploads_dir,
                )
            else:
                # Dry-run path doesn't need a real storage client; the
                # helper short-circuits before any upload calls.
                migrate_one(
                    document=document,
                    storage=None,  # type: ignore[arg-type]
                    apply=False,
                    cleanup_local=False,
                    counters=counters,
                    store=store,
                    uploads_dir=settings.uploads_dir,
                )
        except Exception as exc:
            counters.errors += 1
            logger.exception(
                "Unhandled error on %s: %s", document.document_id, exc
            )

    counters.print_summary()
    return 0 if counters.errors == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
