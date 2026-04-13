from __future__ import annotations

import json
import warnings
from datetime import datetime
from pathlib import Path
from statistics import mean
from typing import Any

from dotenv import load_dotenv
from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from ragas import SingleTurnSample
from ragas.embeddings import LangchainEmbeddingsWrapper
from ragas.llms import LangchainLLMWrapper
from ragas.metrics import Faithfulness, LLMContextPrecisionWithoutReference, ResponseRelevancy

from src.config import get_settings
from src.evals.answer_stack_ablation import _build_indexes, _load_dataset, _variant_settings
from src.evals.evidence_selector_weight_sweep import ROOT
from src.pipeline import HelpmatePipeline


load_dotenv()


REPORTS_DIR = ROOT / "docs" / "evals" / "reports"

DATASET_TO_DOCUMENT = {
    "health_retrieval_eval_dataset.json": ROOT / "static" / "sample_files" / "HealthInsurance_Policy.pdf",
    "thesis_retrieval_eval_dataset.json": ROOT / "static" / "sample_files" / "Final_Thesis_Leander_Antony_A.pdf",
    "pancreas7_retrieval_eval_dataset.json": ROOT / "static" / "sample_files" / "pancreas7.pdf",
    "pancreas8_retrieval_eval_dataset.json": ROOT / "static" / "sample_files" / "pancreas8.pdf",
    "reportgeneration_retrieval_eval_dataset.json": ROOT / "static" / "sample_files" / "reportgeneration.pdf",
    "reportgeneration2_retrieval_eval_dataset.json": ROOT / "static" / "sample_files" / "reportgeneration2.pdf",
    "retrieval_eval_dataset.json": ROOT / "static" / "sample_files" / "Principal-Sample-Life-Insurance-Policy.pdf",
}

DEFAULT_DATASETS = [
    "health_retrieval_eval_dataset.json",
    "thesis_retrieval_eval_dataset.json",
    "pancreas7_retrieval_eval_dataset.json",
]

VARIANT_ORDER = ["reranker_only", "planner_reranker", "full_stack"]


def _safe_mean(values: list[float]) -> float | None:
    numeric = [value for value in values if isinstance(value, (int, float))]
    if not numeric:
        return None
    return float(mean(numeric))


class RagasVariantEvaluator:
    def __init__(self) -> None:
        settings = get_settings()
        self.settings = settings
        self.available = bool(settings.openai_api_key)
        self._metrics: dict[str, Any] = {}

        if not self.available:
            return

        with warnings.catch_warnings():
            warnings.simplefilter("ignore", category=DeprecationWarning)
            llm = LangchainLLMWrapper(ChatOpenAI(model=settings.answer_model, api_key=settings.openai_api_key))
            embeddings = LangchainEmbeddingsWrapper(
                OpenAIEmbeddings(model=settings.embedding_model, api_key=settings.openai_api_key)
            )

        self._metrics = {
            "faithfulness": Faithfulness(llm=llm),
            "answer_relevancy": ResponseRelevancy(llm=llm, embeddings=embeddings),
            "context_precision": LLMContextPrecisionWithoutReference(llm=llm),
        }

    @staticmethod
    def _contexts_from_answer(answer: dict[str, Any]) -> list[str]:
        evidence = answer.get("evidence", [])
        contexts: list[str] = []
        for candidate in evidence:
            text = str(candidate.get("text", "")).strip()
            if text:
                contexts.append(text)
        return contexts

    def _build_sample(self, question: str, answer: dict[str, Any]) -> SingleTurnSample:
        return SingleTurnSample(
            user_input=question,
            response=answer.get("answer", ""),
            retrieved_contexts=self._contexts_from_answer(answer),
        )

    def evaluate(self, dataset_names: list[str]) -> dict[str, Any]:
        if not self.available:
            return {
                "available": False,
                "reason": "OPENAI_API_KEY is not configured, so ragas evaluation could not run.",
                "dataset_names": dataset_names,
            }

        base_settings = get_settings()
        variants = _variant_settings(base_settings)
        pipelines = {name: HelpmatePipeline(settings) for name, settings in variants.items()}
        reference_pipeline = pipelines["full_stack"]
        documents = _build_indexes(reference_pipeline)

        payload: dict[str, Any] = {
            "available": True,
            "dataset_names": dataset_names,
            "variant_order": VARIANT_ORDER,
            "answer_model": self.settings.answer_model,
            "embedding_model": self.settings.embedding_model,
            "variants": {},
        }

        for variant_name in VARIANT_ORDER:
            pipeline = pipelines[variant_name]
            rows: list[dict[str, Any]] = []
            per_dataset: dict[str, Any] = {}
            faithfulness_scores: list[float] = []
            answer_relevancy_scores: list[float] = []
            context_precision_scores: list[float] = []

            for dataset_name in dataset_names:
                dataset_path = ROOT / "docs" / "evals" / dataset_name
                items = _load_dataset(dataset_path)
                document = reference_pipeline.ingest_document(documents[dataset_name]["document_path"])
                dataset_rows: list[dict[str, Any]] = []
                dataset_faithfulness: list[float] = []
                dataset_answer_relevancy: list[float] = []
                dataset_context_precision: list[float] = []

                for item in items:
                    question = item["question"]
                    retrieval = pipeline.retrieve_evidence(document.document_id, document.fingerprint, question)
                    if pipeline.settings.evidence_selector_enabled:
                        retrieval = pipeline.evidence_selector.select(question, retrieval)
                    answer = pipeline.generate_answer(document.document_id, question, retrieval).to_dict()
                    sample = self._build_sample(question, answer)

                    row: dict[str, Any] = {
                        "question": question,
                        "supported": answer.get("supported", False),
                        "citations": answer.get("citations", []),
                        "route_used": answer.get("retrieval_notes", []),
                        "query_used": answer.get("query_used", question),
                    }

                    for metric_name, metric in self._metrics.items():
                        try:
                            score = float(metric.single_turn_score(sample))
                            row[metric_name] = score
                            if metric_name == "faithfulness":
                                faithfulness_scores.append(score)
                                dataset_faithfulness.append(score)
                            elif metric_name == "answer_relevancy":
                                answer_relevancy_scores.append(score)
                                dataset_answer_relevancy.append(score)
                            elif metric_name == "context_precision":
                                context_precision_scores.append(score)
                                dataset_context_precision.append(score)
                        except Exception as exc:
                            row[f"{metric_name}_error"] = str(exc)

                    dataset_rows.append(row)
                    rows.append(row)

                per_dataset[dataset_name] = {
                    "dataset_size": len(dataset_rows),
                    "supported_rate": sum(1 for row in dataset_rows if row.get("supported")) / len(dataset_rows) if dataset_rows else 0.0,
                    "faithfulness_mean": _safe_mean(dataset_faithfulness),
                    "answer_relevancy_mean": _safe_mean(dataset_answer_relevancy),
                    "context_precision_mean": _safe_mean(dataset_context_precision),
                }

            payload["variants"][variant_name] = {
                "settings": {
                    "reranker_enabled": pipeline.settings.reranker_enabled,
                    "evidence_selector_enabled": pipeline.settings.evidence_selector_enabled,
                    "planner_confidence_threshold": pipeline.settings.planner_confidence_threshold,
                    "router_confidence_threshold": pipeline.settings.router_confidence_threshold,
                    "router_llm_enabled": pipeline.settings.router_llm_enabled,
                },
                "dataset_size": len(rows),
                "supported_rate": sum(1 for row in rows if row.get("supported")) / len(rows) if rows else 0.0,
                "faithfulness_mean": _safe_mean(faithfulness_scores),
                "answer_relevancy_mean": _safe_mean(answer_relevancy_scores),
                "context_precision_mean": _safe_mean(context_precision_scores),
                "per_dataset": per_dataset,
                "results": rows,
            }

        return payload


def _save_report(payload: dict[str, Any]) -> Path:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = REPORTS_DIR / f"ragas_stack_ablation_{timestamp}.json"
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def main() -> None:
    evaluator = RagasVariantEvaluator()
    payload = evaluator.evaluate(DEFAULT_DATASETS)
    report_path = _save_report(payload)
    print(json.dumps({"report_path": str(report_path), "dataset_names": DEFAULT_DATASETS, "variant_order": VARIANT_ORDER}, indent=2))


if __name__ == "__main__":
    main()
