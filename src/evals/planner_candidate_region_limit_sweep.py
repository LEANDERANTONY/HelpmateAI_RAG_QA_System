from __future__ import annotations

import argparse
import json
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any

from src.config import Settings, get_settings
from src.evals.evidence_selector_weight_sweep import DATASET_TO_DOCUMENT, ROOT
from src.evals.retrieval_eval import _plan_matches_outcome
from src.pipeline import HelpmatePipeline


REPORTS_DIR = ROOT / "docs" / "evals" / "reports"
LOCAL_STORE_DIR = ROOT / "tmp" / "planner_candidate_region_limit_sweep"
DEFAULT_VALUES = [3, 4, 5, 6, 8, 10]
PAPER_FAMILY = {
    "thesis_retrieval_eval_dataset.json",
    "pancreas7_retrieval_eval_dataset.json",
    "pancreas8_retrieval_eval_dataset.json",
    "reportgeneration_retrieval_eval_dataset.json",
    "reportgeneration2_retrieval_eval_dataset.json",
}


def _build_settings(base: Settings, *, value: int) -> Settings:
    variant_id = f"regions_{value}"
    settings = replace(
        base,
        data_dir=LOCAL_STORE_DIR / "data",
        cache_dir=LOCAL_STORE_DIR / "data" / variant_id / "cache",
        state_store_backend="local",
        vector_store_backend="local",
        planner_candidate_region_limit=value,
        retrieval_version=f"{base.retrieval_version}-{variant_id}",
        generation_version=f"{base.generation_version}-{variant_id}",
    )
    settings.ensure_dirs()
    return settings


def _eval_dataset(pipeline: HelpmatePipeline, dataset_name: str, document_path: Path) -> dict[str, Any]:
    dataset_path = ROOT / "docs" / "evals" / dataset_name
    dataset = json.loads(dataset_path.read_text(encoding="utf-8"))
    document = pipeline.ingest_document(document_path)
    pipeline.build_or_load_index(document)

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


def _aggregate(dataset_metrics: dict[str, dict[str, Any]], dataset_names: set[str] | None = None) -> dict[str, Any]:
    names = [name for name in dataset_metrics if dataset_names is None or name in dataset_names]
    if not names:
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
        "dataset_count": len(names),
        **{
            key: sum(dataset_metrics[name][key] for name in names) / len(names)
            for key in keys
        },
    }


def run_sweep(values: list[int]) -> dict[str, Any]:
    base_settings = get_settings()
    payload: dict[str, Any] = {}

    for value in values:
        settings = _build_settings(base_settings, value=value)
        pipeline = HelpmatePipeline(settings)
        per_dataset = {
            dataset_name: _eval_dataset(pipeline, dataset_name, document_path)
            for dataset_name, document_path in DATASET_TO_DOCUMENT.items()
        }
        payload[f"regions_{value}"] = {
            "settings": {
                "planner_candidate_region_limit": value,
                "synopsis_section_window": settings.synopsis_section_window,
            },
            "overall": _aggregate(per_dataset),
            "paper_family": _aggregate(per_dataset, PAPER_FAMILY),
            "per_dataset": dict(sorted(per_dataset.items())),
        }

    ordered = sorted(
        payload.items(),
        key=lambda item: (
            item[1]["overall"]["plan_accuracy"],
            item[1]["paper_family"]["region_hit_rate"],
            item[1]["overall"]["objective_score"],
            item[1]["paper_family"]["objective_score"],
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
    path = REPORTS_DIR / f"planner_candidate_region_limit_sweep_{timestamp}.json"
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description="Sweep planner_candidate_region_limit across the labeled retrieval corpus.")
    parser.add_argument("--values", nargs="*", type=int, default=DEFAULT_VALUES)
    args = parser.parse_args()
    payload = run_sweep(args.values)
    report_path = _save_report(payload)
    best = payload["variant_order"][0]
    print(
        json.dumps(
            {
                "report_path": str(report_path),
                "best_variant": best,
                "best_overall": payload["variants"][best]["overall"],
                "best_paper_family": payload["variants"][best]["paper_family"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
