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

SECTION_KIND_ALIASES: dict[str, list[str]] = {
    "overview": ["overview", "main focus", "main aim", "objective", "objectives", "contribution", "study purpose"],
    "abstract": ["abstract", "summary", "overview", "aim", "objective", "contribution"],
    "introduction": ["introduction", "background", "motivation", "context"],
    "background": ["background", "motivation", "context", "related work"],
    "methodology": ["methods", "methodology", "approach", "implementation"],
    "results": ["results", "findings", "outcomes", "evaluation"],
    "discussion": ["discussion", "interpretation", "implications", "challenge", "limitation"],
    "conclusion": ["conclusion", "conclusions", "takeaway", "final remarks", "summary"],
    "future work": ["future work", "future directions", "next steps", "follow-up", "recommendations", "further research"],
    "future directions": ["future directions", "future work", "next steps", "follow-up", "recommendations"],
    "limitations": ["limitations", "challenge", "constraints", "barriers"],
}


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


def _sentences_with_keywords(text: str, keywords: tuple[str, ...], limit: int = 3) -> list[str]:
    sentences = _summary_sentences(text)
    lowered_keywords = tuple(keyword.lower() for keyword in keywords)
    picked: list[str] = []
    for sentence in sentences:
        lowered = sentence.lower()
        if any(keyword in lowered for keyword in lowered_keywords):
            picked.append(sentence)
        if len(picked) >= limit:
            break
    return picked


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
    lowered_title = title.lower()
    if lowered_title in {"future work", "future directions"}:
        picked = _sentences_with_keywords(
            excerpt,
            ("future", "next step", "recommend", "follow-up", "further research", "validation", "prospective"),
            limit=3,
        )
        if picked:
            return " ".join(picked)[:900]
    if lowered_title in {"discussion", "conclusion", "conclusions", "limitations"}:
        picked = _sentences_with_keywords(
            excerpt,
            ("conclude", "challenge", "limitation", "implication", "future", "recommend"),
            limit=3,
        )
        if picked:
            return " ".join(picked)[:900]
    if lowered_title in {"abstract", "introduction", "background", "document overview"}:
        picked = _sentences_with_keywords(
            excerpt,
            ("aim", "objective", "focus", "study", "paper", "thesis", "investigate", "overview", "contribution"),
            limit=3,
        )
        if picked:
            return " ".join(picked)[:900]
    if len(sentences) >= 3 and title.lower() in {"discussion", "conclusion", "conclusions", "future work", "future directions"}:
        return " ".join(sentences[-2:])[:900]
    if len(sentences) >= 3 and title.lower() in {"abstract", "introduction", "background"}:
        return " ".join(sentences[:3])[:900]
    if len(sentences) >= 4:
        return f"{sentences[0]} {sentences[-1]}"[:900]
    return excerpt[:900]


def _section_aliases(title: str, section_kind: str, section_path: list[str]) -> list[str]:
    aliases: list[str] = []
    normalized_kind = section_kind.lower()
    aliases.extend(SECTION_KIND_ALIASES.get(normalized_kind, []))
    aliases.append(title)
    aliases.extend(section_path)
    seen: list[str] = []
    for alias in aliases:
        compact = _clean_line(alias).strip()
        if compact and compact.lower() not in {item.lower() for item in seen}:
            seen.append(compact)
    return seen[:10]


def document_overview_section(document: DocumentRecord, sections: list[SectionRecord]) -> SectionRecord | None:
    style = str(document.metadata.get("document_style", "generic_longform"))
    title_page = next((page for page in document.metadata.get("pages", []) if page.get("page_label") == "Page 1"), None)
    title_page_text = str(title_page.get("text", "")) if title_page else ""
    has_research_style_front_matter = bool(
        re.search(r"\babstract\b", title_page_text, flags=re.IGNORECASE)
        or re.search(r"\bintroduction\b", title_page_text, flags=re.IGNORECASE)
    )
    if style not in {"research_paper", "thesis_document"} and not has_research_style_front_matter:
        return None
    abstract_section = next((section for section in sections if str(section.metadata.get("section_kind", "")).lower() == "abstract"), None)
    intro_section = next(
        (
            section
            for section in sections
            if str(section.metadata.get("section_kind", "")).lower() in {"introduction", "background", "background and motivation"}
        ),
        None,
    )

    title_text = ""
    if title_page:
        lines = [_clean_line(line) for line in str(title_page.get("text", "")).splitlines() if _clean_line(line)]
        useful = [line for line in lines[:6] if not _looks_like_noise(line) and not re.search(r"\d\)", line)]
        title_text = " ".join(useful[:2])

    parts = [part for part in [title_text, abstract_section.summary if abstract_section else "", intro_section.summary if intro_section else ""] if part]
    if not parts:
        return None

    summary = " ".join(parts)[:1200]
    page_labels: list[str] = []
    for section in (abstract_section, intro_section):
        if section:
            for label in section.page_labels:
                if label not in page_labels:
                    page_labels.append(label)
    if title_page and "Page 1" not in page_labels:
        page_labels.insert(0, "Page 1")

    return SectionRecord(
        section_id=f"{document.document_id}-document-overview",
        document_id=document.document_id,
        title="Document Overview",
        summary=summary,
        text=summary,
        page_labels=page_labels or ["Page 1"],
        section_path=["Document Overview"],
        clause_ids=[],
        metadata={
            "source_file": document.file_name,
            "content_type": "overview",
            "primary_page_label": page_labels[0] if page_labels else "Page 1",
            "section_key": "document_overview",
            "section_heading": "Document Overview",
            "section_kind": "overview",
            "document_style": style,
            "section_aliases": _section_aliases("Document Overview", "overview", ["Document Overview"]),
        },
    )


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
                "section_kind": str(page.get("section_kind", "")),
                "document_style": str(page.get("document_style", document.metadata.get("document_style", "generic_longform"))),
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
                    "section_kind": payload["section_kind"] or title.lower(),
                    "document_style": payload["document_style"],
                    "section_aliases": _section_aliases(
                        title,
                        payload["section_kind"] or title.lower(),
                        list(payload["section_path"]),
                    ),
                },
            )
        )
    overview = document_overview_section(document, sections)
    if overview is not None:
        sections.insert(0, overview)
    return sections
