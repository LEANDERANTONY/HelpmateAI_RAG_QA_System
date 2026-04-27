from __future__ import annotations

import shutil
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.cache import AnswerCache
from src.chunking import ChunkSemanticsService, chunk_document
from src.config import Settings, get_settings
from src.generation import AnswerGenerator, EvidenceSelector
from src.ingest import ingest_document
from src.retrieval import ChromaIndexStore, HybridRetriever
from src.sections import build_sections
from src.sections.profiles import enrich_section_profiles
from src.sections.repair import StructureRepairService
from src.schemas import AnswerResult, CacheStatus, DocumentRecord, IndexRecord, RetrievalCandidate, RetrievalResult, RunTraceRecord
from src.topology import DocumentTopologyService, SynopsisSemanticsService
from src.traces import build_run_trace_store


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
        self.run_trace_store = build_run_trace_store(self.settings)

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
                "document_section_role": section.metadata.get("document_section_role", "general"),
                "document_scope_labels": list(section.metadata.get("document_scope_labels", []))
                if isinstance(section.metadata.get("document_scope_labels", []), list)
                else section.metadata.get("document_scope_labels", []),
                "chapter_number": section.metadata.get("chapter_number", ""),
                "chapter_title": section.metadata.get("chapter_title", ""),
                "page_range_start": section.metadata.get("page_range_start"),
                "page_range_end": section.metadata.get("page_range_end"),
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
        sections = enrich_section_profiles(sections)
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
        trace = self._build_run_trace(
            document=document,
            question=question,
            retrieval_result=retrieval_result,
            answer=answer,
        )
        self.run_trace_store.save_trace(trace)
        answer.run_trace_id = trace.trace_id
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
        self.run_trace_store.delete_for_document(document.document_id)
        source_path = Path(document.source_path)
        try:
            if source_path.exists() and source_path.is_file():
                source_path.unlink()
        except Exception:
            pass

    def _trace_expires_at(self, document: DocumentRecord, created_at: datetime) -> str:
        metadata = document.metadata or {}
        expires_at = metadata.get("_workspace_expires_at")
        if expires_at:
            return str(expires_at)
        return (created_at + timedelta(hours=self.settings.workspace_retention_hours)).isoformat()

    @staticmethod
    def _candidate_trace(candidate: RetrievalCandidate, rank: int) -> dict:
        metadata = candidate.metadata or {}
        return {
            "rank": rank,
            "chunk_id": candidate.chunk_id,
            "page_label": metadata.get("page_label", "Document"),
            "section_id": metadata.get("section_id", ""),
            "section_heading": metadata.get("section_heading", ""),
            "chapter_number": metadata.get("chapter_number", ""),
            "chapter_title": metadata.get("chapter_title", ""),
            "section_role": metadata.get("document_section_role", ""),
            "dense_score": candidate.dense_score,
            "lexical_score": candidate.lexical_score,
            "fused_score": candidate.fused_score,
            "rerank_score": candidate.rerank_score,
            "preview": candidate.text[:240].replace("\n", " ").strip(),
        }

    def _build_run_trace(
        self,
        *,
        document: DocumentRecord,
        question: str,
        retrieval_result: RetrievalResult,
        answer: AnswerResult,
    ) -> RunTraceRecord:
        created_at = datetime.now(timezone.utc)
        trace_id = f"trace-{uuid.uuid4().hex}"
        payload = {
            "question": question,
            "document": {
                "document_id": document.document_id,
                "file_name": document.file_name,
                "fingerprint": document.fingerprint,
            },
            "retrieval": {
                "route_used": retrieval_result.route_used,
                "evidence_status": retrieval_result.evidence_status,
                "weak_evidence": retrieval_result.weak_evidence,
                "best_score": retrieval_result.best_score,
                "max_lexical_score": retrieval_result.max_lexical_score,
                "content_overlap_score": retrieval_result.content_overlap_score,
                "retrieval_plan": retrieval_result.retrieval_plan,
                "metadata_filters": retrieval_result.metadata_filters,
                "strategy_notes": retrieval_result.strategy_notes,
                "candidates": [
                    self._candidate_trace(candidate, rank)
                    for rank, candidate in enumerate(retrieval_result.candidates[: self.settings.evidence_selector_top_k], start=1)
                ],
            },
            "answer": {
                "supported": answer.supported,
                "model_name": answer.model_name,
                "citations": list(answer.citations),
                "citation_details": list(answer.citation_details),
                "note": answer.note,
            },
        }
        return RunTraceRecord(
            trace_id=trace_id,
            document_id=document.document_id,
            fingerprint=document.fingerprint,
            question=question,
            created_at=created_at.isoformat(),
            expires_at=self._trace_expires_at(document, created_at),
            retrieval_version=self.settings.retrieval_version,
            generation_version=self.settings.generation_version,
            payload=payload,
        )
