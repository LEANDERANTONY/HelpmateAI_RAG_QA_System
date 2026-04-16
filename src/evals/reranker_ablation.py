from __future__ import annotations

import argparse
import json
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any

from src.config import get_settings
from src.evals.evidence_selector_weight_sweep import DATASET_TO_DOCUMENT, ROOT
from src.evals.evidence_selector_ablation import _question_objective
from src.pipeline import HelpmatePipeline


REPORTS_DIR = ROOT / "docs" / "evals" / "reports"
LOCAL_STORE_DIR = ROOT / "tmp" / "reranker_ablation"


def _dataset_paths(names: list[str] | None) -> list[Path]:
    if names:
        return [ROOT / "docs" / "evals" / name for name in names]
    return [ROOT / "docs" / "evals" / name for name in DATASET_TO_DOCUMENT]


def _dataset_items(dataset_path: Path) -> list[dict[str, Any]]:
    return json.loads(dataset_path.read_text(encoding="utf-8"))


def _evaluate_retrieval(question: str, expected_pages: list[str], expected_fragments: list[str], retrieval) -> dict[str, Any]:
    selected_pages = [candidate.metadata.get("page_label", "Document") for candidate in retrieval.candidates]
    selected_text = " ".join(candidate.text.lower() for candidate in retrieval.candidates)
    fragment_hits = sum(1 for fragment in expected_fragments if fragment.lower() in selected_text)
    fragment_recall = fragment_hits / max(len(expected_fragments), 1)
    page_hit = int(any(page in selected_pages for page in expected_pages))
    reciprocal_rank = 0.0
    for rank, page in enumerate(selected_pages, start=1):
        if page in expected_pages:
            reciprocal_rank = 1.0 / rank
            break
    return {
        "question": question,
        "expected_pages": expected_pages,
        "selected_pages": selected_pages,
        "page_hit": page_hit,
        "reciprocal_rank": reciprocal_rank,
        "fragment_recall": fragment_recall,
        "selected_chunk_ids": [candidate.chunk_id for candidate in retrieval.candidates],
        "route_used": retrieval.route_used,
        "retrieval_plan": retrieval.retrieval_plan,
    }


def _summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {
            "dataset_size": 0,
            "page_hit_rate": 0.0,
            "mean_reciprocal_rank": 0.0,
            "mean_fragment_recall": 0.0,
            "objective_score": 0.0,
        }
    page_hit_rate = sum(row["page_hit"] for row in rows) / len(rows)
    mean_reciprocal_rank = sum(row["reciprocal_rank"] for row in rows) / len(rows)
    mean_fragment_recall = sum(row["fragment_recall"] for row in rows) / len(rows)
    objective_score = 0.45 * page_hit_rate + 0.35 * mean_fragment_recall + 0.20 * mean_reciprocal_rank
    return {
        "dataset_size": len(rows),
        "page_hit_rate": page_hit_rate,
        "mean_reciprocal_rank": mean_reciprocal_rank,
        "mean_fragment_recall": mean_fragment_recall,
        "objective_score": objective_score,
    }


def run_ablation(dataset_paths: list[Path]) -> dict[str, Any]:
    settings = get_settings()
    base_settings = replace(
        settings,
        data_dir=LOCAL_STORE_DIR / "data",
        state_store_backend="local",
        vector_store_backend="local",
        evidence_selector_enabled=False,
    )
    base_settings.ensure_dirs()

    pipelines = {
        "reranker_off": HelpmatePipeline(replace(base_settings, reranker_enabled=False)),
        "reranker_on": HelpmatePipeline(replace(base_settings, reranker_enabled=True)),
    }

    per_dataset: dict[str, dict[str, list[dict[str, Any]]]] = {}
    changed_questions: list[dict[str, Any]] = []
    improved = 0
    worsened = 0
    unchanged = 0

    for dataset_path in dataset_paths:
        dataset_name = dataset_path.name
        document_path = DATASET_TO_DOCUMENT[dataset_name]

        document = pipelines["reranker_off"].ingest_document(document_path)
        pipelines["reranker_off"].build_or_load_index(document)
        pipelines["reranker_on"].build_or_load_index(document)

        per_dataset.setdefault(dataset_name, {"reranker_off": [], "reranker_on": []})
        for item in _dataset_items(dataset_path):
            off_retrieval = pipelines["reranker_off"].retrieve_evidence(document.document_id, document.fingerprint, item["question"])
            on_retrieval = pipelines["reranker_on"].retrieve_evidence(document.document_id, document.fingerprint, item["question"])
            off_row = _evaluate_retrieval(item["question"], item.get("expected_pages", []), item.get("expected_fragments", []), off_retrieval)
            on_row = _evaluate_retrieval(item["question"], item.get("expected_pages", []), item.get("expected_fragments", []), on_retrieval)
            per_dataset[dataset_name]["reranker_off"].append(off_row)
            per_dataset[dataset_name]["reranker_on"].append(on_row)

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
                        "question": item["question"],
                        "delta_objective": delta,
                        "reranker_off": off_row,
                        "reranker_on": on_row,
                    }
                )

    overall_off_rows = [row for group in per_dataset.values() for row in group["reranker_off"]]
    overall_on_rows = [row for group in per_dataset.values() for row in group["reranker_on"]]
    dataset_summaries: dict[str, Any] = {}
    for dataset_name, rows in sorted(per_dataset.items()):
        off_summary = _summarize(rows["reranker_off"])
        on_summary = _summarize(rows["reranker_on"])
        dataset_summaries[dataset_name] = {
            "reranker_off": off_summary,
            "reranker_on": on_summary,
            "delta": {
                "page_hit_rate": on_summary["page_hit_rate"] - off_summary["page_hit_rate"],
                "mean_reciprocal_rank": on_summary["mean_reciprocal_rank"] - off_summary["mean_reciprocal_rank"],
                "mean_fragment_recall": on_summary["mean_fragment_recall"] - off_summary["mean_fragment_recall"],
                "objective_score": on_summary["objective_score"] - off_summary["objective_score"],
            },
        }

    overall_off = _summarize(overall_off_rows)
    overall_on = _summarize(overall_on_rows)
    return {
        "case_count": len(overall_off_rows),
        "settings": {
            "reranker_model": settings.reranker_model,
            "final_top_k": settings.final_top_k,
        },
        "reranker_off": overall_off,
        "reranker_on": overall_on,
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
    path = REPORTS_DIR / f"reranker_ablation_{timestamp}.json"
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare reranker off vs on using the labeled retrieval datasets.")
    parser.add_argument("--datasets", nargs="*", help="Dataset filenames under docs/evals to include.")
    args = parser.parse_args()

    payload = run_ablation(_dataset_paths(args.datasets))
    report_path = _save_report(payload)
    print(
        json.dumps(
            {
                "report_path": str(report_path),
                "case_count": payload["case_count"],
                "reranker_off": payload["reranker_off"],
                "reranker_on": payload["reranker_on"],
                "overall_delta": payload["overall_delta"],
                "question_outcomes": payload["question_outcomes"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
