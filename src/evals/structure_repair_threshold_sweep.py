from __future__ import annotations

import argparse
import json
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any

from src.config import Settings, get_settings
from src.evals.evidence_selector_weight_sweep import ROOT
from src.evals.retrieval_eval import _plan_matches_outcome
from src.pipeline import HelpmatePipeline
from src.sections import build_sections


REPORTS_DIR = ROOT / "docs" / "evals" / "reports"
LOCAL_STORE_DIR = ROOT / "tmp" / "structure_repair_threshold_sweep"
DEFAULT_VALUES = [0.50, 0.55, 0.62, 0.68, 0.75]
TARGET_DATASETS = {
    "health_retrieval_eval_dataset.json": ROOT / "static" / "sample_files" / "HealthInsurance_Policy.pdf",
    "thesis_retrieval_eval_dataset.json": ROOT / "static" / "sample_files" / "Final_Thesis_Leander_Antony_A.pdf",
    "reportgeneration_retrieval_eval_dataset.json": ROOT / "static" / "sample_files" / "reportgeneration.pdf",
    "reportgeneration2_retrieval_eval_dataset.json": ROOT / "static" / "sample_files" / "reportgeneration2.pdf",
}
NOISY_DATASETS = {
    "reportgeneration_retrieval_eval_dataset.json",
    "reportgeneration2_retrieval_eval_dataset.json",
}
HEALTHY_DATASETS = {
    "health_retrieval_eval_dataset.json",
    "thesis_retrieval_eval_dataset.json",
}


def _build_settings(base: Settings, *, threshold: float) -> Settings:
    threshold_label = str(threshold).replace(".", "_")
    variant_id = f"repair_{threshold_label}"
    settings = replace(
        base,
        data_dir=LOCAL_STORE_DIR / "data",
        cache_dir=LOCAL_STORE_DIR / "data" / variant_id / "cache",
        state_store_backend="local",
        vector_store_backend="local",
        structure_repair_confidence_threshold=threshold,
        index_schema_version=f"{base.index_schema_version}-{variant_id}",
        retrieval_version=f"{base.retrieval_version}-{variant_id}",
        generation_version=f"{base.generation_version}-{variant_id}",
    )
    settings.ensure_dirs()
    return settings


def _document_repair_summary(pipeline: HelpmatePipeline, document_path: Path) -> tuple[dict[str, Any], Any]:
    document = pipeline.ingest_document(document_path)
    sections = build_sections(document)
    decision = pipeline.structure_repair_service.assess(
        document,
        sections,
        threshold=pipeline.settings.structure_repair_confidence_threshold,
    )
    pipeline.build_or_load_index(document)
    repaired_sections = pipeline.store.load_sections(document.fingerprint)
    repair_applied = any(section.metadata.get("structure_repaired") for section in repaired_sections)
    return {
        "document_path": str(document_path),
        "document_id": document.document_id,
        "fingerprint": document.fingerprint,
        "page_count": document.page_count,
        "base_section_count": len(sections),
        "final_section_count": len(repaired_sections),
        "confidence": decision.confidence,
        "should_repair": decision.should_repair,
        "repair_applied": repair_applied,
        "reasons": list(decision.reasons),
    }, document


def _eval_dataset(pipeline: HelpmatePipeline, dataset_name: str, document_info: dict[str, Any], document) -> dict[str, Any]:
    dataset_path = ROOT / "docs" / "evals" / dataset_name
    dataset = json.loads(dataset_path.read_text(encoding="utf-8"))

    hits = 0
    mrr_total = 0.0
    section_hits = 0
    region_hits = 0
    plan_hits = 0
    global_fallback_uses = 0
    global_fallback_hits = 0
    distributed_questions = 0
    multi_region_total = 0.0

    for item in dataset:
        retrieval = pipeline.retrieve_evidence(document.document_id, document.fingerprint, item["question"])
        found_pages = [candidate.metadata.get("page_label", "Document") for candidate in retrieval.candidates]
        expected_pages = item.get("expected_pages", [])
        matched = any(page in found_pages for page in expected_pages)
        hits += int(matched)
        reciprocal_rank = 0.0
        for index, page in enumerate(found_pages, start=1):
            if page in expected_pages:
                reciprocal_rank = 1.0 / index
                break
        mrr_total += reciprocal_rank
        section_matched = any(
            candidate.metadata.get("page_label", "Document") in expected_pages and candidate.metadata.get("section_id")
            for candidate in retrieval.candidates
        )
        target_region_kinds = set(retrieval.retrieval_plan.get("target_region_kinds", []))
        region_matched = any(
            candidate.metadata.get("page_label", "Document") in expected_pages
            and (
                (target_region_kinds and candidate.metadata.get("region_kind") in target_region_kinds)
                or (not target_region_kinds and bool(candidate.metadata.get("region_kind")))
            )
            for candidate in retrieval.candidates
        )
        section_hits += int(section_matched)
        region_hits += int(region_matched)
        plan_hits += int(_plan_matches_outcome(retrieval, matched, found_pages))
        if retrieval.retrieval_plan.get("global_fallback_used"):
            global_fallback_uses += 1
            global_fallback_hits += int(matched)
        if retrieval.retrieval_plan.get("evidence_spread") == "distributed":
            distributed_questions += 1
            overlap = len(set(found_pages) & set(expected_pages))
            multi_region_total += overlap / max(len(expected_pages), 1)

    size = len(dataset)
    page_hit = hits / max(size, 1)
    mrr = mrr_total / max(size, 1)
    section_hit = section_hits / max(size, 1)
    region_hit = region_hits / max(size, 1)
    plan_accuracy = plan_hits / max(size, 1)
    return {
        "dataset_size": size,
        "repair_confidence": document_info["confidence"],
        "repair_applied": document_info["repair_applied"],
        "top_k_page_hit_rate": page_hit,
        "mean_reciprocal_rank": mrr,
        "section_hit_rate": section_hit,
        "region_hit_rate": region_hit,
        "plan_accuracy": plan_accuracy,
        "global_fallback_recovery_rate": global_fallback_hits / max(global_fallback_uses, 1),
        "multi_region_recall": multi_region_total / max(distributed_questions, 1),
        "objective_score": (
            0.30 * page_hit
            + 0.20 * mrr
            + 0.20 * section_hit
            + 0.20 * region_hit
            + 0.10 * plan_accuracy
        ),
    }


def _aggregate(metrics: dict[str, dict[str, Any]], names: set[str]) -> dict[str, Any]:
    selected = [metrics[name] for name in metrics if name in names]
    if not selected:
        return {"dataset_count": 0}
    keys = [
        "top_k_page_hit_rate",
        "mean_reciprocal_rank",
        "section_hit_rate",
        "region_hit_rate",
        "plan_accuracy",
        "global_fallback_recovery_rate",
        "multi_region_recall",
        "objective_score",
    ]
    return {
        "dataset_count": len(selected),
        **{key: sum(item[key] for item in selected) / len(selected) for key in keys},
    }


def run_sweep(values: list[float]) -> dict[str, Any]:
    base_settings = get_settings()
    payload: dict[str, Any] = {}

    for threshold in values:
        settings = _build_settings(base_settings, threshold=threshold)
        pipeline = HelpmatePipeline(settings)
        document_summaries: dict[str, dict[str, Any]] = {}
        documents: dict[str, Any] = {}
        for dataset_name, document_path in TARGET_DATASETS.items():
            summary, document = _document_repair_summary(pipeline, document_path)
            document_summaries[dataset_name] = summary
            documents[dataset_name] = document

        per_dataset = {
            dataset_name: _eval_dataset(pipeline, dataset_name, document_summaries[dataset_name], documents[dataset_name])
            for dataset_name in TARGET_DATASETS
        }
        repair_rate = sum(1 for summary in document_summaries.values() if summary["repair_applied"]) / len(document_summaries)
        noisy_repairs = sum(1 for name, summary in document_summaries.items() if name in NOISY_DATASETS and summary["repair_applied"])
        healthy_repairs = sum(1 for name, summary in document_summaries.items() if name in HEALTHY_DATASETS and summary["repair_applied"])

        payload[f"threshold_{str(threshold).replace('.', '_')}"] = {
            "settings": {
                "structure_repair_confidence_threshold": threshold,
                "structure_repair_model": settings.structure_repair_model,
            },
            "repair_rate": repair_rate,
            "noisy_trigger_rate": noisy_repairs / len(NOISY_DATASETS),
            "healthy_false_positive_rate": healthy_repairs / len(HEALTHY_DATASETS),
            "overall": _aggregate(per_dataset, set(TARGET_DATASETS)),
            "noisy_family": _aggregate(per_dataset, NOISY_DATASETS),
            "healthy_family": _aggregate(per_dataset, HEALTHY_DATASETS),
            "documents": document_summaries,
            "per_dataset": per_dataset,
        }

    ordered = sorted(
        payload.items(),
        key=lambda item: (
            -item[1]["healthy_false_positive_rate"],
            item[1]["noisy_trigger_rate"],
            item[1]["noisy_family"]["objective_score"],
            item[1]["overall"]["objective_score"],
        ),
        reverse=True,
    )
    return {
        "variant_order": [name for name, _ in ordered],
        "variants": payload,
    }


def _save_report(payload: dict[str, Any]) -> Path:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = REPORTS_DIR / f"structure_repair_threshold_sweep_{timestamp}.json"
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description="Sweep indexing-time structure-repair thresholds.")
    parser.add_argument("--values", nargs="*", type=float, default=DEFAULT_VALUES)
    args = parser.parse_args()
    payload = run_sweep(args.values)
    report_path = _save_report(payload)
    best = payload["variant_order"][0]
    print(
        json.dumps(
            {
                "report_path": str(report_path),
                "best_variant": best,
                "best_noisy_family": payload["variants"][best]["noisy_family"],
                "best_healthy_family": payload["variants"][best]["healthy_family"],
                "repair_profile": {
                    "repair_rate": payload["variants"][best]["repair_rate"],
                    "noisy_trigger_rate": payload["variants"][best]["noisy_trigger_rate"],
                    "healthy_false_positive_rate": payload["variants"][best]["healthy_false_positive_rate"],
                },
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
