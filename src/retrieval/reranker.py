from __future__ import annotations

import logging

from src.config import Settings
from src.schemas import RetrievalCandidate


logger = logging.getLogger(__name__)


class Reranker:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._model = None

    def _get_model(self):
        if self._model is None:
            from sentence_transformers import CrossEncoder

            self._model = CrossEncoder(self.settings.reranker_model)
        return self._model

    def rerank(self, question: str, candidates: list[RetrievalCandidate], top_k: int) -> list[RetrievalCandidate]:
        try:
            model = self._get_model()
            scores = model.predict([[question, candidate.text] for candidate in candidates])
            for candidate, score in zip(candidates, scores):
                candidate.rerank_score = float(score)
            candidates.sort(key=lambda candidate: candidate.rerank_score or 0.0, reverse=True)
        except Exception as exc:
            logger.warning("Reranker failed; falling back to fused-score ordering (%s)", exc.__class__.__name__)
            candidates.sort(key=lambda candidate: candidate.fused_score, reverse=True)
        return candidates[:top_k]
