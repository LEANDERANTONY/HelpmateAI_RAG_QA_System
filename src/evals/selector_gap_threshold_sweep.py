from __future__ import annotations

import argparse
import json
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any

from src.config import get_settings
from src.evals.evidence_selector_weight_sweep import (
    DEFAULT_CACHE_PATH,
    ROOT,
    _evaluate_case,
    _rebuild_retrieval,
    _summarize_cases,
    build_or_load_cases,
)
from src.generation.evidence_selector import EvidenceSelector


REPORTS_DIR = ROOT / "docs" / "evals" / "reports"
DEFAULT_THRESHOLDS = [0.04, 0.06, 0.08, 0.10, 0.15, 0.20, 1.0]


def _dataset_paths(names: list[str] | None) -> list[Path]:
    if names:
        return [ROOT / "docs" / "evals" / name for name in names]
    return sorted((ROOT / "docs" / "evals").glob("*retrieval_eval_dataset.json"))


def _threshold_label(value: float) -> str:
    return "always_on" if value >= 1.0 else f"{value:.2f}"


def _trigger_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(rows)
    if not total:
        return {
            "triggered": 0,
            "trigger_rate": 0.0,
            "weak_evidence_triggered": 0,
            "spread_triggered": 0,
            "ambiguity_triggered": 0,
        }
    triggered = sum(1 for row in rows if row["should_select"])
    weak = sum(1 for row in rows if row["weak_evidence_trigger"])
    spread = sum(1 for row in rows if row["spread_trigger"])
    ambiguity = sum(1 for row in rows if row["ambiguity_trigger"])
    return {
        "triggered": triggered,
        "trigger_rate": triggered / total,
        "weak_evidence_triggered": weak,
        "spread_triggered": spread,
        "ambiguity_triggered": ambiguity,
    }


def sweep_thresholds(cases: list[dict[str, Any]], thresholds: list[float]) -> list[dict[str, Any]]:
    settings = get_settings()
    results: list[dict[str, Any]] = []

    for threshold in thresholds:
        selector_settings = replace(
            settings,
            evidence_selector_enabled=True,
            evidence_selector_prune=False,
            evidence_selector_gap_threshold=threshold,
        )
        selector = EvidenceSelector(selector_settings)

        per_case: list[dict[str, Any]] = []
        trigger_rows: list[dict[str, Any]] = []
        per_dataset_rows: dict[str, list[dict[str, Any]]] = {}
        per_dataset_triggers: dict[str, list[dict[str, Any]]] = {}

        for case in cases:
            retrieval = _rebuild_retrieval(case)
            decision = selector._selection_decision(retrieval)
            trigger_row = {
                "dataset_name": case["dataset_name"],
                "question": case["question"],
                **decision,
            }
            trigger_rows.append(trigger_row)
            per_dataset_triggers.setdefault(case["dataset_name"], []).append(trigger_row)

            row = _evaluate_case(selector, case)
            per_case.append(row)
            per_dataset_rows.setdefault(case["dataset_name"], []).append(row)

        results.append(
            {
                "threshold": threshold,
                "threshold_label": _threshold_label(threshold),
                "overall": _summarize_cases(per_case),
                "trigger_summary": _trigger_summary(trigger_rows),
                "per_dataset": {
                    name: {
                        "retrieval": _summarize_cases(rows),
                        "trigger_summary": _trigger_summary(per_dataset_triggers.get(name, [])),
                    }
                    for name, rows in sorted(per_dataset_rows.items())
                },
            }
        )

    results.sort(
        key=lambda item: (
            item["overall"]["objective_score"],
            item["overall"]["page_hit_rate"],
            item["overall"]["mean_fragment_recall"],
            item["overall"]["mean_reciprocal_rank"],
            -item["trigger_summary"]["trigger_rate"],
        ),
        reverse=True,
    )
    return results


def _save_report(cases: list[dict[str, Any]], thresholds: list[float], results: list[dict[str, Any]]) -> Path:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = REPORTS_DIR / f"selector_gap_threshold_sweep_{timestamp}.json"
    settings = get_settings()
    current = next(
        (item for item in results if abs(item["threshold"] - settings.evidence_selector_gap_threshold) <= 1e-9),
        None,
    )
    payload = {
        "case_count": len(cases),
        "thresholds": thresholds,
        "best": results[0] if results else None,
        "current_default": current,
        "top_results": results[:5],
        "all_results": results,
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description="Sweep evidence-selector gap thresholds in reorder-only mode.")
    parser.add_argument("--datasets", nargs="*", help="Dataset filenames under docs/evals to include.")
    parser.add_argument("--cache-path", default=str(DEFAULT_CACHE_PATH))
    parser.add_argument("--thresholds", nargs="*", type=float, help="Explicit threshold values to sweep.")
    parser.add_argument("--refresh-cache", action="store_true")
    args = parser.parse_args()

    dataset_paths = _dataset_paths(args.datasets)
    cache_path = Path(args.cache_path)
    thresholds = args.thresholds or DEFAULT_THRESHOLDS
    cases = build_or_load_cases(dataset_paths, cache_path, refresh=args.refresh_cache)
    results = sweep_thresholds(cases, thresholds)
    report_path = _save_report(cases, thresholds, results)

    print(
        json.dumps(
            {
                "report_path": str(report_path),
                "case_count": len(cases),
                "best": results[0] if results else None,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
