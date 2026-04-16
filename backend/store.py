from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from src.cloud import create_supabase_client, extract_supabase_rows
from src.config import Settings
from src.schemas import DocumentRecord, IndexRecord


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
        response = (
            self.client.table(self.documents_table)
            .select("payload")
            .eq("document_id", document_id)
            .limit(1)
            .execute()
        )
        rows = extract_supabase_rows(response)
        if not rows:
            return None
        payload = rows[0].get("payload") or {}
        return DocumentRecord(**payload)

    def get_index(self, document_id: str) -> IndexRecord | None:
        response = (
            self.client.table(self.indexes_table)
            .select("payload")
            .eq("document_id", document_id)
            .limit(1)
            .execute()
        )
        rows = extract_supabase_rows(response)
        if not rows:
            return None
        payload = rows[0].get("payload") or {}
        return IndexRecord(**payload)


def build_api_record_store(settings: Settings):
    if settings.uses_supabase_state:
        return SupabaseApiRecordStore(settings)
    return LocalApiRecordStore(settings)
