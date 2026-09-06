from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from src.cloud import create_supabase_client, extract_supabase_rows
from src.config import Settings
from src.schemas import DocumentRecord, IndexRecord

try:  # pragma: no cover - optional dependency in local fallback mode
    from postgrest.exceptions import APIError
except ImportError:  # pragma: no cover
    APIError = None  # type: ignore[assignment, misc]

logger = logging.getLogger(__name__)

WORKSPACE_OWNER_KEY = "_workspace_owner_user_id"
WORKSPACE_LAST_ACTIVITY_KEY = "_workspace_last_activity_at"
WORKSPACE_EXPIRES_AT_KEY = "_workspace_expires_at"

# Transient transport-layer errors: a momentary DNS/connection/read hiccup
# (e.g. HELPMATE-BACKEND-D's "ConnectError: No address associated with
# hostname"). These are worth a bounded retry — the next attempt usually
# succeeds once the blip clears — rather than crashing an idempotent cron run.
_TRANSIENT_HTTPX_ERRORS: tuple[type[Exception], ...] = (
    httpx.ConnectError,
    httpx.ConnectTimeout,
    httpx.ReadTimeout,
    httpx.RemoteProtocolError,
    httpx.PoolTimeout,
)


def _is_transient_jwt_error(exc: Exception) -> bool:
    """Narrowly match the transient postgrest clock-skew auth glitch.

    HELPMATE-BACKEND-C paged on a momentary "JWT issued at future" APIError:
    a clock-skew blip that self-heals on the next call. We retry ONLY that
    specific message — a real auth misconfig ("invalid JWT", "JWT expired",
    missing authorization, any 4xx) still fails fast so it isn't masked.
    """
    if APIError is None or not isinstance(exc, APIError):
        return False
    message = str(getattr(exc, "message", "") or "").lower()
    return "jwt" in message and "future" in message


def _is_transient_error(exc: Exception) -> bool:
    return isinstance(exc, _TRANSIENT_HTTPX_ERRORS) or _is_transient_jwt_error(exc)


def _execute_with_retry(query: Any, *, attempts: int = 3) -> Any:
    """Run ``query.execute()`` with a bounded retry on transient infra blips.

    Retries only transient network/transport errors (and the narrow
    clock-skew JWT glitch) with exponential backoff (~0.5s, 1s; capped at
    ``attempts`` tries). Real errors — a genuine 4xx, auth misconfig, or a
    sustained outage — re-raise immediately (or after exhaustion) so the
    traceback still reaches Sentry.
    """
    for attempt in range(attempts):
        try:
            return query.execute()
        except Exception as exc:  # noqa: BLE001 - re-raised below unless transient
            if not _is_transient_error(exc) or attempt >= attempts - 1:
                raise
            backoff = 0.5 * (2**attempt)
            logger.warning(
                "Transient Supabase read error (%s: %s); retrying in %.1fs "
                "(attempt %d/%d)",
                type(exc).__name__,
                exc,
                backoff,
                attempt + 1,
                attempts,
            )
            time.sleep(backoff)
    # Unreachable: the final attempt either returns or re-raises above.
    raise RuntimeError("retry loop exited without returning")


def _workspace_row_fields(document: DocumentRecord) -> dict[str, str]:
    metadata = document.metadata or {}
    payload: dict[str, str] = {}
    owner_id = str(metadata.get(WORKSPACE_OWNER_KEY) or "").strip()
    last_activity_at = str(metadata.get(WORKSPACE_LAST_ACTIVITY_KEY) or "").strip()
    expires_at = str(metadata.get(WORKSPACE_EXPIRES_AT_KEY) or "").strip()
    if owner_id:
        payload["user_id"] = owner_id
    if last_activity_at:
        payload["last_activity_at"] = last_activity_at
    if expires_at:
        payload["expires_at"] = expires_at
    return payload


class LocalApiRecordStore:
    def __init__(self, settings: Settings):
        self.root = settings.data_dir / "api_state"
        self.documents_dir = self.root / "documents"
        self.indexes_dir = self.root / "indexes"
        self.documents_dir.mkdir(parents=True, exist_ok=True)
        self.indexes_dir.mkdir(parents=True, exist_ok=True)

    def _document_path(self, document_id: str) -> Path:
        return self.documents_dir / f"{document_id}.json"

    def _index_path(self, document_id: str) -> Path:
        return self.indexes_dir / f"{document_id}.json"

    def save_document(self, document: DocumentRecord) -> None:
        self._document_path(document.document_id).write_text(
            json.dumps(document.to_dict(), indent=2),
            encoding="utf-8",
        )

    def save_index(self, index_record: IndexRecord) -> None:
        self._index_path(index_record.document_id).write_text(
            json.dumps(index_record.to_dict(), indent=2),
            encoding="utf-8",
        )

    def get_document(self, document_id: str) -> DocumentRecord | None:
        path = self._document_path(document_id)
        if not path.exists():
            return None
        return DocumentRecord(**json.loads(path.read_text(encoding="utf-8")))

    def get_index(self, document_id: str) -> IndexRecord | None:
        path = self._index_path(document_id)
        if not path.exists():
            return None
        return IndexRecord(**json.loads(path.read_text(encoding="utf-8")))

    def list_indexes(self) -> list[IndexRecord]:
        indexes: list[IndexRecord] = []
        for path in sorted(self.indexes_dir.glob("*.json")):
            indexes.append(IndexRecord(**json.loads(path.read_text(encoding="utf-8"))))
        return indexes

    def list_documents(self) -> list[DocumentRecord]:
        documents: list[DocumentRecord] = []
        for path in sorted(self.documents_dir.glob("*.json")):
            documents.append(DocumentRecord(**json.loads(path.read_text(encoding="utf-8"))))
        return documents

    def list_documents_for_user(self, user_id: str) -> list[DocumentRecord]:
        return [
            document
            for document in self.list_documents()
            if str((document.metadata or {}).get(WORKSPACE_OWNER_KEY) or "") == user_id
        ]

    def delete_document(self, document_id: str) -> None:
        self._document_path(document_id).unlink(missing_ok=True)

    def delete_index(self, document_id: str) -> None:
        self._index_path(document_id).unlink(missing_ok=True)


class SupabaseApiRecordStore:
    def __init__(self, settings: Settings):
        self.client = create_supabase_client(settings.supabase_url, settings.supabase_key)
        self.documents_table = settings.supabase_documents_table
        self.indexes_table = settings.supabase_indexes_table

    @staticmethod
    def _timestamp() -> str:
        return datetime.now(timezone.utc).isoformat()

    def save_document(self, document: DocumentRecord) -> None:
        payload = {
            "document_id": document.document_id,
            "fingerprint": document.fingerprint,
            "file_name": document.file_name,
            "payload": document.to_dict(),
            "updated_at": self._timestamp(),
        }
        payload.update(_workspace_row_fields(document))
        self.client.table(self.documents_table).upsert(payload, on_conflict="document_id").execute()

    def save_index(self, index_record: IndexRecord) -> None:
        payload = {
            "document_id": index_record.document_id,
            "fingerprint": index_record.fingerprint,
            "collection_name": index_record.collection_name,
            "payload": index_record.to_dict(),
            "updated_at": self._timestamp(),
        }
        self.client.table(self.indexes_table).upsert(payload, on_conflict="document_id").execute()

    def get_document(self, document_id: str) -> DocumentRecord | None:
        response = _execute_with_retry(
            self.client.table(self.documents_table)
            .select("payload")
            .eq("document_id", document_id)
            .limit(1)
        )
        rows = extract_supabase_rows(response)
        if not rows:
            return None
        payload = rows[0].get("payload") or {}
        return DocumentRecord(**payload)

    def get_index(self, document_id: str) -> IndexRecord | None:
        response = _execute_with_retry(
            self.client.table(self.indexes_table)
            .select("payload")
            .eq("document_id", document_id)
            .limit(1)
        )
        rows = extract_supabase_rows(response)
        if not rows:
            return None
        payload = rows[0].get("payload") or {}
        return IndexRecord(**payload)

    def list_indexes(self) -> list[IndexRecord]:
        response = _execute_with_retry(self.client.table(self.indexes_table).select("payload"))
        rows = extract_supabase_rows(response)
        return [IndexRecord(**(row.get("payload") or {})) for row in rows if row.get("payload")]

    def list_documents(self) -> list[DocumentRecord]:
        response = _execute_with_retry(self.client.table(self.documents_table).select("payload"))
        rows = extract_supabase_rows(response)
        return [DocumentRecord(**(row.get("payload") or {})) for row in rows if row.get("payload")]

    def list_documents_for_user(self, user_id: str) -> list[DocumentRecord]:
        # Scoped read: uses helpmate_documents_user_id_idx instead of
        # deserializing every user's document payload on every workspace read
        # (H3). The backend runs as service-role (RLS bypassed), so this
        # explicit .eq is what actually constrains the query to one user.
        response = _execute_with_retry(
            self.client.table(self.documents_table)
            .select("payload")
            .eq("user_id", user_id)
        )
        rows = extract_supabase_rows(response)
        return [DocumentRecord(**(row.get("payload") or {})) for row in rows if row.get("payload")]

    def delete_document(self, document_id: str) -> None:
        self.client.table(self.documents_table).delete().eq("document_id", document_id).execute()

    def delete_index(self, document_id: str) -> None:
        self.client.table(self.indexes_table).delete().eq("document_id", document_id).execute()


def build_api_record_store(settings: Settings):
    if settings.uses_supabase_state:
        return SupabaseApiRecordStore(settings)
    return LocalApiRecordStore(settings)
