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


def _dataset_paths(names: list[str] | None) -> list[Path]:
    if names:
        return [ROOT / "docs" / "evals" / name for name in names]
    return sorted((ROOT / "docs" / "evals").glob("*retrieval_eval_dataset.json"))


def _question_objective(row: dict[str, Any]) -> float:
    return 0.45 * row["page_hit"] + 0.35 * row["fragment_recall"] + 0.20 * row["reciprocal_rank"]


def _evaluate_selector_off(case: dict[str, Any]) -> dict[str, Any]:
    retrieval = _rebuild_retrieval(case)
    selected_pages = [candidate.metadata.get("page_label", "Document") for candidate in retrieval.candidates]
    expected_pages = case.get("expected_pages", [])
    selected_text = " ".join(candidate.text.lower() for candidate in retrieval.candidates)
    expected_fragments = [fragment.lower() for fragment in case.get("expected_fragments", [])]
    fragment_hits = sum(1 for fragment in expected_fragments if fragment in selected_text)
    fragment_recall = fragment_hits / max(len(expected_fragments), 1)
    page_hit = int(any(page in selected_pages for page in expected_pages))
    reciprocal_rank = 0.0
    for rank, page in enumerate(selected_pages, start=1):
        if page in expected_pages:
            reciprocal_rank = 1.0 / rank
            break
    return {
        "dataset_name": case["dataset_name"],
        "question": case["question"],
        "expected_pages": expected_pages,
        "selected_pages": selected_pages,
        "page_hit": page_hit,
        "reciprocal_rank": reciprocal_rank,
        "fragment_recall": fragment_recall,
        "selected_chunk_ids": [candidate.chunk_id for candidate in retrieval.candidates],
    }


def run_ablation(
    cases: list[dict[str, Any]],
) -> dict[str, Any]:
    settings = get_settings()
    selector = EvidenceSelector(replace(settings, evidence_selector_enabled=True))

    selector_off_rows = [_evaluate_selector_off(case) for case in cases]
    selector_on_rows = [_evaluate_case(selector, case) for case in cases]

    per_dataset: dict[str, dict[str, list[dict[str, Any]]]] = {}
    changed_questions: list[dict[str, Any]] = []
    improved = 0
    worsened = 0
    unchanged = 0

    for off_row, on_row in zip(selector_off_rows, selector_on_rows):
        dataset_name = off_row["dataset_name"]
        per_dataset.setdefault(dataset_name, {"off": [], "on": []})
        per_dataset[dataset_name]["off"].append(off_row)
        per_dataset[dataset_name]["on"].append(on_row)

        off_objective = _question_objective(off_row)
        on_objective = _question_objective(on_row)
        delta = on_objective - off_objective

        if abs(delta) <= 1e-12:
            unchanged += 1
        elif delta > 0:
            improved += 1
        else:
            worsened += 1

        if off_row["selected_chunk_ids"] != on_row["selected_chunk_ids"]:
            changed_questions.append(
                {
                    "dataset_name": dataset_name,
                    "question": off_row["question"],
                    "delta_objective": delta,
                    "off": off_row,
                    "on": on_row,
                }
            )

    dataset_summaries: dict[str, Any] = {}
    for dataset_name, rows in sorted(per_dataset.items()):
        off_summary = _summarize_cases(rows["off"])
        on_summary = _summarize_cases(rows["on"])
        dataset_summaries[dataset_name] = {
            "selector_off": off_summary,
            "selector_on": on_summary,
            "delta": {
                "page_hit_rate": on_summary["page_hit_rate"] - off_summary["page_hit_rate"],
                "mean_reciprocal_rank": on_summary["mean_reciprocal_rank"] - off_summary["mean_reciprocal_rank"],
                "mean_fragment_recall": on_summary["mean_fragment_recall"] - off_summary["mean_fragment_recall"],
                "objective_score": on_summary["objective_score"] - off_summary["objective_score"],
            },
        }

    overall_off = _summarize_cases(selector_off_rows)
    overall_on = _summarize_cases(selector_on_rows)
    return {
        "case_count": len(cases),
        "settings": {
            "rank_weight": settings.evidence_selector_rank_weight,
            "llm_weight": settings.evidence_selector_llm_weight,
            "top_k": settings.evidence_selector_top_k,
            "max_evidence": settings.evidence_selector_max_evidence,
            "model": settings.evidence_selector_model,
        },
        "selector_off": overall_off,
        "selector_on": overall_on,
        "overall_delta": {
            "page_hit_rate": overall_on["page_hit_rate"] - overall_off["page_hit_rate"],
            "mean_reciprocal_rank": overall_on["mean_reciprocal_rank"] - overall_off["mean_reciprocal_rank"],
            "mean_fragment_recall": overall_on["mean_fragment_recall"] - overall_off["mean_fragment_recall"],
            "objective_score": overall_on["objective_score"] - overall_off["objective_score"],
        },
        "question_outcomes": {
            "improved": improved,
            "worsened": worsened,
            "unchanged": unchanged,
            "changed_candidate_sets": len(changed_questions),
        },
        "per_dataset": dataset_summaries,
        "changed_questions": sorted(changed_questions, key=lambda item: item["delta_objective"], reverse=True),
    }


def _save_report(payload: dict[str, Any]) -> Path:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = REPORTS_DIR / f"evidence_selector_ablation_{timestamp}.json"
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare evidence selector off vs on using cached selector cases.")
    parser.add_argument("--datasets", nargs="*", help="Dataset filenames under docs/evals to include.")
    parser.add_argument("--cache-path", default=str(DEFAULT_CACHE_PATH))
    parser.add_argument("--refresh-cache", action="store_true")
    args = parser.parse_args()

    dataset_paths = _dataset_paths(args.datasets)
    cache_path = Path(args.cache_path)
    cases = build_or_load_cases(dataset_paths, cache_path, refresh=args.refresh_cache)
    payload = run_ablation(cases)
    report_path = _save_report(payload)

    print(
        json.dumps(
            {
                "report_path": str(report_path),
                "case_count": payload["case_count"],
                "selector_off": payload["selector_off"],
                "selector_on": payload["selector_on"],
                "overall_delta": payload["overall_delta"],
                "question_outcomes": payload["question_outcomes"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
