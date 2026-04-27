from __future__ import annotations

import json
from dataclasses import replace

from src.config import Settings
from src.schemas import DocumentRecord, SectionRecord, SectionSynopsisRecord


_TARGET_REGION_KINDS = {"overview", "procedure", "evidence", "discussion"}
_POLICY_TARGET_REGION_KINDS = {"overview", "definitions", "rules", "procedure"}
_POLICY_SECTION_TERMS = {
    "benefit",
    "benefits",
    "claim",
    "claims",
    "coverage",
    "definition",
    "definitions",
    "eligibility",
    "exclusion",
    "exclusions",
    "hospitalization",
    "hospitalisation",
    "preamble",
    "renewal",
    "sum insured",
    "waiting period",
    "waiting periods",
}


class SynopsisSemanticsService:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.client = None
        if settings.openai_api_key and settings.synopsis_semantics_enabled:
            try:
                from openai import OpenAI

                self.client = OpenAI(api_key=settings.openai_api_key)
            except Exception:
                self.client = None

    @staticmethod
    def _clamp(value: float, lower: float = 0.0, upper: float = 1.0) -> float:
        return max(lower, min(upper, value))

    @staticmethod
    def _policy_signal(section: SectionRecord, synopsis: SectionSynopsisRecord | None = None) -> bool:
        signal_text = " ".join(
            [
                section.title.lower(),
                " ".join(section.section_path).lower(),
                str(section.metadata.get("section_kind", "")).lower(),
                str(section.metadata.get("content_type", "")).lower(),
                str(synopsis.region_kind).lower() if synopsis is not None else "",
            ]
        )
        return any(term in signal_text for term in _POLICY_SECTION_TERMS)

    def _should_run_for_document(
        self,
        document: DocumentRecord,
        sections: list[SectionRecord],
        synopses: list[SectionSynopsisRecord],
    ) -> bool:
        gate_mode = self.settings.synopsis_semantics_gate_mode
        if gate_mode in {"off", "disabled"}:
            return False
        if gate_mode in {"all", "always"}:
            return True

        document_style = str(document.metadata.get("document_style", "generic_longform")).lower()

        structure_confidences = [
            float(section.metadata.get("structure_confidence", 1.0) or 1.0)
            for section in sections
        ]
        structure_confidence = min(structure_confidences) if structure_confidences else 1.0
        repaired = any(bool(section.metadata.get("structure_repaired")) for section in sections)
        repair_reasons = {
            str(reason).strip().lower()
            for section in sections
            for reason in section.metadata.get("structure_repair_reasons", [])
            if str(reason).strip()
        }
        noisy_structure = any(
            token in repair_reasons
            for token in {
                "repeated publisher/header noise appears in section titles.",
                "section titles look like running headers instead of semantic headings.",
                "research-style document has weak canonical heading coverage.",
            }
        )

        if repaired:
            return True
        if structure_confidence <= self.settings.structure_repair_confidence_threshold and noisy_structure:
            return True
        if document_style == "research_paper" and noisy_structure:
            return True
        if document_style == "policy_document":
            if structure_confidence <= self.settings.structure_repair_confidence_threshold:
                return True
            synopses_by_id = {synopsis.section_id: synopsis for synopsis in synopses}
            return any(
                self._policy_signal(section, synopses_by_id.get(section.section_id))
                and len(section.text) >= 600
                and self._quality_score(section, synopses_by_id[section.section_id]) < 0.72
                for section in sections
                if section.section_id in synopses_by_id
            )
        return False

    def _quality_score(self, section: SectionRecord, synopsis: SectionSynopsisRecord) -> float:
        synopsis_text = synopsis.synopsis.strip()
        title = section.title.strip().lower()
        summary = section.summary.strip().lower()
        text = section.text.strip()
        document_style = str(section.metadata.get("document_style", "")).lower()
        quality = 0.0
        if len(synopsis_text) >= 160:
            quality += 0.4
        if len(synopsis.key_terms) >= 4:
            quality += 0.2
        if title and synopsis_text.lower() != title:
            quality += 0.15
        if summary and summary not in synopsis_text.lower():
            quality += 0.1
        if len(text) >= 600:
            quality += 0.15
        if document_style == "policy_document" and self._policy_signal(section, synopsis):
            lowered_synopsis = synopsis_text.lower()
            if any(term in lowered_synopsis for term in _POLICY_SECTION_TERMS):
                quality += 0.1
        return self._clamp(quality)

    def _review_priority(self, document_style: str, section: SectionRecord, synopsis: SectionSynopsisRecord) -> float:
        if synopsis.metadata.get("topology_low_value"):
            return 0.0
        if document_style == "policy_document":
            if synopsis.region_kind not in _POLICY_TARGET_REGION_KINDS and not self._policy_signal(section, synopsis):
                return 0.0
        elif synopsis.region_kind not in _TARGET_REGION_KINDS:
            return 0.0
        quality = self._quality_score(section, synopsis)
        priority = 1.0 - quality
        if section.metadata.get("structure_repaired"):
            priority += 0.25
        if len(section.text) >= 1200:
            priority += 0.15
        if document_style == "policy_document" and self._policy_signal(section, synopsis):
            priority += 0.15
        return priority

    def _candidate_section_ids(
        self,
        document: DocumentRecord,
        sections_by_id: dict[str, SectionRecord],
        synopses: list[SectionSynopsisRecord],
    ) -> list[str]:
        document_style = str(document.metadata.get("document_style", "generic_longform")).lower()
        ranked = sorted(
            (
                (synopsis.section_id, self._review_priority(document_style, sections_by_id[synopsis.section_id], synopsis))
                for synopsis in synopses
                if synopsis.section_id in sections_by_id
            ),
            key=lambda item: item[1],
            reverse=True,
        )
        return [
            section_id
            for section_id, priority in ranked[: self.settings.synopsis_semantics_max_sections]
            if priority > 0.2
        ]

    @staticmethod
    def _section_payload(section: SectionRecord, synopsis: SectionSynopsisRecord) -> dict[str, object]:
        return {
            "section_id": section.section_id,
            "title": section.title,
            "region_kind": synopsis.region_kind,
            "section_kind": section.metadata.get("section_kind", ""),
            "current_synopsis": synopsis.synopsis[:700],
            "current_key_terms": synopsis.key_terms[:8],
            "summary": section.summary[:500],
            "text_excerpt": section.text[:1200],
        }

    def _annotation_prompt(
        self,
        document: DocumentRecord,
        payload: list[dict[str, object]],
    ) -> str:
        return (
            "You rewrite indexing synopses for long-document retrieval.\n"
            "Return JSON with top-level key 'sections'. Each item must include:\n"
            "- section_id\n"
            "- synopsis: 1 to 3 sentences, concise but semantically rich\n"
            "- key_terms: list of 4 to 8 short terms\n"
            "Rules:\n"
            "- Focus on the actual content and purpose of the section\n"
            "- Avoid repeating the title mechanically\n"
            "- Prefer concrete findings, methods, scope, or rules depending on the section\n"
            "- Do not invent facts outside the excerpt\n"
            "- For policy documents, surface what is covered, excluded, defined, required, time-limited, or procedurally required when present\n"
            "- For policy documents, mention waiting periods, claim steps, eligibility, renewal, or benefit conditions when supported by the excerpt\n"
            f"Document: {document.file_name}\n"
            f"Document style: {document.metadata.get('document_style', 'generic_longform')}\n"
            f"Sections: {json.dumps(payload, ensure_ascii=True)}"
        )

    def _llm_annotations(
        self,
        document: DocumentRecord,
        payload: list[dict[str, object]],
    ) -> dict[str, dict[str, object]]:
        if self.client is None or not payload:
            return {}
        try:
            response = self.client.chat.completions.create(
                model=self.settings.synopsis_semantics_model,
                messages=[
                    {"role": "system", "content": "You write compact semantic synopses for document retrieval."},
                    {"role": "user", "content": self._annotation_prompt(document, payload)},
                ],
                response_format={"type": "json_object"},
            )
            body = json.loads(response.choices[0].message.content or "{}")
        except Exception:
            return {}

        items = body.get("sections", [])
        if not isinstance(items, list):
            return {}

        annotations: dict[str, dict[str, object]] = {}
        for item in items:
            if not isinstance(item, dict):
                continue
            section_id = str(item.get("section_id", "")).strip()
            synopsis = str(item.get("synopsis", "")).strip()
            key_terms = item.get("key_terms", [])
            if not section_id or not synopsis or not isinstance(key_terms, list):
                continue
            cleaned_terms = [str(term).strip() for term in key_terms if str(term).strip()]
            annotations[section_id] = {
                "synopsis": synopsis[:1200],
                "key_terms": cleaned_terms[:8],
            }
        return annotations

    def annotate_synopses(
        self,
        document: DocumentRecord,
        sections: list[SectionRecord],
        synopses: list[SectionSynopsisRecord],
    ) -> list[SectionSynopsisRecord]:
        if not self.settings.synopsis_semantics_enabled or self.client is None:
            return synopses
        if not self._should_run_for_document(document, sections, synopses):
            return synopses

        sections_by_id = {section.section_id: section for section in sections}
        selected_ids = self._candidate_section_ids(document, sections_by_id, synopses)
        if not selected_ids:
            return synopses

        payload = [
            self._section_payload(sections_by_id[section_id], synopsis)
            for synopsis in synopses
            for section_id in [synopsis.section_id]
            if section_id in selected_ids and section_id in sections_by_id
        ]
        annotations = self._llm_annotations(document, payload)
        if not annotations:
            return synopses

        updated: list[SectionSynopsisRecord] = []
        for synopsis in synopses:
            annotation = annotations.get(synopsis.section_id)
            if not annotation:
                updated.append(synopsis)
                continue
            metadata = dict(synopsis.metadata)
            metadata["semantic_synopsis_written"] = True
            updated.append(
                replace(
                    synopsis,
                    synopsis=str(annotation["synopsis"]),
                    key_terms=list(annotation["key_terms"]),
                    metadata=metadata,
                )
            )
        return updated
