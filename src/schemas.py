from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class DocumentRecord:
    document_id: str
    file_name: str
    file_type: str
    source_path: str
    fingerprint: str
    char_count: int
    page_count: int | None
    metadata: dict[str, Any] = field(default_factory=dict)
    extracted_text: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ChunkRecord:
    chunk_id: str
    document_id: str
    text: str
    chunk_index: int
    page_label: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SectionRecord:
    section_id: str
    document_id: str
    title: str
    summary: str
    text: str
    page_labels: list[str] = field(default_factory=list)
    section_path: list[str] = field(default_factory=list)
    clause_ids: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class IndexRecord:
    document_id: str
    fingerprint: str
    collection_name: str
    storage_path: str
    chunk_count: int
    section_count: int
    embedding_model: str
    chunk_size: int
    chunk_overlap: int
    created_at: str
    index_schema_version: str = "v1"
    reused: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RetrievalCandidate:
    chunk_id: str
    text: str
    metadata: dict[str, Any]
    dense_score: float = 0.0
    lexical_score: float = 0.0
    fused_score: float = 0.0
    rerank_score: float | None = None
    citation_label: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RetrievalResult:
    question: str
    candidates: list[RetrievalCandidate]
    cache_hit: bool = False
    retrieval_version: str = "v1"
    route_used: str = "chunk_first"
    query_used: str = ""
    query_variants: list[str] = field(default_factory=list)
    metadata_filters: dict[str, Any] = field(default_factory=dict)
    strategy_notes: list[str] = field(default_factory=list)
    weak_evidence: bool = False
    evidence_status: str = "strong"
    best_score: float = 0.0
    max_lexical_score: float = 0.0
    content_overlap_score: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "question": self.question,
            "candidates": [candidate.to_dict() for candidate in self.candidates],
            "cache_hit": self.cache_hit,
            "retrieval_version": self.retrieval_version,
            "route_used": self.route_used,
            "query_used": self.query_used,
            "query_variants": list(self.query_variants),
            "metadata_filters": dict(self.metadata_filters),
            "strategy_notes": list(self.strategy_notes),
            "weak_evidence": self.weak_evidence,
            "evidence_status": self.evidence_status,
            "best_score": self.best_score,
            "max_lexical_score": self.max_lexical_score,
            "content_overlap_score": self.content_overlap_score,
        }


@dataclass
class CacheStatus:
    index_reused: bool = False
    answer_cache_hit: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class AnswerResult:
    question: str
    answer: str
    citations: list[str]
    evidence: list[RetrievalCandidate]
    supported: bool = True
    cache_status: CacheStatus = field(default_factory=CacheStatus)
    model_name: str = ""
    note: str | None = None
    citation_details: list[str] = field(default_factory=list)
    retrieval_notes: list[str] = field(default_factory=list)
    query_used: str = ""
    query_variants: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "question": self.question,
            "answer": self.answer,
            "citations": list(self.citations),
            "evidence": [candidate.to_dict() for candidate in self.evidence],
            "supported": self.supported,
            "cache_status": self.cache_status.to_dict(),
            "model_name": self.model_name,
            "note": self.note,
            "citation_details": list(self.citation_details),
            "retrieval_notes": list(self.retrieval_notes),
            "query_used": self.query_used,
            "query_variants": list(self.query_variants),
        }
