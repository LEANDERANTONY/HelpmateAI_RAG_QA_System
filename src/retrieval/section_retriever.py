from __future__ import annotations

import re

from src.schemas import RetrievalCandidate, SectionRecord


class SectionRetriever:
    LOW_VALUE_SECTION_KINDS = {"references", "appendix"}

    @staticmethod
    def _query_terms(question: str) -> set[str]:
        return {
            token
            for token in re.findall(r"[A-Za-z0-9]+", question.lower())
            if len(token) > 3
        }

    @staticmethod
    def _preferred_section_kinds(question: str) -> list[str]:
        lowered = question.lower()
        if any(term in lowered for term in ("main focus", "main aim", "main idea", "primary topic", "research objectives")):
            return ["overview", "abstract", "introduction", "background", "research aim", "research objectives"]
        if any(term in lowered for term in ("future work", "future directions", "next steps", "follow-up work", "future research")):
            return ["future work", "future directions", "conclusion", "conclusions", "discussion", "final remarks"]
        if any(term in lowered for term in ("challenge", "limitations", "clinical adoption", "barrier", "bottleneck")):
            return ["limitations", "discussion", "conclusion", "conclusions", "results"]
        if any(term in lowered for term in ("conclusion", "takeaway", "what does the paper say about", "what did the thesis conclude")):
            return ["conclusion", "conclusions", "discussion", "results", "overview"]
        if any(term in lowered for term in ("method", "methodology", "approach", "implementation")):
            return ["methodology", "methods", "implementation details", "approach"]
        if any(term in lowered for term in ("result", "results", "auc", "accuracy", "finding", "findings")):
            return ["results", "discussion", "conclusion", "conclusions"]
        return []

    @classmethod
    def _heading_overlap_score(cls, question: str, section: SectionRecord) -> float:
        question_terms = cls._query_terms(question)
        if not question_terms:
            return 0.0
        heading_terms = set(
            re.findall(
                r"[A-Za-z0-9]+",
                " ".join(
                    [
                        section.title,
                        " ".join(section.section_path),
                        " ".join(section.metadata.get("section_aliases", [])) if isinstance(section.metadata.get("section_aliases", []), list) else str(section.metadata.get("section_aliases", "")),
                    ]
                ).lower(),
            )
        )
        if not heading_terms:
            return 0.0
        return len(question_terms & heading_terms) / max(len(question_terms), 1)

    @staticmethod
    def _document_style_score(question: str, document_style: str) -> float:
        lowered = question.lower()
        style = (document_style or "").lower()
        if style == "policy_document":
            return 0.2 if any(term in lowered for term in ("clause", "waiting period", "grace period", "cashless", "premium")) else 0.0
        if style in {"research_paper", "thesis_document"}:
            return 0.12 if any(term in lowered for term in ("main focus", "main aim", "research objectives", "future work", "challenge", "conclusion")) else 0.02
        return 0.0

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

    @classmethod
    def _summary_seed_score(cls, question: str, section: SectionRecord) -> float:
        preferred = cls._preferred_section_kinds(question)
        if not preferred:
            return 0.0

        section_kind = str(section.metadata.get("section_kind", "")).lower()
        heading_overlap = cls._heading_overlap_score(question, section)
        title_lower = section.title.lower()
        seed = heading_overlap * 0.6
        if section_kind in preferred:
            seed += 1.0
        if any(kind in title_lower for kind in preferred):
            seed += 0.5
        if section_kind in cls.LOW_VALUE_SECTION_KINDS:
            seed -= 0.8
        return seed

    @classmethod
    def seed_summary_sections(cls, question: str, sections: list[SectionRecord], top_k: int) -> list[RetrievalCandidate]:
        preferred = cls._preferred_section_kinds(question)
        if not preferred:
            return []

        seeded: list[RetrievalCandidate] = []
        for section in sections:
            fused_score = cls._summary_seed_score(question, section)
            if fused_score <= 0:
                continue
            path_text = " > ".join(section.section_path)
            text = f"{section.title}\n{path_text}\n\n{section.summary}".strip()
            seeded.append(
                RetrievalCandidate(
                    chunk_id=section.section_id,
                    text=text,
                    metadata={
                        **section.metadata,
                        "page_label": section.metadata.get("primary_page_label", "Document"),
                        "section_id": section.section_id,
                        "section_path": section.section_path,
                        "clause_ids": section.clause_ids,
                        "retrieval_level": "section",
                    },
                    dense_score=0.0,
                    lexical_score=0.0,
                    fused_score=fused_score,
                    citation_label=f"{section.metadata.get('source_file', 'Document')} - {section.metadata.get('primary_page_label', 'Document')}",
                )
            )
        return sorted(seeded, key=lambda candidate: candidate.fused_score, reverse=True)[:top_k]

    def rank(
        self,
        question: str,
        sections: list[SectionRecord],
        dense_scores: dict[str, float],
        lexical_scores: dict[str, float],
        top_k: int,
    ) -> list[RetrievalCandidate]:
        by_id = {section.section_id: section for section in sections}
        scored_ids = set(dense_scores) | set(lexical_scores)
        ranked: list[RetrievalCandidate] = []
        ranked_ids = sorted(
            scored_ids,
            key=lambda value: dense_scores.get(value, 0.0) + lexical_scores.get(value, 0.0),
            reverse=True,
        )
        for section_id in ranked_ids:
            section = by_id.get(section_id)
            if section is None:
                continue
            path_text = " > ".join(section.section_path)
            text = f"{section.title}\n{path_text}\n\n{section.summary}".strip()
            heading_overlap = self._heading_overlap_score(question, section)
            section_kind = str(section.metadata.get("section_kind", ""))
            penalty = -0.2 if section_kind.lower() in self.LOW_VALUE_SECTION_KINDS and self._preferred_section_kinds(question) else 0.0
            fused_score = (
                dense_scores.get(section_id, 0.0)
                + lexical_scores.get(section_id, 0.0)
                + 0.32 * self._section_kind_score(question, section_kind)
                + 0.05 * self._document_style_score(question, str(section.metadata.get("document_style", "")))
                + 0.18 * heading_overlap
                + penalty
            )
            ranked.append(
                RetrievalCandidate(
                    chunk_id=section.section_id,
                    text=text,
                    metadata={
                        **section.metadata,
                        "page_label": section.metadata.get("primary_page_label", "Document"),
                        "section_id": section.section_id,
                        "section_path": section.section_path,
                        "clause_ids": section.clause_ids,
                        "retrieval_level": "section",
                    },
                    dense_score=dense_scores.get(section_id, 0.0),
                    lexical_score=lexical_scores.get(section_id, 0.0),
                    fused_score=fused_score,
                    citation_label=f"{section.metadata.get('source_file', 'Document')} - {section.metadata.get('primary_page_label', 'Document')}",
                )
            )
        return sorted(ranked, key=lambda candidate: candidate.fused_score, reverse=True)[:top_k]
