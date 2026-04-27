from __future__ import annotations

import re
from collections import Counter

from src.schemas import SectionRecord
from src.sections.service import _clean_line


SECTION_PROFILE_VERSION = "v1"

ROLE_KEYWORDS: dict[str, tuple[str, ...]] = {
    "overview": ("abstract", "aim", "background", "introduction", "objective", "overview", "purpose", "scope"),
    "background": ("background", "context", "literature", "related work", "review"),
    "method": ("approach", "method", "methodology", "methods", "research design"),
    "implementation": ("configuration", "execution", "implementation", "orchestration", "pipeline", "software", "training workflow"),
    "experiment": ("experiment", "experimental", "evaluation protocol", "setup", "test rig", "validation"),
    "results": ("analysis", "auc", "finding", "findings", "metric", "performance", "result", "results"),
    "discussion": ("discussion", "implication", "interpretation", "significance"),
    "conclusion": ("conclusion", "conclusions", "summary", "synthesis", "takeaway"),
    "limitations": ("constraint", "limitation", "limitations", "risk", "shortcoming"),
    "future_work": ("future", "next step", "recommendation", "recommendations", "research direction"),
    "definitions": ("definition", "definitions", "glossary", "meaning", "terminology"),
    "rules": ("condition", "coverage", "eligibility", "exclusion", "obligation", "policy", "rule", "terms"),
    "procedure": ("process", "procedure", "step", "workflow"),
    "appendix": ("appendix", "bibliography", "references", "supplementary"),
}

ROLE_ALIASES: dict[str, tuple[str, ...]] = {
    "overview": ("overview", "summary", "main focus"),
    "background": ("background", "literature review", "related work"),
    "method": ("methods", "methodology", "approach"),
    "implementation": ("implementation", "implementation details", "implementation chapter"),
    "experiment": ("experiment", "experimental setup", "evaluation setup"),
    "results": ("results", "findings", "outcomes"),
    "discussion": ("discussion", "interpretation", "implications"),
    "conclusion": ("conclusion", "conclusions", "takeaways", "final summary"),
    "limitations": ("limitations", "constraints"),
    "future_work": ("future work", "future directions", "recommendations"),
    "definitions": ("definitions", "defined terms", "glossary"),
    "rules": ("rules", "conditions", "requirements"),
    "procedure": ("procedure", "process", "workflow"),
    "appendix": ("appendix", "references"),
}


def _dedupe(items: list[str], *, limit: int | None = None) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        compact = _clean_line(str(item)).strip(" .,:;|-")
        if not compact:
            continue
        key = compact.lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(compact)
        if limit is not None and len(result) >= limit:
            break
    return result


def _page_number(label: str) -> int | None:
    match = re.search(r"(\d+)", label)
    return int(match.group(1)) if match else None


def _roman_to_int(value: str) -> int | None:
    numerals = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100}
    total = 0
    previous = 0
    for char in reversed(value.upper()):
        current = numerals.get(char)
        if current is None:
            return None
        if current < previous:
            total -= current
        else:
            total += current
            previous = current
    return total or None


def _normalize_number(value: str) -> str:
    if value.isdigit():
        return str(int(value))
    converted = _roman_to_int(value)
    return str(converted) if converted is not None else value


def _clean_heading_text(value: str) -> str:
    compact = _clean_line(value)
    compact = re.sub(r"^\d+(?:\.\d+)*\s*", "", compact).strip(" .,:;|-")
    return compact.title() if compact.isupper() else compact


def _chapter_from_path(section: SectionRecord) -> str:
    path_text = " ".join(section.section_path)
    match = re.search(r"\bchapter\s+([0-9IVXLCDM]+)\b", path_text, flags=re.IGNORECASE)
    return _normalize_number(match.group(1)) if match else ""


def _chapter_marker(section: SectionRecord) -> tuple[str, str]:
    lines = [_clean_line(line) for line in section.text.splitlines() if _clean_line(line)]
    for index, line in enumerate(lines[:12]):
        match = re.match(r"^(?:\d+\s+)?chapter\s+([0-9IVXLCDM]+)\b", line, flags=re.IGNORECASE)
        if not match:
            continue
        chapter_number = _normalize_number(match.group(1))
        tail = line[match.end() :].strip(" .,:;|-")
        if tail and len(tail.split()) <= 8 and not re.match(r"^\d+(?:\.\d+)*\b", tail):
            return chapter_number, _clean_heading_text(tail)
        for candidate in lines[index + 1 : index + 5]:
            if re.match(r"^\d+(?:\.\d+)*\b", candidate):
                continue
            if 2 <= len(candidate.split()) <= 8 or candidate.isupper():
                return chapter_number, _clean_heading_text(candidate)
        return chapter_number, ""
    return "", ""


def _numbered_heading(section: SectionRecord) -> tuple[str, str]:
    for line in [_clean_line(line) for line in section.text.splitlines()[:12] if _clean_line(line)]:
        match = re.match(r"^(?:\d+\s+)?([1-9][0-9]?)\.([0-9]+(?:\.[0-9]+)*)\s+([A-Z][A-Za-z0-9()/:, .-]{2,90})", line)
        if match:
            return match.group(1), _clean_heading_text(match.group(3))
    return "", ""


def _role_scores(section: SectionRecord, chapter_title: str) -> dict[str, float]:
    heading_text = " ".join(
        [
            section.title,
            " ".join(section.section_path),
            str(section.metadata.get("section_kind", "")),
            str(section.metadata.get("content_type", "")),
            chapter_title,
        ]
    ).lower()
    summary_text = section.summary[:500].lower()
    scores: dict[str, float] = {}
    for role, keywords in ROLE_KEYWORDS.items():
        score = 0.0
        for keyword in keywords:
            if keyword in heading_text:
                score += 2.0
            if keyword in summary_text:
                score += 0.5
        scores[role] = score
    if str(section.metadata.get("front_matter_kind", "body")).lower() != "body":
        scores["appendix"] = max(scores.get("appendix", 0.0), 1.0)
    return scores


def _classify_role(section: SectionRecord, chapter_title: str) -> tuple[str, float]:
    scores = _role_scores(section, chapter_title)
    best_role, best_score = max(scores.items(), key=lambda item: item[1])
    if best_score <= 0:
        return "general", 0.35
    total = sum(score for score in scores.values() if score > 0)
    confidence = min(0.95, 0.45 + (best_score / max(total, 1.0)) * 0.5)
    return best_role, confidence


def _scope_labels(
    section: SectionRecord,
    *,
    chapter_number: str,
    chapter_title: str,
    numbered_heading: str,
    role: str,
) -> list[str]:
    labels: list[str] = []
    labels.extend(section.section_path)
    labels.append(section.title)
    if numbered_heading:
        labels.append(numbered_heading)
    if chapter_number:
        labels.append(f"Chapter {chapter_number}")
        labels.append(f"chapter {chapter_number}")
    if chapter_title:
        labels.append(chapter_title)
        labels.append(f"{chapter_title} chapter")
        if chapter_number:
            labels.append(f"Chapter {chapter_number} {chapter_title}")
            labels.append(f"{chapter_title} Chapter {chapter_number}")
    labels.extend(ROLE_ALIASES.get(role, ()))
    return _dedupe(labels, limit=24)


def enrich_section_profiles(sections: list[SectionRecord]) -> list[SectionRecord]:
    chapter_titles: dict[str, str] = {}
    numbered_headings: dict[str, str] = {}

    for section in sections:
        chapter_number, chapter_title = _chapter_marker(section)
        if not chapter_number:
            chapter_number = _chapter_from_path(section)
        numbered_chapter, numbered_heading = _numbered_heading(section)
        if numbered_chapter and not chapter_number:
            chapter_number = numbered_chapter
        if chapter_number and chapter_title:
            chapter_titles[chapter_number] = chapter_title
        if numbered_heading:
            numbered_headings[section.section_id] = numbered_heading

    updated: list[SectionRecord] = []
    chapter_counts: Counter[str] = Counter()
    for ordinal, section in enumerate(sections, start=1):
        chapter_number, marker_title = _chapter_marker(section)
        if not chapter_number:
            chapter_number = _chapter_from_path(section)
        numbered_chapter, numbered_heading = _numbered_heading(section)
        if numbered_chapter and not chapter_number:
            chapter_number = numbered_chapter
        chapter_title = marker_title or chapter_titles.get(chapter_number, "")
        role, role_confidence = _classify_role(section, chapter_title)
        page_numbers = [_page_number(label) for label in section.page_labels]
        page_numbers = [page for page in page_numbers if page is not None]
        chapter_counts[chapter_number] += 1 if chapter_number else 0
        scope_labels = _scope_labels(
            section,
            chapter_number=chapter_number,
            chapter_title=chapter_title,
            numbered_heading=numbered_heading or numbered_headings.get(section.section_id, ""),
            role=role,
        )
        existing_aliases = section.metadata.get("section_aliases", [])
        aliases = existing_aliases if isinstance(existing_aliases, list) else [str(existing_aliases)]
        metadata = {
            **section.metadata,
            "section_profile_version": SECTION_PROFILE_VERSION,
            "section_ordinal": ordinal,
            "section_depth": len(section.section_path),
            "document_section_role": role,
            "document_section_role_confidence": role_confidence,
            "document_scope_labels": scope_labels,
            "chapter_number": chapter_number,
            "chapter_title": chapter_title,
            "numbered_heading": numbered_heading or numbered_headings.get(section.section_id, ""),
            "page_range_start": min(page_numbers) if page_numbers else None,
            "page_range_end": max(page_numbers) if page_numbers else None,
            "section_aliases": _dedupe([*aliases, *scope_labels, *ROLE_ALIASES.get(role, ())], limit=32),
        }
        updated.append(
            SectionRecord(
                section_id=section.section_id,
                document_id=section.document_id,
                title=section.title,
                summary=section.summary,
                text=section.text,
                page_labels=list(section.page_labels),
                section_path=list(section.section_path),
                clause_ids=list(section.clause_ids),
                metadata=metadata,
            )
        )
    return updated
