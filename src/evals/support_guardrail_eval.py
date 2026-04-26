from __future__ import annotations

import argparse
import json
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any

from src.config import Settings, get_settings
from src.evals.answer_stack_ablation import _negative_row, _positive_row, _summarize_negative, _summarize_positive
from src.evals.evidence_selector_weight_sweep import DATASET_TO_DOCUMENT, ROOT
from src.pipeline import HelpmatePipeline


REPORTS_DIR = ROOT / "docs" / "evals" / "reports"
LOCAL_STORE_DIR = ROOT / "tmp" / "support_guardrail_eval"

NEGATIVE_DATASETS = {
    "retrieval_eval_dataset.json": "negative_eval_dataset.json",
    "health_retrieval_eval_dataset.json": "health_negative_eval_dataset.json",
    "thesis_retrieval_eval_dataset.json": "thesis_negative_eval_dataset.json",
    "pancreas7_retrieval_eval_dataset.json": "pancreas7_negative_eval_dataset.json",
    "pancreas8_retrieval_eval_dataset.json": "pancreas8_negative_eval_dataset.json",
    "reportgeneration_retrieval_eval_dataset.json": "reportgeneration_negative_eval_dataset.json",
    "reportgeneration2_retrieval_eval_dataset.json": "reportgeneration2_negative_eval_dataset.json",
}

HELDOUT_DATASETS = {
    "manual_questions_genai_modular1_20260419.json": ROOT / "static" / "sample_files" / "test" / "GEnAI_modular1.pdf",
    "manual_questions_genai_modular2_20260419.json": ROOT / "static" / "sample_files" / "test" / "GENAI_modular2.pdf",
    "manual_questions_graduate_project_20260419.json": ROOT / "static" / "sample_files" / "test" / "Graduate Project.pdf",
    "manual_questions_indian_realestate_20260420.json": ROOT
    / "static"
    / "sample_files"
    / "test"
    / "AStudyoftheEmergingTrendsinIndianRealestate.pdf",
    "manual_questions_prefabricated_kbe_20260420.json": ROOT / "static" / "sample_files" / "test" / "buildings-13-02980.pdf",
    "manual_questions_principal_sample_life_insurance_policy_20260419.json": ROOT
    / "static"
    / "sample_files"
    / "test"
    / "Principal-Sample-Life-Insurance-Policy.pdf",
}


def _load_dataset(path: Path) -> list[dict[str, Any]]:
    return json.loads(path.read_text(encoding="utf-8"))


def _resolve_document(path: Path) -> Path:
    if path.exists():
        return path
    fallback = ROOT / "static" / "sample_files" / "test" / path.name
    if fallback.exists():
        return fallback
    raise FileNotFoundError(path)


def _build_settings(base: Settings) -> Settings:
    settings = replace(
        base,
        data_dir=LOCAL_STORE_DIR / "data",
        cache_dir=LOCAL_STORE_DIR / "data" / "cache",
        state_store_backend="local",
        vector_store_backend="local",
        reranker_enabled=True,
        evidence_selector_enabled=True,
        evidence_selector_prune=False,
        router_llm_enabled=True,
        retrieval_version=f"{base.retrieval_version}-support-guardrail-eval",
        generation_version=f"{base.generation_version}-support-guardrail-eval",
    )
    settings.ensure_dirs()
    return settings


def _index_document(pipeline: HelpmatePipeline, document_path: Path) -> dict[str, str]:
    document = pipeline.ingest_document(document_path)
    pipeline.build_or_load_index(document)
    return {
        "document_id": document.document_id,
        "fingerprint": document.fingerprint,
        "document_path": str(document_path),
    }


def _run_answer(pipeline: HelpmatePipeline, document_info: dict[str, str], question: str):
    document = pipeline.ingest_document(document_info["document_path"])
    retrieval = pipeline.retrieve_evidence(document_info["document_id"], document_info["fingerprint"], question)
    selector_decision = pipeline.evidence_selector._selection_decision(retrieval)
    retrieval = pipeline.evidence_selector.select(question, retrieval)
    answer = pipeline.generate_answer(document_info["document_id"], question, retrieval)
    return answer, retrieval, selector_decision


def _retrieval_status_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    total = max(len(rows), 1)
    statuses = {"strong": 0, "weak": 0, "unsupported": 0}
    for row in rows:
        statuses[row["retrieval_status"]] = statuses.get(row["retrieval_status"], 0) + 1
    return {
        "strong_rate": statuses.get("strong", 0) / total,
        "weak_rate": statuses.get("weak", 0) / total,
        "unsupported_rate": statuses.get("unsupported", 0) / total,
    }


def _summarize_heldout(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {
            "dataset_size": 0,
            "answer_supported_rate": 0.0,
            "retrieval_supported_rate": 0.0,
            "selector_trigger_rate": 0.0,
            "retrieval_status": _retrieval_status_summary(rows),
        }
    return {
        "dataset_size": len(rows),
        "answer_supported_rate": sum(row["answer_supported"] for row in rows) / len(rows),
        "retrieval_supported_rate": sum(row["retrieval_status"] != "unsupported" for row in rows) / len(rows),
        "selector_trigger_rate": sum(row["selector_triggered"] for row in rows) / len(rows),
        "retrieval_status": _retrieval_status_summary(rows),
    }


def _heldout_row(question: str, answer, retrieval, selector_decision: dict[str, Any]) -> dict[str, Any]:
    return {
        "question": question,
        "answer_supported": bool(answer.supported),
        "retrieval_status": retrieval.evidence_status,
        "selector_triggered": bool(selector_decision.get("should_select", False)),
        "route_used": retrieval.route_used,
        "best_score": retrieval.best_score,
        "max_lexical_score": retrieval.max_lexical_score,
        "content_overlap_score": retrieval.content_overlap_score,
        "citations": list(answer.citations),
        "note": answer.note,
        "answer_preview": answer.answer[:300],
    }


def run_support_guardrail_eval(*, include_calibration: bool = True, include_heldout: bool = True) -> dict[str, Any]:
    settings = _build_settings(get_settings())
    pipeline = HelpmatePipeline(settings)

    payload: dict[str, Any] = {
        "settings": {
            "retrieval_version": settings.retrieval_version,
            "generation_version": settings.generation_version,
            "answer_model": settings.answer_model,
            "weak_evidence_score_threshold": settings.weak_evidence_score_threshold,
            "unsupported_evidence_score_threshold": settings.unsupported_evidence_score_threshold,
            "lexical_hit_threshold": settings.lexical_hit_threshold,
            "unsupported_lexical_hit_threshold": settings.unsupported_lexical_hit_threshold,
            "unsupported_content_overlap_threshold": settings.unsupported_content_overlap_threshold,
            "evidence_selector_enabled": settings.evidence_selector_enabled,
            "evidence_selector_gap_threshold": settings.evidence_selector_gap_threshold,
        }
    }

    if include_calibration:
        calibration_positive_rows: list[dict[str, Any]] = []
        calibration_negative_rows: list[dict[str, Any]] = []
        calibration_per_dataset: dict[str, Any] = {}

        for dataset_name, configured_document_path in DATASET_TO_DOCUMENT.items():
            document_info = _index_document(pipeline, _resolve_document(configured_document_path))
            positive_items = _load_dataset(ROOT / "docs" / "evals" / dataset_name)
            negative_items = _load_dataset(ROOT / "docs" / "evals" / NEGATIVE_DATASETS[dataset_name])
            dataset_positive_rows: list[dict[str, Any]] = []
            dataset_negative_rows: list[dict[str, Any]] = []

            for item in positive_items:
                answer, retrieval, _selector_decision = _run_answer(pipeline, document_info, item["question"])
                row = _positive_row(item["question"], item.get("expected_pages", []), item.get("expected_fragments", []), answer)
                row["retrieval_status"] = retrieval.evidence_status
                row["route_used"] = retrieval.route_used
                dataset_positive_rows.append(row)
                calibration_positive_rows.append(row)

            for item in negative_items:
                answer, retrieval, _selector_decision = _run_answer(pipeline, document_info, item["question"])
                row = _negative_row(item["question"], answer)
                row["retrieval_status"] = retrieval.evidence_status
                row["route_used"] = retrieval.route_used
                dataset_negative_rows.append(row)
                calibration_negative_rows.append(row)

            calibration_per_dataset[dataset_name] = {
                "positive": _summarize_positive(dataset_positive_rows),
                "negative": _summarize_negative(dataset_negative_rows),
                "positive_retrieval_status": _retrieval_status_summary(dataset_positive_rows),
                "negative_retrieval_status": _retrieval_status_summary(dataset_negative_rows),
            }

        payload["calibration"] = {
            "positive_overall": _summarize_positive(calibration_positive_rows),
            "negative_overall": _summarize_negative(calibration_negative_rows),
            "positive_retrieval_status": _retrieval_status_summary(calibration_positive_rows),
            "negative_retrieval_status": _retrieval_status_summary(calibration_negative_rows),
            "per_dataset": calibration_per_dataset,
        }

    if include_heldout:
        heldout_rows: list[dict[str, Any]] = []
        heldout_per_dataset: dict[str, Any] = {}
        for dataset_name, document_path in HELDOUT_DATASETS.items():
            document_info = _index_document(pipeline, document_path)
            dataset_rows: list[dict[str, Any]] = []
            for item in _load_dataset(REPORTS_DIR / dataset_name):
                answer, retrieval, selector_decision = _run_answer(pipeline, document_info, item["question"])
                row = _heldout_row(item["question"], answer, retrieval, selector_decision)
                dataset_rows.append(row)
                heldout_rows.append({"dataset": dataset_name, **row})
            heldout_per_dataset[dataset_name] = {
                "summary": _summarize_heldout(dataset_rows),
                "results": dataset_rows,
            }

        payload["heldout"] = {
            "overall": _summarize_heldout(heldout_rows),
            "per_dataset": heldout_per_dataset,
        }

    return payload


def _save_report(payload: dict[str, Any]) -> Path:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = REPORTS_DIR / f"support_guardrail_eval_{timestamp}.json"
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate answer support and retrieval guardrails on calibration and held-out document sets.")
    parser.add_argument("--calibration-only", action="store_true", help="Run only labeled sample-file calibration datasets.")
    parser.add_argument("--heldout-only", action="store_true", help="Run only held-out manual sample-file questions.")
    args = parser.parse_args()

    payload = run_support_guardrail_eval(
        include_calibration=not args.heldout_only,
        include_heldout=not args.calibration_only,
    )
    report_path = _save_report(payload)
    summary = {
        "report_path": str(report_path),
        "settings": payload["settings"],
    }
    if "calibration" in payload:
        summary["calibration"] = {
            "positive_overall": payload["calibration"]["positive_overall"],
            "negative_overall": payload["calibration"]["negative_overall"],
            "positive_retrieval_status": payload["calibration"]["positive_retrieval_status"],
            "negative_retrieval_status": payload["calibration"]["negative_retrieval_status"],
        }
    if "heldout" in payload:
        summary["heldout"] = payload["heldout"]["overall"]
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
