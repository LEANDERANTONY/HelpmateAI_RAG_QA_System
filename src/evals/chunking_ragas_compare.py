from __future__ import annotations

import argparse
import json
import os
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

from src.config import get_settings
from src.evals.ragas_stack_ablation import DATASET_TO_DOCUMENT, DEFAULT_DATASETS, ROOT
from src.evals.answer_stack_ablation import _load_dataset
from src.pipeline import HelpmatePipeline


load_dotenv()


REPORTS_DIR = ROOT / "docs" / "evals" / "reports"
LOCAL_STORE_DIR = ROOT / "tmp" / "chunking_ragas_compare"


def _safe_mean(values: list[float]) -> float | None:
    numeric = [value for value in values if isinstance(value, (int, float))]
    if not numeric:
        return None
    return float(mean(numeric))


def _build_settings(base, *, variant_id: str, chunk_size: int, chunk_overlap: int):
    settings = replace(
        base,
        data_dir=LOCAL_STORE_DIR / "data" / variant_id,
        cache_dir=LOCAL_STORE_DIR / "data" / variant_id / "cache",
        state_store_backend="local",
        vector_store_backend="local",
        reranker_enabled=True,
        evidence_selector_enabled=False,
        router_llm_enabled=True,
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        retrieval_version=f"{base.retrieval_version}-{variant_id}",
        generation_version=f"{base.generation_version}-{variant_id}",
    )
    settings.ensure_dirs()
    return settings


def _with_local_env(settings):
    previous_env = {
        "HELPMATE_DATA_DIR": os.getenv("HELPMATE_DATA_DIR"),
        "HELPMATE_STATE_STORE_BACKEND": os.getenv("HELPMATE_STATE_STORE_BACKEND"),
        "HELPMATE_VECTOR_STORE_BACKEND": os.getenv("HELPMATE_VECTOR_STORE_BACKEND"),
    }
    os.environ["HELPMATE_DATA_DIR"] = str(settings.data_dir)
    os.environ["HELPMATE_STATE_STORE_BACKEND"] = "local"
    os.environ["HELPMATE_VECTOR_STORE_BACKEND"] = "local"
    return previous_env


def _restore_env(previous_env: dict[str, str | None]) -> None:
    for key, value in previous_env.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


class ChunkingRagasEvaluator:
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

    def evaluate(self, dataset_names: list[str]) -> dict[str, Any]:
        if not self.available:
            return {
                "available": False,
                "reason": "OPENAI_API_KEY is not configured, so ragas evaluation could not run.",
                "dataset_names": dataset_names,
            }

        base = get_settings()
        variants = {
            "chunk_1200_180": _build_settings(base, variant_id="chunk_1200_180", chunk_size=1200, chunk_overlap=180),
            "chunk_1200_240": _build_settings(base, variant_id="chunk_1200_240", chunk_size=1200, chunk_overlap=240),
        }

        payload: dict[str, Any] = {
            "available": True,
            "dataset_names": dataset_names,
            "variant_order": ["chunk_1200_180", "chunk_1200_240"],
            "answer_model": self.settings.answer_model,
            "embedding_model": self.settings.embedding_model,
            "variants": {},
        }

        for variant_name, settings in variants.items():
            previous_env = _with_local_env(settings)
            try:
                pipeline = HelpmatePipeline(settings)
                rows: list[dict[str, Any]] = []
                per_dataset: dict[str, Any] = {}
                faithfulness_scores: list[float] = []
                answer_relevancy_scores: list[float] = []
                context_precision_scores: list[float] = []

                for dataset_name in dataset_names:
                    dataset_path = ROOT / "docs" / "evals" / dataset_name
                    items = _load_dataset(dataset_path)
                    document = pipeline.ingest_document(DATASET_TO_DOCUMENT[dataset_name])
                    index_record = pipeline.build_or_load_index(document)
                    dataset_rows: list[dict[str, Any]] = []
                    dataset_faithfulness: list[float] = []
                    dataset_answer_relevancy: list[float] = []
                    dataset_context_precision: list[float] = []

                    for item in items:
                        answer = pipeline.answer_question(document, index_record, item["question"]).to_dict()
                        sample = self._build_sample(item["question"], answer)
                        row: dict[str, Any] = {
                            "question": item["question"],
                            "supported": answer.get("supported", False),
                            "citations": answer.get("citations", []),
                            "query_used": answer.get("query_used", item["question"]),
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
                        "chunk_size": settings.chunk_size,
                        "chunk_overlap": settings.chunk_overlap,
                    },
                    "dataset_size": len(rows),
                    "supported_rate": sum(1 for row in rows if row.get("supported")) / len(rows) if rows else 0.0,
                    "faithfulness_mean": _safe_mean(faithfulness_scores),
                    "answer_relevancy_mean": _safe_mean(answer_relevancy_scores),
                    "context_precision_mean": _safe_mean(context_precision_scores),
                    "per_dataset": per_dataset,
                }
            finally:
                _restore_env(previous_env)

        return payload


def _save_report(payload: dict[str, Any]) -> Path:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = REPORTS_DIR / f"chunking_ragas_compare_{timestamp}.json"
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare chunking configs with focused ragas evaluation.")
    parser.add_argument("--datasets", nargs="*", default=DEFAULT_DATASETS)
    args = parser.parse_args()

    evaluator = ChunkingRagasEvaluator()
    payload = evaluator.evaluate(args.datasets)
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
