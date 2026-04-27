from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any

from src.config import Settings, get_settings
from src.evals.evidence_selector_weight_sweep import ROOT
from src.pipeline import HelpmatePipeline


REPORTS_DIR = ROOT / "docs" / "evals" / "reports"
LOCAL_STORE_DIR = ROOT / "tmp" / "scoped_retrieval_eval"

SCOPED_CASES = [
    {
        "case_id": "thesis_implementation_summary",
        "document_path": ROOT / "static" / "sample_files" / "Final_Thesis_Leander_Antony_A.pdf",
        "question": "What were the summary/conclusions from the implementation chapter?",
        "expected_chapter_number": "4",
        "expected_chapter_title": "Implementation",
        "expected_pages": ["Page 52"],
    },
    {
        "case_id": "thesis_literature_review_summary",
        "document_path": ROOT / "static" / "sample_files" / "Final_Thesis_Leander_Antony_A.pdf",
        "question": "In the literature review, what was the section summary?",
        "expected_chapter_number": "2",
        "expected_chapter_title": "Literature Review",
        "expected_pages": ["Page 24"],
    },
    {
        "case_id": "thesis_methodology_summary",
        "document_path": ROOT / "static" / "sample_files" / "Final_Thesis_Leander_Antony_A.pdf",
        "question": "What was the section summary from the methodology chapter?",
        "expected_chapter_number": "3",
        "expected_chapter_title": "Methodology",
        "expected_pages": ["Page 23", "Page 39"],
    },
    {
        "case_id": "thesis_results_discussion_summary",
        "document_path": ROOT / "static" / "sample_files" / "Final_Thesis_Leander_Antony_A.pdf",
        "question": "What were the summary/conclusions from the results and discussion chapter?",
        "expected_chapter_number": "5",
        "expected_chapter_title": "Results And Discussion",
        "expected_pages": ["Page 72"],
    },
]


def _settings(base: Settings, *, variant_id: str, orchestrator_enabled: bool) -> Settings:
    settings = replace(
        base,
        data_dir=LOCAL_STORE_DIR / variant_id,
        uploads_dir=LOCAL_STORE_DIR / variant_id / "uploads",
        indexes_dir=LOCAL_STORE_DIR / variant_id / "indexes",
        cache_dir=LOCAL_STORE_DIR / variant_id / "cache",
        state_store_backend="local",
        vector_store_backend="local",
        retrieval_orchestrator_enabled=orchestrator_enabled,
        planner_llm_enabled=True,
        router_llm_enabled=False,
        reranker_enabled=False,
        evidence_selector_enabled=False,
        retrieval_version=f"{base.retrieval_version}-{variant_id}",
        generation_version=f"{base.generation_version}-{variant_id}",
    )
    settings.ensure_dirs()
    return settings


def _scope_match(candidate: Any, case: dict[str, Any]) -> bool:
    metadata = candidate.metadata
    expected_chapter_number = str(case["expected_chapter_number"])
    expected_chapter_title = str(case["expected_chapter_title"]).lower()
    chapter_number = str(metadata.get("chapter_number", ""))
    chapter_title = str(metadata.get("chapter_title", "")).lower()
    if chapter_number == expected_chapter_number:
        return True
    return expected_chapter_title and expected_chapter_title in chapter_title


def _row(case: dict[str, Any], retrieval: Any) -> dict[str, Any]:
    candidates = list(retrieval.candidates)
    selected_pages = [candidate.metadata.get("page_label", "Document") for candidate in candidates]
    scope_hits = [_scope_match(candidate, case) for candidate in candidates]
    plan = retrieval.retrieval_plan or {}
    return {
        "case_id": case["case_id"],
        "question": case["question"],
        "route_used": retrieval.route_used,
        "evidence_status": retrieval.evidence_status,
        "planner_source": str(plan.get("planner_source", "")),
        "constraint_mode": str(plan.get("constraint_mode", "")),
        "scope_strictness": str(plan.get("scope_strictness", "")),
        "target_region_ids": list(plan.get("target_region_ids", [])),
        "global_fallback_used": bool(plan.get("global_fallback_used", False)),
        "selected_pages": selected_pages,
        "page_hit": int(any(page in case["expected_pages"] for page in selected_pages)),
        "chapter_scope_hit": int(any(scope_hits)),
        "all_candidates_in_scope": int(bool(candidates) and all(scope_hits)),
        "scope_precision": sum(scope_hits) / len(scope_hits) if scope_hits else 0.0,
        "candidate_chapters": [
            {
                "page": candidate.metadata.get("page_label", "Document"),
                "chapter_number": candidate.metadata.get("chapter_number", ""),
                "chapter_title": candidate.metadata.get("chapter_title", ""),
                "section_id": candidate.metadata.get("section_id", ""),
            }
            for candidate in candidates
        ],
    }


def _summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {
            "dataset_size": 0,
            "page_hit_rate": 0.0,
            "chapter_scope_hit_rate": 0.0,
            "scope_compliance_rate": 0.0,
            "scope_precision_mean": 0.0,
            "hard_scope_rate": 0.0,
            "global_fallback_rate": 0.0,
            "planner_source_counts": {},
        }
    planner_source_counts: dict[str, int] = {}
    for row in rows:
        source = str(row["planner_source"])
        planner_source_counts[source] = planner_source_counts.get(source, 0) + 1
    return {
        "dataset_size": len(rows),
        "page_hit_rate": sum(row["page_hit"] for row in rows) / len(rows),
        "chapter_scope_hit_rate": sum(row["chapter_scope_hit"] for row in rows) / len(rows),
        "scope_compliance_rate": sum(row["all_candidates_in_scope"] for row in rows) / len(rows),
        "scope_precision_mean": sum(row["scope_precision"] for row in rows) / len(rows),
        "hard_scope_rate": sum(1 for row in rows if row["constraint_mode"] == "hard_region") / len(rows),
        "global_fallback_rate": sum(1 for row in rows if row["global_fallback_used"]) / len(rows),
        "planner_source_counts": planner_source_counts,
    }


def run_eval() -> dict[str, Any]:
    base = get_settings()
    variants = {
        "orchestrator_off": _settings(base, variant_id="orchestrator_off", orchestrator_enabled=False),
        "orchestrator_on": _settings(base, variant_id="orchestrator_on", orchestrator_enabled=True),
    }
    payload: dict[str, Any] = {
        "case_count": len(SCOPED_CASES),
        "variants": {},
    }

    for variant_name, settings in variants.items():
        pipeline = HelpmatePipeline(settings)
        documents: dict[str, Any] = {}
        rows: list[dict[str, Any]] = []
        for case in SCOPED_CASES:
            document_path = str(case["document_path"])
            if document_path not in documents:
                document = pipeline.ingest_document(case["document_path"])
                pipeline.build_or_load_index(document)
                documents[document_path] = document
            document = documents[document_path]
            retrieval = pipeline.retrieve_evidence(document.document_id, document.fingerprint, case["question"])
            rows.append(_row(case, retrieval))

        payload["variants"][variant_name] = {
            "settings": {
                "retrieval_orchestrator_enabled": settings.retrieval_orchestrator_enabled,
                "planner_llm_enabled": settings.planner_llm_enabled,
                "reranker_enabled": settings.reranker_enabled,
                "evidence_selector_enabled": settings.evidence_selector_enabled,
            },
            "summary": _summarize(rows),
            "rows": rows,
        }

    off = payload["variants"]["orchestrator_off"]["summary"]
    on = payload["variants"]["orchestrator_on"]["summary"]
    payload["delta_orchestrator_on_vs_off"] = {
        "page_hit_rate": on["page_hit_rate"] - off["page_hit_rate"],
        "chapter_scope_hit_rate": on["chapter_scope_hit_rate"] - off["chapter_scope_hit_rate"],
        "scope_compliance_rate": on["scope_compliance_rate"] - off["scope_compliance_rate"],
        "scope_precision_mean": on["scope_precision_mean"] - off["scope_precision_mean"],
        "hard_scope_rate": on["hard_scope_rate"] - off["hard_scope_rate"],
        "global_fallback_rate": on["global_fallback_rate"] - off["global_fallback_rate"],
    }
    return payload


def _save_report(payload: dict[str, Any]) -> Path:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = REPORTS_DIR / f"scoped_retrieval_eval_{timestamp}.json"
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def main() -> None:
    payload = run_eval()
    report_path = _save_report(payload)
    print(
        json.dumps(
            {
                "report_path": str(report_path),
                "orchestrator_off": payload["variants"]["orchestrator_off"]["summary"],
                "orchestrator_on": payload["variants"]["orchestrator_on"]["summary"],
                "delta_orchestrator_on_vs_off": payload["delta_orchestrator_on_vs_off"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
