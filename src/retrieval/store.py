from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from src.schemas import ChunkRecord, IndexRecord, SectionRecord


class ChromaIndexStore:
    def __init__(self, root_dir: str | Path, embedding_model: str, api_key: str | None = None):
        self.root_dir = Path(root_dir)
        self.root_dir.mkdir(parents=True, exist_ok=True)
        self.embedding_model = embedding_model
        self.api_key = api_key

    def _embedding_function(self):
        if not self.api_key:
            return None
        from chromadb.utils.embedding_functions import OpenAIEmbeddingFunction

        return OpenAIEmbeddingFunction(api_key=self.api_key, model_name=self.embedding_model)

    @staticmethod
    def _client_settings():
        from chromadb.config import Settings as ChromaSettings

        return ChromaSettings(anonymized_telemetry=False)

    def _index_dir(self, fingerprint: str) -> Path:
        return self.root_dir / fingerprint

    def _meta_path(self, fingerprint: str) -> Path:
        return self._index_dir(fingerprint) / "index_meta.json"

    def _chunks_path(self, fingerprint: str) -> Path:
        return self._index_dir(fingerprint) / "chunks.json"

    def _sections_path(self, fingerprint: str) -> Path:
        return self._index_dir(fingerprint) / "sections.json"

    def load_index_record(self, fingerprint: str) -> IndexRecord | None:
        meta_path = self._meta_path(fingerprint)
        if not meta_path.exists():
            return None
        payload = json.loads(meta_path.read_text(encoding="utf-8"))
        payload.setdefault("section_count", 0)
        return IndexRecord(**payload)

    def load_chunks(self, fingerprint: str) -> list[ChunkRecord]:
        chunks_path = self._chunks_path(fingerprint)
        if not chunks_path.exists():
            return []
        payload = json.loads(chunks_path.read_text(encoding="utf-8"))
        return [ChunkRecord(**item) for item in payload]

    def load_sections(self, fingerprint: str) -> list[SectionRecord]:
        sections_path = self._sections_path(fingerprint)
        if not sections_path.exists():
            return []
        payload = json.loads(sections_path.read_text(encoding="utf-8"))
        return [SectionRecord(**item) for item in payload]

    def get_or_create_index(
        self,
        fingerprint: str,
        document_id: str,
        chunks: list[ChunkRecord],
        sections: list[SectionRecord],
        embedding_model: str,
        chunk_size: int,
        chunk_overlap: int,
    ) -> IndexRecord:
        existing = self.load_index_record(fingerprint)
        if existing is not None and self._chunks_path(fingerprint).exists() and self._sections_path(fingerprint).exists():
            existing.reused = True
            return existing

        index_dir = self._index_dir(fingerprint)
        index_dir.mkdir(parents=True, exist_ok=True)
        collection_name = f"helpmate-{document_id}"
        import chromadb

        client = chromadb.PersistentClient(
            path=str(index_dir / "chroma"),
            settings=self._client_settings(),
        )
        collection = client.get_or_create_collection(
            name=collection_name,
            embedding_function=self._embedding_function(),
        )
        section_collection = client.get_or_create_collection(
            name=f"{collection_name}-sections",
            embedding_function=self._embedding_function(),
        )
        chroma_metadatas = [self._sanitize_metadata_for_chroma(chunk.metadata) for chunk in chunks]
        collection.upsert(
            ids=[chunk.chunk_id for chunk in chunks],
            documents=[chunk.text for chunk in chunks],
            metadatas=chroma_metadatas,
        )
        section_metadatas = [self._sanitize_metadata_for_chroma(section.metadata) for section in sections]
        section_collection.upsert(
            ids=[section.section_id for section in sections],
            documents=[f"{section.title}\n\n{section.summary}".strip() for section in sections],
            metadatas=section_metadatas,
        )

        index_record = IndexRecord(
            document_id=document_id,
            fingerprint=fingerprint,
            collection_name=collection_name,
            storage_path=str(index_dir / "chroma"),
            chunk_count=len(chunks),
            section_count=len(sections),
            embedding_model=embedding_model,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            created_at=datetime.now(timezone.utc).isoformat(),
            reused=False,
        )
        self._meta_path(fingerprint).write_text(json.dumps(asdict(index_record), indent=2), encoding="utf-8")
        self._chunks_path(fingerprint).write_text(
            json.dumps([chunk.to_dict() for chunk in chunks], indent=2),
            encoding="utf-8",
        )
        self._sections_path(fingerprint).write_text(
            json.dumps([section.to_dict() for section in sections], indent=2),
            encoding="utf-8",
        )
        return index_record

    @staticmethod
    def _sanitize_metadata_for_chroma(metadata: dict) -> dict:
        sanitized: dict = {}
        for key, value in metadata.items():
            if isinstance(value, (str, int, float, bool)) or value is None:
                sanitized[key] = value
            elif isinstance(value, list):
                sanitized[key] = " | ".join(str(item) for item in value)
            else:
                sanitized[key] = str(value)
        return sanitized

    def dense_query(self, fingerprint: str, question: str, top_k: int) -> list[dict]:
        index = self.load_index_record(fingerprint)
        if index is None:
            return []
        import chromadb

        client = chromadb.PersistentClient(
            path=index.storage_path,
            settings=self._client_settings(),
        )
        collection = client.get_collection(
            name=index.collection_name,
            embedding_function=self._embedding_function(),
        )
        results = collection.query(query_texts=[question], n_results=top_k)
        items: list[dict] = []
        for idx, chunk_id in enumerate(results.get("ids", [[]])[0]):
            items.append(
                {
                    "chunk_id": chunk_id,
                    "text": results.get("documents", [[]])[0][idx],
                    "metadata": results.get("metadatas", [[]])[0][idx],
                    "distance": results.get("distances", [[]])[0][idx],
                }
            )
        return items

    def dense_query_sections(self, fingerprint: str, question: str, top_k: int) -> list[dict]:
        index = self.load_index_record(fingerprint)
        if index is None:
            return []
        import chromadb

        client = chromadb.PersistentClient(
            path=index.storage_path,
            settings=self._client_settings(),
        )
        collection = client.get_collection(
            name=f"{index.collection_name}-sections",
            embedding_function=self._embedding_function(),
        )
        results = collection.query(query_texts=[question], n_results=top_k)
        items: list[dict] = []
        for idx, section_id in enumerate(results.get("ids", [[]])[0]):
            items.append(
                {
                    "section_id": section_id,
                    "text": results.get("documents", [[]])[0][idx],
                    "metadata": results.get("metadatas", [[]])[0][idx],
                    "distance": results.get("distances", [[]])[0][idx],
                }
            )
        return items
