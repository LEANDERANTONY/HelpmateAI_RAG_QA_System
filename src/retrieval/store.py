from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from src.schemas import ChunkRecord, IndexRecord, SectionRecord, SectionSynopsisRecord, TopologyEdge


class ChromaIndexStore:
    def __init__(
        self,
        root_dir: str | Path,
        embedding_model: str,
        api_key: str | None = None,
        index_schema_version: str = "v1",
    ):
        self.root_dir = Path(root_dir)
        self.root_dir.mkdir(parents=True, exist_ok=True)
        self.embedding_model = embedding_model
        self.api_key = api_key
        self.index_schema_version = index_schema_version

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
        return self.root_dir / self.index_schema_version / fingerprint

    def _meta_path(self, fingerprint: str) -> Path:
        return self._index_dir(fingerprint) / "index_meta.json"

    def _chunks_path(self, fingerprint: str) -> Path:
        return self._index_dir(fingerprint) / "chunks.json"

    def _sections_path(self, fingerprint: str) -> Path:
        return self._index_dir(fingerprint) / "sections.json"

    def _synopses_path(self, fingerprint: str) -> Path:
        return self._index_dir(fingerprint) / "synopses.json"

    def _topology_path(self, fingerprint: str) -> Path:
        return self._index_dir(fingerprint) / "topology_edges.json"

    @staticmethod
    def _section_search_document(section: SectionRecord) -> str:
        aliases = section.metadata.get("section_aliases", [])
        alias_text = " | ".join(aliases) if isinstance(aliases, list) else str(aliases)
        path_text = " > ".join(section.section_path)
        lead_excerpt = section.text[:700].strip()
        tail_excerpt = section.text[-350:].strip() if len(section.text) > 700 else ""
        parts = [
            section.title,
            path_text,
            alias_text,
            section.summary,
            lead_excerpt,
            tail_excerpt,
        ]
        return "\n\n".join(part for part in parts if part).strip()

    @staticmethod
    def _synopsis_search_document(synopsis: SectionSynopsisRecord) -> str:
        section_path = synopsis.metadata.get("section_path", [])
        path_text = " > ".join(section_path) if isinstance(section_path, list) else str(section_path)
        aliases = synopsis.metadata.get("section_aliases", [])
        alias_text = " | ".join(aliases[:6]) if isinstance(aliases, list) else str(aliases)
        parts = [
            synopsis.title,
            path_text,
            synopsis.region_kind,
            alias_text,
            ", ".join(synopsis.key_terms[:8]),
            synopsis.synopsis,
            " | ".join(synopsis.page_labels),
        ]
        return "\n\n".join(part for part in parts if part).strip()

    def load_index_record(self, fingerprint: str) -> IndexRecord | None:
        meta_path = self._meta_path(fingerprint)
        if not meta_path.exists():
            return None
        payload = json.loads(meta_path.read_text(encoding="utf-8"))
        payload.setdefault("section_count", 0)
        payload.setdefault("synopsis_count", 0)
        payload.setdefault("topology_edge_count", 0)
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

    def load_synopses(self, fingerprint: str) -> list[SectionSynopsisRecord]:
        synopses_path = self._synopses_path(fingerprint)
        if not synopses_path.exists():
            return []
        payload = json.loads(synopses_path.read_text(encoding="utf-8"))
        return [SectionSynopsisRecord(**item) for item in payload]

    def load_topology_edges(self, fingerprint: str) -> list[TopologyEdge]:
        topology_path = self._topology_path(fingerprint)
        if not topology_path.exists():
            return []
        payload = json.loads(topology_path.read_text(encoding="utf-8"))
        return [TopologyEdge(**item) for item in payload]

    def get_or_create_index(
        self,
        fingerprint: str,
        document_id: str,
        chunks: list[ChunkRecord],
        sections: list[SectionRecord],
        synopses: list[SectionSynopsisRecord],
        topology_edges: list[TopologyEdge],
        embedding_model: str,
        chunk_size: int,
        chunk_overlap: int,
    ) -> IndexRecord:
        existing = self.load_index_record(fingerprint)
        if (
            existing is not None
            and existing.index_schema_version == self.index_schema_version
            and self._chunks_path(fingerprint).exists()
            and self._sections_path(fingerprint).exists()
            and self._synopses_path(fingerprint).exists()
            and self._topology_path(fingerprint).exists()
        ):
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
        synopsis_collection = client.get_or_create_collection(
            name=f"{collection_name}-synopses",
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
            documents=[self._section_search_document(section) for section in sections],
            metadatas=section_metadatas,
        )
        synopsis_metadatas = [self._sanitize_metadata_for_chroma(synopsis.metadata) for synopsis in synopses]
        synopsis_collection.upsert(
            ids=[synopsis.section_id for synopsis in synopses],
            documents=[self._synopsis_search_document(synopsis) for synopsis in synopses],
            metadatas=synopsis_metadatas,
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
            index_schema_version=self.index_schema_version,
            synopsis_count=len(synopses),
            topology_edge_count=len(topology_edges),
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
        self._synopses_path(fingerprint).write_text(
            json.dumps([synopsis.to_dict() for synopsis in synopses], indent=2),
            encoding="utf-8",
        )
        self._topology_path(fingerprint).write_text(
            json.dumps([edge.to_dict() for edge in topology_edges], indent=2),
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

    def dense_query_synopses(self, fingerprint: str, question: str, top_k: int) -> list[dict]:
        index = self.load_index_record(fingerprint)
        if index is None:
            return []
        import chromadb

        client = chromadb.PersistentClient(
            path=index.storage_path,
            settings=self._client_settings(),
        )
        collection = client.get_collection(
            name=f"{index.collection_name}-synopses",
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
