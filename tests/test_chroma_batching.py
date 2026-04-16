from src.config import Settings
from src.retrieval.store import ChromaIndexStore


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
