from __future__ import annotations

import re


CLAUSE_RE = re.compile(r"^(?P<clause>\d+(?:\.\d+)+)\b")


def _clean_line(line: str) -> str:
    return " ".join(line.strip().split())


def _is_heading(line: str) -> bool:
    compact = _clean_line(line)
    if not compact:
        return False
    if CLAUSE_RE.match(compact):
        return True
    words = compact.split()
    if len(words) > 14:
        return False
    alpha_chars = [char for char in compact if char.isalpha()]
    if not alpha_chars:
        return False
    upper_ratio = sum(1 for char in alpha_chars if char.isupper()) / len(alpha_chars)
    return upper_ratio >= 0.7


def _content_type_from_text(*values: str) -> str:
    joined = " ".join(values).lower()
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


def enrich_pages_with_structure(pages: list[dict[str, str]]) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    enriched_pages: list[dict[str, object]] = []
    outline: list[dict[str, object]] = []

    current_heading = ""
    current_clause_path: list[str] = []
    current_section_path: list[str] = []

    for page in pages:
        text = page.get("text", "")
        lines = [_clean_line(line) for line in text.splitlines() if _clean_line(line)]

        page_heading = page.get("section_heading", "") or current_heading
        page_clause_ids: list[str] = []
        local_section_path = list(current_section_path)

        for line in lines[:12]:
            clause_match = CLAUSE_RE.match(line)
            if clause_match:
                clause_id = clause_match.group("clause")
                level = clause_id.count(".") + 1
                current_clause_path = current_clause_path[: level - 1] + [clause_id]
                page_clause_ids.append(clause_id)
                title = line[len(clause_id) :].strip(" .:-")
                if title:
                    current_heading = title
                    page_heading = title
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
            elif _is_heading(line):
                current_heading = line
                page_heading = line
                if not current_section_path or current_section_path[-1] != line:
                    current_section_path = current_section_path[:1] + [line] if current_section_path else [line]
                local_section_path = list(current_section_path)
                outline.append(
                    {
                        "page_label": page.get("page_label", "Document"),
                        "clause_id": current_clause_path[-1] if current_clause_path else "",
                        "title": line,
                        "section_path": list(local_section_path),
                    }
                )

        content_type = _content_type_from_text(page_heading, text[:1200])
        enriched_pages.append(
            {
                **page,
                "section_heading": page_heading,
                "section_path": local_section_path or ([page_heading] if page_heading else []),
                "clause_ids": page_clause_ids or list(current_clause_path[-2:]),
                "content_type": content_type,
            }
        )

    return enriched_pages, outline
