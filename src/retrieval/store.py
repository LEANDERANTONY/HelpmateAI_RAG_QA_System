from __future__ import annotations

import json
import shutil
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from src.cloud import create_supabase_client, extract_supabase_rows
from src.config import Settings
from src.schemas import ChunkRecord, IndexRecord, SectionRecord, SectionSynopsisRecord, TopologyEdge


class LocalArtifactStore:
    def __init__(self, root_dir: str | Path, index_schema_version: str):
        self.root_dir = Path(root_dir)
        self.root_dir.mkdir(parents=True, exist_ok=True)
        self.index_schema_version = index_schema_version

    def _index_dir(self, fingerprint: str) -> Path:
        return self.root_dir / self.index_schema_version / fingerprint

    def meta_path(self, fingerprint: str) -> Path:
        return self._index_dir(fingerprint) / "index_meta.json"

    def chunks_path(self, fingerprint: str) -> Path:
        return self._index_dir(fingerprint) / "chunks.json"

    def sections_path(self, fingerprint: str) -> Path:
        return self._index_dir(fingerprint) / "sections.json"

    def synopses_path(self, fingerprint: str) -> Path:
        return self._index_dir(fingerprint) / "synopses.json"

    def topology_path(self, fingerprint: str) -> Path:
        return self._index_dir(fingerprint) / "topology_edges.json"

    def load_bundle(self, fingerprint: str) -> dict | None:
        meta_path = self.meta_path(fingerprint)
        if not meta_path.exists():
            return None
        chunks_path = self.chunks_path(fingerprint)
        sections_path = self.sections_path(fingerprint)
        synopses_path = self.synopses_path(fingerprint)
        topology_path = self.topology_path(fingerprint)
        if not all(path.exists() for path in (chunks_path, sections_path, synopses_path, topology_path)):
            return None
        return {
            "index_record": json.loads(meta_path.read_text(encoding="utf-8")),
            "chunks": json.loads(chunks_path.read_text(encoding="utf-8")),
            "sections": json.loads(sections_path.read_text(encoding="utf-8")),
            "synopses": json.loads(synopses_path.read_text(encoding="utf-8")),
            "topology_edges": json.loads(topology_path.read_text(encoding="utf-8")),
        }

    def save_bundle(
        self,
        fingerprint: str,
        index_record: IndexRecord,
        chunks: list[ChunkRecord],
        sections: list[SectionRecord],
        synopses: list[SectionSynopsisRecord],
        topology_edges: list[TopologyEdge],
    ) -> None:
        index_dir = self._index_dir(fingerprint)
        index_dir.mkdir(parents=True, exist_ok=True)
        self.meta_path(fingerprint).write_text(json.dumps(asdict(index_record), indent=2), encoding="utf-8")
        self.chunks_path(fingerprint).write_text(json.dumps([chunk.to_dict() for chunk in chunks], indent=2), encoding="utf-8")
        self.sections_path(fingerprint).write_text(json.dumps([section.to_dict() for section in sections], indent=2), encoding="utf-8")
        self.synopses_path(fingerprint).write_text(json.dumps([synopsis.to_dict() for synopsis in synopses], indent=2), encoding="utf-8")
        self.topology_path(fingerprint).write_text(
            json.dumps([edge.to_dict() for edge in topology_edges], indent=2),
            encoding="utf-8",
        )

    def delete_bundle(self, fingerprint: str) -> None:
        shutil.rmtree(self._index_dir(fingerprint), ignore_errors=True)


class SupabaseArtifactStore:
    def __init__(self, settings: Settings):
        self.client = create_supabase_client(settings.supabase_url, settings.supabase_key)
        self.table_name = settings.supabase_artifacts_table

    @staticmethod
    def _timestamp() -> str:
        return datetime.now(timezone.utc).isoformat()

    def load_bundle(self, fingerprint: str) -> dict | None:
        response = (
            self.client.table(self.table_name)
            .select("index_record,chunks,sections,synopses,topology_edges")
            .eq("fingerprint", fingerprint)
            .limit(1)
            .execute()
        )
        rows = extract_supabase_rows(response)
        if not rows:
            return None
        row = rows[0]
        return {
            "index_record": row.get("index_record") or {},
            "chunks": row.get("chunks") or [],
            "sections": row.get("sections") or [],
            "synopses": row.get("synopses") or [],
            "topology_edges": row.get("topology_edges") or [],
        }

    def save_bundle(
        self,
        fingerprint: str,
        index_record: IndexRecord,
        chunks: list[ChunkRecord],
        sections: list[SectionRecord],
        synopses: list[SectionSynopsisRecord],
        topology_edges: list[TopologyEdge],
    ) -> None:
        payload = {
            "fingerprint": fingerprint,
            "document_id": index_record.document_id,
            "collection_name": index_record.collection_name,
            "index_record": index_record.to_dict(),
            "chunks": [chunk.to_dict() for chunk in chunks],
            "sections": [section.to_dict() for section in sections],
            "synopses": [synopsis.to_dict() for synopsis in synopses],
            "topology_edges": [edge.to_dict() for edge in topology_edges],
            "updated_at": self._timestamp(),
        }
        self.client.table(self.table_name).upsert(payload, on_conflict="fingerprint").execute()

    def delete_bundle(self, fingerprint: str) -> None:
        self.client.table(self.table_name).delete().eq("fingerprint", fingerprint).execute()


class ChromaIndexStore:
    def __init__(
        self,
        settings: Settings,
    ):
        self.settings = settings
        self.root_dir = Path(settings.indexes_dir)
        self.root_dir.mkdir(parents=True, exist_ok=True)
        self.embedding_model = settings.embedding_model
        self.api_key = settings.openai_api_key
        self.index_schema_version = settings.index_schema_version
        self.artifact_store = (
            SupabaseArtifactStore(settings) if settings.uses_supabase_state else LocalArtifactStore(settings.indexes_dir, settings.index_schema_version)
        )

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

    def _upsert_collection_in_batches(
        self,
        collection,
        *,
        ids: list[str],
        documents: list[str],
        metadatas: list[dict],
    ) -> None:
        if not ids:
            return
        batch_size = max(1, min(self.settings.chroma_upsert_batch_size, 300))
        for start in range(0, len(ids), batch_size):
            end = start + batch_size
            collection.upsert(
                ids=ids[start:end],
                documents=documents[start:end],
                metadatas=metadatas[start:end],
            )

    def _client(self, fingerprint: str | None = None):
        import chromadb

        if self.settings.uses_chroma_http:
            if self.settings.chroma_api_key:
                return chromadb.CloudClient(
                    cloud_host=self.settings.chroma_http_host,
                    cloud_port=self.settings.chroma_http_port,
                    api_key=self.settings.chroma_api_key,
                    tenant=self.settings.chroma_http_tenant,
                    database=self.settings.chroma_http_database,
                )
            return chromadb.HttpClient(
                host=self.settings.chroma_http_host,
                port=self.settings.chroma_http_port,
                ssl=self.settings.chroma_http_ssl,
                headers=self.settings.chroma_http_headers or None,
                settings=self._client_settings(),
                tenant=self.settings.chroma_http_tenant,
                database=self.settings.chroma_http_database,
            )
        if fingerprint is None:
            raise ValueError("Local Chroma storage requires a fingerprint.")
        return chromadb.PersistentClient(
            path=str(self._index_dir(fingerprint) / "chroma"),
            settings=self._client_settings(),
        )

    def delete_index_data(self, fingerprint: str, collection_name: str) -> None:
        if self.settings.uses_chroma_http:
            client = self._client()
            for name in (
                collection_name,
                f"{collection_name}-sections",
                f"{collection_name}-synopses",
            ):
                try:
                    client.delete_collection(name=name)
                except Exception:
                    pass
        self.artifact_store.delete_bundle(fingerprint)

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
        bundle = self.artifact_store.load_bundle(fingerprint)
        if bundle is None:
            return None
        payload = dict(bundle.get("index_record") or {})
        payload.setdefault("section_count", 0)
        payload.setdefault("synopsis_count", 0)
        payload.setdefault("topology_edge_count", 0)
        return IndexRecord(**payload)

    def load_chunks(self, fingerprint: str) -> list[ChunkRecord]:
        bundle = self.artifact_store.load_bundle(fingerprint) or {}
        payload = bundle.get("chunks") or []
        return [ChunkRecord(**item) for item in payload]

    def load_sections(self, fingerprint: str) -> list[SectionRecord]:
        bundle = self.artifact_store.load_bundle(fingerprint) or {}
        payload = bundle.get("sections") or []
        return [SectionRecord(**item) for item in payload]

    def load_synopses(self, fingerprint: str) -> list[SectionSynopsisRecord]:
        bundle = self.artifact_store.load_bundle(fingerprint) or {}
        payload = bundle.get("synopses") or []
        return [SectionSynopsisRecord(**item) for item in payload]

    def load_topology_edges(self, fingerprint: str) -> list[TopologyEdge]:
        bundle = self.artifact_store.load_bundle(fingerprint) or {}
        payload = bundle.get("topology_edges") or []
        return [TopologyEdge(**item) for item in payload]

    def _index_matches_runtime(
        self,
        existing: IndexRecord,
        *,
        embedding_model: str,
        chunk_size: int,
        chunk_overlap: int,
    ) -> bool:
        return (
            existing.index_schema_version == self.index_schema_version
            and existing.embedding_model == embedding_model
            and existing.chunk_size == chunk_size
            and existing.chunk_overlap == chunk_overlap
        )

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
        if existing is not None and self._index_matches_runtime(
            existing,
            embedding_model=embedding_model,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
        ):
            existing.reused = True
            return existing
        if existing is not None:
            self.delete_index_data(fingerprint, existing.collection_name)

        index_dir = self._index_dir(fingerprint)
        if not self.settings.uses_chroma_http:
            index_dir.mkdir(parents=True, exist_ok=True)
        collection_name = f"helpmate-{document_id}"
        client = self._client(fingerprint)
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
        self._upsert_collection_in_batches(
            collection,
            ids=[chunk.chunk_id for chunk in chunks],
            documents=[chunk.text for chunk in chunks],
            metadatas=chroma_metadatas,
        )
        section_metadatas = [self._sanitize_metadata_for_chroma(section.metadata) for section in sections]
        self._upsert_collection_in_batches(
            section_collection,
            ids=[section.section_id for section in sections],
            documents=[self._section_search_document(section) for section in sections],
            metadatas=section_metadatas,
        )
        synopsis_metadatas = [self._sanitize_metadata_for_chroma(synopsis.metadata) for synopsis in synopses]
        self._upsert_collection_in_batches(
            synopsis_collection,
            ids=[synopsis.section_id for synopsis in synopses],
            documents=[self._synopsis_search_document(synopsis) for synopsis in synopses],
            metadatas=synopsis_metadatas,
        )

        index_record = IndexRecord(
            document_id=document_id,
            fingerprint=fingerprint,
            collection_name=collection_name,
            storage_path=(
                f"https://{self.settings.chroma_http_host}"
                if self.settings.uses_chroma_http and self.settings.chroma_http_ssl
                else (
                    f"http://{self.settings.chroma_http_host}:{self.settings.chroma_http_port}"
                    if self.settings.uses_chroma_http
                    else str(index_dir / "chroma")
                )
            ),
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
        self.artifact_store.save_bundle(
            fingerprint=fingerprint,
            index_record=index_record,
            chunks=chunks,
            sections=sections,
            synopses=synopses,
            topology_edges=topology_edges,
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
        client = self._client(None if self.settings.uses_chroma_http else fingerprint)
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
        client = self._client(None if self.settings.uses_chroma_http else fingerprint)
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
        client = self._client(None if self.settings.uses_chroma_http else fingerprint)
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
