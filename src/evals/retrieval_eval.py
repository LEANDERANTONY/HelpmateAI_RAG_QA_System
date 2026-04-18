from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from datetime import datetime

from src.config import Settings, get_settings
from src.pipeline import HelpmatePipeline


def _plan_matches_outcome(retrieval, matched: bool, found_pages: list[str]) -> bool:
    plan = retrieval.retrieval_plan or {}
    evidence_spread = str(plan.get("evidence_spread", ""))
    route_used = retrieval.route_used
    unique_pages = list(dict.fromkeys(found_pages))

    if not matched:
        return False
    if evidence_spread == "atomic":
        return route_used == "chunk_first"
    if evidence_spread == "sectional":
        return route_used in {"synopsis_first", "section_first"} and len(unique_pages) <= 3
    if evidence_spread == "distributed":
        return plan.get("constraint_mode") == "soft_multi_region" and route_used in {"synopsis_first", "hybrid_both"}
    if evidence_spread == "global":
        return route_used in {"synopsis_first", "hybrid_both"}
    return matched


def _save_report(prefix: str, payload: dict) -> Path:
    root = Path(__file__).resolve().parents[2]
    reports_dir = root / "docs" / "evals" / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = reports_dir / f"{prefix}_{timestamp}.json"
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def run_retrieval_eval(
    dataset_path: str | Path,
    document_path: str | Path,
    *,
    settings: Settings | None = None,
    chunk_size: int | None = None,
    chunk_overlap: int | None = None,
    dense_top_k: int | None = None,
    lexical_top_k: int | None = None,
    fused_top_k: int | None = None,
    final_top_k: int | None = None,
    synopsis_dense_top_k: int | None = None,
    synopsis_lexical_top_k: int | None = None,
    synopsis_fused_top_k: int | None = None,
    synopsis_section_window: int | None = None,
    planner_candidate_region_limit: int | None = None,
    global_fallback_top_k: int | None = None,
) -> dict:
    dataset = json.loads(Path(dataset_path).read_text(encoding="utf-8"))
    runtime_settings = settings or get_settings()
    runtime_settings = replace(
        runtime_settings,
        chunk_size=chunk_size or runtime_settings.chunk_size,
        chunk_overlap=chunk_overlap or runtime_settings.chunk_overlap,
        dense_top_k=dense_top_k or runtime_settings.dense_top_k,
        lexical_top_k=lexical_top_k or runtime_settings.lexical_top_k,
        fused_top_k=fused_top_k or runtime_settings.fused_top_k,
        final_top_k=final_top_k or runtime_settings.final_top_k,
        synopsis_dense_top_k=synopsis_dense_top_k or runtime_settings.synopsis_dense_top_k,
        synopsis_lexical_top_k=synopsis_lexical_top_k or runtime_settings.synopsis_lexical_top_k,
        synopsis_fused_top_k=synopsis_fused_top_k or runtime_settings.synopsis_fused_top_k,
        synopsis_section_window=synopsis_section_window or runtime_settings.synopsis_section_window,
        planner_candidate_region_limit=planner_candidate_region_limit or runtime_settings.planner_candidate_region_limit,
        global_fallback_top_k=global_fallback_top_k or runtime_settings.global_fallback_top_k,
    )
    runtime_settings.ensure_dirs()
    pipeline = HelpmatePipeline(runtime_settings)
    document = pipeline.ingest_document(document_path)
    pipeline.build_or_load_index(document)

    results: list[dict] = []
    hits = 0
    mrr_total = 0.0
    section_hits = 0
    region_hits = 0
    plan_hits = 0
    global_fallback_uses = 0
    global_fallback_hits = 0
    distributed_questions = 0
    multi_region_total = 0.0
    for item in dataset:
        retrieval = pipeline.retrieve_evidence(document.document_id, document.fingerprint, item["question"])
        found_pages = [candidate.metadata.get("page_label", "Document") for candidate in retrieval.candidates]
        expected_pages = item.get("expected_pages", [])
        matched = any(page in found_pages for page in expected_pages)
        hits += int(matched)
        reciprocal_rank = 0.0
        for index, page in enumerate(found_pages, start=1):
            if page in expected_pages:
                reciprocal_rank = 1.0 / index
                break
        mrr_total += reciprocal_rank
        section_matched = any(
            candidate.metadata.get("page_label", "Document") in expected_pages and candidate.metadata.get("section_id")
            for candidate in retrieval.candidates
        )
        target_region_kinds = set(retrieval.retrieval_plan.get("target_region_kinds", []))
        region_matched = any(
            candidate.metadata.get("page_label", "Document") in expected_pages
            and (
                (target_region_kinds and candidate.metadata.get("region_kind") in target_region_kinds)
                or (not target_region_kinds and bool(candidate.metadata.get("region_kind")))
            )
            for candidate in retrieval.candidates
        )
        section_hits += int(section_matched)
        region_hits += int(region_matched)
        plan_hits += int(_plan_matches_outcome(retrieval, matched, found_pages))
        if retrieval.retrieval_plan.get("global_fallback_used"):
            global_fallback_uses += 1
            global_fallback_hits += int(matched)
        if retrieval.retrieval_plan.get("evidence_spread") == "distributed":
            distributed_questions += 1
            overlap = len(set(found_pages) & set(expected_pages))
            multi_region_total += overlap / max(len(expected_pages), 1)
        results.append(
            {
                "question": item["question"],
                "expected_pages": expected_pages,
                "found_pages": found_pages,
                "matched": matched,
                "reciprocal_rank": reciprocal_rank,
                "query_used": retrieval.query_used,
                "strategy_notes": retrieval.strategy_notes,
                "retrieval_plan": retrieval.retrieval_plan,
            }
        )

    return {
        "dataset_size": len(dataset),
        "top_k_page_hit_rate": hits / max(len(dataset), 1),
        "mean_reciprocal_rank": mrr_total / max(len(dataset), 1),
        "section_hit_rate": section_hits / max(len(dataset), 1),
        "region_hit_rate": region_hits / max(len(dataset), 1),
        "plan_accuracy": plan_hits / max(len(dataset), 1),
        "global_fallback_recovery_rate": global_fallback_hits / max(global_fallback_uses, 1),
        "multi_region_recall": multi_region_total / max(distributed_questions, 1),
        "settings": {
            "chunk_size": runtime_settings.chunk_size,
            "chunk_overlap": runtime_settings.chunk_overlap,
            "dense_top_k": runtime_settings.dense_top_k,
            "lexical_top_k": runtime_settings.lexical_top_k,
            "fused_top_k": runtime_settings.fused_top_k,
            "final_top_k": runtime_settings.final_top_k,
            "synopsis_dense_top_k": runtime_settings.synopsis_dense_top_k,
            "synopsis_lexical_top_k": runtime_settings.synopsis_lexical_top_k,
            "synopsis_fused_top_k": runtime_settings.synopsis_fused_top_k,
            "synopsis_section_window": runtime_settings.synopsis_section_window,
            "planner_candidate_region_limit": runtime_settings.planner_candidate_region_limit,
            "global_fallback_top_k": runtime_settings.global_fallback_top_k,
        },
        "results": results,
    }


def run_negative_eval(dataset_path: str | Path, document_path: str | Path, *, settings: Settings | None = None) -> dict:
    dataset = json.loads(Path(dataset_path).read_text(encoding="utf-8"))
    runtime_settings = settings or get_settings()
    runtime_settings.ensure_dirs()
    pipeline = HelpmatePipeline(runtime_settings)
    document = pipeline.ingest_document(document_path)
    index_record = pipeline.build_or_load_index(document)

    abstentions = 0
    results: list[dict] = []
    for item in dataset:
        answer = pipeline.answer_question(document, index_record, item["question"])
        abstained = not answer.supported
        abstentions += int(abstained)
        results.append(
            {
                "question": item["question"],
                "abstained": abstained,
                "answer_preview": answer.answer[:250],
                "note": answer.note,
                "supported": answer.supported,
            }
        )

    return {
        "dataset_size": len(dataset),
        "abstention_rate": abstentions / max(len(dataset), 1),
        "results": results,
    }


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[2]
    summary = run_retrieval_eval(
        dataset_path=root / "docs" / "evals" / "retrieval_eval_dataset.json",
        document_path=root / "Principal-Sample-Life-Insurance-Policy.pdf",
    )
    report_path = _save_report("local_retrieval_eval", summary)
    summary["report_path"] = str(report_path)
    print(json.dumps(summary, indent=2))
