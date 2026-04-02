from __future__ import annotations

from src.schemas import RetrievalCandidate, SectionRecord


class SectionRetriever:
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
            return 1.0 if section_kind in {"abstract", "introduction", "background"} else 0.0
        if any(term in lowered for term in ("future work", "next steps", "future directions")):
            return 1.0 if section_kind in {"future work", "future directions", "conclusion", "conclusions", "discussion"} else 0.0
        if any(term in lowered for term in ("challenge", "limitations", "clinical adoption", "argue")):
            return 1.0 if section_kind in {"discussion", "conclusion", "conclusions", "limitations", "results"} else 0.0
        if any(term in lowered for term in ("baseline", "auc", "accuracy", "results", "reduced it to")):
            return 1.0 if section_kind in {"results", "discussion", "conclusion", "conclusions"} else 0.0
        return 0.0

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
            fused_score = (
                dense_scores.get(section_id, 0.0)
                + lexical_scores.get(section_id, 0.0)
                + 0.22 * self._section_kind_score(question, str(section.metadata.get("section_kind", "")))
                + 0.05 * self._document_style_score(question, str(section.metadata.get("document_style", "")))
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
