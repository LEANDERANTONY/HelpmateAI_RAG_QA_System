from __future__ import annotations

import json
import logging
import shutil
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from backend.file_storage import build_file_storage
from backend.store import (
    WORKSPACE_EXPIRES_AT_KEY,
    build_api_record_store,
)
from src.config import Settings, get_settings
from src.pipeline import HelpmatePipeline
from src.schemas import DocumentRecord
from src.traces import build_run_trace_store


logger = logging.getLogger(__name__)


@dataclass
class SweepSummary:
    expired_workspaces_deleted: int = 0
    orphan_uploads_deleted: int = 0
    orphan_index_dirs_deleted: int = 0
    orphan_cache_files_deleted: int = 0
    expired_run_traces_deleted: int = 0

    def to_dict(self) -> dict[str, int]:
        return asdict(self)


def _parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _document_expires_at(document: DocumentRecord) -> datetime | None:
    return _parse_timestamp((document.metadata or {}).get(WORKSPACE_EXPIRES_AT_KEY))


def _safe_resolve(path: str | Path) -> Path:
    return Path(path).expanduser().resolve(strict=False)


def _delete_storage_files(storage, document: DocumentRecord) -> None:
    """Delete the document's source + viewable PDF via the configured
    FileStorage backend. Local backend already cleaned the files
    during pipeline.delete_workspace, so this is a no-op there.
    Supabase backend needs this to remove the bucket objects.

    Best-effort: a FileStorage outage shouldn't block the metadata
    cleanup. Orphaned bucket objects can be cleaned up on a future
    sweep. Mirrors main.py::_delete_storage_files (which runs during
    the user-driven delete path)."""
    keys: set[str] = set()
    if document.source_path:
        keys.add(document.source_path)
    viewable = getattr(document, "viewable_pdf_path", None)
    if viewable:
        keys.add(viewable)
    for key in keys:
        try:
            storage.delete(key)
        except Exception as exc:
            logger.warning(
                "Sweeper FileStorage.delete failed for %s (%s): %s",
                document.document_id,
                key,
                exc,
            )


def sweep_local_workspace_storage(settings: Settings | None = None) -> SweepSummary:
    settings = settings or get_settings()
    store = build_api_record_store(settings)
    trace_store = build_run_trace_store(settings)
    pipeline = HelpmatePipeline(settings)
    # FileStorage handles bucket-object cleanup on the supabase
    # backend. pipeline.delete_workspace unlinks LOCAL paths but is
    # a no-op for bucket-keyed source_paths, so we route through the
    # storage abstraction explicitly below.
    file_storage = build_file_storage(settings)
    now = datetime.now(timezone.utc)
    summary = SweepSummary()

    active_upload_paths: set[Path] = set()
    active_fingerprints: set[str] = set()

    for document in store.list_documents():
        # expires_at is set by _touch_document_workspace using the
        # owner's tier retention (free 30d / pro 365d / business
        # unbounded → field omitted). The sweep treats missing
        # expires_at as "never delete" — Business-tier records flow
        # through to the active-set whitelist below.
        expires_at = _document_expires_at(document)
        index_record = store.get_index(document.document_id)
        if expires_at is not None and expires_at <= now:
            pipeline.delete_workspace(document, index_record)
            # Storage cleanup AFTER pipeline.delete_workspace because
            # the pipeline path may still need to read local files
            # during teardown (cache invalidation, run-trace cleanup).
            _delete_storage_files(file_storage, document)
            if index_record is not None:
                store.delete_index(document.document_id)
            store.delete_document(document.document_id)
            summary.expired_workspaces_deleted += 1
            continue

        # Whitelist both the original upload AND the viewable PDF rendition
        # (Tier-2 viewer). For PDF uploads they point at the same file; for
        # DOCX uploads viewable_pdf_path is a sibling .pdf that LibreOffice
        # produced at ingest time. Without this the sweeper would treat the
        # rendition as an orphan and delete it on its next pass, breaking
        # the inline viewer for any document older than a few minutes.
        uploads_root = _safe_resolve(settings.uploads_dir)
        candidate_paths = [document.source_path]
        viewable = getattr(document, "viewable_pdf_path", None)
        if viewable and viewable != document.source_path:
            candidate_paths.append(viewable)
        for raw_path in candidate_paths:
            resolved = _safe_resolve(raw_path)
            try:
                resolved.relative_to(uploads_root)
                active_upload_paths.add(resolved)
            except ValueError:
                pass

        if index_record is not None:
            active_fingerprints.add(index_record.fingerprint)

    uploads_root = _safe_resolve(settings.uploads_dir)
    if uploads_root.exists():
        for upload_path in uploads_root.iterdir():
            if not upload_path.is_file():
                continue
            resolved = upload_path.resolve(strict=False)
            if resolved in active_upload_paths:
                continue
            upload_path.unlink(missing_ok=True)
            summary.orphan_uploads_deleted += 1

    indexes_root = _safe_resolve(settings.indexes_dir)
    if indexes_root.exists():
        for schema_dir in indexes_root.iterdir():
            if not schema_dir.is_dir():
                continue
            for fingerprint_dir in schema_dir.iterdir():
                if not fingerprint_dir.is_dir():
                    continue
                if fingerprint_dir.name in active_fingerprints:
                    continue
                shutil.rmtree(fingerprint_dir, ignore_errors=True)
                summary.orphan_index_dirs_deleted += 1

    cache_root = _safe_resolve(settings.cache_dir)
    if cache_root.exists():
        for cache_path in cache_root.glob("*.json"):
            try:
                payload = json.loads(cache_path.read_text(encoding="utf-8"))
            except Exception:
                cache_path.unlink(missing_ok=True)
                summary.orphan_cache_files_deleted += 1
                continue

            fingerprint = payload.get("_cache_fingerprint")
            if fingerprint and fingerprint in active_fingerprints:
                continue

            cache_path.unlink(missing_ok=True)
            summary.orphan_cache_files_deleted += 1

    summary.expired_run_traces_deleted += trace_store.delete_expired(now)
    return summary


def main() -> None:
    summary = sweep_local_workspace_storage()
    print(json.dumps(summary.to_dict(), indent=2))


if __name__ == "__main__":
    main()
