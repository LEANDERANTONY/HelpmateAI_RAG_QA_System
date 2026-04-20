from __future__ import annotations

import shutil
from pathlib import Path

from src.cache import AnswerCache
from src.chunking import ChunkSemanticsService, chunk_document
from src.config import Settings, get_settings
from src.generation import AnswerGenerator, EvidenceSelector
from src.ingest import ingest_document
from src.retrieval import ChromaIndexStore, HybridRetriever
from src.sections import build_sections
from src.sections.repair import StructureRepairService
from src.schemas import AnswerResult, CacheStatus, DocumentRecord, IndexRecord, RetrievalResult
from src.topology import DocumentTopologyService, SynopsisSemanticsService


class HelpmatePipeline:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        self.store = ChromaIndexStore(self.settings)
        self.retriever = HybridRetriever(self.store, self.settings)
        self.evidence_selector = EvidenceSelector(self.settings)
        self.generator = AnswerGenerator(self.settings)
        self.answer_cache = AnswerCache(self.settings.cache_dir)
        self.topology_service = DocumentTopologyService()
        self.synopsis_semantics_service = SynopsisSemanticsService(self.settings)
        self.structure_repair_service = StructureRepairService(self.settings)
        self.chunk_semantics_service = ChunkSemanticsService(self.settings)

    def _persist_upload(self, source_path: str | Path) -> Path:
        source = Path(source_path)
        target = self.settings.uploads_dir / source.name
        if source.resolve() != target.resolve():
            shutil.copy2(source, target)
        return target

    def ingest_document(self, file_path: str | Path) -> DocumentRecord:
        persisted_path = self._persist_upload(file_path)
        return ingest_document(persisted_path)

    @staticmethod
    def _apply_section_metadata_to_chunks(chunks, sections) -> None:
        page_lookup: dict[str, dict] = {}
        for section in sections:
            payload = {
                "section_id": section.section_id,
                "section_heading": section.title,
                "section_path": list(section.section_path),
                "section_kind": section.metadata.get("section_kind", section.title.lower()),
                "content_type": section.metadata.get("content_type", "general"),
                "section_aliases": list(section.metadata.get("section_aliases", []))
                if isinstance(section.metadata.get("section_aliases", []), list)
                else section.metadata.get("section_aliases", []),
            }
            for page_label in section.page_labels:
                page_lookup[page_label] = payload

        for chunk in chunks:
            page_label = str(chunk.metadata.get("page_label", chunk.page_label))
            section_payload = page_lookup.get(page_label)
            if not section_payload:
                continue
            chunk.metadata.update(section_payload)

    def build_or_load_index(self, document: DocumentRecord) -> IndexRecord:
        sections = build_sections(document)
        sections, repair_decision = self.structure_repair_service.repair_if_needed(document, sections)
        chunks = chunk_document(document, self.settings.chunk_size, self.settings.chunk_overlap)
        self._apply_section_metadata_to_chunks(chunks, sections)
        chunks = self.chunk_semantics_service.annotate_chunks(document, chunks)
        for section in sections:
            section.metadata.setdefault("structure_confidence", repair_decision.confidence)
            section.metadata.setdefault("structure_repair_reasons", list(repair_decision.reasons))
        synopses, topology_edges = self.topology_service.build(sections)
        synopses = self.synopsis_semantics_service.annotate_synopses(document, sections, synopses)
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
        self.answer_cache.set(
            cache_key,
            answer,
            fingerprint=document.fingerprint,
            document_id=document.document_id,
        )
        return answer

    def delete_workspace(self, document: DocumentRecord, index_record: IndexRecord | None = None) -> None:
        if index_record is not None:
            self.store.delete_index_data(
                fingerprint=index_record.fingerprint,
                collection_name=index_record.collection_name,
            )
        self.answer_cache.delete_for_fingerprint(document.fingerprint)
        source_path = Path(document.source_path)
        try:
            if source_path.exists() and source_path.is_file():
                source_path.unlink()
        except Exception:
            pass
