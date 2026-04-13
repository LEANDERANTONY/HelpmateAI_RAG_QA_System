from __future__ import annotations

import argparse
import json
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any

from src.config import get_settings
from src.evals.evidence_selector_weight_sweep import DATASET_TO_DOCUMENT, ROOT
from src.evals.reranker_ablation import _evaluate_retrieval, _summarize
from src.pipeline import HelpmatePipeline
from src.query_analysis import QueryAnalyzer
from src.query_router import QueryRouter
from src.retrieval.planner import RetrievalPlanner
from src.schemas import RetrievalPlan, RetrievalResult


REPORTS_DIR = ROOT / "docs" / "evals" / "reports"
DEFAULT_CACHE_PATH = REPORTS_DIR / "planner_threshold_cases.json"
LOCAL_STORE_DIR = ROOT / "tmp" / "planner_threshold_sweep"


def _dataset_paths(names: list[str] | None) -> list[Path]:
    if names:
        return [ROOT / "docs" / "evals" / name for name in names]
    return [ROOT / "docs" / "evals" / name for name in DATASET_TO_DOCUMENT]


def _dataset_items(dataset_path: Path) -> list[dict[str, Any]]:
    return json.loads(dataset_path.read_text(encoding="utf-8"))


def _routing_to_dict(routing) -> dict[str, Any]:
    return {
        "route": routing.route,
        "confidence": routing.confidence,
        "reasons": list(routing.reasons),
        "source": routing.source,
    }


def _build_or_load_indexed_pipeline() -> HelpmatePipeline:
    settings = get_settings()
    local_settings = replace(
        settings,
        data_dir=LOCAL_STORE_DIR / "data",
        state_store_backend="local",
        vector_store_backend="local",
        reranker_enabled=True,
        evidence_selector_enabled=False,
    )
    local_settings.ensure_dirs()
    return HelpmatePipeline(local_settings)


def build_or_load_cases(
    dataset_paths: list[Path],
    cache_path: Path,
    *,
    refresh: bool = False,
) -> list[dict[str, Any]]:
    if cache_path.exists() and not refresh:
        return json.loads(cache_path.read_text(encoding="utf-8"))

    pipeline = _build_or_load_indexed_pipeline()
    settings = pipeline.settings

    deterministic_planner = RetrievalPlanner(
        replace(settings, planner_confidence_threshold=2.0, router_llm_enabled=False)
    )
    heuristic_router = QueryRouter(replace(settings, router_llm_enabled=False))
    llm_router = QueryRouter(replace(settings, router_confidence_threshold=1.0, router_llm_enabled=True))

    cases: list[dict[str, Any]] = []
    for dataset_path in dataset_paths:
        dataset_name = dataset_path.name
        document_path = DATASET_TO_DOCUMENT[dataset_name]
        document = pipeline.ingest_document(document_path)
        pipeline.build_or_load_index(document)
        synopses = pipeline.store.load_synopses(document.fingerprint)

        for item in _dataset_items(dataset_path):
            question = item["question"]
            query_profile = QueryAnalyzer.analyze(question)
            metadata_filters = pipeline.retriever._extract_metadata_filters(question)
            deterministic = deterministic_planner.plan(
                question=question,
                query_profile=query_profile,
                metadata_filters=metadata_filters,
                synopses=synopses,
            )
            heuristic = heuristic_router.route(question, query_profile)
            llm_routing = llm_router.route(question, query_profile)
            cases.append(
                {
                    "dataset_name": dataset_name,
                    "document_path": str(document_path),
                    "document_id": document.document_id,
                    "fingerprint": document.fingerprint,
                    "question": question,
                    "expected_pages": item.get("expected_pages", []),
                    "expected_fragments": item.get("expected_fragments", []),
                    "deterministic_plan": deterministic.to_dict(),
                    "heuristic_router": _routing_to_dict(heuristic),
                    "llm_router": _routing_to_dict(llm_routing),
                }
            )

    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(cases, indent=2), encoding="utf-8")
    return cases


def _simulate_plan(case: dict[str, Any], planner_threshold: float, router_threshold: float) -> dict[str, Any]:
    plan = dict(case["deterministic_plan"])
    planner_confidence = float(plan.get("planner_confidence", 0.0))
    if planner_confidence >= planner_threshold:
        return plan

    heuristic = case["heuristic_router"]
    if float(heuristic.get("confidence", 0.0)) > router_threshold:
        return plan

    llm_router = case["llm_router"]
    if llm_router.get("source") != "llm_fallback":
        return plan

    plan["preferred_route"] = llm_router["route"]
    plan["planner_confidence"] = max(planner_confidence, float(llm_router.get("confidence", 0.0)))
    plan["planner_source"] = llm_router["source"]
    return plan


def _execute_retrieval_with_plan(
    pipeline: HelpmatePipeline,
    *,
    fingerprint: str,
    question: str,
    plan_dict: dict[str, Any],
) -> RetrievalResult:
    retriever = pipeline.retriever
    metadata_filters = retriever._extract_metadata_filters(question)
    query_profile = retriever.query_analyzer.analyze(question)
    synopses = retriever.store.load_synopses(fingerprint)
    topology_edges = retriever.store.load_topology_edges(fingerprint)
    plan = RetrievalPlan(**plan_dict)

    notes = [
        f"Planner intent: {plan.intent_type}.",
        f"Planner evidence spread: {plan.evidence_spread}.",
        f"Planner selected {plan.preferred_route} with {plan.constraint_mode} constraints.",
        f"Planner source: {plan.planner_source} at confidence {plan.planner_confidence:.2f}.",
    ]
    if query_profile.preferred_content_types:
        notes.append(f"Preferred content types: {', '.join(query_profile.preferred_content_types)}.")

    if plan.preferred_route == "chunk_first":
        candidates = retriever._chunk_candidates(
            fingerprint,
            question,
            metadata_filters,
            query_profile.preferred_content_types,
            query_profile.clause_terms,
            query_type=query_profile.query_type,
            section_ids=set(plan.target_region_ids) if plan.target_region_ids else None,
            strict=plan.constraint_mode == "hard_region",
        )
        notes.append("Chunk-first retrieval path used for exact grounding.")
        return retriever._build_result(question, metadata_filters, "chunk_first", candidates, notes, plan, query_profile.query_type)

    if plan.preferred_route == "section_first":
        sections = retriever.store.load_sections(fingerprint)
        ranked_sections = retriever.section_retriever.seed_summary_sections(question, sections, top_k=max(retriever.settings.section_fused_top_k, 4))
        candidates = retriever._chunk_candidates(
            fingerprint,
            question,
            metadata_filters,
            query_profile.preferred_content_types,
            query_profile.clause_terms,
            query_type=query_profile.query_type,
            section_ids={candidate.metadata.get("section_id") for candidate in ranked_sections if candidate.metadata.get("section_id")},
        )
        notes.append("Legacy section-first path used as a bounded fallback.")
        return retriever._build_result(question, metadata_filters, "section_first", candidates, notes, plan, query_profile.query_type)

    if query_profile.query_type == "summary_lookup" and plan.evidence_spread == "global":
        synopsis_candidates, synopsis_notes, global_fallback_used = retriever._global_summary_candidates(
            fingerprint,
            question,
            metadata_filters,
            query_profile.preferred_content_types,
            query_profile.clause_terms,
            plan,
            synopses,
        )
    else:
        synopsis_candidates, synopsis_notes, global_fallback_used = retriever._synopsis_first(
            fingerprint,
            question,
            metadata_filters,
            query_profile.preferred_content_types,
            query_profile.clause_terms,
            query_profile.query_type,
            plan,
            synopses,
            topology_edges,
        )

    if plan.preferred_route == "synopsis_first":
        route_name = "global_summary_first" if query_profile.query_type == "summary_lookup" and plan.evidence_spread == "global" else "synopsis_first"
        return retriever._build_result(
            question,
            metadata_filters,
            route_name,
            synopsis_candidates,
            notes + synopsis_notes,
            plan,
            query_profile.query_type,
            global_fallback_used=global_fallback_used,
        )

    chunk_candidates = retriever._chunk_candidates(
        fingerprint,
        question,
        metadata_filters,
        query_profile.preferred_content_types,
        query_profile.clause_terms,
        query_type=query_profile.query_type,
    )
    notes.extend(synopsis_notes)
    notes.append("Hybrid retrieval merged direct chunk evidence with topology-guided synopsis retrieval.")
    return retriever._build_result(
        question,
        metadata_filters,
        "hybrid_both",
        chunk_candidates + synopsis_candidates,
        notes,
        plan,
        query_profile.query_type,
        global_fallback_used=global_fallback_used,
    )


def _planner_threshold_values() -> list[float]:
    return [round(value, 2) for value in (0.55, 0.6, 0.66, 0.7, 0.74, 0.78, 0.82, 0.86, 0.9)]


def _router_threshold_values() -> list[float]:
    return [round(value, 2) for value in (0.5, 0.55, 0.6, 0.62, 0.7, 0.78, 0.84, 0.9)]


def sweep_thresholds(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    pipeline = _build_or_load_indexed_pipeline()
    results: list[dict[str, Any]] = []
    retrieval_cache: dict[str, dict[str, Any]] = {}

    for planner_threshold in _planner_threshold_values():
        for router_threshold in _router_threshold_values():
            rows: list[dict[str, Any]] = []
            llm_count = 0
            route_counts: dict[str, int] = {}
            dataset_rows: dict[str, list[dict[str, Any]]] = {}
            for case in cases:
                plan_dict = _simulate_plan(case, planner_threshold, router_threshold)
                cache_key = json.dumps(
                    {
                        "fingerprint": case["fingerprint"],
                        "question": case["question"],
                        "plan": plan_dict,
                    },
                    sort_keys=True,
                )
                row = retrieval_cache.get(cache_key)
                if row is None:
                    retrieval = _execute_retrieval_with_plan(
                        pipeline,
                        fingerprint=case["fingerprint"],
                        question=case["question"],
                        plan_dict=plan_dict,
                    )
                    row = _evaluate_retrieval(
                        case["question"],
                        case.get("expected_pages", []),
                        case.get("expected_fragments", []),
                        retrieval,
                    )
                    retrieval_cache[cache_key] = row
                rows.append(row)
                dataset_rows.setdefault(case["dataset_name"], []).append(row)
                if plan_dict.get("planner_source") == "llm_fallback":
                    llm_count += 1
                route = str(plan_dict.get("preferred_route", "unknown"))
                route_counts[route] = route_counts.get(route, 0) + 1

            summary = _summarize(rows)
            per_dataset = {name: _summarize(items) for name, items in sorted(dataset_rows.items())}
            results.append(
                {
                    "planner_threshold": planner_threshold,
                    "router_threshold": router_threshold,
                    "summary": summary,
                    "llm_fallback_count": llm_count,
                    "route_counts": route_counts,
                    "per_dataset": per_dataset,
                }
            )

    results.sort(
        key=lambda item: (
            item["summary"]["page_hit_rate"],
            item["summary"]["mean_fragment_recall"],
            item["summary"]["mean_reciprocal_rank"],
            item["summary"]["objective_score"],
        ),
        reverse=True,
    )
    return results


def _save_report(payload: dict[str, Any]) -> Path:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = REPORTS_DIR / f"planner_threshold_sweep_{timestamp}.json"
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description="Calibrate planner and router thresholds before planner ablation.")
    parser.add_argument("--datasets", nargs="*", help="Dataset filenames under docs/evals to include.")
    parser.add_argument("--cache-path", default=str(DEFAULT_CACHE_PATH))
    parser.add_argument("--refresh-cache", action="store_true")
    args = parser.parse_args()

    dataset_paths = _dataset_paths(args.datasets)
    cache_path = Path(args.cache_path)
    cases = build_or_load_cases(dataset_paths, cache_path, refresh=args.refresh_cache)
    sweep_results = sweep_thresholds(cases)
    settings = get_settings()
    current = next(
        (
            item
            for item in sweep_results
            if item["planner_threshold"] == round(settings.planner_confidence_threshold, 2)
            and item["router_threshold"] == round(settings.router_confidence_threshold, 2)
        ),
        None,
    )
    payload = {
        "case_count": len(cases),
        "best_by_primary_metrics": sweep_results[0] if sweep_results else None,
        "current_default": current,
        "top_results": sweep_results[:10],
        "all_results": sweep_results,
    }
    report_path = _save_report(payload)
    print(
        json.dumps(
            {
                "report_path": str(report_path),
                "case_count": len(cases),
                "best_by_primary_metrics": sweep_results[0] if sweep_results else None,
                "current_default": current,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
