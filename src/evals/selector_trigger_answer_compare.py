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
LOCAL_STORE_DIR = ROOT / "tmp" / "selector_trigger_answer_compare"
VARIANT_ORDER = ["weak_only", "spread_only", "combined", "always_on"]


def _variant_settings(base: Settings) -> dict[str, Settings]:
    def build_variant(variant_id: str, **overrides: Any) -> Settings:
        settings = replace(
            base,
            data_dir=LOCAL_STORE_DIR / "data",
            cache_dir=LOCAL_STORE_DIR / "data" / variant_id / "cache",
            state_store_backend="local",
            vector_store_backend="local",
            reranker_enabled=True,
            evidence_selector_enabled=True,
            evidence_selector_prune=False,
            router_llm_enabled=True,
            retrieval_version=f"{base.retrieval_version}-{variant_id}",
            generation_version=f"{base.generation_version}-{variant_id}",
            **overrides,
        )
        settings.ensure_dirs()
        return settings

    return {
        "weak_only": build_variant(
            "weak_only",
            evidence_selector_trigger_weak_evidence=True,
            evidence_selector_trigger_spread=False,
            evidence_selector_trigger_ambiguity=False,
        ),
        "spread_only": build_variant(
            "spread_only",
            evidence_selector_trigger_weak_evidence=False,
            evidence_selector_trigger_spread=True,
            evidence_selector_trigger_ambiguity=False,
        ),
        "combined": build_variant(
            "combined",
            evidence_selector_trigger_weak_evidence=True,
            evidence_selector_trigger_spread=True,
            evidence_selector_trigger_ambiguity=True,
        ),
        "always_on": build_variant(
            "always_on",
            evidence_selector_gap_threshold=1.0,
            evidence_selector_trigger_weak_evidence=False,
            evidence_selector_trigger_spread=False,
            evidence_selector_trigger_ambiguity=True,
        ),
    }


def run_compare() -> dict[str, Any]:
    base_settings = get_settings()
    variants = _variant_settings(base_settings)
    pipelines = {name: HelpmatePipeline(settings) for name, settings in variants.items()}
    reference_pipeline = pipelines["combined"]
    documents = _build_indexes(reference_pipeline)

    variant_payload: dict[str, Any] = {}
    for variant_name in VARIANT_ORDER:
        pipeline = pipelines[variant_name]
        positive_rows: list[dict[str, Any]] = []
        negative_rows: list[dict[str, Any]] = []
        positive_trigger_rows: list[dict[str, Any]] = []
        negative_trigger_rows: list[dict[str, Any]] = []
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

        variant_payload[variant_name] = {
            "settings": {
                "gap_threshold": pipeline.settings.evidence_selector_gap_threshold,
                "trigger_weak_evidence": pipeline.settings.evidence_selector_trigger_weak_evidence,
                "trigger_spread": pipeline.settings.evidence_selector_trigger_spread,
                "trigger_ambiguity": pipeline.settings.evidence_selector_trigger_ambiguity,
                "evidence_selector_prune": pipeline.settings.evidence_selector_prune,
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
        "variant_order": VARIANT_ORDER,
        "variants": variant_payload,
    }


def _save_report(payload: dict[str, Any]) -> Path:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = REPORTS_DIR / f"selector_trigger_answer_compare_{timestamp}.json"
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare selector trigger-source modes on answer-layer evals.")
    parser.parse_args()
    payload = run_compare()
    report_path = _save_report(payload)
    summary = {
        "report_path": str(report_path),
        "variants": {
            name: {
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
