from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any

from src.config import Settings, get_settings
from src.evals.answer_stack_ablation import _load_dataset
from src.evals.evidence_selector_weight_sweep import ROOT
from src.pipeline import HelpmatePipeline
from src.sections import build_sections


REPORTS_DIR = ROOT / "docs" / "evals" / "reports"
LOCAL_STORE_DIR = ROOT / "tmp" / "indexing_full_flow_compare"

POSITIVE_DATASETS = {
    "reportgeneration_retrieval_eval_dataset.json": ROOT / "static" / "sample_files" / "reportgeneration.pdf",
    "reportgeneration2_retrieval_eval_dataset.json": ROOT / "static" / "sample_files" / "reportgeneration2.pdf",
    "health_retrieval_eval_dataset.json": ROOT / "static" / "sample_files" / "HealthInsurance_Policy.pdf",
    "thesis_retrieval_eval_dataset.json": ROOT / "static" / "sample_files" / "Final_Thesis_Leander_Antony_A.pdf",
}

NEGATIVE_DATASETS = {
    "reportgeneration_negative_eval_dataset.json": ROOT / "static" / "sample_files" / "reportgeneration.pdf",
    "reportgeneration2_negative_eval_dataset.json": ROOT / "static" / "sample_files" / "reportgeneration2.pdf",
    "health_negative_eval_dataset.json": ROOT / "static" / "sample_files" / "HealthInsurance_Policy.pdf",
    "thesis_negative_eval_dataset.json": ROOT / "static" / "sample_files" / "Final_Thesis_Leander_Antony_A.pdf",
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
        evidence_selector_enabled=True,
        evidence_selector_prune=False,
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
        "base_section_count": len(sections),
        "final_section_count": len(repaired_sections),
        "confidence": decision.confidence,
        "should_repair": decision.should_repair,
        "repair_applied": repair_applied,
        "reasons": list(decision.reasons),
    }, document


def _positive_row(question: str, expected_pages: list[str], expected_fragments: list[str], answer, planner_source: str) -> dict[str, Any]:
    evidence_pages = [candidate.metadata.get("page_label", "Document") for candidate in answer.evidence]
    evidence_text = " ".join(candidate.text.lower() for candidate in answer.evidence)
    fragment_hits = sum(1 for fragment in expected_fragments if fragment.lower() in evidence_text)
    return {
        "question": question,
        "supported": bool(answer.supported),
        "citation_page_hit": int(any(page in evidence_pages for page in expected_pages)),
        "evidence_fragment_recall": fragment_hits / max(len(expected_fragments), 1),
        "planner_source": planner_source,
        "model_name": answer.model_name,
    }


def _negative_row(question: str, answer, planner_source: str) -> dict[str, Any]:
    return {
        "question": question,
        "abstained": int(not answer.supported),
        "supported": bool(answer.supported),
        "planner_source": planner_source,
        "model_name": answer.model_name,
    }


def _summarize_positive(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {
            "dataset_size": 0,
            "supported_rate": 0.0,
            "citation_page_hit_rate": 0.0,
            "evidence_fragment_recall_mean": 0.0,
        }
    return {
        "dataset_size": len(rows),
        "supported_rate": sum(row["supported"] for row in rows) / len(rows),
        "citation_page_hit_rate": sum(row["citation_page_hit"] for row in rows) / len(rows),
        "evidence_fragment_recall_mean": sum(row["evidence_fragment_recall"] for row in rows) / len(rows),
    }


def _summarize_negative(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {
            "dataset_size": 0,
            "abstention_rate": 0.0,
            "false_support_rate": 0.0,
        }
    return {
        "dataset_size": len(rows),
        "abstention_rate": sum(row["abstained"] for row in rows) / len(rows),
        "false_support_rate": sum(1 for row in rows if row["supported"]) / len(rows),
    }


def _aggregate_positive(metrics: dict[str, dict[str, Any]], names: set[str]) -> dict[str, Any]:
    selected = [metrics[name]["positive"] for name in metrics if name in names]
    if not selected:
        return {"dataset_count": 0}
    return {
        "dataset_count": len(selected),
        "supported_rate": sum(item["supported_rate"] for item in selected) / len(selected),
        "citation_page_hit_rate": sum(item["citation_page_hit_rate"] for item in selected) / len(selected),
        "evidence_fragment_recall_mean": sum(item["evidence_fragment_recall_mean"] for item in selected) / len(selected),
    }


def _aggregate_negative(metrics: dict[str, dict[str, Any]], names: set[str]) -> dict[str, Any]:
    selected = [metrics[name]["negative"] for name in metrics if name in names]
    if not selected:
        return {"dataset_count": 0}
    return {
        "dataset_count": len(selected),
        "abstention_rate": sum(item["abstention_rate"] for item in selected) / len(selected),
        "false_support_rate": sum(item["false_support_rate"] for item in selected) / len(selected),
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
    for dataset_name, document_path in POSITIVE_DATASETS.items():
        summary, document = _document_summary(pipeline, document_path)
        document_summaries[dataset_name] = summary
        documents[dataset_name] = document

    per_dataset: dict[str, Any] = {}
    positive_rows_all: list[dict[str, Any]] = []
    negative_rows_all: list[dict[str, Any]] = []

    for dataset_name, document in documents.items():
        positive_items = _load_dataset(ROOT / "docs" / "evals" / dataset_name)
        negative_dataset_name = dataset_name.replace("_retrieval_eval_dataset.json", "_negative_eval_dataset.json")
        negative_items = _load_dataset(ROOT / "docs" / "evals" / negative_dataset_name)

        dataset_positive_rows: list[dict[str, Any]] = []
        dataset_negative_rows: list[dict[str, Any]] = []

        for item in positive_items:
            retrieval = pipeline.retrieve_evidence(document.document_id, document.fingerprint, item["question"])
            planner_source = str((retrieval.retrieval_plan or {}).get("planner_source", "deterministic"))
            retrieval = pipeline.evidence_selector.select(item["question"], retrieval)
            answer = pipeline.generate_answer(document.document_id, item["question"], retrieval)
            row = _positive_row(
                item["question"],
                item.get("expected_pages", []),
                item.get("expected_fragments", []),
                answer,
                planner_source,
            )
            dataset_positive_rows.append(row)
            positive_rows_all.append(row)

        for item in negative_items:
            retrieval = pipeline.retrieve_evidence(document.document_id, document.fingerprint, item["question"])
            planner_source = str((retrieval.retrieval_plan or {}).get("planner_source", "deterministic"))
            retrieval = pipeline.evidence_selector.select(item["question"], retrieval)
            answer = pipeline.generate_answer(document.document_id, item["question"], retrieval)
            row = _negative_row(item["question"], answer, planner_source)
            dataset_negative_rows.append(row)
            negative_rows_all.append(row)

        per_dataset[dataset_name] = {
            "positive": _summarize_positive(dataset_positive_rows),
            "negative": _summarize_negative(dataset_negative_rows),
        }

    return {
        "settings": {
            "structure_repair_enabled": settings.structure_repair_enabled,
            "structure_repair_require_header_dominated": settings.structure_repair_require_header_dominated,
            "planner_llm_enabled": settings.planner_llm_enabled,
            "router_llm_enabled": settings.router_llm_enabled,
            "reranker_enabled": settings.reranker_enabled,
            "evidence_selector_enabled": settings.evidence_selector_enabled,
            "evidence_selector_prune": settings.evidence_selector_prune,
            "index_schema_version": settings.index_schema_version,
            "retrieval_version": settings.retrieval_version,
            "generation_version": settings.generation_version,
        },
        "documents": document_summaries,
        "per_dataset": per_dataset,
        "positive_overall": _summarize_positive(positive_rows_all),
        "negative_overall": _summarize_negative(negative_rows_all),
        "positive_weak_family": _aggregate_positive(per_dataset, WEAK_FAMILY),
        "positive_guardrails": _aggregate_positive(per_dataset, GUARDRAIL_FAMILY),
        "negative_weak_family": _aggregate_negative(per_dataset, WEAK_FAMILY),
        "negative_guardrails": _aggregate_negative(per_dataset, GUARDRAIL_FAMILY),
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
        "layer2_ungated": _run_variant(
            base_settings,
            variant_name="layer2_ungated",
            structure_repair_enabled=True,
            structure_repair_require_header_dominated=False,
        ),
    }
    return {
        "variant_order": ["layer1_only", "layer2_ungated"],
        "variants": variants,
        "delta_layer2_ungated_vs_layer1": {
            "positive_overall_supported_rate": (
                variants["layer2_ungated"]["positive_overall"]["supported_rate"]
                - variants["layer1_only"]["positive_overall"]["supported_rate"]
            ),
            "positive_overall_citation_rate": (
                variants["layer2_ungated"]["positive_overall"]["citation_page_hit_rate"]
                - variants["layer1_only"]["positive_overall"]["citation_page_hit_rate"]
            ),
            "positive_overall_fragment_recall": (
                variants["layer2_ungated"]["positive_overall"]["evidence_fragment_recall_mean"]
                - variants["layer1_only"]["positive_overall"]["evidence_fragment_recall_mean"]
            ),
            "negative_overall_abstention_rate": (
                variants["layer2_ungated"]["negative_overall"]["abstention_rate"]
                - variants["layer1_only"]["negative_overall"]["abstention_rate"]
            ),
            "negative_overall_false_support_rate": (
                variants["layer2_ungated"]["negative_overall"]["false_support_rate"]
                - variants["layer1_only"]["negative_overall"]["false_support_rate"]
            ),
        },
    }


def _save_report(payload: dict[str, Any]) -> Path:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = REPORTS_DIR / f"indexing_full_flow_compare_{timestamp}.json"
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def main() -> None:
    payload = run_compare()
    report_path = _save_report(payload)
    print(
        json.dumps(
            {
                "report_path": str(report_path),
                "delta_layer2_ungated_vs_layer1": payload["delta_layer2_ungated_vs_layer1"],
                "layer1_only": {
                    "positive_overall": payload["variants"]["layer1_only"]["positive_overall"],
                    "negative_overall": payload["variants"]["layer1_only"]["negative_overall"],
                },
                "layer2_ungated": {
                    "positive_overall": payload["variants"]["layer2_ungated"]["positive_overall"],
                    "negative_overall": payload["variants"]["layer2_ungated"]["negative_overall"],
                },
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
