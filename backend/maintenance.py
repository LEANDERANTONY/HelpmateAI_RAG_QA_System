from __future__ import annotations

import json
import logging
import shutil
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from backend.file_storage import build_file_storage
from backend.observability import _running_under_pytest
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
    # Batch-load every index row once instead of a get_index() round-trip per
    # document (the N+1 the sweeper did on each 10-minute tick). The full
    # document scan is retained because the orphan-file whitelist pass below
    # needs every active doc's paths + fingerprint (M8).
    indexes_by_doc = {index.document_id: index for index in store.list_indexes()}

    for document in store.list_documents():
        # expires_at is set by _touch_document_workspace using the
        # owner's tier retention (free 30d / pro 365d / business
        # unbounded → field omitted). The sweep treats missing
        # expires_at as "never delete" — Business-tier records flow
        # through to the active-set whitelist below.
        expires_at = _document_expires_at(document)
        index_record = indexes_by_doc.get(document.document_id)
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


# Sentry Crons monitor slug for the workspace sweeper. Unlike the
# nightly_eval monitor this is NOT env-gated: the sweeper cron runs
# every 10 minutes unconditionally (see docs/deployment.md), so Sentry
# should always expect a heartbeat.
_CRON_MONITOR_SLUG = "helpmate-workspace-sweeper"


def _open_sentry_checkin() -> tuple[Any, str | None]:
    """Open a Sentry Crons check-in for the workspace sweeper.

    Returns ``(sentry_sdk, check_in_id)``, or ``(None, None)`` when
    Sentry isn't configured or shouldn't run. ``python -m
    backend.maintenance`` is a CLI that does not import
    ``backend.main``, so observability is never bootstrapped — a
    dedicated Sentry client is initialized here, exactly as
    ``backend.nightly_eval`` does. Both inits point at the same DSN.

    NEVER raises: a telemetry failure must not stop the sweep. The
    pytest guard keeps the local ``.env`` SENTRY_DSN from firing
    test-run check-ins at the production monitor.
    """
    if _running_under_pytest():
        return None, None
    settings = get_settings()
    if not settings.sentry_dsn:
        return None, None
    try:
        import sentry_sdk

        sentry_sdk.init(
            dsn=settings.sentry_dsn,
            environment=settings.observability_environment,
            release=settings.observability_release or settings.retrieval_version,
            traces_sample_rate=0.0,  # the CLI does no FastAPI request work
            profiles_sample_rate=0.0,
        )
        check_in_id = sentry_sdk.crons.capture_checkin(
            monitor_slug=_CRON_MONITOR_SLUG,
            status="in_progress",
            monitor_config={
                # Every 10 minutes — matches the VPS crontab line in
                # docs/deployment.md.
                "schedule": {"type": "crontab", "value": "*/10 * * * *"},
                "timezone": "UTC",
                "checkin_margin": 5,   # minutes late before "missed"
                "max_runtime": 8,      # minutes before "timed out"
                "failure_issue_threshold": 1,
                "recovery_threshold": 1,
            },
        )
        return sentry_sdk, check_in_id
    except Exception as exc:  # noqa: BLE001 — telemetry is best-effort
        logger.warning(
            "sentry crons init failed (%s); sweeper continues unmonitored.",
            exc,
        )
        return None, None


def _close_sentry_checkin(
    sentry_sdk: Any, check_in_id: str | None, status: str
) -> None:
    """Resolve the sweeper's Sentry Crons check-in (``ok`` / ``error``)."""
    if sentry_sdk is None or check_in_id is None:
        return
    try:
        sentry_sdk.crons.capture_checkin(
            monitor_slug=_CRON_MONITOR_SLUG,
            check_in_id=check_in_id,
            status=status,
        )
        sentry_sdk.flush(timeout=5)
    except Exception as exc:  # noqa: BLE001 — telemetry is best-effort
        logger.warning("sentry crons close failed (%s); ignoring.", exc)


def main() -> None:
    """CLI entry point — run the workspace sweep under a Sentry Crons
    check-in.

    The VPS crontab runs this every 10 minutes
    (``docker exec helpmate-api python -m backend.maintenance``). The
    Sentry ``helpmate-workspace-sweeper`` monitor turns a
    silently-dead sweeper — the job that keeps the VPS disk from
    filling with expired uploads / indexes / cache — into a visible
    missed-check-in alert. A sweep failure resolves the check-in as
    ``error`` and re-raises so the run also exits non-zero.
    """
    sentry_sdk, check_in_id = _open_sentry_checkin()
    try:
        summary = sweep_local_workspace_storage()
    except Exception:
        _close_sentry_checkin(sentry_sdk, check_in_id, "error")
        raise
    _close_sentry_checkin(sentry_sdk, check_in_id, "ok")
    print(json.dumps(summary.to_dict(), indent=2))


if __name__ == "__main__":
    main()
