from __future__ import annotations

from collections import defaultdict
import re

from src.config import Settings
from src.query_analysis import QueryAnalyzer
from src.schemas import ChunkRecord, RetrievalCandidate, RetrievalResult
from src.retrieval.query_rewriter import QueryRewriter
from src.retrieval.reranker import Reranker
from src.retrieval.store import ChromaIndexStore


class HybridRetriever:
    def __init__(self, store: ChromaIndexStore, settings: Settings):
        self.store = store
        self.settings = settings
        self.reranker = Reranker(settings) if settings.reranker_enabled else None
        self.query_rewriter = QueryRewriter(settings)
        self.query_analyzer = QueryAnalyzer()

    @staticmethod
    def _extract_metadata_filters(question: str) -> dict[str, list[str]]:
        lowered = question.lower()
        page_numbers = re.findall(r"\bpage(?:s)?\s+(\d+)\b", lowered)
        section_terms = re.findall(r'"([^"]+)"', question)
        clause_terms = re.findall(r"\b\d+(?:\.\d+)+\b", question)
        return {
            "page_labels": [f"Page {page}" for page in page_numbers],
            "section_terms": section_terms,
            "clause_terms": clause_terms,
        }

    @staticmethod
    def _apply_metadata_filters(chunks: list[ChunkRecord], metadata_filters: dict[str, list[str]]) -> list[ChunkRecord]:
        filtered = chunks
        page_labels = metadata_filters.get("page_labels") or []
        if page_labels:
            filtered = [chunk for chunk in filtered if chunk.metadata.get("page_label") in page_labels]
        return filtered or chunks

    @staticmethod
    def _rank_dense(items: list[dict]) -> dict[str, float]:
        scores: dict[str, float] = {}
        for item in items:
            distance = float(item.get("distance", 1.0))
            scores[item["chunk_id"]] = 1.0 / (1.0 + max(distance, 0.0))
        return scores

    @staticmethod
    def _rank_lexical(question: str, chunks: list[ChunkRecord], top_k: int) -> dict[str, float]:
        if not chunks:
            return {}
        from sklearn.feature_extraction.text import TfidfVectorizer
        from sklearn.metrics.pairwise import cosine_similarity

        corpus = [chunk.text for chunk in chunks]
        vectorizer = TfidfVectorizer(stop_words="english")
        matrix = vectorizer.fit_transform(corpus + [question])
        query_vector = matrix[-1]
        similarities = cosine_similarity(query_vector, matrix[:-1]).flatten()
        ranked_indices = similarities.argsort()[::-1][:top_k]
        return {
            chunks[index].chunk_id: float(similarities[index])
            for index in ranked_indices
            if similarities[index] > 0
        }

    @staticmethod
    def _keyword_overlap_score(question: str, text: str) -> float:
        question_terms = {
            token
            for token in re.findall(r"[A-Za-z0-9]+", question.lower())
            if len(token) > 3
        }
        if not question_terms:
            return 0.0
        text_terms = set(re.findall(r"[A-Za-z0-9]+", text.lower()))
        overlap = len(question_terms & text_terms)
        return overlap / max(len(question_terms), 1)

    @staticmethod
    def _section_heading_score(question: str, heading: str) -> float:
        if not heading:
            return 0.0
        question_terms = {
            token
            for token in re.findall(r"[A-Za-z0-9]+", question.lower())
            if len(token) > 3
        }
        heading_terms = set(re.findall(r"[A-Za-z0-9]+", heading.lower()))
        if not question_terms or not heading_terms:
            return 0.0
        return len(question_terms & heading_terms) / max(len(question_terms), 1)

    @staticmethod
    def _section_path_score(question: str, section_path: list[str] | str) -> float:
        if isinstance(section_path, str):
            values = [section_path]
        else:
            values = section_path
        joined = " ".join(values).lower()
        if not joined:
            return 0.0
        question_terms = {
            token for token in re.findall(r"[A-Za-z0-9]+", question.lower()) if len(token) > 3
        }
        section_terms = set(re.findall(r"[A-Za-z0-9]+", joined))
        if not question_terms or not section_terms:
            return 0.0
        return len(question_terms & section_terms) / max(len(question_terms), 1)

    @staticmethod
    def _content_type_score(content_type: str, preferred_content_types: list[str]) -> float:
        return 1.0 if content_type and content_type in preferred_content_types else 0.0

    @staticmethod
    def _clause_match_score(chunk_clause_ids: list[str] | str, clause_terms: list[str]) -> float:
        if isinstance(chunk_clause_ids, str):
            values = [chunk_clause_ids] if chunk_clause_ids else []
        else:
            values = chunk_clause_ids
        if not values or not clause_terms:
            return 0.0
        return 1.0 if any(term in values for term in clause_terms) else 0.0

    @staticmethod
    def _candidate_citation_label(candidate: RetrievalCandidate) -> str:
        source_file = candidate.metadata.get("source_file", "Document")
        page_label = candidate.metadata.get("page_label", "Document")
        return f"{source_file} - {page_label}"

    def _retrieve_once(
        self,
        fingerprint: str,
        question: str,
        dense_top_k: int,
        lexical_top_k: int,
        fused_top_k: int,
        metadata_filters: dict[str, list[str]],
        query_variants: list[str],
    ) -> RetrievalResult:
        query_profile = self.query_analyzer.analyze(question)
        chunks = self.store.load_chunks(fingerprint)
        scoped_chunks = self._apply_metadata_filters(chunks, metadata_filters)
        dense_items = self.store.dense_query(fingerprint, question, top_k=dense_top_k)
        allowed_chunk_ids = {chunk.chunk_id for chunk in scoped_chunks}
        dense_items = [item for item in dense_items if item["chunk_id"] in allowed_chunk_ids]
        dense_scores = self._rank_dense(dense_items)
        lexical_scores = self._rank_lexical(question, scoped_chunks, top_k=lexical_top_k)
        chunk_lookup = {chunk.chunk_id: chunk for chunk in chunks}

        fused_scores: defaultdict[str, float] = defaultdict(float)
        for rank, item in enumerate(sorted(dense_scores.items(), key=lambda pair: pair[1], reverse=True), start=1):
            fused_scores[item[0]] += 1.0 / (60 + rank)
        for rank, item in enumerate(sorted(lexical_scores.items(), key=lambda pair: pair[1], reverse=True), start=1):
            fused_scores[item[0]] += 1.0 / (60 + rank)

        candidates: list[RetrievalCandidate] = []
        for chunk_id, fused_score in sorted(fused_scores.items(), key=lambda pair: pair[1], reverse=True)[:fused_top_k]:
            chunk = chunk_lookup.get(chunk_id)
            if chunk is None:
                continue
            keyword_boost = self._keyword_overlap_score(question, chunk.text) * 0.15
            heading_boost = self._section_heading_score(question, chunk.metadata.get("section_heading", "")) * 0.2
            section_path_boost = self._section_path_score(question, chunk.metadata.get("section_path", [])) * 0.12
            content_type_boost = self._content_type_score(
                str(chunk.metadata.get("content_type", "")),
                query_profile.preferred_content_types,
            ) * 0.18
            clause_boost = self._clause_match_score(chunk.metadata.get("clause_ids", []), query_profile.clause_terms) * 0.2
            final_fused = fused_score + keyword_boost + heading_boost + section_path_boost + content_type_boost + clause_boost
            candidates.append(
                RetrievalCandidate(
                    chunk_id=chunk.chunk_id,
                    text=chunk.text,
                    metadata=chunk.metadata,
                    dense_score=dense_scores.get(chunk_id, 0.0),
                    lexical_score=lexical_scores.get(chunk_id, 0.0),
                    fused_score=final_fused,
                    citation_label=f"{chunk.metadata.get('source_file', 'Document')} - {chunk.metadata.get('page_label', 'Document')}",
                )
            )

        if self.reranker is not None and candidates:
            candidates = self.reranker.rerank(question, candidates, top_k=self.settings.final_top_k)
        else:
            candidates = candidates[: self.settings.final_top_k]

        if not candidates:
            weak_evidence = True
        else:
            best_score = candidates[0].rerank_score if candidates[0].rerank_score is not None else candidates[0].fused_score
            max_lexical = max((candidate.lexical_score for candidate in candidates), default=0.0)
            weak_evidence = bool(best_score < self.settings.weak_evidence_score_threshold or max_lexical < self.settings.lexical_hit_threshold)

        return RetrievalResult(
            question=question,
            candidates=candidates,
            cache_hit=False,
            retrieval_version=self.settings.retrieval_version,
            query_used=question,
            query_variants=query_variants,
            metadata_filters=metadata_filters,
            strategy_notes=[],
            weak_evidence=weak_evidence,
        )

    @staticmethod
    def _select_better_result(current: RetrievalResult, challenger: RetrievalResult) -> RetrievalResult:
        def score(result: RetrievalResult) -> float:
            if not result.candidates:
                return -1.0
            top = result.candidates[0]
            return top.rerank_score if top.rerank_score is not None else top.fused_score

        return challenger if score(challenger) > score(current) else current

    def retrieve(self, fingerprint: str, question: str) -> RetrievalResult:
        metadata_filters = self._extract_metadata_filters(question)
        query_profile = self.query_analyzer.analyze(question)
        initial_variants = [question]
        notes: list[str] = [
            "Initial hybrid retrieval run completed.",
            f"Query classified as {query_profile.query_type}.",
        ]
        if metadata_filters.get("page_labels"):
            notes.append(f"Applied page filter: {', '.join(metadata_filters['page_labels'])}.")
        if query_profile.preferred_content_types:
            notes.append(f"Preferred content types: {', '.join(query_profile.preferred_content_types)}.")

        result = self._retrieve_once(
            fingerprint=fingerprint,
            question=question,
            dense_top_k=self.settings.dense_top_k,
            lexical_top_k=self.settings.lexical_top_k,
            fused_top_k=self.settings.fused_top_k,
            metadata_filters=metadata_filters,
            query_variants=initial_variants,
        )

        if result.weak_evidence and self.settings.query_rewrite_enabled:
            rewritten_queries = self.query_rewriter.rewrite(question)
            for rewritten_query in rewritten_queries[1:]:
                challenger = self._retrieve_once(
                    fingerprint=fingerprint,
                    question=rewritten_query,
                    dense_top_k=self.settings.adaptive_dense_top_k,
                    lexical_top_k=self.settings.adaptive_lexical_top_k,
                    fused_top_k=self.settings.adaptive_fused_top_k,
                    metadata_filters=metadata_filters,
                    query_variants=rewritten_queries,
                )
                notes.append("Adaptive re-retrieval used a rewritten query variant.")
                result = self._select_better_result(result, challenger)
            result.query_variants = rewritten_queries
            if result.query_used != question:
                notes.append(f"Best retrieval came from rewritten query: {result.query_used}")
            else:
                notes.append("Rewritten queries did not outperform the original query.")

        result.strategy_notes = notes

        return result
