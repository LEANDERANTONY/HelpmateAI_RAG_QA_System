from __future__ import annotations

import argparse
import json
import os
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any

from src.config import Settings, get_settings
from src.evals.answer_stack_ablation import NEGATIVE_DATASETS, POSITIVE_DATASETS, _load_dataset
from src.pipeline import HelpmatePipeline


ROOT = Path(__file__).resolve().parents[2]
REPORTS_DIR = ROOT / "docs" / "evals" / "reports"
LOCAL_STORE_DIR = ROOT / "tmp" / "chunking_answer_ablation"


def _build_settings(
    base: Settings,
    *,
    variant_id: str,
    chunk_size: int,
    chunk_overlap: int,
) -> Settings:
    settings = replace(
        base,
        data_dir=LOCAL_STORE_DIR / "data" / variant_id,
        cache_dir=LOCAL_STORE_DIR / "data" / variant_id / "cache",
        state_store_backend="local",
        vector_store_backend="local",
        reranker_enabled=True,
        evidence_selector_enabled=False,
        router_llm_enabled=True,
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        retrieval_version=f"{base.retrieval_version}-{variant_id}",
        generation_version=f"{base.generation_version}-{variant_id}",
    )
    settings.ensure_dirs()
    return settings


def _variant_settings(base: Settings) -> dict[str, Settings]:
    return {
        "chunk_1200_180": _build_settings(
            base,
            variant_id="chunk_1200_180",
            chunk_size=1200,
            chunk_overlap=180,
        ),
        "chunk_1200_240": _build_settings(
            base,
            variant_id="chunk_1200_240",
            chunk_size=1200,
            chunk_overlap=240,
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


def _with_local_env(settings: Settings):
    previous_env = {
        "HELPMATE_DATA_DIR": os.getenv("HELPMATE_DATA_DIR"),
        "HELPMATE_STATE_STORE_BACKEND": os.getenv("HELPMATE_STATE_STORE_BACKEND"),
        "HELPMATE_VECTOR_STORE_BACKEND": os.getenv("HELPMATE_VECTOR_STORE_BACKEND"),
    }
    os.environ["HELPMATE_DATA_DIR"] = str(settings.data_dir)
    os.environ["HELPMATE_STATE_STORE_BACKEND"] = "local"
    os.environ["HELPMATE_VECTOR_STORE_BACKEND"] = "local"
    return previous_env


def _restore_env(previous_env: dict[str, str | None]) -> None:
    for key, value in previous_env.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


def run_ablation() -> dict[str, Any]:
    base_settings = get_settings()
    variants = _variant_settings(base_settings)
    variant_payload: dict[str, Any] = {}

    for variant_name, settings in variants.items():
        previous_env = _with_local_env(settings)
        try:
            pipeline = HelpmatePipeline(settings)
            positive_rows: list[dict[str, Any]] = []
            negative_rows: list[dict[str, Any]] = []
            per_dataset: dict[str, Any] = {}

            for dataset_name, document_path in POSITIVE_DATASETS.items():
                positive_items = _load_dataset(ROOT / "docs" / "evals" / dataset_name)
                negative_dataset_name = dataset_name.replace("_retrieval_eval_dataset.json", "_negative_eval_dataset.json")
                if dataset_name == "retrieval_eval_dataset.json":
                    negative_dataset_name = "negative_eval_dataset.json"
                negative_items = _load_dataset(ROOT / "docs" / "evals" / negative_dataset_name)

                document = pipeline.ingest_document(document_path)
                index_record = pipeline.build_or_load_index(document)

                dataset_positive_rows: list[dict[str, Any]] = []
                dataset_negative_rows: list[dict[str, Any]] = []

                for item in positive_items:
                    answer = pipeline.answer_question(document, index_record, item["question"])
                    row = _positive_row(
                        item["question"],
                        item.get("expected_pages", []),
                        item.get("expected_fragments", []),
                        answer,
                    )
                    dataset_positive_rows.append(row)
                    positive_rows.append(row)

                for item in negative_items:
                    answer = pipeline.answer_question(document, index_record, item["question"])
                    row = _negative_row(item["question"], answer)
                    dataset_negative_rows.append(row)
                    negative_rows.append(row)

                per_dataset[dataset_name] = {
                    "positive": _summarize_positive(dataset_positive_rows),
                    "negative": _summarize_negative(dataset_negative_rows),
                }

            variant_payload[variant_name] = {
                "settings": {
                    "chunk_size": settings.chunk_size,
                    "chunk_overlap": settings.chunk_overlap,
                    "reranker_enabled": settings.reranker_enabled,
                    "router_llm_enabled": settings.router_llm_enabled,
                    "planner_confidence_threshold": settings.planner_confidence_threshold,
                    "router_confidence_threshold": settings.router_confidence_threshold,
                },
                "positive_overall": _summarize_positive(positive_rows),
                "negative_overall": _summarize_negative(negative_rows),
                "per_dataset": per_dataset,
            }
        finally:
            _restore_env(previous_env)

    return {
        "variant_order": ["chunk_1200_180", "chunk_1200_240"],
        "variants": variant_payload,
    }


def _save_report(payload: dict[str, Any]) -> Path:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = REPORTS_DIR / f"chunking_answer_ablation_{timestamp}.json"
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare answer-layer behavior across chunking configurations.")
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
