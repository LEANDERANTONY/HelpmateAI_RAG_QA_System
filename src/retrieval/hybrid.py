from __future__ import annotations

from collections import defaultdict
import re

from src.config import Settings
from src.query_analysis import QueryAnalyzer
from src.query_router import QueryRouter, RoutingDecision
from src.retrieval.query_rewriter import QueryRewriter
from src.retrieval.reranker import Reranker
from src.retrieval.section_retriever import SectionRetriever
from src.retrieval.store import ChromaIndexStore
from src.schemas import ChunkRecord, RetrievalCandidate, RetrievalResult, SectionRecord


class HybridRetriever:
    def __init__(self, store: ChromaIndexStore, settings: Settings):
        self.store = store
        self.settings = settings
        self.reranker = Reranker(settings) if settings.reranker_enabled else None
        self.query_rewriter = QueryRewriter(settings)
        self.query_analyzer = QueryAnalyzer()
        self.query_router = QueryRouter(settings)
        self.section_retriever = SectionRetriever()

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
    def _rank_dense(items: list[dict], key_name: str) -> dict[str, float]:
        scores: dict[str, float] = {}
        for item in items:
            distance = float(item.get("distance", 1.0))
            scores[item[key_name]] = 1.0 / (1.0 + max(distance, 0.0))
        return scores

    @staticmethod
    def _rank_lexical(question: str, records: list[tuple[str, str]], top_k: int) -> dict[str, float]:
        if not records:
            return {}
        from sklearn.feature_extraction.text import TfidfVectorizer
        from sklearn.metrics.pairwise import cosine_similarity

        corpus = [text for _, text in records]
        vectorizer = TfidfVectorizer(stop_words="english")
        matrix = vectorizer.fit_transform(corpus + [question])
        query_vector = matrix[-1]
        similarities = cosine_similarity(query_vector, matrix[:-1]).flatten()
        ranked_indices = similarities.argsort()[::-1][:top_k]
        return {
            records[index][0]: float(similarities[index])
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
        values = [section_path] if isinstance(section_path, str) else section_path
        joined = " ".join(values).lower()
        if not joined:
            return 0.0
        question_terms = {token for token in re.findall(r"[A-Za-z0-9]+", question.lower()) if len(token) > 3}
        section_terms = set(re.findall(r"[A-Za-z0-9]+", joined))
        if not question_terms or not section_terms:
            return 0.0
        return len(question_terms & section_terms) / max(len(question_terms), 1)

    @staticmethod
    def _content_type_score(content_type: str, preferred_content_types: list[str]) -> float:
        return 1.0 if content_type and content_type in preferred_content_types else 0.0

    @staticmethod
    def _section_kind_score(question: str, section_kind: str) -> float:
        lowered = question.lower()
        section_kind = (section_kind or "").lower()
        if not section_kind:
            return 0.0

        if any(term in lowered for term in ("main focus", "main aim", "research objectives", "primary topic")):
            return 1.0 if section_kind in {"overview", "abstract", "introduction", "background"} else 0.0
        if any(term in lowered for term in ("future work", "next steps", "future directions")):
            return 1.0 if section_kind in {"future work", "future directions", "conclusion", "conclusions", "discussion"} else 0.0
        if any(term in lowered for term in ("challenge", "limitations", "clinical adoption", "argue")):
            return 1.0 if section_kind in {"discussion", "conclusion", "conclusions", "limitations", "results"} else 0.0
        if any(term in lowered for term in ("baseline", "auc", "accuracy", "results", "reduced it to")):
            return 1.0 if section_kind in {"results", "discussion", "conclusion", "conclusions"} else 0.0
        return 0.0

    @staticmethod
    def _document_style_score(question: str, document_style: str, route_hint: str | None = None) -> float:
        lowered = question.lower()
        style = (document_style or "").lower()
        if not style:
            return 0.0

        if style == "policy_document":
            if any(term in lowered for term in ("clause", "page ", "waiting period", "grace period", "premium", "cashless", "network provider", "sum insured")):
                return 0.5
            return 0.1 if route_hint == "chunk_first" else 0.0

        if style in {"research_paper", "thesis_document"}:
            if any(term in lowered for term in ("main focus", "main aim", "research objectives", "future work", "challenge", "conclusion", "what does the paper say", "what did the thesis conclude")):
                return 0.18
            if any(term in lowered for term in ("auc", "accuracy", "split", "how many", "architecture")):
                return 0.08
            return 0.04 if route_hint == "section_first" else 0.0

        return 0.0

    @staticmethod
    def _clause_match_score(chunk_clause_ids: list[str] | str, clause_terms: list[str]) -> float:
        values = [chunk_clause_ids] if isinstance(chunk_clause_ids, str) and chunk_clause_ids else chunk_clause_ids
        if not values or not clause_terms:
            return 0.0
        return 1.0 if any(term in values for term in clause_terms) else 0.0

    def _score_chunk_candidate(
        self,
        question: str,
        chunk: ChunkRecord,
        dense_scores: dict[str, float],
        lexical_scores: dict[str, float],
        fused_score: float,
        preferred_content_types: list[str],
        clause_terms: list[str],
        section_ids: set[str] | None = None,
    ) -> RetrievalCandidate:
        keyword_boost = self._keyword_overlap_score(question, chunk.text) * 0.15
        heading_boost = self._section_heading_score(question, chunk.metadata.get("section_heading", "")) * 0.2
        section_path_boost = self._section_path_score(question, chunk.metadata.get("section_path", [])) * 0.12
        content_type_boost = self._content_type_score(
            str(chunk.metadata.get("content_type", "")),
            preferred_content_types,
        ) * 0.18
        clause_boost = self._clause_match_score(chunk.metadata.get("clause_ids", []), clause_terms) * 0.2
        section_boost = 0.16 if section_ids and chunk.metadata.get("section_id") in section_ids else 0.0
        section_kind_boost = self._section_kind_score(question, str(chunk.metadata.get("section_kind", ""))) * 0.18
        document_style_boost = self._document_style_score(
            question,
            str(chunk.metadata.get("document_style", "")),
            route_hint="chunk_first" if not section_ids else "section_first",
        ) * 0.05
        final_fused = fused_score + keyword_boost + heading_boost + section_path_boost + content_type_boost + clause_boost + section_boost + section_kind_boost + document_style_boost
        return RetrievalCandidate(
            chunk_id=chunk.chunk_id,
            text=chunk.text,
            metadata=chunk.metadata,
            dense_score=dense_scores.get(chunk.chunk_id, 0.0),
            lexical_score=lexical_scores.get(chunk.chunk_id, 0.0),
            fused_score=final_fused,
            citation_label=f"{chunk.metadata.get('source_file', 'Document')} - {chunk.metadata.get('page_label', 'Document')}",
        )

    def _finalize_candidates(self, question: str, candidates: list[RetrievalCandidate]) -> list[RetrievalCandidate]:
        ranked = sorted(candidates, key=lambda candidate: candidate.fused_score, reverse=True)
        if self.reranker is not None and ranked:
            return self.reranker.rerank(question, ranked, top_k=self.settings.final_top_k)
        return ranked[: self.settings.final_top_k]

    def _chunk_first_candidates(
        self,
        fingerprint: str,
        question: str,
        metadata_filters: dict[str, list[str]],
        preferred_content_types: list[str],
        clause_terms: list[str],
        dense_top_k: int,
        lexical_top_k: int,
        fused_top_k: int,
        section_ids: set[str] | None = None,
    ) -> list[RetrievalCandidate]:
        chunks = self.store.load_chunks(fingerprint)
        scoped_chunks = self._apply_metadata_filters(chunks, metadata_filters)
        if section_ids:
            scoped_chunks = [chunk for chunk in scoped_chunks if chunk.metadata.get("section_id") in section_ids] or scoped_chunks

        dense_items = self.store.dense_query(fingerprint, question, top_k=dense_top_k)
        allowed_chunk_ids = {chunk.chunk_id for chunk in scoped_chunks}
        dense_items = [item for item in dense_items if item["chunk_id"] in allowed_chunk_ids]
        dense_scores = self._rank_dense(dense_items, "chunk_id")
        lexical_scores = self._rank_lexical(question, [(chunk.chunk_id, chunk.text) for chunk in scoped_chunks], top_k=lexical_top_k)
        chunk_lookup = {chunk.chunk_id: chunk for chunk in scoped_chunks}

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
            candidates.append(
                self._score_chunk_candidate(
                    question,
                    chunk,
                    dense_scores,
                    lexical_scores,
                    fused_score,
                    preferred_content_types,
                    clause_terms,
                    section_ids=section_ids,
                )
            )
        return candidates

    def _section_first_candidates(
        self,
        fingerprint: str,
        question: str,
        metadata_filters: dict[str, list[str]],
        preferred_content_types: list[str],
        clause_terms: list[str],
    ) -> tuple[list[RetrievalCandidate], list[RetrievalCandidate]]:
        sections = self.store.load_sections(fingerprint)
        if not sections:
            return [], []

        section_dense = self.store.dense_query_sections(fingerprint, question, top_k=self.settings.section_dense_top_k)
        dense_scores = self._rank_dense(section_dense, "section_id")
        lexical_scores = self._rank_lexical(
            question,
            [
                (
                    section.section_id,
                    f"{section.title}\n{' > '.join(section.section_path)}\n\n{section.summary}".strip(),
                )
                for section in sections
            ],
            top_k=self.settings.section_lexical_top_k,
        )
        ranked_sections = self.section_retriever.rank(
            question=question,
            sections=sections,
            dense_scores=dense_scores,
            lexical_scores=lexical_scores,
            top_k=self.settings.section_fused_top_k,
        )
        top_section_ids = {candidate.metadata.get("section_id") for candidate in ranked_sections if candidate.metadata.get("section_id")}
        chunk_candidates = self._chunk_first_candidates(
            fingerprint=fingerprint,
            question=question,
            metadata_filters=metadata_filters,
            preferred_content_types=preferred_content_types,
            clause_terms=clause_terms,
            dense_top_k=max(self.settings.dense_top_k, self.settings.final_top_k + self.settings.section_chunk_window),
            lexical_top_k=max(self.settings.lexical_top_k, self.settings.final_top_k + self.settings.section_chunk_window),
            fused_top_k=max(self.settings.fused_top_k, self.settings.final_top_k + self.settings.section_chunk_window),
            section_ids=top_section_ids,
        )
        return ranked_sections, chunk_candidates

    @staticmethod
    def _dedupe_candidates(candidates: list[RetrievalCandidate]) -> list[RetrievalCandidate]:
        seen: set[str] = set()
        deduped: list[RetrievalCandidate] = []
        for candidate in sorted(candidates, key=lambda item: item.fused_score, reverse=True):
            if candidate.chunk_id in seen:
                continue
            seen.add(candidate.chunk_id)
            deduped.append(candidate)
        return deduped

    def _build_result(
        self,
        question: str,
        query_used: str,
        query_variants: list[str],
        metadata_filters: dict[str, list[str]],
        route_used: str,
        strategy_notes: list[str],
        candidates: list[RetrievalCandidate],
    ) -> RetrievalResult:
        final_candidates = self._finalize_candidates(question, self._dedupe_candidates(candidates))
        if not final_candidates:
            weak_evidence = True
        else:
            best_score = final_candidates[0].rerank_score if final_candidates[0].rerank_score is not None else final_candidates[0].fused_score
            max_lexical = max((candidate.lexical_score for candidate in final_candidates), default=0.0)
            weak_evidence = bool(best_score < self.settings.weak_evidence_score_threshold or max_lexical < self.settings.lexical_hit_threshold)
        return RetrievalResult(
            question=question,
            candidates=final_candidates,
            cache_hit=False,
            retrieval_version=self.settings.retrieval_version,
            route_used=route_used,
            query_used=query_used,
            query_variants=query_variants,
            metadata_filters=metadata_filters,
            strategy_notes=strategy_notes,
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

    def _retrieve_once(
        self,
        fingerprint: str,
        question: str,
        metadata_filters: dict[str, list[str]],
        query_variants: list[str],
        routing_decision: RoutingDecision,
    ) -> RetrievalResult:
        query_profile = self.query_analyzer.analyze(question)
        notes = [
            f"Query classified as {query_profile.query_type}.",
            f"Router selected {routing_decision.route} with confidence {routing_decision.confidence:.2f}.",
            *routing_decision.reasons,
        ]
        if metadata_filters.get("page_labels"):
            notes.append(f"Applied page filter: {', '.join(metadata_filters['page_labels'])}.")
        if query_profile.preferred_content_types:
            notes.append(f"Preferred content types: {', '.join(query_profile.preferred_content_types)}.")

        chunk_candidates = self._chunk_first_candidates(
            fingerprint=fingerprint,
            question=question,
            metadata_filters=metadata_filters,
            preferred_content_types=query_profile.preferred_content_types,
            clause_terms=query_profile.clause_terms,
            dense_top_k=self.settings.dense_top_k,
            lexical_top_k=self.settings.lexical_top_k,
            fused_top_k=self.settings.fused_top_k,
        )

        if routing_decision.route == "chunk_first":
            notes.append("Chunk-first retrieval path used for exact grounding.")
            return self._build_result(question, question, query_variants, metadata_filters, "chunk_first", notes, chunk_candidates)

        ranked_sections, section_chunk_candidates = self._section_first_candidates(
            fingerprint=fingerprint,
            question=question,
            metadata_filters=metadata_filters,
            preferred_content_types=query_profile.preferred_content_types,
            clause_terms=query_profile.clause_terms,
        )

        if routing_decision.route == "section_first":
            notes.append("Section-first retrieval path narrowed the search before chunk ranking.")
            return self._build_result(question, question, query_variants, metadata_filters, "section_first", notes, section_chunk_candidates or chunk_candidates)

        notes.append("Both retrieval paths ran and their evidence was merged.")
        merged = chunk_candidates + section_chunk_candidates
        if ranked_sections:
            notes.append(f"Top routed section: {ranked_sections[0].metadata.get('section_heading', ranked_sections[0].chunk_id)}.")
        return self._build_result(question, question, query_variants, metadata_filters, "hybrid_both", notes, merged)

    def retrieve(self, fingerprint: str, question: str) -> RetrievalResult:
        metadata_filters = self._extract_metadata_filters(question)
        query_profile = self.query_analyzer.analyze(question)
        routing_decision = self.query_router.route(question, query_profile)
        result = self._retrieve_once(
            fingerprint=fingerprint,
            question=question,
            metadata_filters=metadata_filters,
            query_variants=[question],
            routing_decision=routing_decision,
        )

        if result.weak_evidence and self.settings.query_rewrite_enabled:
            rewritten_queries = self.query_rewriter.rewrite(question)
            for rewritten_query in rewritten_queries[1:]:
                challenger_profile = self.query_analyzer.analyze(rewritten_query)
                challenger_route = self.query_router.route(rewritten_query, challenger_profile)
                challenger = self._retrieve_once(
                    fingerprint=fingerprint,
                    question=rewritten_query,
                    metadata_filters=metadata_filters,
                    query_variants=rewritten_queries,
                    routing_decision=challenger_route,
                )
                challenger.strategy_notes.append("Adaptive re-retrieval used a rewritten query variant.")
                result = self._select_better_result(result, challenger)
            result.query_variants = rewritten_queries
            if result.query_used != question:
                result.strategy_notes.append(f"Best retrieval came from rewritten query: {result.query_used}")
            else:
                result.strategy_notes.append("Rewritten queries did not outperform the original query.")

        return result
