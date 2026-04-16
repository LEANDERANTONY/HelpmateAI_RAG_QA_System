from __future__ import annotations

import argparse
import json
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any

from src.config import Settings, get_settings
from src.evals.evidence_selector_weight_sweep import DATASET_TO_DOCUMENT, ROOT
from src.pipeline import HelpmatePipeline


REPORTS_DIR = ROOT / "docs" / "evals" / "reports"
LOCAL_STORE_DIR = ROOT / "tmp" / "answer_stack_ablation"

POSITIVE_DATASETS = {
    "retrieval_eval_dataset.json": ROOT / "static" / "sample_files" / "Principal-Sample-Life-Insurance-Policy.pdf",
    "health_retrieval_eval_dataset.json": ROOT / "static" / "sample_files" / "HealthInsurance_Policy.pdf",
    "thesis_retrieval_eval_dataset.json": ROOT / "static" / "sample_files" / "Final_Thesis_Leander_Antony_A.pdf",
    "pancreas7_retrieval_eval_dataset.json": ROOT / "static" / "sample_files" / "pancreas7.pdf",
    "pancreas8_retrieval_eval_dataset.json": ROOT / "static" / "sample_files" / "pancreas8.pdf",
    "reportgeneration_retrieval_eval_dataset.json": ROOT / "static" / "sample_files" / "reportgeneration.pdf",
    "reportgeneration2_retrieval_eval_dataset.json": ROOT / "static" / "sample_files" / "reportgeneration2.pdf",
}

NEGATIVE_DATASETS = {
    "negative_eval_dataset.json": ROOT / "static" / "sample_files" / "Principal-Sample-Life-Insurance-Policy.pdf",
    "health_negative_eval_dataset.json": ROOT / "static" / "sample_files" / "HealthInsurance_Policy.pdf",
    "thesis_negative_eval_dataset.json": ROOT / "static" / "sample_files" / "Final_Thesis_Leander_Antony_A.pdf",
    "pancreas7_negative_eval_dataset.json": ROOT / "static" / "sample_files" / "pancreas7.pdf",
    "pancreas8_negative_eval_dataset.json": ROOT / "static" / "sample_files" / "pancreas8.pdf",
    "reportgeneration_negative_eval_dataset.json": ROOT / "static" / "sample_files" / "reportgeneration.pdf",
    "reportgeneration2_negative_eval_dataset.json": ROOT / "static" / "sample_files" / "reportgeneration2.pdf",
}


def _load_dataset(path: Path) -> list[dict[str, Any]]:
    return json.loads(path.read_text(encoding="utf-8"))


def _build_settings(
    base: Settings,
    *,
    variant_id: str,
    reranker_enabled: bool,
    selector_enabled: bool,
    planner_threshold: float,
    router_threshold: float,
    router_llm_enabled: bool,
) -> Settings:
    settings = replace(
        base,
        data_dir=LOCAL_STORE_DIR / "data",
        cache_dir=LOCAL_STORE_DIR / "data" / "cache" / variant_id,
        state_store_backend="local",
        vector_store_backend="local",
        reranker_enabled=reranker_enabled,
        evidence_selector_enabled=selector_enabled,
        planner_confidence_threshold=planner_threshold,
        router_confidence_threshold=router_threshold,
        router_llm_enabled=router_llm_enabled,
        retrieval_version=f"{base.retrieval_version}-{variant_id}",
        generation_version=f"{base.generation_version}-{variant_id}",
    )
    settings.ensure_dirs()
    return settings


def _variant_settings(base: Settings) -> dict[str, Settings]:
    return {
        "baseline": _build_settings(
            base,
            variant_id="baseline",
            reranker_enabled=False,
            selector_enabled=False,
            planner_threshold=2.0,
            router_threshold=1.0,
            router_llm_enabled=False,
        ),
        "reranker_only": _build_settings(
            base,
            variant_id="reranker_only",
            reranker_enabled=True,
            selector_enabled=False,
            planner_threshold=2.0,
            router_threshold=1.0,
            router_llm_enabled=False,
        ),
        "planner_reranker": _build_settings(
            base,
            variant_id="planner_reranker",
            reranker_enabled=True,
            selector_enabled=False,
            planner_threshold=base.planner_confidence_threshold,
            router_threshold=base.router_confidence_threshold,
            router_llm_enabled=True,
        ),
        "full_stack": _build_settings(
            base,
            variant_id="full_stack",
            reranker_enabled=True,
            selector_enabled=True,
            planner_threshold=base.planner_confidence_threshold,
            router_threshold=base.router_confidence_threshold,
            router_llm_enabled=True,
        ),
    }


def _positive_row(question: str, expected_pages: list[str], expected_fragments: list[str], answer) -> dict[str, Any]:
    evidence_pages = [candidate.metadata.get("page_label", "Document") for candidate in answer.evidence]
    evidence_text = " ".join(candidate.text.lower() for candidate in answer.evidence)
    fragment_hits = sum(1 for fragment in expected_fragments if fragment.lower() in evidence_text)
    return {
        "question": question,
        "supported": bool(answer.supported),
        "citation_page_hit": int(any(page in evidence_pages for page in expected_pages)),
        "evidence_fragment_recall": fragment_hits / max(len(expected_fragments), 1),
        "route_used": answer.retrieval_notes[2] if len(answer.retrieval_notes) >= 3 else "",
        "citations": list(answer.citations),
        "model_name": answer.model_name,
        "answer_preview": answer.answer[:300],
    }


def _negative_row(question: str, answer) -> dict[str, Any]:
    return {
        "question": question,
        "abstained": int(not answer.supported),
        "supported": bool(answer.supported),
        "model_name": answer.model_name,
        "answer_preview": answer.answer[:300],
        "note": answer.note,
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
    abstentions = sum(row["abstained"] for row in rows)
    false_support = sum(1 for row in rows if row["supported"])
    return {
        "dataset_size": len(rows),
        "abstention_rate": abstentions / len(rows),
        "false_support_rate": false_support / len(rows),
    }


def _build_indexes(reference_pipeline: HelpmatePipeline) -> dict[str, dict[str, str]]:
    documents: dict[str, dict[str, str]] = {}
    for dataset_name, document_path in POSITIVE_DATASETS.items():
        document = reference_pipeline.ingest_document(document_path)
        reference_pipeline.build_or_load_index(document)
        documents[dataset_name] = {
            "document_id": document.document_id,
            "fingerprint": document.fingerprint,
            "document_path": str(document_path),
        }
    return documents


def run_ablation() -> dict[str, Any]:
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

            dataset_positive_rows: list[dict[str, Any]] = []
            dataset_negative_rows: list[dict[str, Any]] = []

            for item in positive_items:
                retrieval = pipeline.retrieve_evidence(document.document_id, document.fingerprint, item["question"])
                if pipeline.settings.evidence_selector_enabled:
                    retrieval = pipeline.evidence_selector.select(item["question"], retrieval)
                answer = pipeline.generate_answer(document.document_id, item["question"], retrieval)
                row = _positive_row(item["question"], item.get("expected_pages", []), item.get("expected_fragments", []), answer)
                dataset_positive_rows.append(row)
                positive_rows.append(row)

            for item in negative_items:
                retrieval = pipeline.retrieve_evidence(document.document_id, document.fingerprint, item["question"])
                if pipeline.settings.evidence_selector_enabled:
                    retrieval = pipeline.evidence_selector.select(item["question"], retrieval)
                answer = pipeline.generate_answer(document.document_id, item["question"], retrieval)
                row = _negative_row(item["question"], answer)
                dataset_negative_rows.append(row)
                negative_rows.append(row)

            per_dataset[dataset_name] = {
                "positive": _summarize_positive(dataset_positive_rows),
                "negative": _summarize_negative(dataset_negative_rows),
            }

        variant_payload[variant_name] = {
            "settings": {
                "reranker_enabled": pipeline.settings.reranker_enabled,
                "evidence_selector_enabled": pipeline.settings.evidence_selector_enabled,
                "planner_confidence_threshold": pipeline.settings.planner_confidence_threshold,
                "router_confidence_threshold": pipeline.settings.router_confidence_threshold,
                "router_llm_enabled": pipeline.settings.router_llm_enabled,
                "answer_model": pipeline.settings.answer_model,
            },
            "positive_overall": _summarize_positive(positive_rows),
            "negative_overall": _summarize_negative(negative_rows),
            "per_dataset": per_dataset,
        }

    return {
        "variant_order": ["baseline", "reranker_only", "planner_reranker", "full_stack"],
        "variants": variant_payload,
    }


def _save_report(payload: dict[str, Any]) -> Path:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = REPORTS_DIR / f"answer_stack_ablation_{timestamp}.json"
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare full answer-layer behavior across stack variants.")
    parser.parse_args()

    payload = run_ablation()
    report_path = _save_report(payload)
    summary = {
        "report_path": str(report_path),
        "variants": {
            name: {
                "positive_overall": payload["variants"][name]["positive_overall"],
                "negative_overall": payload["variants"][name]["negative_overall"],
            }
            for name in payload["variant_order"]
        },
    }
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
