from pathlib import Path

from backend.auth import AuthenticatedUser
from backend.main import (
    WORKSPACE_EXPIRES_AT_KEY,
    WORKSPACE_LAST_ACTIVITY_KEY,
    WORKSPACE_OWNER_KEY,
    _settings,
    _document_owner_id,
    _touch_document_workspace,
)
from backend.store import LocalApiRecordStore, SupabaseApiRecordStore
from src.config import Settings
from src.schemas import DocumentRecord, IndexRecord


def _document_record() -> DocumentRecord:
    return DocumentRecord(
        document_id="doc-1",
        file_name="paper.pdf",
        file_type=".pdf",
        source_path=str(Path("data/uploads/paper.pdf")),
        fingerprint="fingerprint-1",
        char_count=100,
        page_count=2,
        metadata={"document_style": "generic_longform"},
        extracted_text="example",
    )


def test_touch_document_workspace_sets_owner_and_sliding_expiry(monkeypatch, tmp_path):
    monkeypatch.setenv("HELPMATE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("HELPMATE_WORKSPACE_RETENTION_HOURS", "24")
    _settings.cache_clear()

    document = _document_record()
    user = AuthenticatedUser(id="user-123", email="user@example.com")

    touched = _touch_document_workspace(document, user)

    assert _document_owner_id(touched) == "user-123"
    assert WORKSPACE_LAST_ACTIVITY_KEY in touched.metadata
    assert WORKSPACE_EXPIRES_AT_KEY in touched.metadata
    assert touched.metadata[WORKSPACE_EXPIRES_AT_KEY] > touched.metadata[WORKSPACE_LAST_ACTIVITY_KEY]


def test_local_api_record_store_can_list_and_delete_documents(tmp_path):
    settings = Settings(
        data_dir=tmp_path / "data",
        uploads_dir=tmp_path / "data" / "uploads",
        indexes_dir=tmp_path / "data" / "indexes",
        cache_dir=tmp_path / "data" / "cache",
    )
    store = LocalApiRecordStore(settings)

    document = _document_record()
    document.metadata[WORKSPACE_OWNER_KEY] = "user-123"
    index_record = IndexRecord(
        document_id=document.document_id,
        fingerprint=document.fingerprint,
        collection_name="helpmate-doc-1",
        storage_path=str(tmp_path / "data" / "indexes"),
        chunk_count=10,
        section_count=2,
        embedding_model="text-embedding-3-small",
        chunk_size=1200,
        chunk_overlap=180,
        created_at="2026-04-16T00:00:00+00:00",
    )

    store.save_document(document)
    store.save_index(index_record)

    listed = store.list_documents()
    assert len(listed) == 1
    assert listed[0].document_id == document.document_id

    store.delete_index(document.document_id)
    store.delete_document(document.document_id)

    assert store.get_document(document.document_id) is None
    assert store.get_index(document.document_id) is None


class _FakeQuery:
    def __init__(self, table_name: str, client: "_FakeSupabaseClient"):
        self.table_name = table_name
        self.client = client

    def upsert(self, payload, on_conflict=None):
        self.client.upserts.append(
            {
                "table": self.table_name,
                "payload": payload,
                "on_conflict": on_conflict,
            }
        )
        return self

    def execute(self):
        return {"data": []}


class _FakeSupabaseClient:
    def __init__(self):
        self.upserts = []

    def table(self, table_name: str):
        return _FakeQuery(table_name, self)


def test_supabase_document_rows_persist_owner_and_expiry(monkeypatch, tmp_path):
    fake_client = _FakeSupabaseClient()
    monkeypatch.setattr("backend.store.create_supabase_client", lambda url, key: fake_client)

    settings = Settings(
        data_dir=tmp_path / "data",
        uploads_dir=tmp_path / "data" / "uploads",
        indexes_dir=tmp_path / "data" / "indexes",
        cache_dir=tmp_path / "data" / "cache",
        supabase_url="https://example.supabase.co",
        supabase_key="service-role-key",
    )
    store = SupabaseApiRecordStore(settings)

    document = _document_record()
    document.metadata[WORKSPACE_OWNER_KEY] = "8c3111dd-bba2-460f-b254-3730d0e5eb62"
    document.metadata[WORKSPACE_LAST_ACTIVITY_KEY] = "2026-04-16T12:00:00+00:00"
    document.metadata[WORKSPACE_EXPIRES_AT_KEY] = "2026-04-17T12:00:00+00:00"

    store.save_document(document)

    assert len(fake_client.upserts) == 1
    payload = fake_client.upserts[0]["payload"]
    assert payload["user_id"] == document.metadata[WORKSPACE_OWNER_KEY]
    assert payload["last_activity_at"] == document.metadata[WORKSPACE_LAST_ACTIVITY_KEY]
    assert payload["expires_at"] == document.metadata[WORKSPACE_EXPIRES_AT_KEY]
