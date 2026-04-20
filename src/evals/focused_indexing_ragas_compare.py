from __future__ import annotations

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
from src.evals.answer_stack_ablation import _load_dataset
from src.evals.evidence_selector_weight_sweep import ROOT
from src.pipeline import HelpmatePipeline


load_dotenv()


REPORTS_DIR = ROOT / "docs" / "evals" / "reports"
LOCAL_STORE_DIR = ROOT / "tmp" / "focused_indexing_ragas_compare"

DATASETS = {
    "reportgeneration_retrieval_eval_dataset.json": ROOT / "static" / "sample_files" / "reportgeneration.pdf",
    "reportgeneration2_retrieval_eval_dataset.json": ROOT / "static" / "sample_files" / "reportgeneration2.pdf",
    "health_retrieval_eval_dataset.json": ROOT / "static" / "sample_files" / "HealthInsurance_Policy.pdf",
    "manual_questions_graduate_project_20260419.json": ROOT / "static" / "sample_files" / "test" / "Graduate Project.pdf",
}
VARIANT_ORDER = ["layer2_selective", "layer3_gated_synopsis"]


def _safe_mean(values: list[float]) -> float | None:
    numeric = [value for value in values if isinstance(value, (int, float))]
    if not numeric:
        return None
    return float(mean(numeric))


def _build_settings(base: Settings, *, variant_id: str, synopsis_semantics_enabled: bool) -> Settings:
    settings = replace(
        base,
        data_dir=LOCAL_STORE_DIR / variant_id,
        uploads_dir=LOCAL_STORE_DIR / variant_id / "uploads",
        indexes_dir=LOCAL_STORE_DIR / variant_id / "indexes",
        cache_dir=LOCAL_STORE_DIR / variant_id / "cache",
        state_store_backend="local",
        vector_store_backend="local",
        planner_llm_enabled=False,
        router_llm_enabled=False,
        reranker_enabled=True,
        evidence_selector_enabled=True,
        evidence_selector_prune=False,
        structure_repair_enabled=True,
        structure_repair_require_header_dominated=True,
        chunk_semantics_enabled=False,
        synopsis_semantics_enabled=synopsis_semantics_enabled,
        synopsis_semantics_gate_mode="targeted",
        index_schema_version=f"ragas-{variant_id}",
        retrieval_version=f"ragas-{variant_id}",
        generation_version=f"{base.generation_version}-{variant_id}",
    )
    settings.ensure_dirs()
    return settings


def _variant_settings(base: Settings) -> dict[str, Settings]:
    return {
        "layer2_selective": _build_settings(base, variant_id="layer2_selective", synopsis_semantics_enabled=False),
        "layer3_gated_synopsis": _build_settings(base, variant_id="layer3_gated_synopsis", synopsis_semantics_enabled=True),
    }


class FocusedIndexingRagasEvaluator:
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

    def evaluate(self) -> dict[str, Any]:
        if not self.available:
            return {
                "available": False,
                "reason": "OPENAI_API_KEY is not configured, so ragas evaluation could not run.",
                "dataset_names": list(DATASETS),
            }

        base_settings = get_settings()
        variants = _variant_settings(base_settings)
        pipelines = {name: HelpmatePipeline(settings) for name, settings in variants.items()}
        payload: dict[str, Any] = {
            "available": True,
            "dataset_names": list(DATASETS),
            "variant_order": VARIANT_ORDER,
            "answer_model": self.settings.answer_model,
            "embedding_model": self.settings.embedding_model,
            "variants": {},
        }

        for variant_name in VARIANT_ORDER:
            pipeline = pipelines[variant_name]
            rows: list[dict[str, Any]] = []
            faithfulness_scores: list[float] = []
            answer_relevancy_scores: list[float] = []
            context_precision_scores: list[float] = []
            per_dataset: dict[str, Any] = {}

            for dataset_name, document_path in DATASETS.items():
                items = _load_dataset(ROOT / "docs" / "evals" / ("reports" if dataset_name.startswith("manual_questions_") else "") / dataset_name) if dataset_name.startswith("manual_questions_") else _load_dataset(ROOT / "docs" / "evals" / dataset_name)
                document = pipeline.ingest_document(document_path)
                pipeline.build_or_load_index(document)
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
                        "query_used": answer.get("query_used", question),
                        "citations": answer.get("citations", []),
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
                    "synopsis_semantics_enabled": pipeline.settings.synopsis_semantics_enabled,
                    "synopsis_semantics_gate_mode": pipeline.settings.synopsis_semantics_gate_mode,
                    "reranker_enabled": pipeline.settings.reranker_enabled,
                    "evidence_selector_enabled": pipeline.settings.evidence_selector_enabled,
                },
                "dataset_size": len(rows),
                "supported_rate": sum(1 for row in rows if row.get("supported")) / len(rows) if rows else 0.0,
                "faithfulness_mean": _safe_mean(faithfulness_scores),
                "answer_relevancy_mean": _safe_mean(answer_relevancy_scores),
                "context_precision_mean": _safe_mean(context_precision_scores),
                "per_dataset": per_dataset,
            }

        baseline = payload["variants"]["layer2_selective"]
        gated = payload["variants"]["layer3_gated_synopsis"]
        payload["delta_layer3_vs_layer2"] = {
            "supported_rate": (gated["supported_rate"] or 0.0) - (baseline["supported_rate"] or 0.0),
            "faithfulness_mean": (gated["faithfulness_mean"] or 0.0) - (baseline["faithfulness_mean"] or 0.0),
            "answer_relevancy_mean": (gated["answer_relevancy_mean"] or 0.0) - (baseline["answer_relevancy_mean"] or 0.0),
            "context_precision_mean": (gated["context_precision_mean"] or 0.0) - (baseline["context_precision_mean"] or 0.0),
        }
        return payload


def _save_report(payload: dict[str, Any]) -> Path:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = REPORTS_DIR / f"focused_indexing_ragas_compare_{timestamp}.json"
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def main() -> None:
    evaluator = FocusedIndexingRagasEvaluator()
    payload = evaluator.evaluate()
    report_path = _save_report(payload)
    print(
        json.dumps(
            {
                "report_path": str(report_path),
                "variant_order": VARIANT_ORDER,
                "delta_layer3_vs_layer2": payload.get("delta_layer3_vs_layer2", {}),
                "layer2_selective": payload.get("variants", {}).get("layer2_selective", {}),
                "layer3_gated_synopsis": payload.get("variants", {}).get("layer3_gated_synopsis", {}),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
