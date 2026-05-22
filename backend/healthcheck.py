"""Daily health check for the HelpmateAI retention pipeline.

HelpmateAI retention runs on a single background job: the VPS
workspace sweeper (``backend.maintenance``, every 10 minutes), which
deletes expired workspace rows from Supabase *and* clears the matching
files off local disk and the Storage bucket. The sweeper is wrapped in
a Sentry Crons check-in (``helpmate-workspace-sweeper``) — but that
only proves the sweep *ran*, not that cleanup is actually *keeping
up*: a sweeper that fires every cycle but silently fails to delete (a
broken Supabase call, a partial error) still reports an ``ok``
check-in while expired data piles up.

``run_healthcheck`` is the daily backstop. It reads HelpmateAI's
document store + the local upload directory and asserts a couple of
invariants:

  * retention_backlog — few expired-but-undeleted documents remain.
    A backlog means the sweeper is running but not actually clearing
    expired rows.
  * disk_not_filling  — the upload directory hasn't accumulated far
    more files than there are active documents, which would mean the
    sweeper has stopped clearing local disk.

A degraded result is reported to Sentry as an error-level message so a
quietly-broken retention pipeline pages the operator. Run as a CLI
(``python -m backend.healthcheck``) from the VPS crontab once a day;
the run is wrapped in a Sentry Crons check-in (``helpmate-healthcheck``)
so a healthcheck that never runs at all is also caught.

This module NEVER mutates state — it is a read-only assertion over
what the cleanup jobs maintain. It is the HelpmateAI counterpart of
the AI Job Agent's ``cached-jobs-healthcheck``, adapted to the
VPS-crontab CLI shape the other HelpmateAI cron jobs already use.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from backend.observability import _running_under_pytest
from backend.store import WORKSPACE_EXPIRES_AT_KEY, build_api_record_store
from src.config import Settings, get_settings


logger = logging.getLogger(__name__)


_CRON_MONITOR_SLUG = "helpmate-healthcheck"

# A handful of expired-but-undeleted documents is normal in the gap
# between expiry and the next sweeper tick; a real backlog is not.
MAX_EXPIRED_BACKLOG = 25

# The upload dir holds up to ~2 files per active document (the source
# upload + the LibreOffice PDF rendition). Allow that, plus generous
# slack for in-flight uploads, before calling it disk accumulation.
DISK_SLACK = 100


def _safe_resolve(path: str | Path) -> Path:
    return Path(path).expanduser().resolve(strict=False)


def _parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _document_expires_at(document: Any) -> datetime | None:
    """Workspace expiry for a document, or None when it never expires.

    Mirrors ``backend.maintenance._document_expires_at`` — re-stated
    here so the healthcheck CLI doesn't pull in the maintenance
    module's heavier import chain (pipeline, file storage)."""
    return _parse_timestamp((document.metadata or {}).get(WORKSPACE_EXPIRES_AT_KEY))


def _check(name: str, passed: bool, detail: str) -> dict[str, str]:
    return {"name": name, "status": "pass" if passed else "fail", "detail": detail}


def run_healthcheck(settings: Settings | None = None) -> dict[str, Any]:
    """Evaluate the health of the HelpmateAI retention pipeline.

    Returns a JSON-serializable report::

        {"checked_at": "..", "overall": "ok" | "degraded",
         "checks": [{"name", "status", "detail"}, ...],
         "stats": {...}}

    A degraded ``overall`` is logged at ERROR. A degraded result does
    NOT raise — degradation is data, returned in the report. It DOES
    raise if the document store can't be read at all: "the healthcheck
    could not run" is a different failure from "it ran and found
    problems", and ``main`` maps the two to different Sentry signals.
    """
    settings = settings or get_settings()
    now = datetime.now(timezone.utc)

    # A store-read failure propagates out of here unchanged.
    store = build_api_record_store(settings)
    documents = list(store.list_documents())
    total = len(documents)
    expired = sum(
        1
        for document in documents
        if (exp := _document_expires_at(document)) is not None and exp <= now
    )

    uploads_root = _safe_resolve(settings.uploads_dir)
    upload_files = (
        sum(1 for path in uploads_root.iterdir() if path.is_file())
        if uploads_root.exists()
        else 0
    )
    disk_ceiling = 2 * total + DISK_SLACK

    checks = [
        # retention_backlog — cross-checks the sweeper's Supabase-side cleanup.
        _check(
            "retention_backlog",
            expired <= MAX_EXPIRED_BACKLOG,
            f"{expired} expired documents not yet deleted "
            f"(threshold {MAX_EXPIRED_BACKLOG})",
        ),
        # disk_not_filling — the workspace-sweeper disk signal.
        _check(
            "disk_not_filling",
            upload_files <= disk_ceiling,
            f"{upload_files} files in the upload directory for {total} "
            f"active documents (ceiling {disk_ceiling})",
        ),
    ]
    failed = [c for c in checks if c["status"] != "pass"]
    overall = "degraded" if failed else "ok"
    report: dict[str, Any] = {
        "checked_at": now.isoformat(),
        "overall": overall,
        "checks": checks,
        "stats": {
            "total_documents": total,
            "expired_documents": expired,
            "upload_files": upload_files,
        },
    }
    if failed:
        logger.error(
            "helpmate healthcheck degraded — %s",
            "; ".join(f"{c['name']}: {c['detail']}" for c in failed),
        )
    else:
        logger.info(
            "helpmate healthcheck passed (%s documents, %s expired).",
            total,
            expired,
        )
    return report


def _open_sentry_checkin() -> tuple[Any, str | None]:
    """Open a Sentry Crons check-in for the daily healthcheck.

    Mirrors ``backend.maintenance``: ``python -m backend.healthcheck``
    is a CLI that does not import ``backend.main``, so a dedicated
    Sentry client is initialized here. Returns ``(sentry_sdk,
    check_in_id)`` or ``(None, None)``. NEVER raises; the pytest guard
    keeps test runs from firing check-ins at the production monitor.
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
                # Daily at 04:30 UTC — matches the crontab line in
                # docs/deployment.md.
                "schedule": {"type": "crontab", "value": "30 4 * * *"},
                "timezone": "UTC",
                "checkin_margin": 60,   # minutes late before "missed"
                "max_runtime": 10,      # minutes before "timed out"
                "failure_issue_threshold": 1,
                "recovery_threshold": 1,
            },
        )
        return sentry_sdk, check_in_id
    except Exception as exc:  # noqa: BLE001 — telemetry is best-effort
        logger.warning(
            "sentry crons init failed (%s); healthcheck runs unmonitored.",
            exc,
        )
        return None, None


def _close_sentry_checkin(
    sentry_sdk: Any, check_in_id: str | None, status: str
) -> None:
    """Resolve the healthcheck's Sentry Crons check-in (``ok`` / ``error``)."""
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


def _report_degraded(sentry_sdk: Any, report: dict[str, Any]) -> None:
    """Send a degraded healthcheck result to Sentry as an error issue.

    The cron check-in stays ``ok`` when the healthcheck RAN — a missed
    check-in must mean "the check never ran". A degraded *result* is a
    separate, explicit error-level message so the operator is still
    paged. No-op when Sentry is disabled.
    """
    if sentry_sdk is None:
        return
    failed = [c for c in report.get("checks", []) if c["status"] != "pass"]
    summary = "; ".join(f"{c['name']}: {c['detail']}" for c in failed)
    try:
        sentry_sdk.capture_message(
            f"HelpmateAI healthcheck degraded — {summary}", level="error"
        )
    except Exception as exc:  # noqa: BLE001 — telemetry is best-effort
        logger.warning("sentry capture_message failed (%s); ignoring.", exc)


def main() -> None:
    """CLI entry point — run the daily retention healthcheck under a
    Sentry Crons check-in.

    The VPS crontab runs this once a day. The check-in resolves ``ok``
    when the healthcheck RAN (even if it found degradation) and
    ``error`` only when it could not run at all; a degraded *result* is
    a separate error-level Sentry message. So a missed check-in means
    "the healthcheck never ran", while a degraded message means "it ran
    and the retention pipeline is unhealthy".
    """
    sentry_sdk, check_in_id = _open_sentry_checkin()
    try:
        report = run_healthcheck()
    except Exception:
        _close_sentry_checkin(sentry_sdk, check_in_id, "error")
        raise
    if report["overall"] == "degraded":
        _report_degraded(sentry_sdk, report)
    _close_sentry_checkin(sentry_sdk, check_in_id, "ok")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
