from __future__ import annotations

import argparse
import json
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any

from src.config import Settings, get_settings
from src.evals.answer_stack_ablation import (
    _build_indexes,
    _load_dataset,
    _negative_row,
    _positive_row,
    _summarize_negative,
    _summarize_positive,
)
from src.pipeline import HelpmatePipeline


ROOT = Path(__file__).resolve().parents[2]
REPORTS_DIR = ROOT / "docs" / "evals" / "reports"
LOCAL_STORE_DIR = ROOT / "tmp" / "selector_threshold_answer_compare"
DEFAULT_THRESHOLDS = [0.04, 0.06, 0.08, 0.10, 1.0]


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


def run_compare(thresholds: list[float]) -> dict[str, Any]:
    base_settings = get_settings()
    variants = {threshold: _build_settings(base_settings, threshold=threshold) for threshold in thresholds}
    pipelines = {threshold: HelpmatePipeline(settings) for threshold, settings in variants.items()}
    reference_pipeline = pipelines[thresholds[0]]
    documents = _build_indexes(reference_pipeline)

    variant_payload: dict[str, Any] = {}
    for threshold in thresholds:
        pipeline = pipelines[threshold]
        positive_rows: list[dict[str, Any]] = []
        negative_rows: list[dict[str, Any]] = []
        per_dataset: dict[str, Any] = {}
        positive_trigger_rows: list[dict[str, Any]] = []
        negative_trigger_rows: list[dict[str, Any]] = []

        for dataset_name, doc_info in documents.items():
            positive_dataset_path = ROOT / "docs" / "evals" / dataset_name
            negative_dataset_name = dataset_name.replace("_retrieval_eval_dataset.json", "_negative_eval_dataset.json")
            if dataset_name == "retrieval_eval_dataset.json":
                negative_dataset_name = "negative_eval_dataset.json"
            negative_dataset_path = ROOT / "docs" / "evals" / negative_dataset_name

            positive_items = _load_dataset(positive_dataset_path)
            negative_items = _load_dataset(negative_dataset_path)
            document = reference_pipeline.ingest_document(doc_info["document_path"])

            dataset_positive_rows: list[dict[str, Any]] = []
            dataset_negative_rows: list[dict[str, Any]] = []
            dataset_positive_trigger_rows: list[dict[str, Any]] = []
            dataset_negative_trigger_rows: list[dict[str, Any]] = []

            for item in positive_items:
                retrieval = pipeline.retrieve_evidence(document.document_id, document.fingerprint, item["question"])
                decision = pipeline.evidence_selector._selection_decision(retrieval)
                dataset_positive_trigger_rows.append(decision)
                positive_trigger_rows.append(decision)
                retrieval = pipeline.evidence_selector.select(item["question"], retrieval)
                answer = pipeline.generate_answer(document.document_id, item["question"], retrieval)
                row = _positive_row(item["question"], item.get("expected_pages", []), item.get("expected_fragments", []), answer)
                dataset_positive_rows.append(row)
                positive_rows.append(row)

            for item in negative_items:
                retrieval = pipeline.retrieve_evidence(document.document_id, document.fingerprint, item["question"])
                decision = pipeline.evidence_selector._selection_decision(retrieval)
                dataset_negative_trigger_rows.append(decision)
                negative_trigger_rows.append(decision)
                retrieval = pipeline.evidence_selector.select(item["question"], retrieval)
                answer = pipeline.generate_answer(document.document_id, item["question"], retrieval)
                row = _negative_row(item["question"], answer)
                dataset_negative_rows.append(row)
                negative_rows.append(row)

            per_dataset[dataset_name] = {
                "positive": _summarize_positive(dataset_positive_rows),
                "negative": _summarize_negative(dataset_negative_rows),
                "positive_trigger_rate": (
                    sum(1 for row in dataset_positive_trigger_rows if row["should_select"]) / len(dataset_positive_trigger_rows)
                    if dataset_positive_trigger_rows
                    else 0.0
                ),
                "negative_trigger_rate": (
                    sum(1 for row in dataset_negative_trigger_rows if row["should_select"]) / len(dataset_negative_trigger_rows)
                    if dataset_negative_trigger_rows
                    else 0.0
                ),
            }

        variant_payload[_threshold_label(threshold)] = {
            "threshold": threshold,
            "settings": {
                "evidence_selector_gap_threshold": pipeline.settings.evidence_selector_gap_threshold,
                "evidence_selector_enabled": pipeline.settings.evidence_selector_enabled,
                "evidence_selector_prune": pipeline.settings.evidence_selector_prune,
                "answer_model": pipeline.settings.answer_model,
            },
            "positive_overall": _summarize_positive(positive_rows),
            "negative_overall": _summarize_negative(negative_rows),
            "positive_trigger_rate": (
                sum(1 for row in positive_trigger_rows if row["should_select"]) / len(positive_trigger_rows)
                if positive_trigger_rows
                else 0.0
            ),
            "negative_trigger_rate": (
                sum(1 for row in negative_trigger_rows if row["should_select"]) / len(negative_trigger_rows)
                if negative_trigger_rows
                else 0.0
            ),
            "per_dataset": per_dataset,
        }

    return {
        "thresholds": thresholds,
        "variant_order": [_threshold_label(value) for value in thresholds],
        "variants": variant_payload,
    }


def _save_report(payload: dict[str, Any]) -> Path:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = REPORTS_DIR / f"selector_threshold_answer_compare_{timestamp}.json"
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare selector gap thresholds on answer-layer evals.")
    parser.add_argument("--thresholds", nargs="*", type=float, default=DEFAULT_THRESHOLDS)
    args = parser.parse_args()
    payload = run_compare(args.thresholds)
    report_path = _save_report(payload)
    summary = {
        "report_path": str(report_path),
        "variants": {
            name: {
                "threshold": payload["variants"][name]["threshold"],
                "positive_overall": payload["variants"][name]["positive_overall"],
                "negative_overall": payload["variants"][name]["negative_overall"],
                "positive_trigger_rate": payload["variants"][name]["positive_trigger_rate"],
            }
            for name in payload["variant_order"]
        },
    }
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
