from __future__ import annotations

import json
from pathlib import Path
from statistics import mean

from dotenv import load_dotenv
from ragas import SingleTurnSample

from src.config import Settings, get_settings
from src.evals.ragas_judge import build_ragas_metrics
from src.evals.ragas_retry import call_with_ragas_retry
from src.pipeline import HelpmatePipeline


load_dotenv()


def _safe_mean(values: list[float]) -> float | None:
    numeric = [value for value in values if isinstance(value, (int, float))]
    if not numeric:
        return None
    return float(mean(numeric))


class RagasEvaluator:
    def __init__(self, settings: Settings | None = None) -> None:
        settings = settings or get_settings()
        self.settings = settings
        self._metrics, self.judge_info = build_ragas_metrics(settings)
        self.available = bool(self._metrics)

    @staticmethod
    def _contexts_from_answer(answer: dict) -> list[str]:
        evidence = answer.get("evidence", [])
        contexts: list[str] = []
        for candidate in evidence:
            text = str(candidate.get("text", "")).strip()
            if text:
                contexts.append(text)
        return contexts

    def _build_sample(self, question: str, answer: dict) -> SingleTurnSample:
        return SingleTurnSample(
            user_input=question,
            response=answer.get("answer", ""),
            retrieved_contexts=self._contexts_from_answer(answer),
        )

    def evaluate(self, dataset_path: str | Path, document_path: str | Path) -> dict:
        dataset = json.loads(Path(dataset_path).read_text(encoding="utf-8"))
        if not self.available:
            return {
                "available": False,
                "reason": self.judge_info.get("reason", "RAGAS judge is not configured, so evaluation could not run."),
                "dataset_size": len(dataset),
                "ragas_judge": self.judge_info,
            }

        self.settings.ensure_dirs()
        pipeline = HelpmatePipeline(self.settings)
        document = pipeline.ingest_document(document_path)
        index_record = pipeline.build_or_load_index(document)

        results: list[dict] = []
        faithfulness_scores: list[float] = []
        answer_relevancy_scores: list[float] = []
        context_precision_scores: list[float] = []

        for item in dataset:
            question = item["question"]
            retrieval = pipeline.retrieve_evidence(document.document_id, document.fingerprint, question)
            if pipeline.settings.evidence_selector_enabled:
                retrieval = pipeline.evidence_selector.select(question, retrieval)
            answer = pipeline.generate_answer(document.document_id, question, retrieval).to_dict()
            sample = self._build_sample(question, answer)
            row: dict[str, object] = {
                "question": question,
                "supported": answer.get("supported", False),
                "route_used": answer.get("retrieval_notes", []),
                "query_used": answer.get("query_used", question),
                "citations": answer.get("citations", []),
            }

            for metric_name, metric in self._metrics.items():
                try:
                    score = float(call_with_ragas_retry(lambda: metric.single_turn_score(sample)))
                    row[metric_name] = score
                    if metric_name == "faithfulness":
                        faithfulness_scores.append(score)
                    elif metric_name == "answer_relevancy":
                        answer_relevancy_scores.append(score)
                    elif metric_name == "context_precision":
                        context_precision_scores.append(score)
                except Exception as exc:
                    row[f"{metric_name}_error"] = str(exc)

            results.append(row)

        return {
            "available": True,
            "dataset_size": len(dataset),
            "document_path": str(document_path),
            "answer_model": self.settings.answer_model,
            "embedding_model": self.settings.embedding_model,
            "ragas_judge": self.judge_info,
            "faithfulness_mean": _safe_mean(faithfulness_scores),
            "answer_relevancy_mean": _safe_mean(answer_relevancy_scores),
            "context_precision_mean": _safe_mean(context_precision_scores),
            "results": results,
        }
