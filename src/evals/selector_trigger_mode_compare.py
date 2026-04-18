from __future__ import annotations

import argparse
import json
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any

from src.config import Settings, get_settings
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
VARIANT_ORDER = ["weak_only", "spread_only", "combined", "always_on"]


def _dataset_paths(names: list[str] | None) -> list[Path]:
    if names:
        return [ROOT / "docs" / "evals" / name for name in names]
    return sorted((ROOT / "docs" / "evals").glob("*retrieval_eval_dataset.json"))


def _variant_settings(base: Settings) -> dict[str, Settings]:
    return {
        "weak_only": replace(
            base,
            evidence_selector_enabled=True,
            evidence_selector_prune=False,
            evidence_selector_trigger_weak_evidence=True,
            evidence_selector_trigger_spread=False,
            evidence_selector_trigger_ambiguity=False,
        ),
        "spread_only": replace(
            base,
            evidence_selector_enabled=True,
            evidence_selector_prune=False,
            evidence_selector_trigger_weak_evidence=False,
            evidence_selector_trigger_spread=True,
            evidence_selector_trigger_ambiguity=False,
        ),
        "combined": replace(
            base,
            evidence_selector_enabled=True,
            evidence_selector_prune=False,
            evidence_selector_trigger_weak_evidence=True,
            evidence_selector_trigger_spread=True,
            evidence_selector_trigger_ambiguity=True,
        ),
        "always_on": replace(
            base,
            evidence_selector_enabled=True,
            evidence_selector_prune=False,
            evidence_selector_gap_threshold=1.0,
            evidence_selector_trigger_weak_evidence=False,
            evidence_selector_trigger_spread=False,
            evidence_selector_trigger_ambiguity=True,
        ),
    }


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


def run_compare(cases: list[dict[str, Any]]) -> dict[str, Any]:
    base_settings = get_settings()
    settings_map = _variant_settings(base_settings)
    payload: dict[str, Any] = {}

    for variant_name in VARIANT_ORDER:
        selector = EvidenceSelector(settings_map[variant_name])
        rows: list[dict[str, Any]] = []
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
            rows.append(row)
            per_dataset_rows.setdefault(case["dataset_name"], []).append(row)

        payload[variant_name] = {
            "settings": {
                "gap_threshold": selector.settings.evidence_selector_gap_threshold,
                "trigger_weak_evidence": selector.settings.evidence_selector_trigger_weak_evidence,
                "trigger_spread": selector.settings.evidence_selector_trigger_spread,
                "trigger_ambiguity": selector.settings.evidence_selector_trigger_ambiguity,
            },
            "overall": _summarize_cases(rows),
            "trigger_summary": _trigger_summary(trigger_rows),
            "per_dataset": {
                name: {
                    "retrieval": _summarize_cases(dataset_rows),
                    "trigger_summary": _trigger_summary(per_dataset_triggers.get(name, [])),
                }
                for name, dataset_rows in sorted(per_dataset_rows.items())
            },
        }

    return {
        "case_count": len(cases),
        "variant_order": VARIANT_ORDER,
        "variants": payload,
    }


def _save_report(payload: dict[str, Any]) -> Path:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = REPORTS_DIR / f"selector_trigger_mode_compare_{timestamp}.json"
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare selector trigger-source modes on cached retrieval cases.")
    parser.add_argument("--datasets", nargs="*", help="Dataset filenames under docs/evals to include.")
    parser.add_argument("--cache-path", default=str(DEFAULT_CACHE_PATH))
    parser.add_argument("--refresh-cache", action="store_true")
    args = parser.parse_args()

    dataset_paths = _dataset_paths(args.datasets)
    cache_path = Path(args.cache_path)
    cases = build_or_load_cases(dataset_paths, cache_path, refresh=args.refresh_cache)
    payload = run_compare(cases)
    report_path = _save_report(payload)
    print(json.dumps({"report_path": str(report_path), "variant_order": VARIANT_ORDER}, indent=2))


if __name__ == "__main__":
    main()
