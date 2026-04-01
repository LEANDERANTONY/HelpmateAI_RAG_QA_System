from __future__ import annotations

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


def chunk_document(document: DocumentRecord, chunk_size: int, chunk_overlap: int) -> list[ChunkRecord]:
    pages = document.metadata.get("pages") or [{"page_label": "Document", "text": document.extracted_text}]
    records: list[ChunkRecord] = []
    chunk_index = 0
    for page in pages:
        page_label = page.get("page_label", "Document")
        text = page.get("text", "")
        section_heading = page.get("section_heading", "")
        for text_chunk in _split_text(text, chunk_size=chunk_size, chunk_overlap=chunk_overlap):
            chunk_id = f"{document.document_id}-chunk-{chunk_index:04d}"
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
                    },
                )
            )
            chunk_index += 1
    return records
