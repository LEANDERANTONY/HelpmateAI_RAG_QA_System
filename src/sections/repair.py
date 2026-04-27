from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass

from src.config import Settings
from src.schemas import DocumentRecord, SectionRecord
from src.sections.service import (
    _best_title,
    _clean_line,
    _extract_canonical_heading,
    _section_aliases,
    _section_summary,
    document_overview_section,
)


_SECTION_KIND_MAP = {
    "abstract": "abstract",
    "background": "background",
    "benefit": "benefits",
    "benefits": "benefits",
    "claim": "claims",
    "claim procedure": "claims",
    "claim procedures": "claims",
    "claims": "claims",
    "conclusion": "conclusion",
    "conclusions": "conclusion",
    "coverage": "coverage",
    "definition": "definitions",
    "definitions": "definitions",
    "discussion": "discussion",
    "eligibility": "eligibility",
    "exclusion": "exclusions",
    "exclusions": "exclusions",
    "future work": "future work",
    "future directions": "future work",
    "general": "general",
    "implementation": "methodology",
    "introduction": "introduction",
    "limitations": "limitations",
    "method": "methodology",
    "methodology": "methodology",
    "methods": "methodology",
    "overview": "overview",
    "preamble": "overview",
    "references": "references",
    "related work": "background",
    "renewal": "renewal",
    "result": "results",
    "results": "results",
    "schedule of benefits": "benefits",
    "waiting period": "waiting periods",
    "waiting periods": "waiting periods",
}

_NOISY_TITLE_PATTERNS = (
    "article",
    "doi.org",
    "nature medicine",
    "volume ",
    "author manuscript",
    "available in pmc",
)

_DEFAULT_CONFIDENCE_PENALTIES = {
    "long_document_too_few_sections": 0.28,
    "coarse_for_length": 0.24,
    "duplicate_titles": 0.14,
    "header_dominated_titles": 0.18,
    "noisy_titles": 0.22,
    "policy_too_few_sections": 0.18,
    "weak_canonical_headings": 0.18,
}


@dataclass(frozen=True)
class StructureRepairDecision:
    confidence: float
    should_repair: bool
    reasons: list[str]
    reason_codes: list[str]


class StructureRepairService:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.client = None
        if settings.openai_api_key and settings.structure_repair_enabled:
            try:
                from openai import OpenAI

                self.client = OpenAI(api_key=settings.openai_api_key)
            except Exception:
                self.client = None

    @staticmethod
    def _is_noisy_title(title: str) -> bool:
        lowered = title.lower()
        return any(pattern in lowered for pattern in _NOISY_TITLE_PATTERNS)

    @staticmethod
    def _header_signature(title: str) -> str:
        compact = _clean_line(title).lower()
        compact = compact.replace("…", " ")
        compact = re.sub(r"\bpage\s+\d+\b", " ", compact)
        compact = re.sub(r"\b\d+\b", " ", compact)
        compact = re.sub(r"\s+", " ", compact).strip(" .,:;|-")
        return compact

    @classmethod
    def _is_header_dominated_title(cls, title: str) -> bool:
        lowered = _clean_line(title).lower()
        if cls._is_noisy_title(title):
            return True
        if lowered.count(",") >= 3:
            return True
        if re.match(r"^\d+\s+[a-z]", lowered):
            return True
        if re.search(r"\bgeneration\s+\d+\b", lowered):
            return True
        signature = cls._header_signature(title)
        if len(signature) >= 28 and any(char.isdigit() for char in lowered):
            return True
        return False

    @classmethod
    def assess(
        cls,
        document: DocumentRecord,
        sections: list[SectionRecord],
        *,
        threshold: float = 0.62,
        penalty_overrides: dict[str, float] | None = None,
    ) -> StructureRepairDecision:
        reasons: list[str] = []
        reason_codes: list[str] = []
        if not sections:
            return StructureRepairDecision(
                confidence=0.0,
                should_repair=False,
                reasons=["No sections were available to assess."],
                reason_codes=["no_sections"],
            )

        confidence = 0.92
        penalties = {**_DEFAULT_CONFIDENCE_PENALTIES, **(penalty_overrides or {})}
        page_count = document.page_count or len(document.metadata.get("pages", []))
        section_count = len(sections)
        document_style = str(document.metadata.get("document_style", "generic_longform")).lower()

        unique_titles = {section.title.strip().lower() for section in sections if section.title.strip()}
        duplicate_ratio = 1 - (len(unique_titles) / max(section_count, 1))
        noisy_ratio = sum(1 for section in sections if cls._is_noisy_title(section.title)) / max(section_count, 1)
        header_like_ratio = sum(1 for section in sections if cls._is_header_dominated_title(section.title)) / max(section_count, 1)
        signatures = Counter(
            signature
            for section in sections
            for signature in [cls._header_signature(section.title)]
            if signature
        )
        repeated_header_ratio = max(signatures.values(), default=0) / max(section_count, 1)
        canonical_count = sum(1 for section in sections if _extract_canonical_heading(section.text) or section.title.lower() in _SECTION_KIND_MAP)

        if page_count >= 12 and section_count <= 4:
            confidence -= penalties["long_document_too_few_sections"]
            reasons.append("Long document collapsed into too few sections.")
            reason_codes.append("long_document_too_few_sections")
        elif page_count >= 6 and section_count <= 2:
            confidence -= penalties["coarse_for_length"]
            reasons.append("Document structure is too coarse for its length.")
            reason_codes.append("coarse_for_length")
        if document_style == "policy_document" and page_count >= 20 and section_count <= 4:
            confidence -= penalties["policy_too_few_sections"]
            reasons.append("Policy document collapsed into too few semantic sections.")
            reason_codes.append("policy_too_few_sections")
        if duplicate_ratio >= 0.3:
            confidence -= penalties["duplicate_titles"]
            reasons.append("Section titles are heavily duplicated.")
            reason_codes.append("duplicate_titles")
        if (
            document_style in {"research_paper", "thesis_document"}
            and header_like_ratio >= 0.25
            and repeated_header_ratio >= 0.15
        ):
            confidence -= penalties["header_dominated_titles"]
            reasons.append("Section titles look like running headers instead of semantic headings.")
            reason_codes.append("header_dominated_titles")
        if noisy_ratio >= 0.25:
            confidence -= penalties["noisy_titles"]
            reasons.append("Repeated publisher/header noise appears in section titles.")
            reason_codes.append("noisy_titles")
        if document_style in {"research_paper", "thesis_document"} and canonical_count <= 2:
            confidence -= penalties["weak_canonical_headings"]
            reasons.append("Research-style document has weak canonical heading coverage.")
            reason_codes.append("weak_canonical_headings")

        confidence = max(0.0, min(1.0, confidence))
        should_repair = confidence < threshold and page_count >= 6
        if not reasons:
            reasons.append("Deterministic structure extraction looked healthy.")
            reason_codes.append("healthy_structure")
        return StructureRepairDecision(
            confidence=confidence,
            should_repair=should_repair,
            reasons=reasons,
            reason_codes=reason_codes,
        )

    def _should_apply_llm_repair(self, decision: StructureRepairDecision) -> bool:
        if not self.settings.structure_repair_require_header_dominated:
            return True
        return any(code in decision.reason_codes for code in {"header_dominated_titles", "policy_too_few_sections"})

    @staticmethod
    def _page_brief(page: dict[str, object]) -> dict[str, str]:
        text = str(page.get("text", ""))
        lines = [_clean_line(line) for line in text.splitlines() if _clean_line(line)]
        useful: list[str] = []
        for line in lines[:12]:
            if len(useful) >= 3:
                break
            if len(line) > 180:
                continue
            useful.append(line)
        heading = _best_title(
            str(page.get("section_heading", "") or (list(page.get("section_path", []))[-1] if page.get("section_path") else page.get("page_label", "Document"))),
            text,
            str(page.get("page_label", "Document")),
        )
        return {
            "page_label": str(page.get("page_label", "Document")),
            "heading_candidate": heading,
            "excerpt": " | ".join(useful)[:600],
        }

    def _llm_assignments(self, document: DocumentRecord, pages: list[dict[str, object]]) -> list[dict[str, str]]:
        if self.client is None:
            return []
        page_payload = [self._page_brief(page) for page in pages]
        document_style = str(document.metadata.get("document_style", "generic_longform"))
        valid_kinds = [
            "overview",
            "abstract",
            "introduction",
            "background",
            "methodology",
            "results",
            "discussion",
            "conclusion",
            "future work",
            "limitations",
            "definitions",
            "references",
            "general",
            "coverage",
            "benefits",
            "exclusions",
            "claims",
            "waiting periods",
            "eligibility",
            "renewal",
        ]
        extra_rules = ""
        if document_style == "policy_document":
            extra_rules = (
                "- for policy documents, prefer semantic headings such as Preamble, Definitions, Coverage, Benefits, "
                "Exclusions, Waiting Periods, Claims, Eligibility, Renewal, General Conditions, or Schedule of Benefits when clearly present\n"
                "- distinguish claim-process sections from benefits or exclusion sections\n"
                "- preserve contiguous clause-heavy policy pages as separate semantic sections rather than merging the whole policy into one block\n"
            )
        prompt = (
            "Repair the section structure of a long-form document.\n"
            "You are given page-level heading candidates and excerpts. Infer clean contiguous sections.\n"
            "Return JSON with a top-level key 'pages' containing one item per page.\n"
            "Each item must include: page_label, title, section_kind.\n"
            f"Valid section_kind values: {', '.join(valid_kinds)}.\n"
            "Rules:\n"
            "- remove repeated publisher/header noise\n"
            "- prefer canonical research-paper section roles when clearly present\n"
            "- keep page assignments contiguous where possible\n"
            "- do not invent missing pages\n"
            f"{extra_rules}"
            f"Document: {document.file_name}\n"
            f"Document style: {document_style}\n"
            f"Pages: {json.dumps(page_payload, ensure_ascii=True)}"
        )
        try:
            response = self.client.chat.completions.create(
                model=self.settings.structure_repair_model,
                messages=[
                    {"role": "system", "content": "You repair section structure for long-document indexing."},
                    {"role": "user", "content": prompt},
                ],
                response_format={"type": "json_object"},
            )
            payload = json.loads(response.choices[0].message.content or "{}")
            page_assignments = payload.get("pages", [])
            return [item for item in page_assignments if isinstance(item, dict)]
        except Exception:
            return []

    @staticmethod
    def _normalized_kind(section_kind: str) -> str:
        compact = " ".join(section_kind.strip().lower().split())
        return _SECTION_KIND_MAP.get(compact, compact or "general")

    def _build_repaired_sections(
        self,
        document: DocumentRecord,
        pages: list[dict[str, object]],
        assignments: list[dict[str, str]],
    ) -> list[SectionRecord]:
        assignment_by_page = {str(item.get("page_label", "")): item for item in assignments if item.get("page_label")}
        grouped: list[dict[str, object]] = []

        for page in pages:
            page_label = str(page.get("page_label", "Document"))
            assignment = assignment_by_page.get(page_label, {})
            raw_title = str(assignment.get("title", "")).strip() or _best_title(
                str(page.get("section_heading", "") or page_label),
                str(page.get("text", "")),
                page_label,
            )
            section_kind = self._normalized_kind(str(assignment.get("section_kind", "")))
            if not section_kind:
                section_kind = str(page.get("section_kind", "") or "general")
            canonical_from_text = _extract_canonical_heading(str(page.get("text", "")))
            if canonical_from_text:
                title = canonical_from_text
            elif self._is_noisy_title(raw_title) and section_kind in {
                "abstract",
                "background",
                "conclusion",
                "discussion",
                "future work",
                "introduction",
                "methodology",
                "results",
                "overview",
                "references",
            }:
                title = "Overview" if section_kind == "overview" else section_kind.title()
            else:
                title = raw_title[:120]

            if grouped and grouped[-1]["title"] == title and grouped[-1]["section_kind"] == section_kind:
                grouped[-1]["pages"].append(page)
            else:
                grouped.append({"title": title, "section_kind": section_kind, "pages": [page]})

        sections: list[SectionRecord] = []
        for index, group in enumerate(grouped, start=1):
            group_pages = list(group["pages"])
            texts = [str(page.get("text", "")).strip() for page in group_pages if str(page.get("text", "")).strip()]
            page_labels = [str(page.get("page_label", "Document")) for page in group_pages]
            title = str(group["title"])
            section_kind = str(group["section_kind"])
            text = "\n\n".join(texts).strip()
            summary = _section_summary(title, text)
            sections.append(
                SectionRecord(
                    section_id=f"{document.document_id}-repair-{index:02d}-{re.sub(r'[^a-z0-9]+', '-', title.lower()).strip('-') or 'section'}",
                    document_id=document.document_id,
                    title=title,
                    summary=summary,
                    text=text,
                    page_labels=page_labels,
                    section_path=[title],
                    clause_ids=[],
                    metadata={
                        "source_file": document.file_name,
                        "content_type": section_kind,
                        "primary_page_label": page_labels[0] if page_labels else "Document",
                        "section_key": title,
                        "section_heading": title,
                        "section_kind": section_kind,
                        "document_style": str(document.metadata.get("document_style", "generic_longform")),
                        "section_aliases": _section_aliases(title, section_kind, [title]),
                        "structure_repaired": True,
                    },
                )
            )
        overview = document_overview_section(document, sections)
        if overview is not None:
            overview.metadata["structure_repaired"] = True
            sections.insert(0, overview)
        return sections

    def repair_if_needed(self, document: DocumentRecord, sections: list[SectionRecord]) -> tuple[list[SectionRecord], StructureRepairDecision]:
        decision = self.assess(
            document,
            sections,
            threshold=self.settings.structure_repair_confidence_threshold,
        )
        if (
            not self.settings.structure_repair_enabled
            or decision.confidence >= self.settings.structure_repair_confidence_threshold
            or self.client is None
            or not self._should_apply_llm_repair(decision)
        ):
            return sections, decision

        pages = list(document.metadata.get("pages") or [])
        if not pages:
            return sections, decision

        assignments = self._llm_assignments(document, pages)
        if not assignments:
            return sections, decision

        repaired_sections = self._build_repaired_sections(document, pages, assignments)
        if not repaired_sections:
            return sections, decision

        updated_reasons = [*decision.reasons, "Applied indexing-time LLM structure repair."]
        return repaired_sections, StructureRepairDecision(
            confidence=decision.confidence,
            should_repair=True,
            reasons=updated_reasons,
            reason_codes=list(decision.reason_codes),
        )
