from __future__ import annotations

import argparse
import json
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from types import MethodType
from typing import Any

from src.config import Settings, get_settings
from src.evals.evidence_selector_weight_sweep import DATASET_TO_DOCUMENT, ROOT
from src.evals.retrieval_eval import _plan_matches_outcome
from src.pipeline import HelpmatePipeline


REPORTS_DIR = ROOT / "docs" / "evals" / "reports"
LOCAL_STORE_DIR = ROOT / "tmp" / "topology_edge_ablation"
DATASET_NAMES = [
    "health_retrieval_eval_dataset.json",
    "thesis_retrieval_eval_dataset.json",
    "pancreas7_retrieval_eval_dataset.json",
    "pancreas8_retrieval_eval_dataset.json",
    "reportgeneration_retrieval_eval_dataset.json",
    "reportgeneration2_retrieval_eval_dataset.json",
]
POLICY_THESIS_FAMILY = {
    "health_retrieval_eval_dataset.json",
    "thesis_retrieval_eval_dataset.json",
}
PAPER_FAMILY = set(DATASET_NAMES) - POLICY_THESIS_FAMILY


def _build_settings(base: Settings, *, variant_id: str) -> Settings:
    settings = replace(
        base,
        data_dir=LOCAL_STORE_DIR / "data",
        cache_dir=LOCAL_STORE_DIR / "data" / variant_id / "cache",
        state_store_backend="local",
        vector_store_backend="local",
        retrieval_version=f"{base.retrieval_version}-{variant_id}",
        generation_version=f"{base.generation_version}-{variant_id}",
    )
    settings.ensure_dirs()
    return settings


def _patch_edge_types(pipeline: HelpmatePipeline, *, soft_local: set[str], soft_multi_region: set[str]) -> None:
    def custom_expand(self, selected, plan, edges):
        if plan.constraint_mode == "hard_region":
            return list(dict.fromkeys(selected))
        edge_types = soft_local if plan.constraint_mode == "soft_local" else soft_multi_region
        expanded = list(selected)
        for section_id in selected[: 1 if plan.constraint_mode == "soft_local" else 2]:
            expanded.extend(self.topology_service.neighbor_section_ids(section_id, edges, edge_types=edge_types, top_k=2))
        return list(dict.fromkeys(expanded))

    pipeline.retriever._expand_section_scope = MethodType(custom_expand, pipeline.retriever)


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


def _aggregate(metrics: dict[str, dict[str, Any]], dataset_names: set[str]) -> dict[str, Any]:
    selected = [metrics[name] for name in metrics if name in dataset_names]
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


def run_ablation() -> dict[str, Any]:
    base_settings = get_settings()
    variants = {
        "current": {
            "soft_local": {"previous_next", "parent_child", "semantic_neighbor"},
            "soft_multi_region": {"previous_next", "same_region_family", "semantic_neighbor"},
        },
        "no_previous_next": {
            "soft_local": {"parent_child", "semantic_neighbor"},
            "soft_multi_region": {"same_region_family", "semantic_neighbor"},
        },
        "no_parent_child": {
            "soft_local": {"previous_next", "semantic_neighbor"},
            "soft_multi_region": {"previous_next", "same_region_family", "semantic_neighbor"},
        },
        "no_same_region_family": {
            "soft_local": {"previous_next", "parent_child", "semantic_neighbor"},
            "soft_multi_region": {"previous_next", "semantic_neighbor"},
        },
        "no_semantic_neighbor": {
            "soft_local": {"previous_next", "parent_child"},
            "soft_multi_region": {"previous_next", "same_region_family"},
        },
    }
    payload: dict[str, Any] = {}

    for variant_name, edge_types in variants.items():
        settings = _build_settings(base_settings, variant_id=variant_name)
        pipeline = HelpmatePipeline(settings)
        _patch_edge_types(
            pipeline,
            soft_local=edge_types["soft_local"],
            soft_multi_region=edge_types["soft_multi_region"],
        )
        per_dataset = {
            dataset_name: _eval_dataset(pipeline, dataset_name, DATASET_TO_DOCUMENT[dataset_name])
            for dataset_name in DATASET_NAMES
        }
        payload[variant_name] = {
            "edge_types": {
                "soft_local": sorted(edge_types["soft_local"]),
                "soft_multi_region": sorted(edge_types["soft_multi_region"]),
            },
            "overall": _aggregate(per_dataset, set(DATASET_NAMES)),
            "policy_thesis_family": _aggregate(per_dataset, POLICY_THESIS_FAMILY),
            "paper_family": _aggregate(per_dataset, PAPER_FAMILY),
            "per_dataset": per_dataset,
        }

    ordered = sorted(
        payload.items(),
        key=lambda item: (
            item[1]["overall"]["objective_score"],
            item[1]["policy_thesis_family"]["objective_score"],
            item[1]["paper_family"]["objective_score"],
            item[1]["overall"]["plan_accuracy"],
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
    path = REPORTS_DIR / f"topology_edge_ablation_{timestamp}.json"
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description="Ablate topology edge types used in section scope expansion.")
    parser.parse_args()
    payload = run_ablation()
    report_path = _save_report(payload)
    best = payload["variant_order"][0]
    print(
        json.dumps(
            {
                "report_path": str(report_path),
                "best_variant": best,
                "best_overall": payload["variants"][best]["overall"],
                "best_policy_thesis_family": payload["variants"][best]["policy_thesis_family"],
                "best_paper_family": payload["variants"][best]["paper_family"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
