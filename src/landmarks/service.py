from __future__ import annotations

import json
from typing import Any

from src.config import Settings
from src.schemas import ChunkRecord, DocumentRecord
from src.sections.service import classify_front_matter


_ALLOWED_LANDMARK_TYPES = {
    "abstract",
    "acknowledgements",
    "author_correspondence",
    "definition_region",
    "executive_summary",
    "foreword",
    "glossary",
    "preface",
    "title_page",
    "volume_boundary",
}

_SEMANTIC_ROLE_BY_LANDMARK = {
    "abstract": "summary_evidence",
    "acknowledgements": "metadata_evidence",
    "author_correspondence": "metadata_evidence",
    "definition_region": "definition_evidence",
    "executive_summary": "summary_evidence",
    "foreword": "metadata_evidence",
    "glossary": "definition_evidence",
    "preface": "metadata_evidence",
    "title_page": "metadata_evidence",
    "volume_boundary": "metadata_evidence",
}


class DocumentLandmarkService:
    """Build compact, page-linked document landmarks at indexing time.

    The service deliberately proposes broad document-navigation landmarks rather
    than query-specific boosts. Retrieval consumes the resulting chunks through
    normal dense/lexical search and semantic evidence labels.
    """

    def __init__(self, settings: Settings):
        self.settings = settings
        self.client = None
        if settings.openai_api_key and settings.document_landmarks_enabled:
            try:
                from openai import OpenAI

                self.client = OpenAI(api_key=settings.openai_api_key)
            except Exception:
                self.client = None

    @staticmethod
    def _clean_text(text: str, limit: int = 1600) -> str:
        compact = " ".join(str(text or "").split())
        return compact[:limit]

    def _candidate_pages(self, document: DocumentRecord) -> list[dict[str, Any]]:
        pages = list(document.metadata.get("pages") or [])
        if not pages:
            return []
        max_pages = max(1, self.settings.document_landmarks_max_pages)
        edge_window = max(2, min(max_pages // 2, 8))
        selected: dict[str, dict[str, Any]] = {}

        def add(page: dict[str, Any], reason: str) -> None:
            page_label = str(page.get("page_label", "Document"))
            payload = dict(page)
            payload.setdefault("_landmark_candidate_reasons", [])
            payload["_landmark_candidate_reasons"] = list(payload.get("_landmark_candidate_reasons", [])) + [reason]
            selected[page_label] = payload

        for page in pages[:edge_window]:
            add(page, "front_window")
        for page in pages[-min(edge_window, len(pages)) :]:
            add(page, "back_window")

        for page in pages:
            if len(selected) >= max_pages:
                break
            title = str(page.get("section_heading") or page.get("page_label") or "")
            kind, score, _ = classify_front_matter(title, str(page.get("text", "")), [str(page.get("page_label", ""))])
            section_kind = str(page.get("section_kind", "")).lower()
            if score >= 0.5 or section_kind in {"abstract", "definitions", "overview"}:
                add(page, f"classified:{kind or section_kind}")

        return list(selected.values())[:max_pages]

    @staticmethod
    def _candidate_payload(page: dict[str, Any]) -> dict[str, Any]:
        return {
            "page_label": page.get("page_label", "Document"),
            "section_heading": page.get("section_heading", ""),
            "section_kind": page.get("section_kind", ""),
            "section_path": page.get("section_path", []),
            "candidate_reasons": page.get("_landmark_candidate_reasons", []),
            "text": DocumentLandmarkService._clean_text(str(page.get("text", ""))),
        }

    def _prompt(self, document: DocumentRecord, pages: list[dict[str, Any]]) -> str:
        payload = [self._candidate_payload(page) for page in pages]
        return (
            "You identify durable document landmarks for long-document retrieval.\n"
            "Return JSON with top-level key 'landmarks'. Each item must include:\n"
            "- landmark_type: one of abstract, acknowledgements, author_correspondence, definition_region, executive_summary, foreword, glossary, preface, title_page, volume_boundary\n"
            "- title: short label\n"
            "- page_label: page where the landmark appears\n"
            "- summary: concise description of what the page/region contains\n"
            "- key_value_facts: array of short factual strings visible in the supplied text, such as author, contact, signature, version, definition, volume scope, or funding facts\n"
            "- source_snippet: exact short snippet from supplied text supporting the landmark\n"
            "- confidence: number between 0 and 1\n"
            "Rules:\n"
            "- Create only landmarks supported by supplied page text.\n"
            "- Do not infer facts from outside the text.\n"
            "- Prefer no landmark over a vague landmark.\n"
            "- Use definition_region or glossary for acronym/definition-heavy regions.\n"
            "- Use author_correspondence for author, affiliation, contact, or corresponding-author blocks.\n"
            "- Use foreword/preface/title_page/executive_summary/abstract/volume_boundary only when the supplied text supports that region type.\n"
            f"Document: {document.file_name}\n"
            f"Document style: {document.metadata.get('document_style', 'generic_longform')}\n"
            f"Candidate pages: {json.dumps(payload, ensure_ascii=True)}"
        )

    def _llm_landmarks(self, document: DocumentRecord, pages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if self.client is None or not pages:
            return []
        try:
            # Don't pass temperature — document_landmarks_model defaults
            # to gpt-5.4-nano which (like all gpt-5.x / o-series
            # reasoning models) rejects any non-default temperature.
            # See openai_service._supports_temperature for the central
            # detector + Sentry HELPMATE-BACKEND-B for the incident
            # that motivated removing the param across the codebase.
            response = self.client.chat.completions.create(
                model=self.settings.document_landmarks_model,
                messages=[
                    {"role": "system", "content": "You extract compact document landmarks for retrieval indexing."},
                    {"role": "user", "content": self._prompt(document, pages)},
                ],
                response_format={"type": "json_object"},
            )
            payload = json.loads(response.choices[0].message.content or "{}")
        except Exception:
            return []

        landmarks = payload.get("landmarks", [])
        if not isinstance(landmarks, list):
            return []
        cleaned: list[dict[str, Any]] = []
        seen: set[tuple[str, str, str]] = set()
        candidate_page_labels = {str(page.get("page_label", "Document")) for page in pages}
        for item in landmarks:
            if not isinstance(item, dict):
                continue
            landmark_type = str(item.get("landmark_type", "")).strip().lower()
            page_label = str(item.get("page_label", "")).strip()
            title = self._clean_text(str(item.get("title", "")), limit=120)
            source_snippet = self._clean_text(str(item.get("source_snippet", "")), limit=500)
            if landmark_type not in _ALLOWED_LANDMARK_TYPES or page_label not in candidate_page_labels:
                continue
            if not title or not source_snippet:
                continue
            try:
                confidence = max(0.0, min(1.0, float(item.get("confidence", 0.0) or 0.0)))
            except (TypeError, ValueError):
                confidence = 0.0
            if confidence < self.settings.document_landmarks_min_confidence:
                continue
            key = (landmark_type, page_label, title.lower())
            if key in seen:
                continue
            seen.add(key)
            facts = item.get("key_value_facts", [])
            if isinstance(facts, list):
                key_value_facts = [self._clean_text(str(fact), limit=180) for fact in facts if self._clean_text(str(fact), limit=180)]
            else:
                key_value_facts = []
            cleaned.append(
                {
                    "landmark_type": landmark_type,
                    "page_label": page_label,
                    "title": title,
                    "summary": self._clean_text(str(item.get("summary", "")), limit=450),
                    "key_value_facts": key_value_facts[:10],
                    "source_snippet": source_snippet,
                    "confidence": confidence,
                }
            )
        return cleaned[: self.settings.document_landmarks_max_landmarks]

    @staticmethod
    def _landmark_text(landmark: dict[str, Any]) -> str:
        facts = landmark.get("key_value_facts") or []
        fact_text = "\n".join(f"- {fact}" for fact in facts)
        parts = [
            f"Document landmark: {landmark['title']}",
            f"Landmark type: {landmark['landmark_type']}",
            f"Summary: {landmark.get('summary', '')}",
            f"Key facts:\n{fact_text}" if fact_text else "",
            f"Source snippet: {landmark.get('source_snippet', '')}",
        ]
        return "\n".join(part for part in parts if part).strip()

    def annotate_chunks(self, document: DocumentRecord, chunks: list[ChunkRecord]) -> list[ChunkRecord]:
        if not self.settings.document_landmarks_enabled or self.client is None:
            return chunks
        pages = self._candidate_pages(document)
        landmarks = self._llm_landmarks(document, pages)
        if not landmarks:
            return chunks

        page_lookup = {str(page.get("page_label", "Document")): page for page in document.metadata.get("pages", []) or []}
        next_index = max((chunk.chunk_index for chunk in chunks), default=-1) + 1
        landmark_chunks: list[ChunkRecord] = []
        for offset, landmark in enumerate(landmarks):
            page_label = str(landmark["page_label"])
            page = page_lookup.get(page_label, {})
            landmark_type = str(landmark["landmark_type"])
            semantic_role = _SEMANTIC_ROLE_BY_LANDMARK.get(landmark_type, "metadata_evidence")
            confidence = float(landmark.get("confidence", 0.0) or 0.0)
            metadata = {
                "source_file": document.file_name,
                "page_label": page_label,
                "document_id": document.document_id,
                "section_heading": page.get("section_heading", landmark.get("title", "")),
                "section_path": page.get("section_path", []),
                "section_id": page.get("section_id", ""),
                "clause_ids": page.get("clause_ids", []),
                "primary_clause_id": (page.get("clause_ids") or [""])[0] if isinstance(page.get("clause_ids", []), list) else "",
                "content_type": "landmark",
                "artifact_entry": True,
                "artifact_type": "landmark",
                "artifact_id": f"{document.document_id}-landmark-{landmark_type}-{offset + 1}",
                "artifact_index": offset + 1,
                "artifact_parent_page": page_label,
                "retrieval_visibility": "targeted_or_summary",
                "document_style": page.get("document_style", document.metadata.get("document_style", "generic_longform")),
                "chunk_role_prior": "landmark_artifact",
                "body_evidence_score": max(0.72, confidence),
                "heading_only_flag": False,
                "low_value_prior": 0.05,
                "landmark_entry": True,
                "landmark_type": landmark_type,
                "landmark_title": landmark.get("title", ""),
                "landmark_confidence": confidence,
                "landmark_key_value_facts": list(landmark.get("key_value_facts", [])),
                "semantic_chunk_role": semantic_role,
                "semantic_chunk_confidence": confidence,
                "semantic_body_evidence_score": max(0.72, confidence),
            }
            landmark_chunks.append(
                ChunkRecord(
                    chunk_id=f"{document.document_id}-landmark-{next_index + offset:04d}",
                    document_id=document.document_id,
                    text=self._landmark_text(landmark),
                    chunk_index=next_index + offset,
                    page_label=page_label,
                    metadata=metadata,
                )
            )
        return [*chunks, *landmark_chunks]
