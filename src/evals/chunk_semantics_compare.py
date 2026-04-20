from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any

from src.config import Settings, get_settings
from src.evals.evidence_selector_weight_sweep import ROOT
from src.evals.indexing_layer_gate_compare import (
    GUARDRAIL_FAMILY,
    TARGET_DATASETS,
    WEAK_FAMILY,
    _aggregate,
    _document_summary,
    _eval_dataset,
)
from src.pipeline import HelpmatePipeline


REPORTS_DIR = ROOT / "docs" / "evals" / "reports"
LOCAL_STORE_DIR = ROOT / "tmp" / "chunk_semantics_compare"


def _build_settings(base: Settings, *, variant: str, chunk_semantics_enabled: bool) -> Settings:
    settings = replace(
        base,
        data_dir=LOCAL_STORE_DIR / "data",
        cache_dir=LOCAL_STORE_DIR / "data" / variant / "cache",
        state_store_backend="local",
        vector_store_backend="local",
        planner_llm_enabled=False,
        router_llm_enabled=False,
        reranker_enabled=True,
        structure_repair_enabled=True,
        structure_repair_require_header_dominated=True,
        chunk_semantics_enabled=chunk_semantics_enabled,
        index_schema_version=f"{base.index_schema_version}-{variant}",
        retrieval_version=f"{base.retrieval_version}-{variant}",
        generation_version=f"{base.generation_version}-{variant}",
    )
    settings.ensure_dirs()
    return settings


def _run_variant(base: Settings, *, variant: str, chunk_semantics_enabled: bool) -> dict[str, Any]:
    settings = _build_settings(base, variant=variant, chunk_semantics_enabled=chunk_semantics_enabled)
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
            "chunk_semantics_enabled": chunk_semantics_enabled,
            "chunk_semantics_model": settings.chunk_semantics_model,
            "chunk_semantics_max_review_chunks": settings.chunk_semantics_max_review_chunks,
            "structure_repair_enabled": settings.structure_repair_enabled,
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
    base = get_settings()
    variants = {
        "layer2_selective": _run_variant(base, variant="layer2_selective", chunk_semantics_enabled=False),
        "layer2_selective_chunk_semantics": _run_variant(
            base,
            variant="layer2_selective_chunk_semantics",
            chunk_semantics_enabled=True,
        ),
    }
    return {
        "variant_order": ["layer2_selective", "layer2_selective_chunk_semantics"],
        "variants": variants,
        "delta_chunk_semantics_vs_layer2_selective": {
            "overall_objective": variants["layer2_selective_chunk_semantics"]["overall"]["objective_score"]
            - variants["layer2_selective"]["overall"]["objective_score"],
            "weak_family_objective": variants["layer2_selective_chunk_semantics"]["weak_family"]["objective_score"]
            - variants["layer2_selective"]["weak_family"]["objective_score"],
            "guardrails_objective": variants["layer2_selective_chunk_semantics"]["guardrails"]["objective_score"]
            - variants["layer2_selective"]["guardrails"]["objective_score"],
        },
    }


def _save_report(payload: dict[str, Any]) -> Path:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = REPORTS_DIR / f"chunk_semantics_compare_{timestamp}.json"
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def main() -> None:
    payload = run_compare()
    report_path = _save_report(payload)
    print(
        json.dumps(
            {
                "report_path": str(report_path),
                "delta_chunk_semantics_vs_layer2_selective": payload["delta_chunk_semantics_vs_layer2_selective"],
                "layer2_selective": payload["variants"]["layer2_selective"]["overall"],
                "layer2_selective_chunk_semantics": payload["variants"]["layer2_selective_chunk_semantics"]["overall"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
