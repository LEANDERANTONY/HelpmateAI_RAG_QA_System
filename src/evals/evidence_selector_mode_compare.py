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


def _evaluate_selector_off(case: dict[str, Any]) -> dict[str, Any]:
    retrieval = _rebuild_retrieval(case)
    selected_pages = [candidate.metadata.get("page_label", "Document") for candidate in retrieval.candidates]
    expected_pages = case.get("expected_pages", [])
    selected_text = " ".join(candidate.text.lower() for candidate in retrieval.candidates)
    expected_fragments = [fragment.lower() for fragment in case.get("expected_fragments", [])]
    fragment_hits = sum(1 for fragment in expected_fragments if fragment in selected_text)
    fragment_recall = fragment_hits / max(len(expected_fragments), 1)
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
        "page_hit": int(any(page in selected_pages for page in expected_pages)),
        "reciprocal_rank": reciprocal_rank,
        "fragment_recall": fragment_recall,
        "selected_chunk_ids": [candidate.chunk_id for candidate in retrieval.candidates],
    }


def _selector_rows(cases: list[dict[str, Any]], *, prune: bool) -> list[dict[str, Any]]:
    settings = replace(
        get_settings(),
        evidence_selector_enabled=True,
        evidence_selector_prune=prune,
    )
    selector = EvidenceSelector(settings)
    return [_evaluate_case(selector, case) for case in cases]


def _per_dataset_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(row["dataset_name"], []).append(row)
    return {name: _summarize_cases(dataset_rows) for name, dataset_rows in sorted(grouped.items())}


def run_compare(cases: list[dict[str, Any]]) -> dict[str, Any]:
    selector_off_rows = [_evaluate_selector_off(case) for case in cases]
    selector_prune_rows = _selector_rows(cases, prune=True)
    selector_reorder_rows = _selector_rows(cases, prune=False)

    return {
        "case_count": len(cases),
        "settings": {
            "top_k": get_settings().evidence_selector_top_k,
            "max_evidence": get_settings().evidence_selector_max_evidence,
            "rank_weight": get_settings().evidence_selector_rank_weight,
            "llm_weight": get_settings().evidence_selector_llm_weight,
            "model": get_settings().evidence_selector_model,
        },
        "selector_off": _summarize_cases(selector_off_rows),
        "selector_prune": _summarize_cases(selector_prune_rows),
        "selector_reorder_only": _summarize_cases(selector_reorder_rows),
        "per_dataset": {
            "selector_off": _per_dataset_summary(selector_off_rows),
            "selector_prune": _per_dataset_summary(selector_prune_rows),
            "selector_reorder_only": _per_dataset_summary(selector_reorder_rows),
        },
    }


def _save_report(payload: dict[str, Any]) -> Path:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = REPORTS_DIR / f"evidence_selector_mode_compare_{timestamp}.json"
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare selector-off vs prune vs reorder-only retrieval behavior.")
    parser.add_argument("--datasets", nargs="*", help="Dataset filenames under docs/evals to include.")
    parser.add_argument("--cache-path", default=str(DEFAULT_CACHE_PATH))
    parser.add_argument("--refresh-cache", action="store_true")
    args = parser.parse_args()

    dataset_paths = _dataset_paths(args.datasets)
    cases = build_or_load_cases(dataset_paths, Path(args.cache_path), refresh=args.refresh_cache)
    payload = run_compare(cases)
    report_path = _save_report(payload)
    print(
        json.dumps(
            {
                "report_path": str(report_path),
                "case_count": payload["case_count"],
                "selector_off": payload["selector_off"],
                "selector_prune": payload["selector_prune"],
                "selector_reorder_only": payload["selector_reorder_only"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
