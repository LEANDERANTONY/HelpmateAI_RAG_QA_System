from __future__ import annotations

import argparse
import json
import os
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any

from src.config import get_settings
from src.evals.evidence_selector_weight_sweep import DATASET_TO_DOCUMENT, ROOT
from src.evals.retrieval_eval import run_retrieval_eval


REPORTS_DIR = ROOT / "docs" / "evals" / "reports"
LOCAL_STORE_DIR = ROOT / "tmp" / "chunking_sweep"


def _dataset_paths(names: list[str] | None) -> list[Path]:
    if names:
        return [ROOT / "docs" / "evals" / name for name in names]
    return [ROOT / "docs" / "evals" / name for name in DATASET_TO_DOCUMENT]


def _candidate_configs(preset: str) -> list[dict[str, int]]:
    if preset == "fast":
        return [
            {"chunk_size": 900, "chunk_overlap": 180},
            {"chunk_size": 1200, "chunk_overlap": 180},
            {"chunk_size": 1200, "chunk_overlap": 240},
            {"chunk_size": 1500, "chunk_overlap": 180},
        ]
    return [
        {"chunk_size": 900, "chunk_overlap": 120},
        {"chunk_size": 900, "chunk_overlap": 180},
        {"chunk_size": 900, "chunk_overlap": 240},
        {"chunk_size": 1200, "chunk_overlap": 120},
        {"chunk_size": 1200, "chunk_overlap": 180},
        {"chunk_size": 1200, "chunk_overlap": 240},
        {"chunk_size": 1500, "chunk_overlap": 120},
        {"chunk_size": 1500, "chunk_overlap": 180},
        {"chunk_size": 1500, "chunk_overlap": 240},
    ]


def _summarize_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {
            "dataset_count": 0,
            "dataset_size": 0,
            "top_k_page_hit_rate": 0.0,
            "mean_reciprocal_rank": 0.0,
            "section_hit_rate": 0.0,
            "region_hit_rate": 0.0,
            "plan_accuracy": 0.0,
            "global_fallback_recovery_rate": 0.0,
            "multi_region_recall": 0.0,
        }

    total_size = sum(int(row["dataset_size"]) for row in rows)
    if total_size <= 0:
        total_size = len(rows)

    def weighted(metric: str) -> float:
        return sum(float(row[metric]) * int(row["dataset_size"]) for row in rows) / total_size

    return {
        "dataset_count": len(rows),
        "dataset_size": total_size,
        "top_k_page_hit_rate": weighted("top_k_page_hit_rate"),
        "mean_reciprocal_rank": weighted("mean_reciprocal_rank"),
        "section_hit_rate": weighted("section_hit_rate"),
        "region_hit_rate": weighted("region_hit_rate"),
        "plan_accuracy": weighted("plan_accuracy"),
        "global_fallback_recovery_rate": weighted("global_fallback_recovery_rate"),
        "multi_region_recall": weighted("multi_region_recall"),
    }


def _sort_key(item: dict[str, Any]) -> tuple[float, float, float, float, float]:
    overall = item["overall"]
    return (
        float(overall["top_k_page_hit_rate"]),
        float(overall["mean_reciprocal_rank"]),
        float(overall["section_hit_rate"]),
        float(overall["region_hit_rate"]),
        float(overall["plan_accuracy"]),
    )


def run_chunking_sweep(dataset_paths: list[Path], *, preset: str = "full") -> dict[str, Any]:
    settings = get_settings()
    current_config = {
        "chunk_size": settings.chunk_size,
        "chunk_overlap": settings.chunk_overlap,
    }
    base_settings = replace(
        settings,
        data_dir=LOCAL_STORE_DIR / "data",
        state_store_backend="local",
        vector_store_backend="local",
        evidence_selector_enabled=False,
    )
    base_settings.ensure_dirs()

    previous_env = {
        "HELPMATE_DATA_DIR": os.getenv("HELPMATE_DATA_DIR"),
        "HELPMATE_STATE_STORE_BACKEND": os.getenv("HELPMATE_STATE_STORE_BACKEND"),
        "HELPMATE_VECTOR_STORE_BACKEND": os.getenv("HELPMATE_VECTOR_STORE_BACKEND"),
    }
    os.environ["HELPMATE_DATA_DIR"] = str(base_settings.data_dir)
    os.environ["HELPMATE_STATE_STORE_BACKEND"] = "local"
    os.environ["HELPMATE_VECTOR_STORE_BACKEND"] = "local"

    try:
        results: list[dict[str, Any]] = []
        for config in _candidate_configs(preset):
            per_dataset: dict[str, Any] = {}
            dataset_rows: list[dict[str, Any]] = []
            for dataset_path in dataset_paths:
                document_path = DATASET_TO_DOCUMENT[dataset_path.name]
                summary = run_retrieval_eval(
                    dataset_path=dataset_path,
                    document_path=document_path,
                    chunk_size=config["chunk_size"],
                    chunk_overlap=config["chunk_overlap"],
                    dense_top_k=base_settings.dense_top_k,
                    lexical_top_k=base_settings.lexical_top_k,
                    fused_top_k=base_settings.fused_top_k,
                    final_top_k=base_settings.final_top_k,
                )
                per_dataset[dataset_path.name] = summary
                dataset_rows.append(summary)

            results.append(
                {
                    "config": config,
                    "overall": _summarize_rows(dataset_rows),
                    "per_dataset": per_dataset,
                }
            )
    finally:
        for key, value in previous_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    results.sort(key=_sort_key, reverse=True)
    current = next(
        (
            item
            for item in results
            if item["config"]["chunk_size"] == current_config["chunk_size"]
            and item["config"]["chunk_overlap"] == current_config["chunk_overlap"]
        ),
        None,
    )

    return {
        "settings_snapshot": {
            "dense_top_k": base_settings.dense_top_k,
            "lexical_top_k": base_settings.lexical_top_k,
            "fused_top_k": base_settings.fused_top_k,
            "final_top_k": base_settings.final_top_k,
            "preset": preset,
        },
        "current_default": current,
        "best": results[0] if results else None,
        "all_results": results,
    }


def _save_report(payload: dict[str, Any]) -> Path:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = REPORTS_DIR / f"chunking_sweep_{timestamp}.json"
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Sweep chunk size and overlap across the labeled retrieval datasets.",
    )
    parser.add_argument("--datasets", nargs="*", help="Dataset filenames under docs/evals to include.")
    parser.add_argument(
        "--preset",
        choices=("fast", "full"),
        default="fast",
        help="Use a tight comparison around the live defaults or the broader 9-config sweep.",
    )
    args = parser.parse_args()

    payload = run_chunking_sweep(_dataset_paths(args.datasets), preset=args.preset)
    report_path = _save_report(payload)
    print(
        json.dumps(
            {
                "report_path": str(report_path),
                "best": payload["best"],
                "current_default": payload["current_default"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
