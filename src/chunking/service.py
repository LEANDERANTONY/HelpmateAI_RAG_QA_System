from __future__ import annotations

import re

from src.sections.service import classify_front_matter
from src.schemas import ChunkRecord, DocumentRecord


def _split_text(text: str, chunk_size: int, chunk_overlap: int) -> list[str]:
    if not text:
        return []

    chunks: list[str] = []
    start = 0
    text_length = len(text)
    while start < text_length:
        end = min(text_length, start + chunk_size)
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= text_length:
            break
        start = max(end - chunk_overlap, start + 1)
    return chunks


def _semantic_blocks(text: str) -> list[str]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return []

    blocks: list[list[str]] = []
    current: list[str] = []
    for line in lines:
        starts_new_block = bool(re.match(r"^\d+(?:\.\d+)+\b", line)) or line.isupper()
        if starts_new_block and current:
            blocks.append(current)
            current = [line]
        else:
            current.append(line)
    if current:
        blocks.append(current)
    return ["\n".join(block) for block in blocks if block]


def _normalized_lines(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if line.strip()]


def _looks_like_heading_line(line: str) -> bool:
    compact = line.strip()
    if not compact:
        return False
    if re.match(r"^\d+(?:\.\d+)*\s+[A-Za-z]", compact):
        return True
    if compact.isupper() and len(compact.split()) <= 8:
        return True
    if compact.istitle() and len(compact.split()) <= 8 and len(compact) <= 80 and not re.search(r"[.!?]$", compact):
        return True
    return False


def _chunk_role_prior(text: str) -> tuple[str, float]:
    compact = text.strip()
    if not compact:
        return "navigation_like", 0.0

    lines = _normalized_lines(compact)
    lowered = compact.lower()
    heading_like_lines = sum(1 for line in lines if _looks_like_heading_line(line))
    numeric_ratio = sum(char.isdigit() for char in compact) / max(len(compact), 1)
    has_sentence_punctuation = bool(re.search(r"[A-Za-z][.!?](?:\s|$)", compact))
    has_dot_leaders = bool(re.search(r"\.{4,}\s*\d+$", compact, flags=re.MULTILINE))
    line_trailing_pages = sum(1 for line in lines if re.search(r"\.{3,}\s*\d+$", line))

    if (
        "table of contents" in lowered
        or lowered.startswith("contents")
        or "list of figures" in lowered
        or "list of tables" in lowered
        or has_dot_leaders
        or line_trailing_pages >= 1
    ):
        return "navigation_like", 0.02

    if any(token in lowered for token in ("doi", "available in pmc", "author manuscript", "et al.", "vol.", "pmid", "pmcid")):
        return "reference_like", 0.08

    if numeric_ratio > 0.18 and len(lines) <= 8 and not has_sentence_punctuation:
        return "table_fragment", 0.22

    if len(compact) <= 180 and len(lines) <= 3 and heading_like_lines >= max(1, len(lines) - 1) and not has_sentence_punctuation:
        return "heading_stub", 0.12

    if len(compact) <= 120 and heading_like_lines >= 1 and not has_sentence_punctuation:
        return "heading_stub", 0.18

    return "body", 0.88


def chunk_document(document: DocumentRecord, chunk_size: int, chunk_overlap: int) -> list[ChunkRecord]:
    pages = document.metadata.get("pages") or [{"page_label": "Document", "text": document.extracted_text}]
    records: list[ChunkRecord] = []
    chunk_index = 0
    for page in pages:
        page_label = page.get("page_label", "Document")
        text = page.get("text", "")
        section_heading = page.get("section_heading", "")
        section_path = page.get("section_path", [])
        section_id = page.get("section_id", "")
        clause_ids = page.get("clause_ids", [])
        content_type = page.get("content_type", "general")
        section_kind = page.get("section_kind", page.get("section_heading", "").lower())
        document_style = page.get("document_style", document.metadata.get("document_style", "generic_longform"))
        front_matter_kind, front_matter_score, low_value_section_flag = classify_front_matter(
            str(section_heading or page_label),
            str(text),
            [str(page_label)],
        )
        blocks = _semantic_blocks(text) or [text]
        page_records: list[ChunkRecord] = []
        for block in blocks:
            block_records: list[ChunkRecord] = []
            for text_chunk in _split_text(block, chunk_size=chunk_size, chunk_overlap=chunk_overlap):
                chunk_id = f"{document.document_id}-chunk-{chunk_index:04d}"
                chunk_role_prior, body_evidence_score = _chunk_role_prior(text_chunk)
                low_value_prior = max(1.0 - body_evidence_score, front_matter_score if low_value_section_flag else front_matter_score * 0.55)
                records.append(
                    ChunkRecord(
                        chunk_id=chunk_id,
                        document_id=document.document_id,
                        text=text_chunk,
                        chunk_index=chunk_index,
                        page_label=page_label,
                        metadata={
                            "source_file": document.file_name,
                            "page_label": page_label,
                            "document_id": document.document_id,
                            "section_heading": section_heading,
                            "section_path": section_path,
                            "section_id": section_id,
                            "clause_ids": clause_ids,
                            "primary_clause_id": clause_ids[0] if clause_ids else "",
                            "content_type": content_type,
                            "section_kind": section_kind,
                            "document_style": document_style,
                            "chunk_role_prior": chunk_role_prior,
                            "body_evidence_score": body_evidence_score,
                            "heading_only_flag": chunk_role_prior == "heading_stub",
                            "low_value_prior": low_value_prior,
                            "front_matter_kind": front_matter_kind,
                            "front_matter_score": front_matter_score,
                            "low_value_section_flag": low_value_section_flag,
                        },
                    )
                )
                block_records.append(records[-1])
                page_records.append(records[-1])
                chunk_index += 1
            for current, following in zip(block_records, block_records[1:]):
                current.metadata["continuation_chunk_id"] = following.chunk_id
        for index, current in enumerate(page_records):
            if current.metadata.get("continuation_chunk_id"):
                continue
            if not current.metadata.get("heading_only_flag"):
                continue
            for following in page_records[index + 1 :]:
                following_role = str(following.metadata.get("chunk_role_prior", "")).lower()
                if following_role in {"navigation_like", "reference_like", "heading_stub"}:
                    continue
                current.metadata["continuation_chunk_id"] = following.chunk_id
                break
    return records
