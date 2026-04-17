from src.config import Settings
from src.retrieval.store import ChromaIndexStore
from src.schemas import IndexRecord


class _FakeCollection:
    def __init__(self):
        self.calls = []

    def upsert(self, *, ids, documents, metadatas):
        self.calls.append(
            {
                "ids": list(ids),
                "documents": list(documents),
                "metadatas": list(metadatas),
            }
        )


def test_cloud_upserts_are_batched_under_request_limit(tmp_path):
    store = ChromaIndexStore(
        Settings(
            data_dir=tmp_path / "data",
            indexes_dir=tmp_path / "indexes",
            uploads_dir=tmp_path / "uploads",
            cache_dir=tmp_path / "cache",
            chroma_upsert_batch_size=250,
        )
    )
    collection = _FakeCollection()

    ids = [f"id-{index}" for index in range(601)]
    documents = [f"doc-{index}" for index in range(601)]
    metadatas = [{"row": index} for index in range(601)]

    store._upsert_collection_in_batches(
        collection,
        ids=ids,
        documents=documents,
        metadatas=metadatas,
    )

    assert [len(call["ids"]) for call in collection.calls] == [250, 250, 101]
    assert collection.calls[0]["ids"][0] == "id-0"
    assert collection.calls[-1]["ids"][-1] == "id-600"


def test_batch_size_is_capped_at_chroma_request_limit(tmp_path):
    store = ChromaIndexStore(
        Settings(
            data_dir=tmp_path / "data",
            indexes_dir=tmp_path / "indexes",
            uploads_dir=tmp_path / "uploads",
            cache_dir=tmp_path / "cache",
            chroma_upsert_batch_size=500,
        )
    )
    collection = _FakeCollection()

    ids = [f"id-{index}" for index in range(301)]
    documents = [f"doc-{index}" for index in range(301)]
    metadatas = [{"row": index} for index in range(301)]

    store._upsert_collection_in_batches(
        collection,
        ids=ids,
        documents=documents,
        metadatas=metadatas,
    )

    assert [len(call["ids"]) for call in collection.calls] == [300, 1]


def test_index_reuse_requires_matching_chunk_settings(tmp_path):
    store = ChromaIndexStore(
        Settings(
            data_dir=tmp_path / "data",
            indexes_dir=tmp_path / "indexes",
            uploads_dir=tmp_path / "uploads",
            cache_dir=tmp_path / "cache",
        )
    )
    existing = IndexRecord(
        document_id="doc-1",
        fingerprint="fp-1",
        collection_name="helpmate-doc-1",
        storage_path=str(tmp_path / "indexes"),
        chunk_count=10,
        section_count=2,
        embedding_model="text-embedding-3-small",
        chunk_size=1200,
        chunk_overlap=180,
        created_at="2026-01-01T00:00:00+00:00",
        index_schema_version=store.index_schema_version,
    )

    assert store._index_matches_runtime(
        existing,
        embedding_model="text-embedding-3-small",
        chunk_size=1200,
        chunk_overlap=180,
    )
    assert not store._index_matches_runtime(
        existing,
        embedding_model="text-embedding-3-small",
        chunk_size=900,
        chunk_overlap=180,
    )
    assert not store._index_matches_runtime(
        existing,
        embedding_model="text-embedding-3-small",
        chunk_size=1200,
        chunk_overlap=240,
    )
