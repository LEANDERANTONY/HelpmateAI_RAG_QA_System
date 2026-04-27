from pathlib import Path
from datetime import datetime, timezone

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
from src.schemas import RunTraceRecord
from src.traces import LocalRunTraceStore, SupabaseRunTraceStore


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

    def delete(self):
        self.client.deletes.append({"table": self.table_name, "filters": []})
        return self

    def eq(self, key, value):
        if self.client.deletes:
            self.client.deletes[-1]["filters"].append(("eq", key, value))
        return self

    def lte(self, key, value):
        if self.client.deletes:
            self.client.deletes[-1]["filters"].append(("lte", key, value))
        return self

    def execute(self):
        return {"data": []}


class _FakeSupabaseClient:
    def __init__(self):
        self.upserts = []
        self.deletes = []

    def table(self, table_name: str):
        return _FakeQuery(table_name, self)


class _FailingQuery:
    def upsert(self, payload, on_conflict=None):
        return self

    def delete(self):
        return self

    def select(self, *args, **kwargs):
        return self

    def eq(self, key, value):
        return self

    def lte(self, key, value):
        return self

    def execute(self):
        raise RuntimeError("relation does not exist")


class _FailingSupabaseClient:
    def table(self, table_name: str):
        return _FailingQuery()


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


def test_local_run_trace_store_expires_records(tmp_path):
    settings = Settings(
        data_dir=tmp_path / "data",
        uploads_dir=tmp_path / "data" / "uploads",
        indexes_dir=tmp_path / "data" / "indexes",
        cache_dir=tmp_path / "data" / "cache",
    )
    store = LocalRunTraceStore(settings)
    now = "2026-04-17T12:00:00+00:00"
    store.save_trace(
        RunTraceRecord(
            trace_id="trace-1",
            document_id="doc-1",
            fingerprint="fingerprint-1",
            question="What happened?",
            created_at=now,
            expires_at="2026-04-17T11:00:00+00:00",
            retrieval_version="v1",
            generation_version="v1",
            payload={"retrieval": {"candidates": []}},
        )
    )
    store.save_trace(
        RunTraceRecord(
            trace_id="trace-2",
            document_id="doc-1",
            fingerprint="fingerprint-1",
            question="What is active?",
            created_at=now,
            expires_at="2026-04-18T12:00:00+00:00",
            retrieval_version="v1",
            generation_version="v1",
            payload={},
        )
    )

    deleted = store.delete_expired(datetime(2026, 4, 17, 12, 0, tzinfo=timezone.utc))

    assert deleted == 1
    assert [trace.trace_id for trace in store.list_traces("doc-1")] == ["trace-2"]


def test_supabase_run_trace_store_persists_expiry_and_can_delete(monkeypatch, tmp_path):
    fake_client = _FakeSupabaseClient()
    monkeypatch.setattr("src.traces.store.create_supabase_client", lambda url, key: fake_client)

    settings = Settings(
        data_dir=tmp_path / "data",
        uploads_dir=tmp_path / "data" / "uploads",
        indexes_dir=tmp_path / "data" / "indexes",
        cache_dir=tmp_path / "data" / "cache",
        supabase_url="https://example.supabase.co",
        supabase_key="service-role-key",
        supabase_run_traces_table="run_traces",
    )
    store = SupabaseRunTraceStore(settings)
    trace = RunTraceRecord(
        trace_id="trace-1",
        document_id="doc-1",
        fingerprint="fingerprint-1",
        question="What happened?",
        created_at="2026-04-17T12:00:00+00:00",
        expires_at="2026-04-18T12:00:00+00:00",
        retrieval_version="v1",
        generation_version="v1",
        payload={"answer": {"supported": True}},
    )

    store.save_trace(trace)
    store.delete_for_document("doc-1")

    payload = fake_client.upserts[0]["payload"]
    assert fake_client.upserts[0]["table"] == "run_traces"
    assert payload["trace_id"] == "trace-1"
    assert payload["expires_at"] == "2026-04-18T12:00:00+00:00"
    assert payload["payload"]["payload"]["answer"]["supported"] is True
    assert fake_client.deletes[0]["filters"] == [("eq", "document_id", "doc-1")]


def test_supabase_run_trace_store_is_best_effort_when_table_is_missing(monkeypatch, tmp_path):
    monkeypatch.setattr("src.traces.store.create_supabase_client", lambda url, key: _FailingSupabaseClient())

    settings = Settings(
        data_dir=tmp_path / "data",
        uploads_dir=tmp_path / "data" / "uploads",
        indexes_dir=tmp_path / "data" / "indexes",
        cache_dir=tmp_path / "data" / "cache",
        supabase_url="https://example.supabase.co",
        supabase_key="service-role-key",
        supabase_run_traces_table="missing_run_traces",
    )
    store = SupabaseRunTraceStore(settings)
    trace = RunTraceRecord(
        trace_id="trace-1",
        document_id="doc-1",
        fingerprint="fingerprint-1",
        question="What happened?",
        created_at="2026-04-17T12:00:00+00:00",
        expires_at="2026-04-18T12:00:00+00:00",
        retrieval_version="v1",
        generation_version="v1",
        payload={},
    )

    store.save_trace(trace)
    assert store.list_traces("doc-1") == []
    assert store.delete_for_document("doc-1") == 0
    assert store.delete_expired(datetime(2026, 4, 17, 12, 0, tzinfo=timezone.utc)) == 0
