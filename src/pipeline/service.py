from __future__ import annotations

import shutil
from pathlib import Path

from src.cache import AnswerCache
from src.chunking import chunk_document
from src.config import Settings, get_settings
from src.generation import AnswerGenerator, EvidenceSelector
from src.ingest import ingest_document
from src.retrieval import ChromaIndexStore, HybridRetriever
from src.sections import build_sections
from src.schemas import AnswerResult, CacheStatus, DocumentRecord, IndexRecord, RetrievalResult
from src.topology import DocumentTopologyService


class HelpmatePipeline:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        self.store = ChromaIndexStore(
            self.settings.indexes_dir,
            embedding_model=self.settings.embedding_model,
            api_key=self.settings.openai_api_key,
            index_schema_version=self.settings.index_schema_version,
        )
        self.retriever = HybridRetriever(self.store, self.settings)
        self.evidence_selector = EvidenceSelector(self.settings)
        self.generator = AnswerGenerator(self.settings)
        self.answer_cache = AnswerCache(self.settings.cache_dir)
        self.topology_service = DocumentTopologyService()

    def _persist_upload(self, source_path: str | Path) -> Path:
        source = Path(source_path)
        target = self.settings.uploads_dir / source.name
        if source.resolve() != target.resolve():
            shutil.copy2(source, target)
        return target

    def ingest_document(self, file_path: str | Path) -> DocumentRecord:
        persisted_path = self._persist_upload(file_path)
        return ingest_document(persisted_path)

    def build_or_load_index(self, document: DocumentRecord) -> IndexRecord:
        chunks = chunk_document(document, self.settings.chunk_size, self.settings.chunk_overlap)
        sections = build_sections(document)
        synopses, topology_edges = self.topology_service.build(sections)
        return self.store.get_or_create_index(
            fingerprint=document.fingerprint,
            document_id=document.document_id,
            chunks=chunks,
            sections=sections,
            synopses=synopses,
            topology_edges=topology_edges,
            embedding_model=self.settings.embedding_model,
            chunk_size=self.settings.chunk_size,
            chunk_overlap=self.settings.chunk_overlap,
        )

    def retrieve_evidence(self, document_id: str, fingerprint: str, question: str) -> RetrievalResult:
        del document_id
        return self.retriever.retrieve(fingerprint=fingerprint, question=question)

    def generate_answer(self, document_id: str, question: str, retrieval_result: RetrievalResult) -> AnswerResult:
        del document_id
        return self.generator.generate(question=question, retrieval_result=retrieval_result)

    def answer_question(self, document: DocumentRecord, index_record: IndexRecord, question: str) -> AnswerResult:
        cache_key = self.answer_cache.build_key(
            fingerprint=document.fingerprint,
            question=question,
            retrieval_version=self.settings.retrieval_version,
            generation_version=self.settings.generation_version,
            model_name=self.settings.answer_model,
        )
        cached = self.answer_cache.get(cache_key)
        if cached is not None:
            cached.cache_status.index_reused = index_record.reused
            return cached

        retrieval_result = self.retrieve_evidence(document.document_id, document.fingerprint, question)
        retrieval_result = self.evidence_selector.select(question, retrieval_result)
        answer = self.generate_answer(document.document_id, question, retrieval_result)
        answer.cache_status = CacheStatus(index_reused=index_record.reused, answer_cache_hit=False)
        self.answer_cache.set(cache_key, answer)
        return answer
