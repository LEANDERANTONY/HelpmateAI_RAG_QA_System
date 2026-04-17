from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from backend.main import WORKSPACE_EXPIRES_AT_KEY
from backend.maintenance import sweep_local_workspace_storage
from backend.store import LocalApiRecordStore
from src.cache.answer_cache import AnswerCache
from src.config import Settings
from src.schemas import AnswerResult, CacheStatus, DocumentRecord, IndexRecord


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        data_dir=tmp_path / "data",
        uploads_dir=tmp_path / "data" / "uploads",
        indexes_dir=tmp_path / "data" / "indexes",
        cache_dir=tmp_path / "data" / "cache",
        state_store_backend="local",
        vector_store_backend="local",
    )


def _document(
    *,
    tmp_path: Path,
    document_id: str,
    fingerprint: str,
    file_name: str,
    expires_at: datetime,
) -> DocumentRecord:
    upload_path = tmp_path / "data" / "uploads" / file_name
    upload_path.parent.mkdir(parents=True, exist_ok=True)
    upload_path.write_text("example", encoding="utf-8")
    return DocumentRecord(
        document_id=document_id,
        file_name=file_name,
        file_type=".pdf",
        source_path=str(upload_path),
        fingerprint=fingerprint,
        char_count=100,
        page_count=1,
        metadata={WORKSPACE_EXPIRES_AT_KEY: expires_at.isoformat()},
        extracted_text="example",
    )


def _index(tmp_path: Path, *, document_id: str, fingerprint: str) -> IndexRecord:
    index_dir = tmp_path / "data" / "indexes" / "v10" / fingerprint
    index_dir.mkdir(parents=True, exist_ok=True)
    (index_dir / "index_meta.json").write_text("{}", encoding="utf-8")
    return IndexRecord(
        document_id=document_id,
        fingerprint=fingerprint,
        collection_name=f"helpmate-{document_id}",
        storage_path=str(index_dir),
        chunk_count=1,
        section_count=1,
        embedding_model="text-embedding-3-small",
        chunk_size=1200,
        chunk_overlap=180,
        created_at=datetime.now(timezone.utc).isoformat(),
    )


def test_sweeper_deletes_expired_workspaces_and_orphans(tmp_path: Path):
    settings = _settings(tmp_path)
    settings.ensure_dirs()
    store = LocalApiRecordStore(settings)
    now = datetime.now(timezone.utc)

    expired_document = _document(
        tmp_path=tmp_path,
        document_id="doc-expired",
        fingerprint="fingerprint-expired",
        file_name="expired.pdf",
        expires_at=now - timedelta(hours=1),
    )
    active_document = _document(
        tmp_path=tmp_path,
        document_id="doc-active",
        fingerprint="fingerprint-active",
        file_name="active.pdf",
        expires_at=now + timedelta(hours=8),
    )
    expired_index = _index(tmp_path, document_id="doc-expired", fingerprint="fingerprint-expired")
    active_index = _index(tmp_path, document_id="doc-active", fingerprint="fingerprint-active")

    store.save_document(expired_document)
    store.save_document(active_document)
    store.save_index(expired_index)
    store.save_index(active_index)

    orphan_upload = settings.uploads_dir / "orphan.pdf"
    orphan_upload.write_text("orphan", encoding="utf-8")
    orphan_index_dir = settings.indexes_dir / settings.index_schema_version / "orphan-fingerprint"
    orphan_index_dir.mkdir(parents=True, exist_ok=True)
    (orphan_index_dir / "index_meta.json").write_text("{}", encoding="utf-8")
    cache = AnswerCache(settings.cache_dir)
    active_cache_key = cache.build_key("fingerprint-active", "What is active?", "v1", "v1", "gpt")
    expired_cache_key = cache.build_key("fingerprint-expired", "What is expired?", "v1", "v1", "gpt")
    orphan_cache_key = cache.build_key("fingerprint-orphan", "What is orphaned?", "v1", "v1", "gpt")
    answer = AnswerResult(
        question="What is covered?",
        answer="Coverage is limited to the cited clauses.",
        citations=["Page 2"],
        evidence=[],
        supported=True,
        cache_status=CacheStatus(index_reused=False, answer_cache_hit=False),
        model_name="gpt",
    )
    cache.set(active_cache_key, answer, fingerprint="fingerprint-active", document_id="doc-active")
    cache.set(expired_cache_key, answer, fingerprint="fingerprint-expired", document_id="doc-expired")
    cache.set(orphan_cache_key, answer, fingerprint="fingerprint-orphan", document_id="doc-orphan")
    legacy_cache_path = settings.cache_dir / "legacy-cache.json"
    legacy_cache_path.write_text("{}", encoding="utf-8")

    summary = sweep_local_workspace_storage(settings)

    assert summary.expired_workspaces_deleted == 1
    assert summary.orphan_uploads_deleted == 1
    assert summary.orphan_index_dirs_deleted == 1
    assert summary.orphan_cache_files_deleted == 2

    assert store.get_document("doc-expired") is None
    assert store.get_index("doc-expired") is None
    assert store.get_document("doc-active") is not None
    assert store.get_index("doc-active") is not None

    assert not (settings.uploads_dir / "expired.pdf").exists()
    assert (settings.uploads_dir / "active.pdf").exists()
    assert not orphan_upload.exists()

    assert not (settings.indexes_dir / settings.index_schema_version / "fingerprint-expired").exists()
    assert (settings.indexes_dir / settings.index_schema_version / "fingerprint-active").exists()
    assert not orphan_index_dir.exists()
    assert not (settings.cache_dir / f"{expired_cache_key}.json").exists()
    assert not (settings.cache_dir / f"{orphan_cache_key}.json").exists()
    assert not legacy_cache_path.exists()
    assert (settings.cache_dir / f"{active_cache_key}.json").exists()
