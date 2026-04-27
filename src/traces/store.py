from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from src.cloud import create_supabase_client, extract_supabase_rows
from src.config import Settings
from src.schemas import RunTraceRecord


def _parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


class LocalRunTraceStore:
    def __init__(self, settings: Settings):
        self.root = settings.data_dir / "api_state" / "run_traces"
        self.root.mkdir(parents=True, exist_ok=True)

    def _trace_path(self, trace_id: str) -> Path:
        return self.root / f"{trace_id}.json"

    def save_trace(self, trace: RunTraceRecord) -> None:
        self._trace_path(trace.trace_id).write_text(json.dumps(trace.to_dict(), indent=2), encoding="utf-8")

    def list_traces(self, document_id: str | None = None) -> list[RunTraceRecord]:
        traces: list[RunTraceRecord] = []
        for path in sorted(self.root.glob("*.json")):
            try:
                trace = RunTraceRecord(**json.loads(path.read_text(encoding="utf-8")))
            except Exception:
                continue
            if document_id is not None and trace.document_id != document_id:
                continue
            traces.append(trace)
        return traces

    def delete_for_document(self, document_id: str) -> int:
        deleted = 0
        for trace in self.list_traces(document_id):
            self._trace_path(trace.trace_id).unlink(missing_ok=True)
            deleted += 1
        return deleted

    def delete_expired(self, now: datetime | None = None) -> int:
        now = now or datetime.now(timezone.utc)
        deleted = 0
        for trace in self.list_traces():
            expires_at = _parse_timestamp(trace.expires_at)
            if expires_at is None or expires_at > now:
                continue
            self._trace_path(trace.trace_id).unlink(missing_ok=True)
            deleted += 1
        return deleted


class SupabaseRunTraceStore:
    def __init__(self, settings: Settings):
        self.client = create_supabase_client(settings.supabase_url, settings.supabase_key)
        self.table_name = settings.supabase_run_traces_table

    def save_trace(self, trace: RunTraceRecord) -> None:
        payload = {
            "trace_id": trace.trace_id,
            "document_id": trace.document_id,
            "fingerprint": trace.fingerprint,
            "question": trace.question,
            "created_at": trace.created_at,
            "expires_at": trace.expires_at,
            "payload": trace.to_dict(),
        }
        self.client.table(self.table_name).upsert(payload, on_conflict="trace_id").execute()

    def list_traces(self, document_id: str | None = None) -> list[RunTraceRecord]:
        query = self.client.table(self.table_name).select("payload")
        if document_id is not None:
            query = query.eq("document_id", document_id)
        response = query.execute()
        rows = extract_supabase_rows(response)
        return [RunTraceRecord(**(row.get("payload") or {})) for row in rows if row.get("payload")]

    def delete_for_document(self, document_id: str) -> int:
        self.client.table(self.table_name).delete().eq("document_id", document_id).execute()
        return 0

    def delete_expired(self, now: datetime | None = None) -> int:
        now = now or datetime.now(timezone.utc)
        self.client.table(self.table_name).delete().lte("expires_at", now.isoformat()).execute()
        return 0


def build_run_trace_store(settings: Settings):
    if settings.uses_supabase_state:
        return SupabaseRunTraceStore(settings)
    return LocalRunTraceStore(settings)
