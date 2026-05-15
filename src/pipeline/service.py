from __future__ import annotations

import shutil
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import logging

from src.cache import AnswerCache
from src.chunking import ChunkSemanticsService, chunk_document
from src.config import Settings, get_settings
from src.generation import AnswerGenerator, EvidenceSelector
from src.ingest import DocxConversionError, convert_docx_to_pdf, ingest_document
from src.landmarks import DocumentLandmarkService
from src.openai_service import CostCollector
from src.retrieval import ChromaIndexStore, HybridRetriever
from src.sections import build_sections
from src.sections.profiles import enrich_section_profiles
from src.sections.repair import StructureRepairService
from src.schemas import AnswerResult, CacheStatus, DocumentRecord, IndexRecord, RetrievalCandidate, RetrievalResult, RunTraceRecord
from src.structure.classifier import DocumentClassifierService
from src.topology import DocumentTopologyService, SynopsisSemanticsService
from src.traces import build_run_trace_store


logger = logging.getLogger(__name__)


class HelpmatePipeline:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        # Per-request CostCollector — wired into the AnswerGenerator and
        # the HybridRetriever (which threads it into RetrievalPlanner →
        # QueryRouter) so every LLM call across the /qa request lands in
        # a single collector. _build_run_trace folds the totals into the
        # RunTraceRecord cost columns and the collector is reset between
        # requests so they don't accumulate forever.
        self.cost_collector = CostCollector()
        self.store = ChromaIndexStore(self.settings)
        self.retriever = HybridRetriever(self.store, self.settings, cost_collector=self.cost_collector)
        self.evidence_selector = EvidenceSelector(self.settings)
        self.generator = AnswerGenerator(self.settings, cost_collector=self.cost_collector)
        self.answer_cache = AnswerCache(self.settings.cache_dir)
        self.topology_service = DocumentTopologyService()
        self.synopsis_semantics_service = SynopsisSemanticsService(self.settings)
        self.structure_repair_service = StructureRepairService(self.settings)
        self.document_classifier_service = DocumentClassifierService(self.settings)
        self.document_landmark_service = DocumentLandmarkService(self.settings)
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
        document = ingest_document(persisted_path)
        # Normalize storage AFTER ingestion (which reads from disk) so the
        # extractor sees the original file at the original name. Once we know
        # the document_id we rename to `{id}{ext}` to dodge cross-user
        # filename collisions in the flat uploads directory, and produce a
        # viewable PDF for DOCX uploads so the in-app viewer can render them.
        self.normalize_upload_paths(document)
        return document

    def normalize_upload_paths(self, document: DocumentRecord) -> DocumentRecord:
        """Rename the source upload to `{document_id}{ext}` and produce a
        viewable PDF rendition when needed.

        Two responsibilities folded into one pass over disk because they
        share the same target filename stem:

        1.  Source rename. Multiple users uploading `policy.pdf` would
            otherwise overwrite each other's file under the shared uploads
            dir. The document_id is a 16-char content fingerprint so it's
            unique enough to avoid clashes while still being deterministic
            on re-ingest of the same content.
        2.  Viewable PDF. PDF uploads are usable as-is — `viewable_pdf_path`
            is just an alias for the renamed source. DOCX uploads get a
            sibling `.pdf` produced by LibreOffice headless; if the
            converter fails or is disabled we leave `viewable_pdf_path` as
            None and the `/file` endpoint falls back to download-only.

        This mutates `document.source_path` and `document.viewable_pdf_path`
        in place and returns the same record for chaining.
        """
        source = Path(document.source_path)
        if not source.exists() or not source.is_file():
            logger.warning(
                "normalize_upload_paths: source missing on disk for %s (%s)",
                document.document_id,
                source,
            )
            return document

        suffix = source.suffix.lower()
        target_dir = source.parent
        renamed = target_dir / f"{document.document_id}{suffix}"

        if source.resolve() != renamed.resolve():
            if renamed.exists():
                # document_id is a content fingerprint, so a pre-existing file
                # at the renamed path is byte-identical to what we just got.
                # Drop the temporary upload and reuse the canonical file —
                # this also avoids racing a concurrent reader who has the
                # canonical file open (matters on Windows, where unlink of
                # an open file fails with permission denied).
                try:
                    source.unlink()
                except OSError:
                    pass
            else:
                source.rename(renamed)
            document.source_path = str(renamed)

        if suffix == ".pdf":
            document.viewable_pdf_path = document.source_path
            return document

        if suffix != ".docx":
            # Other file types reach here only if SUPPORTED_UPLOAD_TYPES is
            # extended without updating this method. Leave viewable empty so
            # the viewer falls back to a download affordance.
            return document

        if not self.settings.docx_pdf_conversion_enabled:
            return document

        viewable = target_dir / f"{document.document_id}.pdf"
        if viewable.exists() and viewable.is_file():
            # A prior ingest of the same DOCX (same fingerprint) already
            # produced the rendition — reuse it instead of paying the
            # LibreOffice subprocess cost again.
            document.viewable_pdf_path = str(viewable)
            return document
        try:
            convert_docx_to_pdf(
                Path(document.source_path),
                viewable,
                soffice_binary=self.settings.docx_pdf_soffice_binary,
                timeout=self.settings.docx_pdf_conversion_timeout,
            )
        except DocxConversionError as exc:
            logger.warning(
                "DOCX viewer rendition failed for %s; viewer will fall back "
                "to download-only (%s)",
                document.document_id,
                exc,
            )
            return document

        document.viewable_pdf_path = str(viewable)
        return document

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
        # Refine the document's genre with an LLM read of the first few pages
        # before landmark / chunk-semantics services consume the label. The
        # keyword classifier in ingest provides the fast initial guess; this
        # service can override it when the model is confident.
        self.document_classifier_service.refine_document_style(document, sections)
        chunks = chunk_document(document, self.settings.chunk_size, self.settings.chunk_overlap)
        self._apply_section_metadata_to_chunks(chunks, sections)
        chunks = self.document_landmark_service.annotate_chunks(document, chunks)
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

    def generate_answer(
        self,
        document_id: str,
        question: str,
        retrieval_result: RetrievalResult,
        *,
        model_override: str | None = None,
    ) -> AnswerResult:
        del document_id
        return self.generator.generate(
            question=question,
            retrieval_result=retrieval_result,
            model_override=model_override,
        )

    def answer_question(
        self,
        document: DocumentRecord,
        index_record: IndexRecord,
        question: str,
        *,
        model_override: str | None = None,
    ) -> AnswerResult:
        # The cache key must include the actual model used — otherwise a
        # free-tier nano answer would be served to a pro-tier user who
        # asked the same question, sandbagging the paid tier's quality.
        # Resolve once and reuse for both the cache key and the
        # downstream generator call.
        active_model = model_override or self.settings.answer_model
        cache_key = self.answer_cache.build_key(
            fingerprint=document.fingerprint,
            question=question,
            retrieval_version=self.settings.retrieval_version,
            generation_version=self.settings.generation_version,
            model_name=active_model,
        )
        cached = self.answer_cache.get(cache_key)
        if cached is not None:
            cached.cache_status.index_reused = index_record.reused
            return cached

        retrieval_result = self.retrieve_evidence(document.document_id, document.fingerprint, question)
        retrieval_result = self.evidence_selector.select(question, retrieval_result)
        answer = self.generate_answer(
            document.document_id,
            question,
            retrieval_result,
            model_override=model_override,
        )
        if not answer.supported and self.retriever.should_recover_after_abstention(question, retrieval_result):
            recovered_retrieval = self.retriever.recover_after_abstention(
                fingerprint=document.fingerprint,
                question=question,
                initial_result=retrieval_result,
            )
            if recovered_retrieval.retrieval_plan.get("abstention_recovery_applied"):
                retrieval_result = self.evidence_selector.select(question, recovered_retrieval)
                answer = self.generate_answer(
            document.document_id,
            question,
            retrieval_result,
            model_override=model_override,
        )
                if answer.supported:
                    verifier_status, verifier_reason = self.generator.verify_support_status(
                        question=question,
                        answer_text=answer.answer,
                        reason_text=answer.note or "",
                        claimed_support_status=answer.support_status,
                        evidence=answer.evidence,
                    )
                    answer.note = (
                        f"{answer.note} Recovery support verifier: {verifier_reason}"
                        if answer.note
                        else f"Recovery support verifier: {verifier_reason}"
                    )
                    if verifier_status == "partial":
                        answer.supported = False
                        answer.support_status = "partial"
                    elif verifier_status != "supported":
                        answer.supported = False
                        answer.support_status = "unsupported"
                        answer.answer = (
                            "Unsupported by the recovered evidence. "
                            "The recovered evidence did not fully support every required fact in the question."
                        )
                        answer.citations = []
                        answer.citation_details = []
        self.retriever.stamp_union_highlight_terms(
            question=question,
            answer=answer.answer,
            candidates=answer.evidence,
        )
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
        # Clean up both the source upload and the viewable PDF rendition if
        # they're distinct files. For PDF uploads they alias the same path so
        # the second unlink is a no-op via the exists() guard.
        paths_to_clean = [document.source_path]
        if document.viewable_pdf_path and document.viewable_pdf_path != document.source_path:
            paths_to_clean.append(document.viewable_pdf_path)
        for raw in paths_to_clean:
            try:
                path = Path(raw)
                if path.exists() and path.is_file():
                    path.unlink()
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
        # Snapshot LLM-call totals from the per-request collector. The
        # collector aggregates calls from the generator (answer +
        # support verifier + support-status verifier) plus the router
        # (LLM fallback when heuristic confidence is below threshold).
        # Per-call breakdown lands in the payload JSON for debugging;
        # the totals populate the dedicated cost columns on the trace
        # store (prompt_tokens / completion_tokens / cost_usd / model_name).
        cost_totals = self.cost_collector.totals()
        llm_call_breakdown = self.cost_collector.to_payload()
        # Reset before the next request so collectors don't accumulate
        # across calls — the pipeline is process-lived but the collector
        # is per-request. We do this AFTER reading totals to make sure
        # the current trace captures everything.
        self.cost_collector.records.clear()

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
                "support_status": answer.support_status,
                "model_name": answer.model_name,
                "citations": list(answer.citations),
                "citation_details": list(answer.citation_details),
                "note": answer.note,
            },
            # Per-call LLM breakdown — kept as a payload field for
            # debugging-grade introspection. The summary numbers (the
            # ones the dashboard joins on) sit on the dedicated columns.
            "llm_calls": llm_call_breakdown,
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
            prompt_tokens=int(cost_totals.get("prompt_tokens", 0) or 0),
            completion_tokens=int(cost_totals.get("completion_tokens", 0) or 0),
            cost_usd=float(cost_totals.get("cost_usd", 0.0) or 0.0),
            model_name=str(cost_totals.get("model_name", "") or ""),
        )
