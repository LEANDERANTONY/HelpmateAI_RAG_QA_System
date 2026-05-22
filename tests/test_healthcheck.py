"""Tests for backend.healthcheck — the daily retention healthcheck.

Mirrors the local-Settings + LocalApiRecordStore pattern from
test_workspace_sweeper.py: a tmp-rooted store, real DocumentRecords,
real upload files.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from backend import healthcheck as hc
from backend.healthcheck import (
    _close_sentry_checkin,
    _open_sentry_checkin,
    main,
    run_healthcheck,
)
from backend.store import WORKSPACE_EXPIRES_AT_KEY, LocalApiRecordStore
from src.config import Settings
from src.schemas import DocumentRecord


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        data_dir=tmp_path / "data",
        uploads_dir=tmp_path / "data" / "uploads",
        indexes_dir=tmp_path / "data" / "indexes",
        cache_dir=tmp_path / "data" / "cache",
        state_store_backend="local",
        vector_store_backend="local",
    )


def _save_document(
    store: LocalApiRecordStore,
    settings: Settings,
    *,
    document_id: str,
    expires_at: datetime,
) -> None:
    upload_path = settings.uploads_dir / f"{document_id}.pdf"
    upload_path.parent.mkdir(parents=True, exist_ok=True)
    upload_path.write_text("example", encoding="utf-8")
    store.save_document(
        DocumentRecord(
            document_id=document_id,
            file_name=f"{document_id}.pdf",
            file_type=".pdf",
            source_path=str(upload_path),
            fingerprint=f"fp-{document_id}",
            char_count=100,
            page_count=1,
            metadata={WORKSPACE_EXPIRES_AT_KEY: expires_at.isoformat()},
            extracted_text="example",
        )
    )


def test_healthcheck_passes_on_clean_state(tmp_path):
    """An empty store + empty upload dir → overall ok, all checks pass."""
    settings = _settings(tmp_path)
    settings.ensure_dirs()

    report = run_healthcheck(settings)

    assert report["overall"] == "ok"
    assert {c["name"] for c in report["checks"]} == {
        "retention_backlog",
        "disk_not_filling",
    }
    assert all(c["status"] == "pass" for c in report["checks"])


def test_healthcheck_degraded_on_expired_backlog(tmp_path, monkeypatch):
    """A backlog of expired-but-undeleted documents — the signature of
    a sweeper that runs but isn't clearing expired rows — fails
    retention_backlog."""
    monkeypatch.setattr(hc, "MAX_EXPIRED_BACKLOG", 2)
    settings = _settings(tmp_path)
    settings.ensure_dirs()
    store = LocalApiRecordStore(settings)
    past = datetime.now(timezone.utc) - timedelta(hours=1)
    for i in range(3):
        _save_document(store, settings, document_id=f"old-{i}", expires_at=past)

    report = run_healthcheck(settings)

    assert report["overall"] == "degraded"
    backlog = next(
        c for c in report["checks"] if c["name"] == "retention_backlog"
    )
    assert backlog["status"] == "fail"
    assert report["stats"]["expired_documents"] == 3


def test_healthcheck_degraded_on_disk_accumulation(tmp_path, monkeypatch):
    """Far more upload files than active documents — the signature of a
    stalled workspace sweeper — fails disk_not_filling."""
    monkeypatch.setattr(hc, "DISK_SLACK", 0)
    settings = _settings(tmp_path)
    settings.ensure_dirs()
    for i in range(5):
        (settings.uploads_dir / f"orphan-{i}.pdf").write_text("x", encoding="utf-8")

    report = run_healthcheck(settings)

    assert report["overall"] == "degraded"
    disk = next(c for c in report["checks"] if c["name"] == "disk_not_filling")
    assert disk["status"] == "fail"
    assert report["stats"]["upload_files"] == 5


def test_open_sentry_checkin_is_noop_under_pytest():
    """The pytest guard keeps a real .env SENTRY_DSN from firing
    test-run check-ins at the production helpmate-healthcheck monitor."""
    assert _open_sentry_checkin() == (None, None)


def test_close_sentry_checkin_handles_none():
    """Closing a check-in that was never opened is a safe no-op."""
    _close_sentry_checkin(None, None, "ok")  # must not raise


def test_main_runs_and_prints_report(monkeypatch, capsys):
    """main() wraps run_healthcheck in a Sentry cron check-in; with
    Sentry off under pytest it still runs and prints the JSON report."""
    monkeypatch.setattr(
        hc,
        "run_healthcheck",
        lambda *args, **kwargs: {
            "checked_at": "2026-05-22T00:00:00+00:00",
            "overall": "ok",
            "checks": [],
            "stats": {"total_documents": 0},
        },
    )
    main()
    printed = json.loads(capsys.readouterr().out)
    assert printed["overall"] == "ok"
