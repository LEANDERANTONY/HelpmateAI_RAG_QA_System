from __future__ import annotations

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
LOCAL_STORE_DIR = ROOT / "tmp" / "indexing_layer_gate_compare"

TARGET_DATASETS = {
    "reportgeneration_retrieval_eval_dataset.json": ROOT / "static" / "sample_files" / "reportgeneration.pdf",
    "reportgeneration2_retrieval_eval_dataset.json": ROOT / "static" / "sample_files" / "reportgeneration2.pdf",
    "health_retrieval_eval_dataset.json": ROOT / "static" / "sample_files" / "HealthInsurance_Policy.pdf",
    "thesis_retrieval_eval_dataset.json": ROOT / "static" / "sample_files" / "Final_Thesis_Leander_Antony_A.pdf",
}
WEAK_FAMILY = {
    "reportgeneration_retrieval_eval_dataset.json",
    "reportgeneration2_retrieval_eval_dataset.json",
}
GUARDRAIL_FAMILY = {
    "health_retrieval_eval_dataset.json",
    "thesis_retrieval_eval_dataset.json",
}


def _build_settings(
    base: Settings,
    *,
    variant: str,
    structure_repair_enabled: bool,
    structure_repair_require_header_dominated: bool,
) -> Settings:
    settings = replace(
        base,
        data_dir=LOCAL_STORE_DIR / "data",
        cache_dir=LOCAL_STORE_DIR / "data" / variant / "cache",
        state_store_backend="local",
        vector_store_backend="local",
        planner_llm_enabled=False,
        router_llm_enabled=False,
        reranker_enabled=True,
        structure_repair_enabled=structure_repair_enabled,
        structure_repair_require_header_dominated=structure_repair_require_header_dominated,
        index_schema_version=(
            f"{base.index_schema_version}-{variant}-"
            f"{'header_gate' if structure_repair_require_header_dominated else 'ungated'}"
        ),
        retrieval_version=(
            f"{base.retrieval_version}-{variant}-"
            f"{'header_gate' if structure_repair_require_header_dominated else 'ungated'}"
        ),
        generation_version=f"{base.generation_version}-{variant}",
    )
    settings.ensure_dirs()
    return settings


def _document_summary(pipeline: HelpmatePipeline, document_path: Path) -> tuple[dict[str, Any], Any]:
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
    misses: list[dict[str, Any]] = []

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
        if not matched:
            misses.append(
                {
                    "question": item["question"],
                    "expected_pages": expected_pages,
                    "found_pages": found_pages,
                    "strategy_notes": retrieval.strategy_notes[:6],
                    "retrieval_plan": retrieval.retrieval_plan,
                }
            )

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
        "misses": misses,
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


def _run_variant(
    base_settings: Settings,
    *,
    variant_name: str,
    structure_repair_enabled: bool,
    structure_repair_require_header_dominated: bool,
) -> dict[str, Any]:
    settings = _build_settings(
        base_settings,
        variant=variant_name,
        structure_repair_enabled=structure_repair_enabled,
        structure_repair_require_header_dominated=structure_repair_require_header_dominated,
    )
    pipeline = HelpmatePipeline(settings)
    documents: dict[str, Any] = {}
    document_summaries: dict[str, dict[str, Any]] = {}
    for dataset_name, document_path in TARGET_DATASETS.items():
        summary, document = _document_summary(pipeline, document_path)
        document_summaries[dataset_name] = summary
        documents[dataset_name] = document

    per_dataset = {
        dataset_name: _eval_dataset(pipeline, dataset_name, document_summaries[dataset_name], documents[dataset_name])
        for dataset_name in TARGET_DATASETS
    }

    return {
        "settings": {
            "structure_repair_enabled": structure_repair_enabled,
            "structure_repair_confidence_threshold": settings.structure_repair_confidence_threshold,
            "structure_repair_model": settings.structure_repair_model,
            "structure_repair_require_header_dominated": settings.structure_repair_require_header_dominated,
            "index_schema_version": settings.index_schema_version,
            "retrieval_version": settings.retrieval_version,
        },
        "documents": document_summaries,
        "per_dataset": per_dataset,
        "overall": _aggregate(per_dataset, set(TARGET_DATASETS)),
        "weak_family": _aggregate(per_dataset, WEAK_FAMILY),
        "guardrails": _aggregate(per_dataset, GUARDRAIL_FAMILY),
    }


def run_compare() -> dict[str, Any]:
    base_settings = get_settings()
    variants = {
        "layer1_only": _run_variant(
            base_settings,
            variant_name="layer1_only",
            structure_repair_enabled=False,
            structure_repair_require_header_dominated=True,
        ),
        "layer2_selective": _run_variant(
            base_settings,
            variant_name="layer2_selective",
            structure_repair_enabled=True,
            structure_repair_require_header_dominated=True,
        ),
        "layer2_ungated": _run_variant(
            base_settings,
            variant_name="layer2_ungated",
            structure_repair_enabled=True,
            structure_repair_require_header_dominated=False,
        ),
    }
    return {
        "variant_order": ["layer1_only", "layer2_selective", "layer2_ungated"],
        "variants": variants,
        "delta_layer2_selective_vs_layer1": {
            "overall_objective": variants["layer2_selective"]["overall"]["objective_score"] - variants["layer1_only"]["overall"]["objective_score"],
            "weak_family_objective": variants["layer2_selective"]["weak_family"]["objective_score"] - variants["layer1_only"]["weak_family"]["objective_score"],
            "guardrails_objective": variants["layer2_selective"]["guardrails"]["objective_score"] - variants["layer1_only"]["guardrails"]["objective_score"],
        },
        "delta_layer2_ungated_vs_layer1": {
            "overall_objective": variants["layer2_ungated"]["overall"]["objective_score"] - variants["layer1_only"]["overall"]["objective_score"],
            "weak_family_objective": variants["layer2_ungated"]["weak_family"]["objective_score"] - variants["layer1_only"]["weak_family"]["objective_score"],
            "guardrails_objective": variants["layer2_ungated"]["guardrails"]["objective_score"] - variants["layer1_only"]["guardrails"]["objective_score"],
        },
        "delta_layer2_selective_vs_layer2_ungated": {
            "overall_objective": variants["layer2_selective"]["overall"]["objective_score"] - variants["layer2_ungated"]["overall"]["objective_score"],
            "weak_family_objective": variants["layer2_selective"]["weak_family"]["objective_score"] - variants["layer2_ungated"]["weak_family"]["objective_score"],
            "guardrails_objective": variants["layer2_selective"]["guardrails"]["objective_score"] - variants["layer2_ungated"]["guardrails"]["objective_score"],
        },
    }


def _save_report(payload: dict[str, Any]) -> Path:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = REPORTS_DIR / f"indexing_layer_gate_compare_{timestamp}.json"
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def main() -> None:
    payload = run_compare()
    report_path = _save_report(payload)
    print(
        json.dumps(
            {
                "report_path": str(report_path),
                "delta_layer2_selective_vs_layer1": payload["delta_layer2_selective_vs_layer1"],
                "delta_layer2_ungated_vs_layer1": payload["delta_layer2_ungated_vs_layer1"],
                "delta_layer2_selective_vs_layer2_ungated": payload["delta_layer2_selective_vs_layer2_ungated"],
                "layer1_only": payload["variants"]["layer1_only"]["overall"],
                "layer2_selective": payload["variants"]["layer2_selective"]["overall"],
                "layer2_ungated": payload["variants"]["layer2_ungated"]["overall"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
