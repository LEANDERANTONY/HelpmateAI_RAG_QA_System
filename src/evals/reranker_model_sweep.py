from __future__ import annotations

import argparse
import json
import time
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any

from src.config import get_settings
from src.evals.evidence_selector_ablation import _question_objective
from src.evals.evidence_selector_weight_sweep import DATASET_TO_DOCUMENT, ROOT
from src.evals.reranker_ablation import _evaluate_retrieval, _summarize
from src.pipeline import HelpmatePipeline


REPORTS_DIR = ROOT / "docs" / "evals" / "reports"
LOCAL_STORE_DIR = ROOT / "tmp" / "reranker_model_sweep"

DEFAULT_MODELS = [
    "cross-encoder/ms-marco-TinyBERT-L2-v2",
    "cross-encoder/ms-marco-MiniLM-L6-v2",
    "cross-encoder/ms-marco-MiniLM-L12-v2",
]


def _dataset_paths(names: list[str] | None) -> list[Path]:
    if names:
        return [ROOT / "docs" / "evals" / name for name in names]
    return [ROOT / "docs" / "evals" / name for name in DATASET_TO_DOCUMENT]


def _dataset_items(dataset_path: Path) -> list[dict[str, Any]]:
    return json.loads(dataset_path.read_text(encoding="utf-8"))


def _safe_mean(values: list[float]) -> float:
    if not values:
        return 0.0
    return sum(values) / len(values)


def _variant_id(model_name: str) -> str:
    return model_name.replace("/", "__").replace("-", "_").replace(".", "_")


def run_sweep(dataset_paths: list[Path], model_names: list[str]) -> dict[str, Any]:
    settings = get_settings()
    base_settings = replace(
        settings,
        data_dir=LOCAL_STORE_DIR / "data",
        state_store_backend="local",
        vector_store_backend="local",
        reranker_enabled=True,
        evidence_selector_enabled=False,
    )
    base_settings.ensure_dirs()

    pipelines = {
        model_name: HelpmatePipeline(
            replace(
                base_settings,
                reranker_model=model_name,
                retrieval_version=f"{base_settings.retrieval_version}-{_variant_id(model_name)}",
                generation_version=f"{base_settings.generation_version}-{_variant_id(model_name)}",
            )
        )
        for model_name in model_names
    }

    per_dataset: dict[str, dict[str, list[dict[str, Any]]]] = {}
    overall_rows: dict[str, list[dict[str, Any]]] = {model_name: [] for model_name in model_names}
    overall_latency_ms: dict[str, list[float]] = {model_name: [] for model_name in model_names}

    for dataset_path in dataset_paths:
        dataset_name = dataset_path.name
        document_path = DATASET_TO_DOCUMENT[dataset_name]
        reference_pipeline = pipelines[model_names[0]]
        document = reference_pipeline.ingest_document(document_path)
        reference_pipeline.build_or_load_index(document)

        per_dataset.setdefault(dataset_name, {model_name: [] for model_name in model_names})
        dataset_latency_ms: dict[str, list[float]] = {model_name: [] for model_name in model_names}

        for model_name, pipeline in pipelines.items():
            if model_name != model_names[0]:
                pipeline.build_or_load_index(document)

        for item in _dataset_items(dataset_path):
            question = item["question"]
            expected_pages = item.get("expected_pages", [])
            expected_fragments = item.get("expected_fragments", [])

            for model_name, pipeline in pipelines.items():
                started = time.perf_counter()
                retrieval = pipeline.retrieve_evidence(document.document_id, document.fingerprint, question)
                elapsed_ms = (time.perf_counter() - started) * 1000.0
                row = _evaluate_retrieval(question, expected_pages, expected_fragments, retrieval)
                row["latency_ms"] = elapsed_ms
                row["objective_score"] = _question_objective(row)
                per_dataset[dataset_name][model_name].append(row)
                overall_rows[model_name].append(row)
                dataset_latency_ms[model_name].append(elapsed_ms)
                overall_latency_ms[model_name].append(elapsed_ms)

        for model_name, rows in per_dataset[dataset_name].items():
            summary = _summarize(rows)
            summary["mean_latency_ms"] = _safe_mean(dataset_latency_ms[model_name])
            summary["p95_latency_ms"] = sorted(dataset_latency_ms[model_name])[max(int(len(dataset_latency_ms[model_name]) * 0.95) - 1, 0)] if dataset_latency_ms[model_name] else 0.0
            per_dataset[dataset_name][model_name] = {
                "summary": summary,
                "results": rows,
            }

    model_summaries: dict[str, Any] = {}
    for model_name in model_names:
        summary = _summarize(overall_rows[model_name])
        latencies = overall_latency_ms[model_name]
        summary["mean_latency_ms"] = _safe_mean(latencies)
        summary["p95_latency_ms"] = sorted(latencies)[max(int(len(latencies) * 0.95) - 1, 0)] if latencies else 0.0
        model_summaries[model_name] = summary

    ranked_models = sorted(
        (
            {
                "model_name": model_name,
                **model_summaries[model_name],
            }
            for model_name in model_names
        ),
        key=lambda item: (
            item["objective_score"],
            item["page_hit_rate"],
            item["mean_fragment_recall"],
            -item["mean_latency_ms"],
        ),
        reverse=True,
    )

    return {
        "case_count": sum(len(rows) for rows in overall_rows.values()) // max(len(model_names), 1),
        "models": model_names,
        "settings": {
            "chunk_size": base_settings.chunk_size,
            "chunk_overlap": base_settings.chunk_overlap,
            "final_top_k": base_settings.final_top_k,
            "router_llm_enabled": base_settings.router_llm_enabled,
            "planner_confidence_threshold": base_settings.planner_confidence_threshold,
            "router_confidence_threshold": base_settings.router_confidence_threshold,
        },
        "model_summaries": model_summaries,
        "ranked_models": ranked_models,
        "per_dataset": per_dataset,
    }


def _save_report(payload: dict[str, Any]) -> Path:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = REPORTS_DIR / f"reranker_model_sweep_{timestamp}.json"
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare reranker models using the labeled retrieval datasets.")
    parser.add_argument("--datasets", nargs="*", help="Dataset filenames under docs/evals to include.")
    parser.add_argument("--models", nargs="*", help="Cross-encoder model names to compare.")
    args = parser.parse_args()

    model_names = args.models or DEFAULT_MODELS
    payload = run_sweep(_dataset_paths(args.datasets), model_names)
    report_path = _save_report(payload)
    print(
        json.dumps(
            {
                "report_path": str(report_path),
                "case_count": payload["case_count"],
                "ranked_models": payload["ranked_models"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
