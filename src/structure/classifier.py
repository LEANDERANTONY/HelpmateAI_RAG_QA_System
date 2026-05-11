"""LLM-backed document-style refinement for the indexing layer.

`infer_document_style` (in `src/structure/service.py`) runs at ingest time and
classifies a document by keyword heuristics. The heuristics are fast but
brittle — they miss whole-doc genre signals like "this is a standards
framework" vs "this is a research paper", and they over-fire on shared
markers ("appendix" → thesis).

This service runs during indexing, reads the first few pages plus the
outline, and asks a small LLM to pick the best style label. The keyword
classification stays as a fast fallback; the LLM result only wins when its
confidence clears `document_classifier_min_confidence`.

Downstream services (landmarks, chunk semantics, starter questions) all read
`document.metadata["document_style"]`, so the refined value flows everywhere
once it's written back.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

from src.config import Settings
from src.schemas import DocumentRecord, SectionRecord
from src.structure.service import infer_document_style


logger = logging.getLogger(__name__)


ALLOWED_STYLES: tuple[str, ...] = (
    "policy_document",
    "thesis_document",
    "research_paper",
    "framework_document",
    "generic_longform",
)

_MAX_STARTER_LEN = 130
_STARTER_COUNT = 3


@dataclass(frozen=True)
class ClassificationResult:
    style: str
    confidence: float
    source: str  # "llm" | "keyword" | "keyword_fallback"
    starter_questions: list[str] = field(default_factory=list)


def _clean_starter(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    text = " ".join(value.split())
    if not text:
        return None
    if len(text) > _MAX_STARTER_LEN:
        text = text[:_MAX_STARTER_LEN].rstrip(" ,;:-")
    if not text.endswith("?"):
        text = text.rstrip(".!") + "?"
    return text


def _sanitize_starter_questions(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    cleaned: list[str] = []
    seen: set[str] = set()
    for item in value:
        text = _clean_starter(item)
        if not text:
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(text)
        if len(cleaned) >= _STARTER_COUNT:
            break
    return cleaned if len(cleaned) == _STARTER_COUNT else []


class DocumentClassifierService:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.client = None
        if settings.openai_api_key and settings.document_classifier_enabled:
            try:
                from openai import OpenAI

                self.client = OpenAI(api_key=settings.openai_api_key)
            except Exception:
                self.client = None

    @staticmethod
    def _clean_text(text: str, limit: int = 1200) -> str:
        compact = " ".join(str(text or "").split())
        return compact[:limit]

    def _page_payload(self, document: DocumentRecord) -> list[dict[str, Any]]:
        pages = list(document.metadata.get("pages") or [])
        if not pages:
            return []
        max_pages = max(1, self.settings.document_classifier_max_pages)
        return [
            {
                "page_label": page.get("page_label", "Document"),
                "section_heading": page.get("section_heading", ""),
                "text": self._clean_text(str(page.get("text", ""))),
            }
            for page in pages[:max_pages]
        ]

    @staticmethod
    def _outline_payload(sections: list[SectionRecord]) -> list[str]:
        return [section.title for section in sections[:14] if section.title]

    def _prompt(
        self,
        document: DocumentRecord,
        page_payload: list[dict[str, Any]],
        outline_payload: list[str],
        keyword_style: str,
    ) -> str:
        return (
            "You classify long documents by genre and propose starter questions "
            "tailored to this specific document for a document-QA workspace. "
            "Return JSON with keys `style`, `confidence` (0..1), `reason` "
            "(short phrase), and `starter_questions` (array of EXACTLY 3 "
            "short questions).\n\n"
            "Allowed style labels and what each means:\n"
            "- policy_document: insurance, healthcare or contract policy with "
            "  terms, exclusions, claims, premium, waiting period.\n"
            "- research_paper: scholarly paper with abstract, methods, results, "
            "  citations to other work.\n"
            "- thesis_document: doctoral or master's thesis with chapters, "
            "  research aim, methodology, conclusions, future work.\n"
            "- framework_document: standard, guideline, framework, or governance "
            "  publication (e.g. NIST RMF, ISO standard, cybersecurity framework) "
            "  emphasizing principles, controls, functions, or audience guidance.\n"
            "- generic_longform: a long-form document that doesn't clearly fit "
            "  any of the above (books, manuals, internal reports).\n\n"
            "Style rules:\n"
            "- Pick the SINGLE best label.\n"
            "- Set confidence below 0.55 when signals are weak or mixed.\n"
            "- Do not invent label names outside the list above.\n\n"
            "Starter question rules:\n"
            "- Generate EXACTLY 3 short questions a user would ask about THIS "
            "  specific document on first reading.\n"
            "- Each question must be answerable from the document and grounded "
            "  in concrete terms or topics that appear in the supplied excerpts "
            "  or outline (names, headings, defined terms, sections, framework "
            "  components, etc.).\n"
            "- Cover DISTINCT angles — don't ask three variants of the same "
            "  question. Aim for breadth: structure/overview, key claims or "
            "  components, application/limits.\n"
            "- Each question must be under 130 characters.\n"
            "- Phrase as a natural question ending with `?`.\n"
            "- Do not include source markers, citations, or bracketed labels.\n\n"
            f"File name: {document.file_name}\n"
            f"Initial keyword guess: {keyword_style}\n"
            f"Outline: {json.dumps(outline_payload, ensure_ascii=True)}\n"
            f"Excerpts: {json.dumps(page_payload, ensure_ascii=True)}"
        )

    def classify(
        self,
        document: DocumentRecord,
        sections: list[SectionRecord],
    ) -> ClassificationResult:
        """Classify the document and propose doc-tuned starter questions.

        `source` is one of `llm`, `keyword`, or `keyword_fallback` so callers
        can record which path produced the label. starter_questions is empty
        when the LLM didn't return three valid items — the endpoint falls
        back to the deterministic per-style pack in that case.
        """
        keyword_style = str(document.metadata.get("document_style") or "generic_longform")
        if not keyword_style or keyword_style not in ALLOWED_STYLES:
            # Recompute on the fly if metadata is missing or malformed.
            pages = list(document.metadata.get("pages") or [])
            outline = list(document.metadata.get("outline") or [])
            keyword_style = infer_document_style(pages, outline)

        if self.client is None:
            return ClassificationResult(style=keyword_style, confidence=0.0, source="keyword")

        page_payload = self._page_payload(document)
        if not page_payload:
            return ClassificationResult(style=keyword_style, confidence=0.0, source="keyword")

        outline_payload = self._outline_payload(sections)
        prompt = self._prompt(document, page_payload, outline_payload, keyword_style)
        try:
            response = self.client.chat.completions.create(
                model=self.settings.document_classifier_model,
                messages=[
                    {
                        "role": "system",
                        "content": "You label documents by genre and propose starter questions for a QA workspace. Output JSON only.",
                    },
                    {"role": "user", "content": prompt},
                ],
                response_format={"type": "json_object"},
                temperature=0,
            )
            payload = json.loads(response.choices[0].message.content or "{}")
        except Exception as exc:
            logger.warning(
                "Document classifier LLM call failed; keeping keyword style (%s)",
                exc.__class__.__name__,
            )
            return ClassificationResult(style=keyword_style, confidence=0.0, source="keyword_fallback")

        style = str(payload.get("style", "")).strip().lower()
        try:
            confidence = max(0.0, min(1.0, float(payload.get("confidence", 0.0) or 0.0)))
        except (TypeError, ValueError):
            confidence = 0.0

        # Starter questions are independent of the style classification — even
        # if the LLM's style guess is weak, the questions are derived from the
        # document content directly, so they're still worth keeping.
        starter_questions = _sanitize_starter_questions(payload.get("starter_questions"))

        if style not in ALLOWED_STYLES:
            return ClassificationResult(
                style=keyword_style,
                confidence=0.0,
                source="keyword_fallback",
                starter_questions=starter_questions,
            )

        if confidence < self.settings.document_classifier_min_confidence:
            return ClassificationResult(
                style=keyword_style,
                confidence=confidence,
                source="keyword_fallback",
                starter_questions=starter_questions,
            )

        return ClassificationResult(
            style=style,
            confidence=confidence,
            source="llm",
            starter_questions=starter_questions,
        )

    def refine_document_style(
        self,
        document: DocumentRecord,
        sections: list[SectionRecord],
    ) -> ClassificationResult:
        """Run `classify` and write the chosen style + starter questions back
        into document metadata so downstream services and the starters
        endpoint can read them."""
        result = self.classify(document, sections)
        if document.metadata is None:
            document.metadata = {}
        document.metadata["document_style"] = result.style
        document.metadata["document_style_confidence"] = result.confidence
        document.metadata["document_style_source"] = result.source
        document.metadata["document_starter_questions"] = list(result.starter_questions)
        logger.info(
            "Document classifier finished: style=%s confidence=%.2f source=%s "
            "starter_questions=%d",
            result.style,
            result.confidence,
            result.source,
            len(result.starter_questions),
        )
        return result
