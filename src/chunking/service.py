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


def _raw_lines(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines()]


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


def _looks_like_table_caption(line: str) -> bool:
    compact = line.strip().lower()
    if compact in {"table of contents", "list of tables"}:
        return False
    return bool(re.match(r"^(?:table|exhibit)\s+[A-Za-z0-9.\-:]+", line.strip(), flags=re.IGNORECASE))


def _looks_like_tabular_line(line: str) -> bool:
    compact = line.strip()
    if not compact:
        return False
    numeric_ratio = sum(char.isdigit() for char in compact) / max(len(compact), 1)
    has_columns = bool(re.search(r"\s{2,}|\t|[|]", compact))
    return has_columns and (numeric_ratio >= 0.12 or len(compact.split()) >= 4)


def _extract_table_blocks(text: str) -> list[str]:
    lines = _raw_lines(text)
    blocks: list[str] = []
    consumed: set[int] = set()

    for index, line in enumerate(lines):
        if index in consumed or not _looks_like_table_caption(line):
            continue
        block: list[str] = [line]
        consumed.add(index)
        for following_index in range(index + 1, len(lines)):
            following = lines[following_index].strip()
            if not following:
                if len(block) >= 3:
                    break
                continue
            if len(block) >= 3 and _looks_like_heading_line(following) and not _looks_like_tabular_line(following):
                break
            block.append(following)
            consumed.add(following_index)
        if len(block) >= 2:
            blocks.append("\n".join(block).strip())

    index = 0
    while index < len(lines):
        if index in consumed:
            index += 1
            continue
        run: list[str] = []
        run_indices: list[int] = []
        while index < len(lines) and index not in consumed and _looks_like_tabular_line(lines[index]):
            run.append(lines[index].strip())
            run_indices.append(index)
            index += 1
        if len(run) >= 3:
            blocks.append("\n".join(run).strip())
            consumed.update(run_indices)
        index += 1

    return list(dict.fromkeys(block for block in blocks if block))


def _extract_footnote_block(text: str) -> str:
    lines = [line for line in _normalized_lines(text)[-14:] if not _looks_like_table_caption(line)]
    footnote_lines = [
        line
        for line in lines
        if re.match(r"^(?:\d{1,3}|[*†‡§])\s+[\w(]", line)
        and len(line) >= 24
        and not re.match(r"^\d+(?:\.\d+)+\s+[A-Za-z]", line)
    ]
    return "\n".join(footnote_lines).strip()


def _definition_artifact_text(label: str, definition: str, source_line: str) -> str:
    label = " ".join(label.strip(" .:-").split())
    definition = " ".join(definition.strip(" .:-").split())
    source_line = " ".join(source_line.strip().split())
    if not label or not definition:
        return ""
    return f"Definition: {label} = {definition}\nSource text: {source_line}"


def _extract_definition_blocks(text: str) -> list[tuple[str, dict]]:
    blocks: list[tuple[str, dict]] = []
    seen: set[str] = set()
    lines = _normalized_lines(text)
    for line in lines:
        if len(line) > 360:
            continue
        candidates: list[tuple[str, str, str]] = []
        for term, acronym in re.findall(r"\b([A-Z][A-Za-z][A-Za-z0-9 /,+-]{4,90}?)\s*\(([A-Z][A-Z0-9-]{1,12})\)", line):
            candidates.append((acronym, term, "long_form_parenthetical"))
        for acronym, term in re.findall(r"\b([A-Z][A-Z0-9-]{1,12})\s*\(([A-Z][A-Za-z][A-Za-z0-9 /,+-]{4,90}?)\)", line):
            candidates.append((acronym, term, "acronym_parenthetical"))
        match = re.search(
            r"\b([A-Z][A-Za-z0-9-]{1,16})\s+(?:stands for|means|refers to|is short for)\s+([^.;:]{4,120})",
            line,
            flags=re.IGNORECASE,
        )
        if match:
            candidates.append((match.group(1), match.group(2), "definition_phrase"))
        for label, definition, source_kind in candidates:
            label = label.strip()
            definition = definition.strip()
            if len(label) < 2 or len(definition.split()) < 2:
                continue
            key = f"{label.lower()}={definition.lower()}"
            if key in seen:
                continue
            seen.add(key)
            artifact_text = _definition_artifact_text(label, definition, line)
            if artifact_text:
                blocks.append(
                    (
                        artifact_text,
                        {
                            "definition_label": label,
                            "definition_source": source_kind,
                        },
                    )
                )
    return blocks[:8]


def _artifact_chunk(
    *,
    document: DocumentRecord,
    page: dict,
    artifact_type: str,
    artifact_index: int,
    chunk_index: int,
    text: str,
    section_heading: str,
    section_path: list,
    section_id: str,
    clause_ids: list,
    document_style: str,
    extra_metadata: dict | None = None,
) -> ChunkRecord:
    page_label = page.get("page_label", "Document")
    artifact_id = f"{document.document_id}-{artifact_type}-{page_label.lower().replace(' ', '-')}-{artifact_index}"
    visibility = {
        "table": "targeted_or_numeric",
        "definition": "targeted_only",
        "footnote": "targeted_only",
        "front_matter": "targeted_only",
        "bibliography": "explicit_only",
    }.get(artifact_type, "targeted_only")
    body_score = {
        "table": 0.72,
        "definition": 0.68,
        "footnote": 0.56,
        "front_matter": 0.48,
        "bibliography": 0.12,
    }.get(artifact_type, 0.4)
    metadata = {
        "source_file": document.file_name,
        "page_label": page_label,
        "document_id": document.document_id,
        "section_heading": section_heading,
        "section_path": section_path,
        "section_id": section_id,
        "clause_ids": clause_ids,
        "primary_clause_id": clause_ids[0] if clause_ids else "",
        "content_type": artifact_type,
        "artifact_entry": True,
        "artifact_type": artifact_type,
        "artifact_id": artifact_id,
        "artifact_index": artifact_index,
        "artifact_parent_page": page_label,
        "retrieval_visibility": visibility,
        "document_style": document_style,
        "chunk_role_prior": f"{artifact_type}_artifact",
        "body_evidence_score": body_score,
        "heading_only_flag": False,
        "low_value_prior": 0.88 if artifact_type == "bibliography" else 0.18 if artifact_type == "definition" else 0.35,
        "table_complete": artifact_type == "table",
    }
    if extra_metadata:
        metadata.update(extra_metadata)
    return ChunkRecord(
        chunk_id=f"{document.document_id}-artifact-{chunk_index:04d}",
        document_id=document.document_id,
        text=text.strip(),
        chunk_index=chunk_index,
        page_label=str(page_label),
        metadata=metadata,
    )


def _artifact_specs(page: dict, *, front_matter_kind: str, front_matter_score: float, low_value_section_flag: bool) -> list[tuple[str, str, dict]]:
    text = str(page.get("text", ""))
    section_heading = str(page.get("section_heading", ""))
    section_kind = str(page.get("section_kind", section_heading)).lower()
    specs: list[tuple[str, str, dict]] = []
    for artifact in page.get("table_artifacts", []) or []:
        if not isinstance(artifact, dict):
            continue
        artifact_text = str(artifact.get("text", "")).strip()
        if not artifact_text:
            continue
        specs.append(
            (
                "table",
                artifact_text,
                {
                    "table_extraction_backend": artifact.get("source_backend", ""),
                    "table_original_page_number": artifact.get("original_page_number"),
                    "table_original_page_label": artifact.get("original_page_label", page.get("page_label", "Document")),
                    "table_index_on_page": artifact.get("table_index_on_page"),
                    "table_row_count": artifact.get("row_count"),
                    "table_column_count": artifact.get("column_count"),
                },
            )
        )
    for table_block in _extract_table_blocks(text):
        specs.append(
            (
                "table",
                table_block,
                {
                    "table_extraction_backend": "pypdf_text_heuristic",
                    "table_complete": False,
                },
            )
        )
    footnote_block = _extract_footnote_block(text)
    if footnote_block:
        specs.append(("footnote", footnote_block, {}))
    for definition_text, definition_metadata in _extract_definition_blocks(text):
        specs.append(("definition", definition_text, definition_metadata))
    if front_matter_score >= 0.5 and front_matter_kind and text.strip():
        specs.append(("front_matter", text.strip(), {"front_matter_kind": front_matter_kind}))
    if section_kind in {"references", "bibliography"} or section_heading.strip().lower() in {"references", "bibliography"}:
        specs.append(("bibliography", text.strip(), {}))
    return [(artifact_type, artifact_text, metadata) for artifact_type, artifact_text, metadata in specs if artifact_text.strip()]


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
        specs = _artifact_specs(
            page,
            front_matter_kind=front_matter_kind,
            front_matter_score=front_matter_score,
            low_value_section_flag=low_value_section_flag,
        )
        artifact_ids_by_type: dict[str, list[str]] = {}
        for artifact_type, artifact_text, artifact_metadata in specs:
            artifact = _artifact_chunk(
                document=document,
                page=page,
                artifact_type=artifact_type,
                artifact_index=len(artifact_ids_by_type.get(artifact_type, [])) + 1,
                chunk_index=chunk_index,
                text=artifact_text,
                section_heading=section_heading,
                section_path=section_path,
                section_id=section_id,
                clause_ids=clause_ids,
                document_style=document_style,
                extra_metadata=artifact_metadata,
            )
            records.append(artifact)
            artifact_ids_by_type.setdefault(artifact_type, []).append(str(artifact.metadata["artifact_id"]))
            chunk_index += 1
        page["artifact_counts"] = {artifact_type: len(ids) for artifact_type, ids in artifact_ids_by_type.items()}
        page["artifact_ids"] = [artifact_id for ids in artifact_ids_by_type.values() for artifact_id in ids]
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
                            "page_artifact_counts": dict(page.get("artifact_counts", {})),
                            "page_artifact_ids": list(page.get("artifact_ids", [])),
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
