from __future__ import annotations

import re


CLAUSE_RE = re.compile(r"^(?P<clause>\d+(?:\.\d+)+)\b")
ACADEMIC_HEADING_RE = re.compile(r"^(?P<num>\d+(?:\.\d+){0,2})\s+(?P<title>[A-Z][A-Za-z0-9 ,/&()-]{2,120})$")
TOC_RE = re.compile(r"\.{4,}\s*\d+\s*$")

CANONICAL_HEADINGS = {
    "abstract": "Abstract",
    "introduction": "Introduction",
    "background": "Background",
    "background and motivation": "Background and Motivation",
    "literature review": "Literature Review",
    "research aim and objectives": "Research Aim and Objectives",
    "research aim": "Research Aim",
    "research objectives": "Research Objectives",
    "methodology": "Methodology",
    "implementation details": "Implementation Details",
    "results": "Results",
    "discussion": "Discussion",
    "conclusion": "Conclusion",
    "conclusions": "Conclusions",
    "future work": "Future Work",
    "future directions": "Future Directions",
    "final remarks": "Final Remarks",
    "references": "References",
}


def _clean_line(line: str) -> str:
    return " ".join(line.strip().split())


def _normalize_heading_candidate(line: str) -> str:
    compact = _clean_line(line)
    compact = TOC_RE.sub("", compact).strip(" .:-")
    return compact


def _looks_like_page_artifact(line: str) -> bool:
    lowered = line.lower()
    if re.fullmatch(r"[ivxlcdm]+", lowered):
        return True
    if re.fullmatch(r"\d+", lowered):
        return True
    if re.fullmatch(r"page\s+\d+", lowered):
        return True
    if "author manuscript" in lowered:
        return True
    if re.fullmatch(r".*page\s+\d+", lowered):
        return True
    return False


def _looks_like_author_line(line: str) -> bool:
    if "@" in line:
        return True
    lowered = line.lower()
    if any(term in lowered for term in ("department of", "university", "institute", "center for")):
        return True
    if any(token in line for token in ("1,3", "2,3", "1)", "2)", "3)")):
        return True
    words = line.replace(",", " ").split()
    if not words or len(words) > 12:
        return False
    capitalized = sum(1 for word in words if word[:1].isupper())
    return capitalized == len(words) and any(word.endswith(("1", "2", "3")) for word in words)


def _looks_like_reference_line(line: str) -> bool:
    lowered = line.lower()
    if "[pubmed:" in lowered:
        return True
    if re.match(r"^\d{1,3}\.\s+[A-Z]", line):
        return True
    if "et al." in lowered and any(char.isdigit() for char in line):
        return True
    return False


def _is_sentence_like(line: str) -> bool:
    words = line.split()
    if len(words) < 6:
        return False
    lowered_words = sum(1 for word in words if word[:1].islower())
    return lowered_words >= max(2, len(words) // 3)


def _canonical_heading(line: str) -> str:
    candidate = _normalize_heading_candidate(line)
    if not candidate:
        return ""
    stripped = re.sub(r"^\d+(?:\.\d+)*\s*", "", candidate).strip().lower()
    if stripped.startswith("appendix"):
        return candidate.title()
    return CANONICAL_HEADINGS.get(stripped, "")


def _is_heading(line: str) -> bool:
    compact = _normalize_heading_candidate(line)
    if not compact or _looks_like_page_artifact(compact) or _looks_like_author_line(compact) or _looks_like_reference_line(compact):
        return False
    if _canonical_heading(compact):
        return True
    if CLAUSE_RE.match(compact):
        return True
    if TOC_RE.search(line):
        return False
    academic_match = ACADEMIC_HEADING_RE.match(compact)
    if academic_match:
        title = academic_match.group("title")
        return not _is_sentence_like(title)
    if (
        len(words := compact.split()) <= 10
        and compact[:1].isupper()
        and compact[-1] not in ".?!"
        and not any(char in compact for char in ",;")
        and not _is_sentence_like(compact)
    ):
        return True
    if len(words := compact.split()) <= 12 and words[0][:1].isupper() and compact[-1] not in ".!?":
        title_case_ratio = sum(1 for word in words if word[:1].isupper() or word.lower() in {"and", "for", "of", "in", "to", "the"}) / max(len(words), 1)
        if title_case_ratio >= 0.8:
            return True
    words = compact.split()
    if len(words) > 14:
        return False
    if _is_sentence_like(compact):
        return False
    alpha_chars = [char for char in compact if char.isalpha()]
    if not alpha_chars:
        return False
    upper_ratio = sum(1 for char in alpha_chars if char.isupper()) / len(alpha_chars)
    title_case_ratio = sum(1 for word in words if word[:1].isupper()) / max(len(words), 1)
    return upper_ratio >= 0.7 or title_case_ratio >= 0.8


def _section_kind_from_heading(heading: str) -> str:
    lowered = heading.lower()
    if lowered.startswith("appendix"):
        return "appendix"
    normalized = re.sub(r"^\d+(?:\.\d+)*\s*", "", lowered).strip()
    if normalized in CANONICAL_HEADINGS:
        return normalized
    if "future work" in normalized or "future direction" in normalized:
        return "future work"
    if "conclusion" in normalized or "final remarks" in normalized:
        return "conclusion"
    if "results" in normalized:
        return "results"
    if "discussion" in normalized:
        return "discussion"
    if "method" in normalized or "methodology" in normalized:
        return "methodology"
    if "abstract" in normalized:
        return "abstract"
    if "introduction" in normalized or "background" in normalized:
        return "introduction"
    if "reference" in normalized:
        return "references"
    return "general"


def _content_type_from_text(heading: str, text: str, section_kind: str) -> str:
    joined = f"{heading} {text[:1200]}".lower()
    if section_kind in {"references", "appendix"}:
        return section_kind
    if section_kind in {"future work", "discussion", "conclusion", "results", "methodology", "abstract", "introduction"}:
        return section_kind
    if any(term in joined for term in ("means", "shall mean", "definition", "defined as")):
        return "definition"
    if "waiting period" in joined:
        return "waiting_period"
    if any(term in joined for term in ("exclusion", "not admissible", "not covered", "excluded")):
        return "exclusion"
    if any(term in joined for term in ("claim", "cashless", "pre-authorization", "reimbursement facility", "claim assessment")):
        return "claims_procedure"
    if any(term in joined for term in ("benefit", "cover", "coverage", "sum insured")):
        return "benefit"
    if any(term in joined for term in ("renewal", "premium", "grace period", "installment")):
        return "policy_admin"
    if any(term in joined for term in ("hospital", "network provider", "room rent")):
        return "provider_or_facility"
    return "general"


def infer_document_style(pages: list[dict[str, object]], outline: list[dict[str, object]]) -> str:
    joined_headings = " ".join(str(item.get("title", "")) for item in outline).lower()
    joined_text = " ".join(str(page.get("text", ""))[:800] for page in pages[:8]).lower()
    combined = f"{joined_headings} {joined_text}"

    thesis_score = 0
    research_score = 0
    policy_score = 0

    if any(term in combined for term in ("thesis report", "dissertation", "research aim", "research objectives", "final remarks", "appendix")):
        thesis_score += 4
    if any(term in combined for term in ("chapter 1", "chapter 2", "chapter 3", "methodological perspective")):
        thesis_score += 2

    if "abstract" in combined:
        research_score += 2
    if "introduction" in combined:
        research_score += 1
    if "references" in combined:
        research_score += 1
    if any(term in combined for term in ("et al.", "pubmed", "author manuscript", "stanford university", "department of")):
        research_score += 2

    if any(term in combined for term in ("policy", "sum insured", "waiting period", "grace period", "network provider", "cashless", "premium")):
        policy_score += 4
    if any(term in combined for term in ("policy wording", "claims procedure", "exclusion", "benefit", "room rent")):
        policy_score += 2

    scores = {
        "policy_document": policy_score,
        "thesis_document": thesis_score,
        "research_paper": research_score,
    }
    best_style, best_score = max(scores.items(), key=lambda item: item[1])
    return best_style if best_score >= 2 else "generic_longform"


def enrich_pages_with_structure(pages: list[dict[str, str]]) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    enriched_pages: list[dict[str, object]] = []
    outline: list[dict[str, object]] = []

    current_heading = ""
    current_clause_path: list[str] = []
    current_section_path: list[str] = []

    for page in pages:
        text = page.get("text", "")
        raw_lines = [_clean_line(line) for line in text.splitlines() if _clean_line(line)]

        page_heading = _canonical_heading(page.get("section_heading", "")) or page.get("section_heading", "") or current_heading
        page_clause_ids: list[str] = []
        local_section_path = list(current_section_path)
        section_kind = _section_kind_from_heading(page_heading) if page_heading else "general"
        page_heading_locked = False

        for line in raw_lines[:32]:
            if _looks_like_page_artifact(line) or _looks_like_author_line(line) or _looks_like_reference_line(line):
                continue

            canonical = _canonical_heading(line)
            if canonical:
                if not page_heading_locked:
                    page_heading = canonical
                    section_kind = _section_kind_from_heading(canonical)
                    current_section_path = [canonical]
                    local_section_path = list(current_section_path)
                    outline.append(
                        {
                            "page_label": page.get("page_label", "Document"),
                            "clause_id": current_clause_path[-1] if current_clause_path else "",
                            "title": canonical,
                            "section_path": list(local_section_path),
                        }
                    )
                    current_heading = canonical
                    page_heading_locked = True
                continue

            clause_match = CLAUSE_RE.match(line)
            if clause_match:
                clause_id = clause_match.group("clause")
                level = clause_id.count(".") + 1
                current_clause_path = current_clause_path[: level - 1] + [clause_id]
                page_clause_ids.append(clause_id)
                title = _normalize_heading_candidate(line[len(clause_id) :])
                if title and not _is_sentence_like(title):
                    if not page_heading_locked:
                        current_heading = title
                        page_heading = title
                        section_kind = _section_kind_from_heading(title)
                        local_section_path = current_section_path[: max(level - 2, 0)] + [title]
                        current_section_path = list(local_section_path)
                        outline.append(
                            {
                                "page_label": page.get("page_label", "Document"),
                                "clause_id": clause_id,
                                "title": title,
                                "section_path": list(local_section_path),
                            }
                        )
                        page_heading_locked = True
                continue

            if _is_heading(line):
                title = _canonical_heading(line) or _normalize_heading_candidate(line)
                if not page_heading_locked:
                    current_heading = title
                    page_heading = title
                    section_kind = _section_kind_from_heading(title)
                    current_section_path = [title] if section_kind in {"references", "appendix", "abstract", "introduction", "background"} else (current_section_path[:1] + [title] if current_section_path else [title])
                    local_section_path = list(current_section_path)
                    outline.append(
                        {
                            "page_label": page.get("page_label", "Document"),
                            "clause_id": current_clause_path[-1] if current_clause_path else "",
                            "title": title,
                            "section_path": list(local_section_path),
                        }
                    )
                    page_heading_locked = True
                break

        if not page_heading:
            meaningful = [line for line in raw_lines[:12] if not _looks_like_page_artifact(line) and not _looks_like_author_line(line)]
            page_heading = _canonical_heading(meaningful[0]) if meaningful else ""
            page_heading = page_heading or (meaningful[0] if meaningful and not _is_sentence_like(meaningful[0]) else current_heading)
            section_kind = _section_kind_from_heading(page_heading) if page_heading else "general"

        content_type = _content_type_from_text(page_heading, text, section_kind)
        enriched_pages.append(
            {
                **page,
                "section_heading": page_heading,
                "section_path": local_section_path or ([page_heading] if page_heading else []),
                "clause_ids": page_clause_ids or list(current_clause_path[-2:]),
                "content_type": content_type,
                "section_kind": section_kind,
            }
        )

    return enriched_pages, outline
