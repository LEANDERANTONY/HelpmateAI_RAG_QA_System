from __future__ import annotations

import json
import math
import statistics
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from src.evals.answer_stack_ablation import (
    NEGATIVE_DATASETS,
    POSITIVE_DATASETS,
    _build_indexes,
    _load_dataset,
    _variant_settings,
)
from src.evals.evidence_selector_weight_sweep import ROOT
from src.pipeline import HelpmatePipeline


REPORTS_DIR = ROOT / "docs" / "evals" / "reports"


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _p95(values: list[float]) -> float:
    if not values:
        return 0.0
    if len(values) == 1:
        return values[0]
    ordered = sorted(values)
    index = math.ceil(0.95 * len(ordered)) - 1
    index = max(0, min(index, len(ordered) - 1))
    return ordered[index]


def _stdev(values: list[float]) -> float:
    return statistics.pstdev(values) if len(values) > 1 else 0.0


def _selector_used(notes: list[str]) -> bool:
    return any("Evidence selector reviewed top" in note for note in notes)


def _run_question(pipeline: HelpmatePipeline, document, question: str) -> dict[str, Any]:
    total_start = time.perf_counter()

    retrieval_start = time.perf_counter()
    retrieval = pipeline.retrieve_evidence(document.document_id, document.fingerprint, question)
    retrieval_ms = (time.perf_counter() - retrieval_start) * 1000

    selector_start = time.perf_counter()
    if pipeline.settings.evidence_selector_enabled:
        retrieval = pipeline.evidence_selector.select(question, retrieval)
    selector_ms = (time.perf_counter() - selector_start) * 1000

    generation_start = time.perf_counter()
    answer = pipeline.generate_answer(document.document_id, question, retrieval)
    generation_ms = (time.perf_counter() - generation_start) * 1000

    total_ms = (time.perf_counter() - total_start) * 1000
    planner_source = str((retrieval.retrieval_plan or {}).get("planner_source", "deterministic"))

    return {
        "retrieval_ms": retrieval_ms,
        "selector_ms": selector_ms,
        "generation_ms": generation_ms,
        "total_ms": total_ms,
        "planner_llm_fallback": int(planner_source == "llm_fallback"),
        "selector_used": int(_selector_used(retrieval.strategy_notes)),
        "answer_model_call": int(answer.model_name == pipeline.settings.answer_model),
        "guardrail_blocked": int(answer.model_name == "retrieval_guardrail"),
        "fallback_answer": int(answer.model_name == "fallback"),
        "supported": int(bool(answer.supported)),
    }


def _summarize_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    retrieval_values = [row["retrieval_ms"] for row in rows]
    selector_values = [row["selector_ms"] for row in rows]
    generation_values = [row["generation_ms"] for row in rows]
    total_values = [row["total_ms"] for row in rows]
    count = len(rows)

    return {
        "dataset_size": count,
        "retrieval_ms_mean": _mean(retrieval_values),
        "retrieval_ms_p95": _p95(retrieval_values),
        "retrieval_ms_stdev": _stdev(retrieval_values),
        "selector_ms_mean": _mean(selector_values),
        "selector_ms_p95": _p95(selector_values),
        "generation_ms_mean": _mean(generation_values),
        "generation_ms_p95": _p95(generation_values),
        "total_ms_mean": _mean(total_values),
        "total_ms_p95": _p95(total_values),
        "total_ms_stdev": _stdev(total_values),
        "planner_llm_fallback_rate": sum(row["planner_llm_fallback"] for row in rows) / count if count else 0.0,
        "selector_used_rate": sum(row["selector_used"] for row in rows) / count if count else 0.0,
        "answer_model_call_rate": sum(row["answer_model_call"] for row in rows) / count if count else 0.0,
        "guardrail_block_rate": sum(row["guardrail_blocked"] for row in rows) / count if count else 0.0,
        "fallback_answer_rate": sum(row["fallback_answer"] for row in rows) / count if count else 0.0,
        "supported_rate": sum(row["supported"] for row in rows) / count if count else 0.0,
        "estimated_llm_stage_calls_per_question": (
            (
                sum(row["planner_llm_fallback"] for row in rows)
                + sum(row["selector_used"] for row in rows)
                + sum(row["answer_model_call"] for row in rows)
            )
            / count
            if count
            else 0.0
        ),
    }


def run_benchmark() -> dict[str, Any]:
    from src.config import get_settings

    base_settings = get_settings()
    variants = _variant_settings(base_settings)
    pipelines = {name: HelpmatePipeline(settings) for name, settings in variants.items()}
    reference_pipeline = pipelines["full_stack"]
    documents = _build_indexes(reference_pipeline)

    variant_payload: dict[str, Any] = {}
    for variant_name, pipeline in pipelines.items():
        positive_rows: list[dict[str, Any]] = []
        negative_rows: list[dict[str, Any]] = []
        per_dataset: dict[str, Any] = {}

        for dataset_name, doc_info in documents.items():
            positive_dataset_path = ROOT / "docs" / "evals" / dataset_name
            negative_dataset_name = dataset_name.replace("_retrieval_eval_dataset.json", "_negative_eval_dataset.json")
            if dataset_name == "retrieval_eval_dataset.json":
                negative_dataset_name = "negative_eval_dataset.json"
            negative_dataset_path = ROOT / "docs" / "evals" / negative_dataset_name

            positive_items = _load_dataset(positive_dataset_path)
            negative_items = _load_dataset(negative_dataset_path)
            document = reference_pipeline.ingest_document(doc_info["document_path"])

            dataset_positive_rows = [_run_question(pipeline, document, item["question"]) for item in positive_items]
            dataset_negative_rows = [_run_question(pipeline, document, item["question"]) for item in negative_items]

            positive_rows.extend(dataset_positive_rows)
            negative_rows.extend(dataset_negative_rows)

            per_dataset[dataset_name] = {
                "positive": _summarize_rows(dataset_positive_rows),
                "negative": _summarize_rows(dataset_negative_rows),
            }

        all_rows = [*positive_rows, *negative_rows]
        variant_payload[variant_name] = {
            "settings": {
                "reranker_enabled": pipeline.settings.reranker_enabled,
                "evidence_selector_enabled": pipeline.settings.evidence_selector_enabled,
                "planner_confidence_threshold": pipeline.settings.planner_confidence_threshold,
                "router_confidence_threshold": pipeline.settings.router_confidence_threshold,
                "router_llm_enabled": pipeline.settings.router_llm_enabled,
                "answer_model": pipeline.settings.answer_model,
                "router_model": pipeline.settings.router_model,
                "selector_model": pipeline.settings.evidence_selector_model,
            },
            "overall": _summarize_rows(all_rows),
            "positive_overall": _summarize_rows(positive_rows),
            "negative_overall": _summarize_rows(negative_rows),
            "per_dataset": per_dataset,
        }

    return {
        "variant_order": ["baseline", "reranker_only", "planner_reranker", "full_stack"],
        "positive_dataset_count": len(POSITIVE_DATASETS),
        "negative_dataset_count": len(NEGATIVE_DATASETS),
        "notes": [
            "This benchmark measures answer-path latency only: retrieval, optional selector, and generation.",
            "Index construction is intentionally excluded because these variants differ mainly in the query-time stack.",
            "Estimated LLM stage calls per question counts planner fallbacks, selector activations, and answer-model calls.",
            "This is an operational proxy, not direct token-level billing telemetry.",
        ],
        "variants": variant_payload,
    }


def _save_report(payload: dict[str, Any]) -> Path:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = REPORTS_DIR / f"latency_cost_benchmark_{timestamp}.json"
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def main() -> None:
    payload = run_benchmark()
    report_path = _save_report(payload)
    print(json.dumps({"report_path": str(report_path), "variants": payload["variant_order"]}, indent=2))


if __name__ == "__main__":
    main()
