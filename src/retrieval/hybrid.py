from __future__ import annotations

from collections import defaultdict
import re

from src.config import Settings
from src.query_analysis import QueryAnalyzer
from src.retrieval.planner import RetrievalPlanner
from src.retrieval.reranker import Reranker
from src.retrieval.section_retriever import SectionRetriever
from src.retrieval.store import ChromaIndexStore
from src.retrieval.synopsis_retriever import SynopsisRetriever
from src.schemas import ChunkRecord, RetrievalCandidate, RetrievalPlan, RetrievalResult, SectionSynopsisRecord, TopologyEdge
from src.topology import DocumentTopologyService


class HybridRetriever:
    GENERIC_QUERY_TERMS = {
        "about",
        "answer",
        "answers",
        "document",
        "documents",
        "main",
        "overall",
        "paper",
        "policy",
        "question",
        "report",
        "research",
        "say",
        "summary",
        "summarize",
        "thesis",
        "this",
        "what",
    }
    SUMMARY_SUPPORT_SECTION_KINDS = {
        "overview",
        "abstract",
        "introduction",
        "background",
        "discussion",
        "conclusion",
        "conclusions",
        "future work",
        "future directions",
        "final remarks",
    }
    EARLY_SUMMARY_TERMS = {"about", "aim", "contribution", "contributions", "focus", "objective", "overview", "purpose", "scope", "summary", "topic"}
    FINDINGS_SUMMARY_TERMS = {"finding", "findings", "headline", "result", "results", "performance", "outcome"}
    LATE_SUMMARY_TERMS = {"conclusion", "conclusions", "future", "implication", "implications", "limitation", "limitations", "next", "recommendation", "recommendations"}
    LOW_VALUE_SUMMARY_SECTION_KINDS = {"references", "appendix"}
    OVERVIEW_SECTION_KINDS = {"overview", "abstract", "introduction", "background"}
    FINDINGS_SECTION_KINDS = {"results", "discussion", "evidence"}
    LATE_SECTION_KINDS = {"discussion", "conclusion", "conclusions", "future work", "future directions", "limitations"}
    GENERIC_SECTION_TERMS = {"chapter", "chapter summary", "section", "section summary", "summary"}

    def __init__(self, store: ChromaIndexStore, settings: Settings):
        self.store = store
        self.settings = settings
        self.reranker = Reranker(settings) if settings.reranker_enabled else None
        self.query_analyzer = QueryAnalyzer()
        self.section_retriever = SectionRetriever()
        self.synopsis_retriever = SynopsisRetriever()
        self.topology_service = DocumentTopologyService()
        self.planner = RetrievalPlanner(settings)

    @staticmethod
    def _evidence_score(candidate: RetrievalCandidate) -> float:
        # Cross-encoder reranker outputs are useful for ordering, but their raw
        # score scale is not calibrated against the retrieval thresholds below.
        return candidate.fused_score

    @classmethod
    def _normalize_term_set(cls, terms: set[str]) -> set[str]:
        normalized: set[str] = set()
        for term in terms:
            normalized.add(term)
            if len(term) > 4 and term.endswith("s"):
                normalized.add(term[:-1])
            if len(term) > 5 and term.endswith("ies"):
                normalized.add(term[:-3] + "y")
        return normalized

    @classmethod
    def _significant_question_terms(cls, question: str) -> set[str]:
        return cls._normalize_term_set(
            {
            token
            for token in re.findall(r"[A-Za-z0-9]+", question.lower())
            if len(token) > 3 and token not in cls.GENERIC_QUERY_TERMS
            }
        )

    @classmethod
    def _content_overlap_ratio(cls, question: str, candidates: list[RetrievalCandidate]) -> float:
        significant_terms = cls._significant_question_terms(question)
        if not significant_terms or not candidates:
            return 0.0
        candidate_terms: set[str] = set()
        for candidate in candidates[:3]:
            candidate_terms.update(re.findall(r"[A-Za-z0-9]+", candidate.text.lower()))
            candidate_terms.update(re.findall(r"[A-Za-z0-9]+", str(candidate.metadata.get("section_heading", "")).lower()))
        candidate_terms = cls._normalize_term_set(candidate_terms)
        return len(significant_terms & candidate_terms) / max(len(significant_terms), 1)

    def _assess_evidence_status(
        self,
        question: str,
        candidates: list[RetrievalCandidate],
        query_type: str = "general_lookup",
    ) -> tuple[str, float, float, float]:
        if not candidates:
            return "unsupported", 0.0, 0.0, 0.0
        best_score = self._evidence_score(candidates[0])
        max_lexical = max((candidate.lexical_score for candidate in candidates), default=0.0)
        content_overlap = self._content_overlap_ratio(question, candidates)
        section_kinds = {
            str(candidate.metadata.get("section_kind", "")).lower()
            for candidate in candidates[:4]
            if candidate.metadata.get("section_kind")
        }
        summary_section_present = bool(section_kinds & self.SUMMARY_SUPPORT_SECTION_KINDS)
        if best_score < self.settings.unsupported_evidence_score_threshold and max_lexical < self.settings.unsupported_lexical_hit_threshold:
            return "unsupported", best_score, max_lexical, content_overlap
        if best_score < self.settings.weak_evidence_score_threshold and content_overlap <= self.settings.unsupported_content_overlap_threshold:
            if query_type == "summary_lookup" and summary_section_present:
                return "weak", best_score, max_lexical, content_overlap
            return "unsupported", best_score, max_lexical, content_overlap
        if best_score < self.settings.weak_evidence_score_threshold or max_lexical < self.settings.lexical_hit_threshold:
            return "weak", best_score, max_lexical, content_overlap
        return "strong", best_score, max_lexical, content_overlap

    @staticmethod
    def _clean_section_term(term: str) -> str:
        compact = re.sub(r"\s+", " ", term.strip(" .,:;?!\"'")).strip()
        if not compact:
            return ""
        compact = re.split(
            r"\b(?:what|which|how|why|when|where|tell|summarize|explain|describe)\b",
            compact,
            maxsplit=1,
            flags=re.IGNORECASE,
        )[0].strip(" .,:;?!\"'")
        return compact

    @classmethod
    def _extract_metadata_filters(cls, question: str) -> dict[str, list[str]]:
        lowered = question.lower()
        page_numbers = re.findall(r"\bpage(?:s)?\s+(\d+)\b", lowered)
        section_terms = [cls._clean_section_term(term) for term in re.findall(r'"([^"]+)"', question)]
        section_terms.extend(
            cls._clean_section_term(term)
            for term in re.findall(r"(?:section|chapter)\s+([A-Za-z][A-Za-z0-9 ._-]*)", question, flags=re.IGNORECASE)
        )
        section_terms.extend(
            cls._clean_section_term(term)
            for term in re.findall(
                r"\bin the\s+([A-Za-z][A-Za-z0-9 ._-]*?)\s+(?:what|which|how|why|when|where|tell|summarize|explain|describe|is|are|was|were|does|do|did)\b",
                question,
                flags=re.IGNORECASE,
            )
        )
        section_terms = [term for term in section_terms if term]
        if any(term.lower() not in cls.GENERIC_SECTION_TERMS for term in section_terms):
            section_terms = [term for term in section_terms if term.lower() not in cls.GENERIC_SECTION_TERMS]
        clause_terms = re.findall(r"(?:clause|section)\s+(\d+(?:\.\d+)+)", question, flags=re.IGNORECASE)
        return {
            "page_labels": [f"Page {page}" for page in page_numbers],
            "section_terms": list(dict.fromkeys(section_terms)),
            "clause_terms": clause_terms,
        }

    @staticmethod
    def _apply_metadata_filters(
        chunks: list[ChunkRecord],
        metadata_filters: dict[str, list[str]],
        *,
        section_ids: set[str] | None = None,
        strict: bool = False,
    ) -> list[ChunkRecord]:
        scoped = list(chunks)
        for key, predicate in (
            ("page_labels", lambda chunk: chunk.metadata.get("page_label") in metadata_filters.get("page_labels", [])),
            (
                "clause_terms",
                lambda chunk: any(term in chunk.metadata.get("clause_ids", []) for term in metadata_filters.get("clause_terms", [])),
            ),
        ):
            values = metadata_filters.get(key) or []
            if not values:
                continue
            filtered = [chunk for chunk in scoped if predicate(chunk)]
            if filtered or strict:
                scoped = filtered
        if section_ids:
            filtered = [chunk for chunk in scoped if chunk.metadata.get("section_id") in section_ids]
            if filtered or strict:
                scoped = filtered
        return scoped

    @staticmethod
    def _rank_dense(items: list[dict], key_name: str) -> dict[str, float]:
        return {item[key_name]: 1.0 / (1.0 + max(float(item.get("distance", 1.0)), 0.0)) for item in items}

    @staticmethod
    def _rank_lexical(question: str, records: list[tuple[str, str]], top_k: int) -> dict[str, float]:
        if not records:
            return {}
        from sklearn.feature_extraction.text import TfidfVectorizer
        from sklearn.metrics.pairwise import cosine_similarity

        try:
            matrix = TfidfVectorizer(stop_words="english").fit_transform([text for _, text in records] + [question])
        except ValueError:
            return {}
        similarities = cosine_similarity(matrix[-1], matrix[:-1]).flatten()
        indices = similarities.argsort()[::-1][:top_k]
        return {records[index][0]: float(similarities[index]) for index in indices if similarities[index] > 0}

    @staticmethod
    def _keyword_overlap(question: str, text: str) -> float:
        question_terms = {token for token in re.findall(r"[A-Za-z0-9]+", question.lower()) if len(token) > 3}
        if not question_terms:
            return 0.0
        text_terms = set(re.findall(r"[A-Za-z0-9]+", text.lower()))
        return len(question_terms & text_terms) / max(len(question_terms), 1)

    def _score_chunk(
        self,
        question: str,
        chunk: ChunkRecord,
        dense_scores: dict[str, float],
        lexical_scores: dict[str, float],
        fused_score: float,
        preferred_content_types: list[str],
        clause_terms: list[str],
        *,
        scoped_section_ids: set[str] | None = None,
        region_lookup: dict[str, str] | None = None,
        preferred_region_kinds: set[str] | None = None,
        query_type: str = "general_lookup",
    ) -> RetrievalCandidate:
        keyword_boost = 0.15 * self._keyword_overlap(question, chunk.text)
        heading_boost = 0.15 * self._keyword_overlap(question, str(chunk.metadata.get("section_heading", "")))
        path_boost = 0.08 * self._keyword_overlap(question, " ".join(chunk.metadata.get("section_path", [])))
        content_boost = 0.14 if str(chunk.metadata.get("content_type", "")) in preferred_content_types else 0.0
        clause_boost = 0.18 if any(term in chunk.metadata.get("clause_ids", []) for term in clause_terms) else 0.0
        scoped_boost = 0.14 if scoped_section_ids and chunk.metadata.get("section_id") in scoped_section_ids else 0.0
        region_boost = 0.08 if preferred_region_kinds and region_lookup and region_lookup.get(chunk.metadata.get("section_id", "")) in preferred_region_kinds else 0.0
        chunk_role = str(chunk.metadata.get("chunk_role_prior", "")).lower()
        body_evidence_score = float(chunk.metadata.get("body_evidence_score", 0.5) or 0.5)
        low_value_prior = float(chunk.metadata.get("low_value_prior", 0.0) or 0.0)
        heading_only = bool(chunk.metadata.get("heading_only_flag"))
        front_matter_kind = str(chunk.metadata.get("front_matter_kind", "")).lower()
        front_matter_score = float(chunk.metadata.get("front_matter_score", 0.0) or 0.0)
        semantic_role = str(chunk.metadata.get("semantic_chunk_role", "")).lower()
        semantic_confidence = float(chunk.metadata.get("semantic_chunk_confidence", 0.0) or 0.0)
        semantic_body_evidence_score = float(chunk.metadata.get("semantic_body_evidence_score", body_evidence_score) or body_evidence_score)
        evidence_adjustment = 0.06 * max(min(body_evidence_score, 1.0), 0.0)
        if chunk_role == "navigation_like":
            evidence_adjustment -= 0.18
        elif chunk_role == "reference_like":
            evidence_adjustment -= 0.14
        elif chunk_role == "table_fragment":
            evidence_adjustment -= 0.08 if query_type != "numeric_lookup" else 0.02
        elif heading_only:
            evidence_adjustment -= 0.10
        if low_value_prior >= 0.7:
            evidence_adjustment -= 0.06
        if semantic_confidence > 0.0:
            evidence_adjustment += 0.08 * semantic_confidence * (max(min(semantic_body_evidence_score, 1.0), 0.0) - 0.5)
            if semantic_role == "body_evidence":
                evidence_adjustment += 0.06 * semantic_confidence
            elif semantic_role == "summary_evidence":
                evidence_adjustment += (0.08 if query_type == "summary_lookup" else 0.04) * semantic_confidence
            elif semantic_role == "heading_stub":
                evidence_adjustment -= 0.08 * semantic_confidence
            elif semantic_role == "navigation_noise":
                evidence_adjustment -= 0.12 * semantic_confidence
            elif semantic_role == "reference_noise":
                evidence_adjustment -= 0.1 * semantic_confidence
            elif semantic_role == "table_fragment":
                evidence_adjustment -= (0.05 if query_type != "numeric_lookup" else 0.02) * semantic_confidence
        summary_adjustment = 0.0
        section_kind = str(chunk.metadata.get("section_kind", "")).lower()
        question_terms = self._significant_question_terms(question)
        if front_matter_kind in {"acknowledgements", "certificate", "contents", "declaration", "dedication", "list_of_figures", "list_of_tables", "preface"}:
            evidence_adjustment -= 0.18 * max(front_matter_score, 0.65)
        elif front_matter_kind == "title_page":
            evidence_adjustment -= 0.05 * max(front_matter_score, 0.5)
        if query_type == "summary_lookup":
            asks_early_summary = bool(question_terms & self.EARLY_SUMMARY_TERMS)
            asks_findings_summary = bool(question_terms & self.FINDINGS_SUMMARY_TERMS)
            asks_late_summary = bool(question_terms & self.LATE_SUMMARY_TERMS)
            if section_kind in self.LOW_VALUE_SUMMARY_SECTION_KINDS:
                summary_adjustment -= 0.20
            if asks_early_summary and section_kind in {"overview", "abstract", "introduction", "background"}:
                summary_adjustment += 0.16
            if asks_findings_summary and section_kind in {"results", "discussion"}:
                summary_adjustment += 0.14
            if asks_late_summary and section_kind in {"discussion", "conclusion", "conclusions", "future work", "future directions", "limitations"}:
                summary_adjustment += 0.14
            lowered_text = chunk.text[:400].lower()
            if "author contributions" in lowered_text or "supplementary information" in lowered_text:
                summary_adjustment -= 0.15
            if heading_only:
                summary_adjustment -= 0.06
            if chunk_role == "navigation_like":
                summary_adjustment -= 0.08
        metadata = dict(chunk.metadata)
        if region_lookup and chunk.metadata.get("section_id") in region_lookup:
            metadata["region_kind"] = region_lookup[chunk.metadata["section_id"]]
        return RetrievalCandidate(
            chunk_id=chunk.chunk_id,
            text=chunk.text,
            metadata=metadata,
            dense_score=dense_scores.get(chunk.chunk_id, 0.0),
            lexical_score=lexical_scores.get(chunk.chunk_id, 0.0),
            fused_score=(
                fused_score
                + keyword_boost
                + heading_boost
                + path_boost
                + content_boost
                + clause_boost
                + scoped_boost
                + region_boost
                + evidence_adjustment
                + summary_adjustment
            ),
            citation_label=f"{chunk.metadata.get('source_file', 'Document')} - {chunk.metadata.get('page_label', 'Document')}",
        )

    @staticmethod
    def _promote_continuation_chunks(
        fused_scores: defaultdict[str, float],
        chunk_lookup: dict[str, ChunkRecord],
    ) -> None:
        ranked_chunk_ids = [chunk_id for chunk_id, _ in sorted(fused_scores.items(), key=lambda pair: pair[1], reverse=True)[:6]]
        for chunk_id in ranked_chunk_ids:
            chunk = chunk_lookup.get(chunk_id)
            if chunk is None:
                continue
            if str(chunk.metadata.get("chunk_role_prior", "")).lower() != "heading_stub":
                continue
            continuation_id = str(chunk.metadata.get("continuation_chunk_id", "")).strip()
            if not continuation_id or continuation_id in fused_scores or continuation_id not in chunk_lookup:
                continue
            continuation = chunk_lookup[continuation_id]
            continuation_role = str(continuation.metadata.get("chunk_role_prior", "")).lower()
            if continuation_role in {"navigation_like", "reference_like"}:
                continue
            continuation_score = max(fused_scores[chunk_id] * 0.96, fused_scores[chunk_id] - 0.005) + 0.03
            fused_scores[continuation_id] = continuation_score

    @classmethod
    def _summary_focus(cls, question: str) -> str:
        question_terms = cls._significant_question_terms(question)
        asks_findings = bool(question_terms & cls.FINDINGS_SUMMARY_TERMS)
        asks_late = bool(question_terms & cls.LATE_SUMMARY_TERMS)
        asks_early = bool(question_terms & cls.EARLY_SUMMARY_TERMS)
        if asks_findings:
            return "findings"
        if asks_late:
            return "late"
        if asks_early:
            return "overview"
        return "balanced"

    @staticmethod
    def _is_heading_stub(candidate: RetrievalCandidate) -> bool:
        return bool(candidate.metadata.get("heading_only_flag")) or str(candidate.metadata.get("chunk_role_prior", "")).lower() == "heading_stub"

    @classmethod
    def _prefer_body_evidence(cls, candidates: list[RetrievalCandidate]) -> list[RetrievalCandidate]:
        by_id = {candidate.chunk_id: candidate for candidate in candidates}
        selected: list[RetrievalCandidate] = []
        selected_ids: set[str] = set()
        deferred_heading_stubs: list[RetrievalCandidate] = []

        def add(candidate: RetrievalCandidate) -> None:
            if candidate.chunk_id in selected_ids:
                return
            selected_ids.add(candidate.chunk_id)
            selected.append(candidate)

        for candidate in candidates:
            if cls._is_heading_stub(candidate):
                continuation_id = str(candidate.metadata.get("continuation_chunk_id", "")).strip()
                continuation = by_id.get(continuation_id)
                if continuation is not None and not cls._is_heading_stub(continuation):
                    add(continuation)
                    continue
                deferred_heading_stubs.append(candidate)
                continue
            add(candidate)

        for candidate in deferred_heading_stubs:
            add(candidate)
        return selected

    def _finalize_candidates(self, question: str, candidates: list[RetrievalCandidate]) -> list[RetrievalCandidate]:
        ranked = sorted(candidates, key=lambda candidate: candidate.fused_score, reverse=True)
        if self.reranker is not None and ranked:
            ranked = self.reranker.rerank(question, ranked, top_k=len(ranked))
        return self._prefer_body_evidence(ranked)[: self.settings.final_top_k]

    def _seed_selected_section_chunks(
        self,
        fingerprint: str,
        question: str,
        selected_section_ids: list[str],
        *,
        region_lookup: dict[str, str] | None = None,
        preferred_region_kinds: set[str] | None = None,
        query_type: str = "general_lookup",
        limit: int = 2,
    ) -> list[RetrievalCandidate]:
        chunks = self.store.load_chunks(fingerprint)
        seeded: list[RetrievalCandidate] = []
        chunk_groups: dict[str, list[ChunkRecord]] = defaultdict(list)
        for chunk in chunks:
            section_id = str(chunk.metadata.get("section_id", ""))
            if section_id in selected_section_ids:
                chunk_groups[section_id].append(chunk)

        summary_focus = self._summary_focus(question)
        for section_id in selected_section_ids[:limit]:
            members = chunk_groups.get(section_id, [])
            if not members:
                continue
            region_kind = ""
            if region_lookup is not None:
                region_kind = region_lookup.get(section_id, "")
            if query_type == "summary_lookup" and summary_focus == "overview" and region_kind in self.OVERVIEW_SECTION_KINDS:
                best_chunk = min(members, key=lambda chunk: chunk.chunk_index)
            elif query_type == "summary_lookup" and summary_focus in {"findings", "late"} and region_kind in self.FINDINGS_SECTION_KINDS | self.LATE_SECTION_KINDS:
                best_chunk = max(
                    members,
                    key=lambda chunk: (
                        self._keyword_overlap(question, str(chunk.metadata.get("section_heading", "")))
                        + self._keyword_overlap(question, chunk.text[:600])
                        + (0.05 if chunk.chunk_index > 0 else 0.0)
                    ),
                )
            else:
                best_chunk = max(
                    members,
                    key=lambda chunk: (
                        self._keyword_overlap(question, str(chunk.metadata.get("section_heading", "")))
                        + self._keyword_overlap(question, chunk.text[:600])
                    ),
                )
            seeded.append(
                self._score_chunk(
                    question,
                    best_chunk,
                    {},
                    {},
                    0.22,
                    [],
                    [],
                    scoped_section_ids={section_id},
                    region_lookup=region_lookup,
                    preferred_region_kinds=preferred_region_kinds,
                    query_type=query_type,
                )
            )
        return seeded

    def _choose_global_summary_sections(
        self,
        question: str,
        ranked_synopses: list[RetrievalCandidate],
        plan: RetrievalPlan,
    ) -> tuple[list[str], list[str]]:
        selected: list[str] = []
        notes: list[str] = []
        summary_focus = self._summary_focus(question)

        def pick_first(section_kinds: set[str]) -> str | None:
            for candidate in ranked_synopses:
                section_id = str(candidate.metadata.get("section_id", ""))
                section_kind = str(candidate.metadata.get("section_kind", "")).lower()
                if not section_id or section_kind in self.LOW_VALUE_SUMMARY_SECTION_KINDS:
                    continue
                if section_kind in section_kinds:
                    return section_id
            return None

        overview_id = pick_first(self.OVERVIEW_SECTION_KINDS)
        findings_id = pick_first(self.FINDINGS_SECTION_KINDS)
        late_id = pick_first(self.LATE_SECTION_KINDS)

        if overview_id:
            selected.append(overview_id)
        if summary_focus == "findings":
            if findings_id:
                selected.append(findings_id)
            if late_id:
                selected.append(late_id)
        elif summary_focus == "late":
            if late_id:
                selected.append(late_id)
            if findings_id:
                selected.append(findings_id)
        else:
            if findings_id:
                selected.append(findings_id)
            if summary_focus == "balanced" and late_id:
                selected.append(late_id)

        if not selected:
            selected.extend(
                candidate.metadata.get("section_id")
                for candidate in ranked_synopses[: max(2, self.settings.synopsis_section_window - 1)]
                if candidate.metadata.get("section_id")
                and str(candidate.metadata.get("section_kind", "")).lower() not in self.LOW_VALUE_SUMMARY_SECTION_KINDS
            )

        ranked_lookup = {
            str(candidate.metadata.get("section_id", "")): str(candidate.metadata.get("section_kind", "")).lower()
            for candidate in ranked_synopses
            if candidate.metadata.get("section_id")
        }
        for section_id in plan.target_region_ids:
            if ranked_lookup.get(section_id, "") in self.LOW_VALUE_SUMMARY_SECTION_KINDS:
                continue
            if section_id and section_id not in selected:
                selected.append(section_id)
            if len(selected) >= self.settings.synopsis_section_window:
                break

        deduped = list(dict.fromkeys(section_id for section_id in selected if section_id))
        if deduped:
            notes.append(f"Global-summary routing assembled {len(deduped)} anchor sections.")
        return deduped[: self.settings.synopsis_section_window], notes

    def _global_summary_candidates(
        self,
        fingerprint: str,
        question: str,
        metadata_filters: dict[str, list[str]],
        preferred_content_types: list[str],
        clause_terms: list[str],
        plan: RetrievalPlan,
        synopses: list[SectionSynopsisRecord],
    ) -> tuple[list[RetrievalCandidate], list[str], bool]:
        if not synopses:
            return [], ["No synopsis artifacts were available for global-summary retrieval."], False

        ranked_synopses = self._rank_synopses(fingerprint, question, synopses, plan)
        selected_section_ids, notes = self._choose_global_summary_sections(question, ranked_synopses, plan)
        region_lookup = {synopsis.section_id: synopsis.region_kind for synopsis in synopses}
        preferred_region_kinds = set(plan.target_region_kinds)

        local_candidates = self._chunk_candidates(
            fingerprint,
            question,
            metadata_filters,
            preferred_content_types,
            clause_terms,
            query_type="summary_lookup",
            section_ids=set(selected_section_ids) if selected_section_ids else None,
            strict=False,
            dense_top_k=max(self.settings.dense_top_k, self.settings.final_top_k + self.settings.section_chunk_window + 2),
            lexical_top_k=max(self.settings.lexical_top_k, self.settings.final_top_k + self.settings.section_chunk_window + 2),
            fused_top_k=max(self.settings.fused_top_k, self.settings.final_top_k + self.settings.section_chunk_window + 3),
            region_lookup=region_lookup,
            preferred_region_kinds=preferred_region_kinds,
        )
        local_candidates.extend(
            self._seed_selected_section_chunks(
                fingerprint,
                question,
                selected_section_ids,
                region_lookup=region_lookup,
                preferred_region_kinds=preferred_region_kinds,
                query_type="summary_lookup",
                limit=min(3, max(2, len(selected_section_ids))),
            )
        )

        global_candidates: list[RetrievalCandidate] = []
        if plan.use_global_fallback:
            global_candidates = [
                candidate
                for candidate in self._chunk_candidates(
                    fingerprint,
                    question,
                    {key: value for key, value in metadata_filters.items() if key != "section_terms"},
                    preferred_content_types,
                    clause_terms,
                    query_type="summary_lookup",
                    dense_top_k=max(self.settings.global_fallback_top_k + 3, self.settings.final_top_k),
                    lexical_top_k=max(self.settings.global_fallback_top_k + 3, self.settings.final_top_k),
                    fused_top_k=max(self.settings.global_fallback_top_k + 2, 3),
                    region_lookup=region_lookup,
                )
                if candidate.metadata.get("section_id") not in set(selected_section_ids)
                and str(candidate.metadata.get("section_kind", "")).lower() not in self.LOW_VALUE_SUMMARY_SECTION_KINDS
            ][: self.settings.global_fallback_top_k]

        if ranked_synopses:
            notes.append(
                f"Global-summary top synopsis: {ranked_synopses[0].metadata.get('section_heading', ranked_synopses[0].chunk_id)}."
            )
        if global_candidates:
            notes.append("Global-summary fallback added extra overview evidence outside the anchor sections.")
        return local_candidates + global_candidates, notes, bool(global_candidates)

    def _chunk_candidates(
        self,
        fingerprint: str,
        question: str,
        metadata_filters: dict[str, list[str]],
        preferred_content_types: list[str],
        clause_terms: list[str],
        *,
        query_type: str = "general_lookup",
        section_ids: set[str] | None = None,
        strict: bool = False,
        dense_top_k: int | None = None,
        lexical_top_k: int | None = None,
        fused_top_k: int | None = None,
        region_lookup: dict[str, str] | None = None,
        preferred_region_kinds: set[str] | None = None,
    ) -> list[RetrievalCandidate]:
        chunks = self._apply_metadata_filters(
            self.store.load_chunks(fingerprint),
            metadata_filters,
            section_ids=section_ids,
            strict=strict,
        )
        if not chunks:
            return []
        dense_items = self.store.dense_query(fingerprint, question, top_k=dense_top_k or self.settings.dense_top_k)
        allowed_chunk_ids = {chunk.chunk_id for chunk in chunks}
        dense_items = [item for item in dense_items if item["chunk_id"] in allowed_chunk_ids]
        dense_scores = self._rank_dense(dense_items, "chunk_id")
        lexical_scores = self._rank_lexical(question, [(chunk.chunk_id, chunk.text) for chunk in chunks], lexical_top_k or self.settings.lexical_top_k)
        fused_scores: defaultdict[str, float] = defaultdict(float)
        for rank, item in enumerate(sorted(dense_scores.items(), key=lambda pair: pair[1], reverse=True), start=1):
            fused_scores[item[0]] += 1.0 / (60 + rank)
        for rank, item in enumerate(sorted(lexical_scores.items(), key=lambda pair: pair[1], reverse=True), start=1):
            fused_scores[item[0]] += 1.0 / (60 + rank)
        chunk_lookup = {chunk.chunk_id: chunk for chunk in chunks}
        self._promote_continuation_chunks(fused_scores, chunk_lookup)
        return [
            self._score_chunk(
                question,
                chunk_lookup[chunk_id],
                dense_scores,
                lexical_scores,
                fused_score,
                preferred_content_types,
                clause_terms,
                scoped_section_ids=section_ids,
                region_lookup=region_lookup,
                preferred_region_kinds=preferred_region_kinds,
                query_type=query_type,
            )
            for chunk_id, fused_score in sorted(fused_scores.items(), key=lambda pair: pair[1], reverse=True)[: fused_top_k or self.settings.fused_top_k]
            if chunk_id in chunk_lookup
        ]

    @staticmethod
    def _synopsis_text(synopsis: SectionSynopsisRecord) -> str:
        path_text = " > ".join(synopsis.metadata.get("section_path", [])) if isinstance(synopsis.metadata.get("section_path", []), list) else str(synopsis.metadata.get("section_path", ""))
        return "\n".join([synopsis.title, path_text, synopsis.region_kind, ", ".join(synopsis.key_terms), synopsis.synopsis]).strip()

    def _rank_synopses(self, fingerprint: str, question: str, synopses: list[SectionSynopsisRecord], plan: RetrievalPlan) -> list[RetrievalCandidate]:
        dense_scores = self._rank_dense(self.store.dense_query_synopses(fingerprint, question, top_k=self.settings.synopsis_dense_top_k), "section_id")
        lexical_scores = self._rank_lexical(question, [(synopsis.section_id, self._synopsis_text(synopsis)) for synopsis in synopses], self.settings.synopsis_lexical_top_k)
        return self.synopsis_retriever.rank(
            question=question,
            synopses=synopses,
            dense_scores=dense_scores,
            lexical_scores=lexical_scores,
            top_k=self.settings.synopsis_fused_top_k,
            target_region_ids=plan.target_region_ids,
            target_region_kinds=plan.target_region_kinds,
        )

    @staticmethod
    def _dedupe(candidates: list[RetrievalCandidate]) -> list[RetrievalCandidate]:
        seen: set[str] = set()
        deduped: list[RetrievalCandidate] = []
        for candidate in sorted(candidates, key=lambda item: item.fused_score, reverse=True):
            if candidate.chunk_id in seen:
                continue
            seen.add(candidate.chunk_id)
            deduped.append(candidate)
        return deduped

    @staticmethod
    def _scope_compliant_candidates(
        candidates: list[RetrievalCandidate],
        plan: RetrievalPlan,
        notes: list[str],
    ) -> list[RetrievalCandidate]:
        if getattr(plan, "scope_strictness", "none") != "hard":
            return candidates
        allowed_section_ids = set(getattr(plan, "allowed_section_ids", []) or plan.target_region_ids)
        if not allowed_section_ids:
            return candidates
        filtered = [
            candidate
            for candidate in candidates
            if str(candidate.metadata.get("section_id", "")).strip() in allowed_section_ids
        ]
        removed_count = len(candidates) - len(filtered)
        if removed_count:
            notes.append(f"Scope compliance removed {removed_count} candidates outside the orchestrated hard scope.")
        return filtered

    def _expand_section_scope(self, selected: list[str], plan: RetrievalPlan, edges: list[TopologyEdge]) -> list[str]:
        if plan.constraint_mode == "hard_region":
            return list(dict.fromkeys(selected))
        edge_types = {"previous_next", "parent_child", "semantic_neighbor"} if plan.constraint_mode == "soft_local" else {"previous_next", "same_region_family", "semantic_neighbor"}
        expanded = list(selected)
        for section_id in selected[: 1 if plan.constraint_mode == "soft_local" else 2]:
            expanded.extend(self.topology_service.neighbor_section_ids(section_id, edges, edge_types=edge_types, top_k=2))
        return list(dict.fromkeys(expanded))

    def _synopsis_first(
        self,
        fingerprint: str,
        question: str,
        metadata_filters: dict[str, list[str]],
        preferred_content_types: list[str],
        clause_terms: list[str],
        query_type: str,
        plan: RetrievalPlan,
        synopses: list[SectionSynopsisRecord],
        topology_edges: list[TopologyEdge],
    ) -> tuple[list[RetrievalCandidate], list[str], bool]:
        if not synopses:
            return [], ["No synopsis artifacts were available for topology-guided retrieval."], False
        ranked_synopses = self._rank_synopses(fingerprint, question, synopses, plan)
        selected_section_ids = list(plan.target_region_ids) or [
            candidate.metadata.get("section_id")
            for candidate in ranked_synopses[: self.settings.synopsis_section_window]
            if candidate.metadata.get("section_id")
        ]
        selected_section_ids = self._expand_section_scope(selected_section_ids, plan, topology_edges)
        region_lookup = {synopsis.section_id: synopsis.region_kind for synopsis in synopses}
        local_candidates = self._chunk_candidates(
            fingerprint,
            question,
            metadata_filters,
            preferred_content_types,
            clause_terms,
            query_type=query_type,
            section_ids=set(selected_section_ids) if selected_section_ids else None,
            strict=plan.constraint_mode == "hard_region",
            dense_top_k=max(self.settings.dense_top_k, self.settings.final_top_k + self.settings.section_chunk_window),
            lexical_top_k=max(self.settings.lexical_top_k, self.settings.final_top_k + self.settings.section_chunk_window),
            fused_top_k=max(self.settings.fused_top_k, self.settings.final_top_k + self.settings.section_chunk_window),
            region_lookup=region_lookup,
            preferred_region_kinds=set(plan.target_region_kinds),
        )
        if query_type == "summary_lookup" and plan.evidence_spread == "global" and selected_section_ids:
            local_candidates.extend(
                self._seed_selected_section_chunks(
                    fingerprint,
                    question,
                    selected_section_ids,
                    region_lookup=region_lookup,
                    preferred_region_kinds=set(plan.target_region_kinds),
                    query_type=query_type,
                    limit=2,
                )
            )
        global_candidates: list[RetrievalCandidate] = []
        if plan.use_global_fallback:
            global_candidates = [
                candidate
                for candidate in self._chunk_candidates(
                    fingerprint,
                    question,
                    {key: value for key, value in metadata_filters.items() if key != "section_terms"},
                    preferred_content_types,
                    clause_terms,
                    query_type=query_type,
                    dense_top_k=max(self.settings.global_fallback_top_k + 2, self.settings.final_top_k),
                    lexical_top_k=max(self.settings.global_fallback_top_k + 2, self.settings.final_top_k),
                    fused_top_k=max(self.settings.global_fallback_top_k, 2),
                    region_lookup=region_lookup,
                )
                if candidate.metadata.get("section_id") not in set(selected_section_ids)
            ][: self.settings.global_fallback_top_k]
        notes = [f"Synopsis-first routing prioritized {len(selected_section_ids)} sections before chunk retrieval."]
        if ranked_synopses:
            notes.append(f"Top synopsis region: {ranked_synopses[0].metadata.get('section_heading', ranked_synopses[0].chunk_id)}.")
        if global_candidates:
            notes.append("Global fallback added extra evidence outside the prioritized regions.")
        return local_candidates + global_candidates, notes, bool(global_candidates)

    def _build_result(
        self,
        question: str,
        metadata_filters: dict[str, list[str]],
        route_used: str,
        candidates: list[RetrievalCandidate],
        notes: list[str],
        plan: RetrievalPlan,
        query_type: str,
        *,
        global_fallback_used: bool = False,
    ) -> RetrievalResult:
        scoped_candidates = self._scope_compliant_candidates(candidates, plan, notes)
        final_candidates = self._finalize_candidates(question, self._dedupe(scoped_candidates))
        evidence_status, best_score, max_lexical, content_overlap = self._assess_evidence_status(question, final_candidates, query_type=query_type)
        retrieval_plan = plan.to_dict()
        retrieval_plan["global_fallback_used"] = global_fallback_used
        return RetrievalResult(
            question=question,
            candidates=final_candidates,
            cache_hit=False,
            retrieval_version=self.settings.retrieval_version,
            route_used=route_used,
            query_used=question,
            query_variants=[question],
            metadata_filters=metadata_filters,
            strategy_notes=notes,
            weak_evidence=evidence_status == "weak",
            evidence_status=evidence_status,
            best_score=best_score,
            max_lexical_score=max_lexical,
            content_overlap_score=content_overlap,
            retrieval_plan=retrieval_plan,
        )

    def retrieve(self, fingerprint: str, question: str) -> RetrievalResult:
        metadata_filters = self._extract_metadata_filters(question)
        synopses = self.store.load_synopses(fingerprint)
        topology_edges = self.store.load_topology_edges(fingerprint)
        query_profile, plan = self.planner.analyze_and_plan(
            question=question,
            metadata_filters=metadata_filters,
            synopses=synopses,
        )
        notes = [
            f"Planner intent: {plan.intent_type}.",
            f"Planner evidence spread: {plan.evidence_spread}.",
            f"Planner selected {plan.preferred_route} with {plan.constraint_mode} constraints.",
            f"Planner source: {plan.planner_source} at confidence {plan.planner_confidence:.2f}.",
        ]
        if query_profile.preferred_content_types:
            notes.append(f"Preferred content types: {', '.join(query_profile.preferred_content_types)}.")
        if plan.scope_strictness != "none":
            notes.append(
                f"Orchestrated scope: {plan.scope_strictness} over {len(plan.allowed_section_ids or plan.target_region_ids)} sections."
            )
        if plan.answer_focus:
            notes.append(f"Answer focus: {', '.join(plan.answer_focus)}.")
        if plan.orchestrator_reason:
            notes.append(f"Orchestrator reason: {plan.orchestrator_reason}.")

        if plan.preferred_route == "chunk_first":
            candidates = self._chunk_candidates(
                fingerprint,
                question,
                metadata_filters,
                query_profile.preferred_content_types,
                query_profile.clause_terms,
                query_type=query_profile.query_type,
                section_ids=set(plan.target_region_ids) if plan.target_region_ids else None,
                strict=plan.constraint_mode == "hard_region",
            )
            notes.append("Chunk-first retrieval path used for exact grounding.")
            return self._build_result(question, metadata_filters, "chunk_first", candidates, notes, plan, query_profile.query_type)

        if plan.preferred_route == "section_first":
            sections = self.store.load_sections(fingerprint)
            ranked_sections = self.section_retriever.seed_summary_sections(question, sections, top_k=max(self.settings.section_fused_top_k, 4))
            candidates = self._chunk_candidates(
                fingerprint,
                question,
                metadata_filters,
                query_profile.preferred_content_types,
                query_profile.clause_terms,
                query_type=query_profile.query_type,
                section_ids={candidate.metadata.get("section_id") for candidate in ranked_sections if candidate.metadata.get("section_id")},
            )
            notes.append("Legacy section-first path used as a bounded fallback.")
            return self._build_result(question, metadata_filters, "section_first", candidates, notes, plan, query_profile.query_type)

        if query_profile.query_type == "summary_lookup" and plan.evidence_spread == "global":
            synopsis_candidates, synopsis_notes, global_fallback_used = self._global_summary_candidates(
                fingerprint,
                question,
                metadata_filters,
                query_profile.preferred_content_types,
                query_profile.clause_terms,
                plan,
                synopses,
            )
        else:
            synopsis_candidates, synopsis_notes, global_fallback_used = self._synopsis_first(
                fingerprint,
                question,
                metadata_filters,
                query_profile.preferred_content_types,
                query_profile.clause_terms,
                query_profile.query_type,
                plan,
                synopses,
                topology_edges,
            )
        if plan.preferred_route == "synopsis_first":
            return self._build_result(
                question,
                metadata_filters,
                "global_summary_first" if query_profile.query_type == "summary_lookup" and plan.evidence_spread == "global" else "synopsis_first",
                synopsis_candidates,
                notes + synopsis_notes,
                plan,
                query_profile.query_type,
                global_fallback_used=global_fallback_used,
            )

        chunk_candidates = self._chunk_candidates(
            fingerprint,
            question,
            metadata_filters,
            query_profile.preferred_content_types,
            query_profile.clause_terms,
            query_type=query_profile.query_type,
        )
        notes.extend(synopsis_notes)
        notes.append("Hybrid retrieval merged direct chunk evidence with topology-guided synopsis retrieval.")
        return self._build_result(
            question,
            metadata_filters,
            "hybrid_both",
            chunk_candidates + synopsis_candidates,
            notes,
            plan,
            query_profile.query_type,
            global_fallback_used=global_fallback_used,
        )
