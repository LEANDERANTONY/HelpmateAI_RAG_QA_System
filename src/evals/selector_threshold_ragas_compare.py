from __future__ import annotations

import argparse
import json
import warnings
from dataclasses import replace
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

from src.config import Settings, get_settings
from src.evals.answer_stack_ablation import _build_indexes, _load_dataset

from src.pipeline import HelpmatePipeline


load_dotenv()


ROOT = Path(__file__).resolve().parents[2]
REPORTS_DIR = ROOT / "docs" / "evals" / "reports"
LOCAL_STORE_DIR = ROOT / "tmp" / "selector_threshold_ragas_compare"
DEFAULT_DATASETS = [
    "health_retrieval_eval_dataset.json",
    "thesis_retrieval_eval_dataset.json",
    "pancreas7_retrieval_eval_dataset.json",
]
DEFAULT_THRESHOLDS = [0.04, 0.06, 0.08, 0.10, 1.0]


def _safe_mean(values: list[float]) -> float | None:
    numeric = [value for value in values if isinstance(value, (int, float))]
    if not numeric:
        return None
    return float(mean(numeric))


def _threshold_label(value: float) -> str:
    return "always_on" if value >= 1.0 else f"threshold_{value:.2f}".replace(".", "_")


def _build_settings(base: Settings, *, threshold: float) -> Settings:
    variant_id = _threshold_label(threshold)
    settings = replace(
        base,
        data_dir=LOCAL_STORE_DIR / "data",
        cache_dir=LOCAL_STORE_DIR / "data" / variant_id / "cache",
        state_store_backend="local",
        vector_store_backend="local",
        reranker_enabled=True,
        evidence_selector_enabled=True,
        evidence_selector_prune=False,
        evidence_selector_gap_threshold=threshold,
        router_llm_enabled=True,
        retrieval_version=f"{base.retrieval_version}-{variant_id}",
        generation_version=f"{base.generation_version}-{variant_id}",
    )
    settings.ensure_dirs()
    return settings


class SelectorThresholdRagasEvaluator:
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
        contexts: list[str] = []
        for candidate in answer.get("evidence", []):
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

    def evaluate(self, thresholds: list[float], dataset_names: list[str]) -> dict[str, Any]:
        if not self.available:
            return {
                "available": False,
                "reason": "OPENAI_API_KEY is not configured, so ragas evaluation could not run.",
                "dataset_names": dataset_names,
            }

        base_settings = get_settings()
        variants = {threshold: _build_settings(base_settings, threshold=threshold) for threshold in thresholds}
        pipelines = {threshold: HelpmatePipeline(settings) for threshold, settings in variants.items()}
        reference_pipeline = pipelines[thresholds[0]]
        documents = _build_indexes(reference_pipeline)

        payload: dict[str, Any] = {
            "available": True,
            "dataset_names": dataset_names,
            "thresholds": thresholds,
            "variant_order": [_threshold_label(value) for value in thresholds],
            "answer_model": self.settings.answer_model,
            "embedding_model": self.settings.embedding_model,
            "variants": {},
        }

        for threshold in thresholds:
            variant_name = _threshold_label(threshold)
            pipeline = pipelines[threshold]
            rows: list[dict[str, Any]] = []
            per_dataset: dict[str, Any] = {}
            faithfulness_scores: list[float] = []
            answer_relevancy_scores: list[float] = []
            context_precision_scores: list[float] = []
            trigger_rows: list[dict[str, Any]] = []

            for dataset_name in dataset_names:
                dataset_path = ROOT / "docs" / "evals" / dataset_name
                items = _load_dataset(dataset_path)
                document = reference_pipeline.ingest_document(documents[dataset_name]["document_path"])
                dataset_rows: list[dict[str, Any]] = []
                dataset_faithfulness: list[float] = []
                dataset_answer_relevancy: list[float] = []
                dataset_context_precision: list[float] = []
                dataset_trigger_rows: list[dict[str, Any]] = []

                for item in items:
                    question = item["question"]
                    retrieval = pipeline.retrieve_evidence(document.document_id, document.fingerprint, question)
                    decision = pipeline.evidence_selector._selection_decision(retrieval)
                    trigger_rows.append(decision)
                    dataset_trigger_rows.append(decision)
                    retrieval = pipeline.evidence_selector.select(question, retrieval)
                    answer = pipeline.generate_answer(document.document_id, question, retrieval).to_dict()
                    sample = self._build_sample(question, answer)

                    row: dict[str, Any] = {
                        "question": question,
                        "supported": answer.get("supported", False),
                        "citations": answer.get("citations", []),
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
                    "trigger_rate": (
                        sum(1 for row in dataset_trigger_rows if row["should_select"]) / len(dataset_trigger_rows)
                        if dataset_trigger_rows
                        else 0.0
                    ),
                }

            payload["variants"][variant_name] = {
                "threshold": threshold,
                "settings": {
                    "evidence_selector_enabled": pipeline.settings.evidence_selector_enabled,
                    "evidence_selector_prune": pipeline.settings.evidence_selector_prune,
                    "evidence_selector_gap_threshold": pipeline.settings.evidence_selector_gap_threshold,
                },
                "dataset_size": len(rows),
                "supported_rate": sum(1 for row in rows if row.get("supported")) / len(rows) if rows else 0.0,
                "faithfulness_mean": _safe_mean(faithfulness_scores),
                "answer_relevancy_mean": _safe_mean(answer_relevancy_scores),
                "context_precision_mean": _safe_mean(context_precision_scores),
                "trigger_rate": (
                    sum(1 for row in trigger_rows if row["should_select"]) / len(trigger_rows) if trigger_rows else 0.0
                ),
                "per_dataset": per_dataset,
            }

        return payload


def _save_report(payload: dict[str, Any]) -> Path:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = REPORTS_DIR / f"selector_threshold_ragas_compare_{timestamp}.json"
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare selector gap thresholds on focused ragas evals.")
    parser.add_argument("--thresholds", nargs="*", type=float, default=DEFAULT_THRESHOLDS)
    parser.add_argument("--datasets", nargs="*", default=DEFAULT_DATASETS)
    args = parser.parse_args()

    evaluator = SelectorThresholdRagasEvaluator()
    payload = evaluator.evaluate(args.thresholds, args.datasets)
    report_path = _save_report(payload)
    print(
        json.dumps(
            {
                "report_path": str(report_path),
                "dataset_names": args.datasets,
                "variant_order": payload.get("variant_order", []),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
