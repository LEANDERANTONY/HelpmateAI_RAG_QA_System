from __future__ import annotations

import re

from src.schemas import RetrievalCandidate, SectionSynopsisRecord


class SynopsisRetriever:
    LOW_VALUE_REGION_KINDS = {"appendix"}
    PRIMARY_OVERVIEW_TITLES = {"abstract", "document overview", "executive summary", "overview", "introduction"}
    LATE_SUMMARY_TITLES = {"discussion", "conclusion", "conclusions", "future work", "future directions", "limitations"}
    RESULTS_SUMMARY_TITLES = {"results", "findings", "evaluation", "performance"}

    @staticmethod
    def _query_terms(question: str) -> set[str]:
        return {token for token in re.findall(r"[A-Za-z0-9]+", question.lower()) if len(token) > 3}

    @classmethod
    def _heading_overlap_score(cls, question: str, synopsis: SectionSynopsisRecord) -> float:
        question_terms = cls._query_terms(question)
        if not question_terms:
            return 0.0
        synopsis_terms = set(
            re.findall(
                r"[A-Za-z0-9]+",
                " ".join(
                    [
                        synopsis.title,
                        synopsis.synopsis,
                        " ".join(synopsis.key_terms),
                        " ".join(synopsis.metadata.get("section_path", []))
                        if isinstance(synopsis.metadata.get("section_path", []), list)
                        else str(synopsis.metadata.get("section_path", "")),
                    ]
                ).lower(),
            )
        )
        return len(question_terms & synopsis_terms) / max(len(question_terms), 1)

    @classmethod
    def _summary_role_boost(cls, question: str, synopsis: SectionSynopsisRecord) -> float:
        question_terms = cls._query_terms(question)
        title = synopsis.title.lower().strip()
        if not question_terms:
            return 0.0

        boost = 0.0
        asks_global_summary = bool(
            {
                "about",
                "aim",
                "contribution",
                "contributions",
                "focus",
                "objective",
                "objectives",
                "overview",
                "purpose",
                "summary",
                "topic",
            }
            & question_terms
        )
        asks_late_summary = bool({"future", "next", "conclusion", "conclusions", "limitation", "limitations", "recommendation", "recommendations"} & question_terms)
        asks_findings_summary = bool({"finding", "findings", "result", "results", "headline", "performance"} & question_terms)

        if asks_global_summary and title in cls.PRIMARY_OVERVIEW_TITLES:
            boost += 0.34
        if asks_global_summary and synopsis.region_kind == "overview":
            boost += 0.18
        if asks_global_summary and synopsis.metadata.get("section_kind") in {"abstract", "introduction", "overview"}:
            boost += 0.12
        if asks_global_summary and not asks_findings_summary and synopsis.region_kind == "evidence":
            boost -= 0.18
        if asks_late_summary and title in cls.LATE_SUMMARY_TITLES:
            boost += 0.24
        if asks_late_summary and synopsis.region_kind == "discussion":
            boost += 0.14
        if asks_findings_summary and title in cls.RESULTS_SUMMARY_TITLES:
            boost += 0.28
        if asks_findings_summary and synopsis.region_kind in {"evidence", "discussion"}:
            boost += 0.12
        return boost

    def rank(
        self,
        *,
        question: str,
        synopses: list[SectionSynopsisRecord],
        dense_scores: dict[str, float],
        lexical_scores: dict[str, float],
        top_k: int,
        target_region_ids: list[str] | None = None,
        target_region_kinds: list[str] | None = None,
    ) -> list[RetrievalCandidate]:
        by_id = {synopsis.section_id: synopsis for synopsis in synopses}
        scored_ids = set(dense_scores) | set(lexical_scores) | set(target_region_ids or [])
        preferred_ids = set(target_region_ids or [])
        preferred_kinds = set(target_region_kinds or [])
        ranked: list[RetrievalCandidate] = []

        for section_id in scored_ids:
            synopsis = by_id.get(section_id)
            if synopsis is None:
                continue
            heading_overlap = self._heading_overlap_score(question, synopsis)
            region_boost = 0.22 if preferred_kinds and synopsis.region_kind in preferred_kinds else 0.0
            id_boost = 0.35 if section_id in preferred_ids else 0.0
            role_boost = self._summary_role_boost(question, synopsis)
            penalty = -0.25 if synopsis.region_kind in self.LOW_VALUE_REGION_KINDS else 0.0
            if synopsis.metadata.get("topology_low_value"):
                penalty -= 0.35
            fused_score = (
                dense_scores.get(section_id, 0.0)
                + lexical_scores.get(section_id, 0.0)
                + 0.22 * heading_overlap
                + region_boost
                + id_boost
                + role_boost
                + penalty
            )
            ranked.append(
                RetrievalCandidate(
                    chunk_id=synopsis.section_id,
                    text=synopsis.synopsis,
                    metadata={
                        **synopsis.metadata,
                        "page_label": synopsis.page_labels[0] if synopsis.page_labels else "Document",
                        "section_id": synopsis.section_id,
                        "region_kind": synopsis.region_kind,
                        "retrieval_level": "synopsis",
                    },
                    dense_score=dense_scores.get(section_id, 0.0),
                    lexical_score=lexical_scores.get(section_id, 0.0),
                    fused_score=fused_score,
                    citation_label=f"{synopsis.metadata.get('source_file', 'Document')} - {synopsis.page_labels[0] if synopsis.page_labels else 'Document'}",
                )
            )

        ranked.sort(key=lambda candidate: candidate.fused_score, reverse=True)
        return ranked[:top_k]
