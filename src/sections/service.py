from __future__ import annotations

import re
from collections import OrderedDict

from src.schemas import DocumentRecord, SectionRecord


CANONICAL_HEADINGS = (
    "abstract",
    "introduction",
    "background",
    "methodology",
    "methods",
    "materials and methods",
    "results",
    "discussion",
    "conclusion",
    "conclusions",
    "future work",
    "future directions",
    "limitations",
    "references",
)


def _clean_line(line: str) -> str:
    return " ".join(line.strip().split())


def _looks_like_noise(line: str) -> bool:
    lowered = line.lower()
    if not lowered:
        return True
    if "@" in line:
        return True
    if lowered.startswith("doi") or lowered.startswith("http"):
        return True
    if any(token in lowered for token in ("author manuscript", "available in pmc", "[pubmed:", "copyright")):
        return True
    if re.fullmatch(r"page\s+\d+", lowered):
        return True
    if len(line.split()) > 24:
        return False
    digits = sum(1 for char in line if char.isdigit())
    letters = sum(1 for char in line if char.isalpha())
    if digits > letters and digits >= 6:
        return True
    return False


def _extract_canonical_heading(text: str) -> str:
    lines = [_clean_line(line) for line in text.splitlines() if _clean_line(line)]
    for line in lines[:20]:
        normalized = re.sub(r"^\d+(?:\.\d+)*\s*", "", line).strip(" :-").lower()
        if normalized in CANONICAL_HEADINGS:
            return normalized.title()
        for heading in CANONICAL_HEADINGS:
            if normalized.startswith(f"{heading}:"):
                return heading.title()
    return ""


def _best_title(fallback_title: str, text: str, page_label: str) -> str:
    canonical = _extract_canonical_heading(text)
    if canonical:
        return canonical

    lines = [_clean_line(line) for line in text.splitlines() if _clean_line(line)]
    for line in lines[:15]:
        candidate = re.sub(r"^\d+(?:\.\d+)*\s*", "", line).strip(" :-")
        lowered = candidate.lower()
        if _looks_like_noise(candidate):
            continue
        if len(candidate.split()) > 12:
            continue
        if lowered in CANONICAL_HEADINGS:
            return candidate.title()
        if candidate.isupper() or candidate.istitle():
            return candidate

    return fallback_title or page_label


def _summary_sentences(text: str) -> list[str]:
    normalized = re.sub(r"\s+", " ", text).strip()
    return [sentence.strip() for sentence in re.split(r"(?<=[.!?])\s+", normalized) if sentence.strip()]


def _representative_excerpt(text: str, limit: int = 3) -> str:
    lines = [_clean_line(line) for line in text.splitlines() if _clean_line(line)]
    filtered: list[str] = []
    for line in lines:
        if _looks_like_noise(line):
            continue
        filtered.append(line)
    sentences = _summary_sentences(" ".join(filtered))
    if not sentences:
        sentences = _summary_sentences(text)
    return " ".join(sentences[:limit])[:900]


def _section_summary(title: str, text: str) -> str:
    excerpt = _representative_excerpt(text, limit=3)
    if not excerpt:
        return title

    sentences = _summary_sentences(excerpt)
    if len(sentences) >= 3 and title.lower() in {"discussion", "conclusion", "conclusions", "future work", "future directions"}:
        return " ".join(sentences[-2:])[:900]
    if len(sentences) >= 3 and title.lower() in {"abstract", "introduction", "background"}:
        return " ".join(sentences[:3])[:900]
    if len(sentences) >= 4:
        return f"{sentences[0]} {sentences[-1]}"[:900]
    return excerpt[:900]


def build_sections(document: DocumentRecord) -> list[SectionRecord]:
    pages = document.metadata.get("pages") or []
    grouped: OrderedDict[str, dict] = OrderedDict()

    for page in pages:
        section_path = list(page.get("section_path", []))
        page_label = str(page.get("page_label", "Document"))
        section_id = str(page.get("section_id") or "|".join(section_path) or page_label)
        text = str(page.get("text", "")).strip()
        title = _best_title(
            str(page.get("section_heading", "") or (section_path[-1] if section_path else page_label)),
            text,
            page_label,
        )
        entry = grouped.setdefault(
            section_id,
            {
                "title": title,
                "texts": [],
                "page_labels": [],
                "section_path": section_path,
                "clause_ids": [],
                "content_type": str(page.get("content_type", "general")),
                "source_file": document.file_name,
            },
        )
        if text:
            entry["texts"].append(text)
        if page_label not in entry["page_labels"]:
            entry["page_labels"].append(page_label)
        for clause_id in page.get("clause_ids", []):
            if clause_id not in entry["clause_ids"]:
                entry["clause_ids"].append(clause_id)
        canonical_title = _extract_canonical_heading(text)
        if canonical_title and entry["title"] not in {canonical_title, title}:
            entry["title"] = canonical_title

    sections: list[SectionRecord] = []
    for section_id, payload in grouped.items():
        text = "\n\n".join(payload["texts"]).strip()
        title = _best_title(payload["title"], text, payload["page_labels"][0] if payload["page_labels"] else "Document")
        summary = _section_summary(title, text)
        sections.append(
            SectionRecord(
                section_id=section_id,
                document_id=document.document_id,
                title=title,
                summary=summary,
                text=text,
                page_labels=list(payload["page_labels"]),
                section_path=list(payload["section_path"]),
                clause_ids=list(payload["clause_ids"]),
                metadata={
                    "source_file": payload["source_file"],
                    "content_type": payload["content_type"],
                    "primary_page_label": payload["page_labels"][0] if payload["page_labels"] else "Document",
                    "section_key": section_id,
                    "section_heading": title,
                    "section_kind": title.lower(),
                },
            )
        )
    return sections
