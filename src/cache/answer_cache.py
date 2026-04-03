from __future__ import annotations

import hashlib
import json
from pathlib import Path

from src.schemas import AnswerResult, CacheStatus, RetrievalCandidate


class AnswerCache:
    def __init__(self, cache_dir: str | Path):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def build_key(
        fingerprint: str,
        question: str,
        retrieval_version: str,
        generation_version: str,
        model_name: str,
    ) -> str:
        normalized = " ".join(question.lower().split())
        payload = "||".join([fingerprint, normalized, retrieval_version, generation_version, model_name])
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def _cache_path(self, key: str) -> Path:
        return self.cache_dir / f"{key}.json"

    def get(self, key: str) -> AnswerResult | None:
        path = self._cache_path(key)
        if not path.exists():
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
        cache_status = payload.get("cache_status", {})
        evidence = [
            RetrievalCandidate(
                chunk_id=item["chunk_id"],
                text=item["text"],
                metadata=item.get("metadata", {}),
                dense_score=item.get("dense_score", 0.0),
                lexical_score=item.get("lexical_score", 0.0),
                fused_score=item.get("fused_score", 0.0),
                rerank_score=item.get("rerank_score"),
                citation_label=item.get("citation_label", ""),
            )
            for item in payload.get("evidence", [])
        ]
        return AnswerResult(
            question=payload["question"],
            answer=payload["answer"],
            citations=payload["citations"],
            evidence=evidence,
            supported=payload.get("supported", True),
            cache_status=CacheStatus(
                index_reused=cache_status.get("index_reused", False),
                answer_cache_hit=True,
            ),
            model_name=payload["model_name"],
            note=payload.get("note"),
            citation_details=payload.get("citation_details", []),
            retrieval_notes=payload.get("retrieval_notes", []),
            query_used=payload.get("query_used", payload["question"]),
            query_variants=payload.get("query_variants", [payload["question"]]),
        )

    def set(self, key: str, answer: AnswerResult) -> None:
        self._cache_path(key).write_text(json.dumps(answer.to_dict(), indent=2), encoding="utf-8")
