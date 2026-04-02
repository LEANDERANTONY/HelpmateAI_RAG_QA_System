from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from statistics import mean

from dotenv import load_dotenv

from src.config import get_settings
from src.pipeline import HelpmatePipeline


load_dotenv()


def _safe_mean(values: list[float]) -> float | None:
    if not values:
        return None
    return float(mean(values))


class VectaraEvaluator:
    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = api_key or os.getenv("VECTARA_API_KEY")
        self.available = bool(self.api_key)
        self.base_url = "https://api.vectara.io/v2"
        self.settings = get_settings()

    def _request(self, payload: dict) -> dict:
        if not self.api_key:
            raise RuntimeError("VECTARA_API_KEY is not configured.")

        request = urllib.request.Request(
            f"{self.base_url}/evaluate_factual_consistency",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "x-api-key": self.api_key,
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                body = response.read().decode("utf-8")
                return json.loads(body) if body else {}
        except urllib.error.HTTPError as exc:
            message = exc.read().decode("utf-8")
            raise RuntimeError(f"Vectara eval error {exc.code}: {message}") from exc

    @staticmethod
    def _source_texts(answer: dict) -> list[str]:
        evidence = answer.get("evidence", [])
        texts: list[str] = []
        for candidate in evidence:
            text = str(candidate.get("text", "")).strip()
            if text:
                texts.append(text)
        return texts

    def evaluate(self, dataset_path: str | Path, document_path: str | Path) -> dict:
        dataset = json.loads(Path(dataset_path).read_text(encoding="utf-8"))
        if not self.available:
            return {
                "available": False,
                "reason": "VECTARA_API_KEY is not configured.",
                "dataset_size": len(dataset),
            }

        pipeline = HelpmatePipeline(self.settings)
        document = pipeline.ingest_document(document_path)
        index_record = pipeline.build_or_load_index(document)

        results: list[dict] = []
        scores: list[float] = []
        for item in dataset:
            question = item["question"]
            retrieval = pipeline.retrieve_evidence(document.document_id, document.fingerprint, question)
            answer = pipeline.generate_answer(document.document_id, question, retrieval).to_dict()
            source_texts = self._source_texts(answer)
            row: dict[str, object] = {
                "question": question,
                "supported": answer.get("supported", False),
                "query_used": answer.get("query_used", question),
                "citations": answer.get("citations", []),
            }
            if not source_texts:
                row["score_error"] = "No evidence texts were available for factual consistency evaluation."
                results.append(row)
                continue

            try:
                payload = {
                    "generated_text": str(answer.get("answer", "")),
                    "source_texts": source_texts,
                }
                response = self._request(payload)
                score = float(response.get("score"))
                row["factual_consistency_score"] = score
                scores.append(score)
            except Exception as exc:
                row["score_error"] = str(exc)
            results.append(row)

        return {
            "available": True,
            "dataset_size": len(dataset),
            "document_path": str(document_path),
            "factual_consistency_mean": _safe_mean(scores),
            "results": results,
        }
