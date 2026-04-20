from __future__ import annotations

import json
from dataclasses import replace

from src.config import Settings
from src.schemas import ChunkRecord, DocumentRecord


_ALLOWED_CHUNK_ROLES = {
    "body_evidence",
    "heading_stub",
    "navigation_noise",
    "reference_noise",
    "summary_evidence",
    "table_fragment",
}


class ChunkSemanticsService:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.client = None
        if settings.openai_api_key and settings.chunk_semantics_enabled:
            try:
                from openai import OpenAI

                self.client = OpenAI(api_key=settings.openai_api_key)
            except Exception:
                self.client = None

    @staticmethod
    def _clamp(value: float, lower: float = 0.0, upper: float = 1.0) -> float:
        return max(lower, min(upper, value))

    def _review_priority(self, chunk: ChunkRecord) -> float:
        metadata = chunk.metadata
        role = str(metadata.get("chunk_role_prior", "")).lower()
        front_matter_kind = str(metadata.get("front_matter_kind", "")).lower()
        body_score = float(metadata.get("body_evidence_score", 0.5) or 0.5)
        text = chunk.text.strip()

        priority = 0.0
        if role in {"heading_stub", "navigation_like", "reference_like", "table_fragment"}:
            priority += 1.2
        if front_matter_kind and front_matter_kind != "body":
            priority += 1.0
        if 0.15 <= body_score <= 0.85:
            priority += 0.5
        if len(text) <= 220:
            priority += 0.3
        if len(text.splitlines()) >= 3 and body_score <= 0.65:
            priority += 0.2
        return priority

    def _candidate_chunks(self, chunks: list[ChunkRecord]) -> list[ChunkRecord]:
        suspicious = [chunk for chunk in chunks if self._review_priority(chunk) > 0.0]
        suspicious.sort(key=self._review_priority, reverse=True)
        return suspicious[: self.settings.chunk_semantics_max_review_chunks]

    @staticmethod
    def _chunk_payload(chunks: list[ChunkRecord], index: int) -> dict[str, object]:
        chunk = chunks[index]
        previous_excerpt = chunks[index - 1].text[:180].replace("\n", " ") if index > 0 else ""
        next_excerpt = chunks[index + 1].text[:180].replace("\n", " ") if index + 1 < len(chunks) else ""
        return {
            "chunk_id": chunk.chunk_id,
            "page_label": chunk.metadata.get("page_label", chunk.page_label),
            "section_heading": chunk.metadata.get("section_heading", ""),
            "section_kind": chunk.metadata.get("section_kind", ""),
            "deterministic_role": chunk.metadata.get("chunk_role_prior", ""),
            "deterministic_body_evidence_score": chunk.metadata.get("body_evidence_score", 0.5),
            "front_matter_kind": chunk.metadata.get("front_matter_kind", "body"),
            "text": chunk.text[:700],
            "previous_excerpt": previous_excerpt,
            "next_excerpt": next_excerpt,
        }

    def _annotation_prompt(self, document: DocumentRecord, chunks: list[ChunkRecord], review_indices: list[int]) -> str:
        payload = [self._chunk_payload(chunks, index) for index in review_indices]
        return (
            "You classify suspicious document chunks for indexing-time retrieval quality.\n"
            "Return JSON with top-level key 'chunks'. Each item must include:\n"
            "- chunk_id\n"
            "- role: one of body_evidence, heading_stub, navigation_noise, reference_noise, summary_evidence, table_fragment\n"
            "- confidence: number between 0 and 1\n"
            "- body_evidence_score: number between 0 and 1\n"
            "Rules:\n"
            "- body_evidence means substantive explanatory/supporting text\n"
            "- summary_evidence means substantive overview/findings text useful for synthesis questions\n"
            "- heading_stub means mostly a heading/title with little standalone evidence\n"
            "- navigation_noise means table-of-contents/listing/front-matter navigation text\n"
            "- reference_noise means bibliography, citation, publisher, or author-reference text\n"
            "- table_fragment means mostly tabular or metric fragments with little prose explanation\n"
            "- Prefer conservative labels. If a chunk is ambiguous but informative, choose body_evidence or summary_evidence rather than noise.\n"
            f"Document: {document.file_name}\n"
            f"Document style: {document.metadata.get('document_style', 'generic_longform')}\n"
            f"Chunks: {json.dumps(payload, ensure_ascii=True)}"
        )

    def _llm_annotations(self, document: DocumentRecord, chunks: list[ChunkRecord], review_indices: list[int]) -> dict[str, dict[str, float | str]]:
        if self.client is None or not review_indices:
            return {}
        try:
            response = self.client.chat.completions.create(
                model=self.settings.chunk_semantics_model,
                messages=[
                    {"role": "system", "content": "You classify document chunks for long-document retrieval indexing."},
                    {"role": "user", "content": self._annotation_prompt(document, chunks, review_indices)},
                ],
                response_format={"type": "json_object"},
            )
            payload = json.loads(response.choices[0].message.content or "{}")
        except Exception:
            return {}

        chunk_items = payload.get("chunks", [])
        if not isinstance(chunk_items, list):
            return {}

        annotations: dict[str, dict[str, float | str]] = {}
        for item in chunk_items:
            if not isinstance(item, dict):
                continue
            chunk_id = str(item.get("chunk_id", "")).strip()
            role = str(item.get("role", "")).strip().lower()
            if not chunk_id or role not in _ALLOWED_CHUNK_ROLES:
                continue
            confidence = self._clamp(float(item.get("confidence", 0.0) or 0.0))
            body_evidence_score = self._clamp(float(item.get("body_evidence_score", 0.5) or 0.5))
            annotations[chunk_id] = {
                "semantic_chunk_role": role,
                "semantic_chunk_confidence": confidence,
                "semantic_body_evidence_score": body_evidence_score,
            }
        return annotations

    def annotate_chunks(self, document: DocumentRecord, chunks: list[ChunkRecord]) -> list[ChunkRecord]:
        if not self.settings.chunk_semantics_enabled or self.client is None:
            return chunks

        candidate_chunks = self._candidate_chunks(chunks)
        if not candidate_chunks:
            return chunks

        candidate_ids = {chunk.chunk_id for chunk in candidate_chunks}
        review_indices = [index for index, chunk in enumerate(chunks) if chunk.chunk_id in candidate_ids]
        annotations = self._llm_annotations(document, chunks, review_indices)
        if not annotations:
            return chunks

        annotated_chunks: list[ChunkRecord] = []
        for chunk in chunks:
            update = annotations.get(chunk.chunk_id)
            if not update:
                annotated_chunks.append(chunk)
                continue
            metadata = dict(chunk.metadata)
            metadata.update(update)
            annotated_chunks.append(replace(chunk, metadata=metadata))
        return annotated_chunks
