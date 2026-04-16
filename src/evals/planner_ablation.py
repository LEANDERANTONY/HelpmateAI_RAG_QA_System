from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from src.config import get_settings
from src.evals.planner_threshold_sweep import (
    DEFAULT_CACHE_PATH,
    ROOT,
    _dataset_paths,
    _evaluate_retrieval,
    _execute_retrieval_with_plan,
    _simulate_plan,
    _summarize,
    build_or_load_cases,
    _build_or_load_indexed_pipeline,
)


REPORTS_DIR = ROOT / "docs" / "evals" / "reports"


def run_ablation(cases: list[dict[str, Any]]) -> dict[str, Any]:
    settings = get_settings()
    pipeline = _build_or_load_indexed_pipeline()

    calibrated_rows: list[dict[str, Any]] = []
    deterministic_rows: list[dict[str, Any]] = []
    per_dataset: dict[str, dict[str, list[dict[str, Any]]]] = {}
    changed_questions: list[dict[str, Any]] = []
    improved = 0
    worsened = 0
    unchanged = 0

    for case in cases:
        deterministic_plan = dict(case["deterministic_plan"])
        calibrated_plan = _simulate_plan(
            case,
            planner_threshold=round(settings.planner_confidence_threshold, 2),
            router_threshold=round(settings.router_confidence_threshold, 2),
        )

        deterministic_retrieval = _execute_retrieval_with_plan(
            pipeline,
            fingerprint=case["fingerprint"],
            question=case["question"],
            plan_dict=deterministic_plan,
        )
        calibrated_retrieval = _execute_retrieval_with_plan(
            pipeline,
            fingerprint=case["fingerprint"],
            question=case["question"],
            plan_dict=calibrated_plan,
        )

        deterministic_row = _evaluate_retrieval(
            case["question"],
            case.get("expected_pages", []),
            case.get("expected_fragments", []),
            deterministic_retrieval,
        )
        calibrated_row = _evaluate_retrieval(
            case["question"],
            case.get("expected_pages", []),
            case.get("expected_fragments", []),
            calibrated_retrieval,
        )
        deterministic_row["planner_source"] = deterministic_plan.get("planner_source", "deterministic")
        deterministic_row["preferred_route"] = deterministic_plan.get("preferred_route", "unknown")
        calibrated_row["planner_source"] = calibrated_plan.get("planner_source", "deterministic")
        calibrated_row["preferred_route"] = calibrated_plan.get("preferred_route", "unknown")

        deterministic_rows.append(deterministic_row)
        calibrated_rows.append(calibrated_row)
        per_dataset.setdefault(case["dataset_name"], {"deterministic_only": [], "planner_calibrated": []})
        per_dataset[case["dataset_name"]]["deterministic_only"].append(deterministic_row)
        per_dataset[case["dataset_name"]]["planner_calibrated"].append(calibrated_row)

        deterministic_objective = 0.45 * deterministic_row["page_hit"] + 0.35 * deterministic_row["fragment_recall"] + 0.20 * deterministic_row["reciprocal_rank"]
        calibrated_objective = 0.45 * calibrated_row["page_hit"] + 0.35 * calibrated_row["fragment_recall"] + 0.20 * calibrated_row["reciprocal_rank"]
        delta = calibrated_objective - deterministic_objective
        if abs(delta) <= 1e-12:
            unchanged += 1
        elif delta > 0:
            improved += 1
        else:
            worsened += 1

        if deterministic_row["selected_chunk_ids"] != calibrated_row["selected_chunk_ids"] or deterministic_row["preferred_route"] != calibrated_row["preferred_route"]:
            changed_questions.append(
                {
                    "dataset_name": case["dataset_name"],
                    "question": case["question"],
                    "delta_objective": delta,
                    "deterministic_only": deterministic_row,
                    "planner_calibrated": calibrated_row,
                }
            )

    overall_deterministic = _summarize(deterministic_rows)
    overall_calibrated = _summarize(calibrated_rows)
    dataset_summaries: dict[str, Any] = {}
    for dataset_name, rows in sorted(per_dataset.items()):
        deterministic_summary = _summarize(rows["deterministic_only"])
        calibrated_summary = _summarize(rows["planner_calibrated"])
        dataset_summaries[dataset_name] = {
            "deterministic_only": deterministic_summary,
            "planner_calibrated": calibrated_summary,
            "delta": {
                "page_hit_rate": calibrated_summary["page_hit_rate"] - deterministic_summary["page_hit_rate"],
                "mean_reciprocal_rank": calibrated_summary["mean_reciprocal_rank"] - deterministic_summary["mean_reciprocal_rank"],
                "mean_fragment_recall": calibrated_summary["mean_fragment_recall"] - deterministic_summary["mean_fragment_recall"],
                "objective_score": calibrated_summary["objective_score"] - deterministic_summary["objective_score"],
            },
        }

    llm_fallback_count = sum(1 for row in calibrated_rows if row["planner_source"] == "llm_fallback")
    route_counts: dict[str, int] = {}
    for row in calibrated_rows:
        route = str(row["preferred_route"])
        route_counts[route] = route_counts.get(route, 0) + 1

    return {
        "case_count": len(cases),
        "settings": {
            "planner_confidence_threshold": settings.planner_confidence_threshold,
            "router_confidence_threshold": settings.router_confidence_threshold,
            "reranker_enabled": settings.reranker_enabled,
            "evidence_selector_enabled": False,
        },
        "deterministic_only": overall_deterministic,
        "planner_calibrated": overall_calibrated,
        "overall_delta": {
            "page_hit_rate": overall_calibrated["page_hit_rate"] - overall_deterministic["page_hit_rate"],
            "mean_reciprocal_rank": overall_calibrated["mean_reciprocal_rank"] - overall_deterministic["mean_reciprocal_rank"],
            "mean_fragment_recall": overall_calibrated["mean_fragment_recall"] - overall_deterministic["mean_fragment_recall"],
            "objective_score": overall_calibrated["objective_score"] - overall_deterministic["objective_score"],
        },
        "question_outcomes": {
            "improved": improved,
            "worsened": worsened,
            "unchanged": unchanged,
            "changed_candidate_sets_or_routes": len(changed_questions),
        },
        "llm_fallback_count": llm_fallback_count,
        "route_counts": route_counts,
        "per_dataset": dataset_summaries,
        "changed_questions": sorted(changed_questions, key=lambda item: item["delta_objective"], reverse=True),
    }


def _save_report(payload: dict[str, Any]) -> Path:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = REPORTS_DIR / f"planner_ablation_{timestamp}.json"
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare calibrated planner/router behavior against deterministic-only routing.")
    parser.add_argument("--datasets", nargs="*", help="Dataset filenames under docs/evals to include.")
    parser.add_argument("--cache-path", default=str(DEFAULT_CACHE_PATH))
    parser.add_argument("--refresh-cache", action="store_true")
    args = parser.parse_args()

    cases = build_or_load_cases(_dataset_paths(args.datasets), Path(args.cache_path), refresh=args.refresh_cache)
    payload = run_ablation(cases)
    report_path = _save_report(payload)
    print(
        json.dumps(
            {
                "report_path": str(report_path),
                "case_count": payload["case_count"],
                "deterministic_only": payload["deterministic_only"],
                "planner_calibrated": payload["planner_calibrated"],
                "overall_delta": payload["overall_delta"],
                "question_outcomes": payload["question_outcomes"],
                "llm_fallback_count": payload["llm_fallback_count"],
                "route_counts": payload["route_counts"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
