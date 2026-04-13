from __future__ import annotations

import argparse
import json
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any

from src.config import get_settings
from src.generation.evidence_selector import EvidenceSelector
from src.pipeline import HelpmatePipeline
from src.schemas import RetrievalCandidate, RetrievalResult


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REPORTS_DIR = ROOT / "docs" / "evals" / "reports"
DEFAULT_CACHE_PATH = DEFAULT_REPORTS_DIR / "evidence_selector_weight_cases.json"
DEFAULT_LOCAL_STORE_DIR = ROOT / "tmp" / "evidence_selector_weight_sweep"

DATASET_TO_DOCUMENT = {
    "retrieval_eval_dataset.json": ROOT / "static" / "sample_files" / "Principal-Sample-Life-Insurance-Policy.pdf",
    "health_retrieval_eval_dataset.json": ROOT / "static" / "sample_files" / "HealthInsurance_Policy.pdf",
    "thesis_retrieval_eval_dataset.json": ROOT / "static" / "sample_files" / "Final_Thesis_Leander_Antony_A.pdf",
    "pancreas7_retrieval_eval_dataset.json": ROOT / "static" / "sample_files" / "pancreas7.pdf",
    "pancreas8_retrieval_eval_dataset.json": ROOT / "static" / "sample_files" / "pancreas8.pdf",
    "reportgeneration_retrieval_eval_dataset.json": ROOT / "static" / "sample_files" / "reportgeneration.pdf",
    "reportgeneration2_retrieval_eval_dataset.json": ROOT / "static" / "sample_files" / "reportgeneration2.pdf",
}


class _CachedResponse:
    def __init__(self, content: str):
        self.choices = [type("Choice", (), {"message": type("Message", (), {"content": content})()})()]


class _CachedCompletions:
    def __init__(self, payload: dict[str, Any]):
        self.payload = payload

    def create(self, **kwargs):
        del kwargs
        return _CachedResponse(json.dumps(self.payload))


class _CachedClient:
    def __init__(self, payload: dict[str, Any]):
        self.chat = type("Chat", (), {"completions": _CachedCompletions(payload)})()


def _dataset_items(dataset_path: Path) -> list[dict[str, Any]]:
    return json.loads(dataset_path.read_text(encoding="utf-8"))


def _default_dataset_paths() -> list[Path]:
    return [ROOT / "docs" / "evals" / name for name in DATASET_TO_DOCUMENT]


def _safe_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _case_path_key(dataset_path: Path) -> str:
    return dataset_path.name


def _capture_selector_payload(selector: EvidenceSelector, retrieval: RetrievalResult) -> dict[str, Any]:
    candidates = retrieval.candidates[: selector.settings.evidence_selector_top_k]
    prompt = selector._selection_prompt(retrieval.question, candidates)
    response = selector.client.chat.completions.create(
        model=selector.settings.evidence_selector_model,
        messages=[
            {"role": "system", "content": "You select the most direct evidence chunks for grounded document QA."},
            {"role": "user", "content": prompt},
        ],
        response_format={"type": "json_object"},
    )
    payload = json.loads(response.choices[0].message.content or "{}")
    if not isinstance(payload, dict):
        return {"candidate_scores": {}, "selected_ids": []}
    return {
        "candidate_scores": payload.get("candidate_scores", {}),
        "selected_ids": payload.get("selected_ids", []),
    }


def _candidate_snapshot(candidate: RetrievalCandidate) -> dict[str, Any]:
    return {
        "chunk_id": candidate.chunk_id,
        "text": candidate.text,
        "metadata": candidate.metadata,
        "dense_score": candidate.dense_score,
        "lexical_score": candidate.lexical_score,
        "fused_score": candidate.fused_score,
        "rerank_score": candidate.rerank_score,
        "citation_label": candidate.citation_label,
    }


def build_or_load_cases(
    dataset_paths: list[Path],
    cache_path: Path,
    *,
    refresh: bool = False,
) -> list[dict[str, Any]]:
    if cache_path.exists() and not refresh:
        return json.loads(cache_path.read_text(encoding="utf-8"))

    settings = get_settings()
    if not settings.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY is required to collect evidence selector cases.")
    temp_path = DEFAULT_LOCAL_STORE_DIR
    temp_path.mkdir(parents=True, exist_ok=True)
    selector_settings = replace(
        settings,
        data_dir=temp_path / "data",
        docs_dir=settings.docs_dir,
        state_store_backend="local",
        vector_store_backend="local",
        reranker_enabled=True,
        evidence_selector_enabled=True,
    )
    selector_settings.ensure_dirs()
    pipeline = HelpmatePipeline(selector_settings)

    all_cases: list[dict[str, Any]] = []
    for dataset_path in dataset_paths:
        document_path = DATASET_TO_DOCUMENT[dataset_path.name]
        document = pipeline.ingest_document(document_path)
        pipeline.build_or_load_index(document)
        selector = pipeline.evidence_selector
        if selector.client is None:
            raise RuntimeError("Evidence selector client is unavailable; cannot collect selector cases.")

        for item in _dataset_items(dataset_path):
            retrieval = pipeline.retrieve_evidence(document.document_id, document.fingerprint, item["question"])
            selector_payload = (
                _capture_selector_payload(selector, retrieval) if selector._should_select(retrieval) else {"candidate_scores": {}, "selected_ids": []}
            )
            all_cases.append(
                {
                    "dataset_name": dataset_path.name,
                    "document_path": str(document_path),
                    "question": item["question"],
                    "expected_pages": item.get("expected_pages", []),
                    "expected_fragments": item.get("expected_fragments", []),
                    "retrieval": retrieval.to_dict(),
                    "selector_payload": selector_payload,
                }
            )

    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(all_cases, indent=2), encoding="utf-8")
    return all_cases


def _rebuild_retrieval(case: dict[str, Any]) -> RetrievalResult:
    retrieval = case["retrieval"]
    candidates = [
        RetrievalCandidate(
            chunk_id=item["chunk_id"],
            text=item["text"],
            metadata=dict(item["metadata"]),
            dense_score=_safe_float(item.get("dense_score")),
            lexical_score=_safe_float(item.get("lexical_score")),
            fused_score=_safe_float(item.get("fused_score")),
            rerank_score=_safe_float(item.get("rerank_score")) if item.get("rerank_score") is not None else None,
            citation_label=item.get("citation_label", ""),
        )
        for item in retrieval["candidates"]
    ]
    return RetrievalResult(
        question=retrieval["question"],
        candidates=candidates,
        cache_hit=bool(retrieval.get("cache_hit", False)),
        retrieval_version=str(retrieval.get("retrieval_version", "v1")),
        route_used=str(retrieval.get("route_used", "chunk_first")),
        query_used=str(retrieval.get("query_used", "")),
        query_variants=list(retrieval.get("query_variants", [])),
        metadata_filters=dict(retrieval.get("metadata_filters", {})),
        strategy_notes=list(retrieval.get("strategy_notes", [])),
        weak_evidence=bool(retrieval.get("weak_evidence", False)),
        evidence_status=str(retrieval.get("evidence_status", "strong")),
        best_score=_safe_float(retrieval.get("best_score")),
        max_lexical_score=_safe_float(retrieval.get("max_lexical_score")),
        content_overlap_score=_safe_float(retrieval.get("content_overlap_score")),
        retrieval_plan=dict(retrieval.get("retrieval_plan", {})),
    )


def _evaluate_case(
    selector: EvidenceSelector,
    case: dict[str, Any],
) -> dict[str, Any]:
    retrieval = _rebuild_retrieval(case)
    cached_client = _CachedClient(case.get("selector_payload", {"candidate_scores": {}, "selected_ids": []}))
    selector.client = cached_client
    selected = selector.select(case["question"], retrieval)
    selected_pages = [candidate.metadata.get("page_label", "Document") for candidate in selected.candidates]
    expected_pages = case.get("expected_pages", [])
    selected_text = " ".join(candidate.text.lower() for candidate in selected.candidates)
    expected_fragments = [fragment.lower() for fragment in case.get("expected_fragments", [])]
    fragment_hits = sum(1 for fragment in expected_fragments if fragment in selected_text)
    fragment_recall = fragment_hits / max(len(expected_fragments), 1)
    page_hit = int(any(page in selected_pages for page in expected_pages))
    first_hit_rank = 0.0
    for rank, page in enumerate(selected_pages, start=1):
        if page in expected_pages:
            first_hit_rank = 1.0 / rank
            break
    return {
        "dataset_name": case["dataset_name"],
        "question": case["question"],
        "expected_pages": expected_pages,
        "selected_pages": selected_pages,
        "page_hit": page_hit,
        "reciprocal_rank": first_hit_rank,
        "fragment_recall": fragment_recall,
        "selected_chunk_ids": [candidate.chunk_id for candidate in selected.candidates],
    }


def _summarize_cases(rows: list[dict[str, Any]]) -> dict[str, Any]:
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


def sweep_weights(
    cases: list[dict[str, Any]],
    *,
    step: float = 0.05,
) -> list[dict[str, Any]]:
    settings = get_settings()
    results: list[dict[str, Any]] = []
    weight = 0.0
    while weight <= 1.000001:
        rank_weight = round(weight, 2)
        llm_weight = round(1.0 - rank_weight, 2)
        selector_settings = replace(
            settings,
            evidence_selector_enabled=True,
            evidence_selector_rank_weight=rank_weight,
            evidence_selector_llm_weight=llm_weight,
        )
        selector = EvidenceSelector(selector_settings)
        per_case = [_evaluate_case(selector, case) for case in cases]

        per_dataset: dict[str, list[dict[str, Any]]] = {}
        for row in per_case:
            per_dataset.setdefault(row["dataset_name"], []).append(row)

        dataset_summary = {
            name: _summarize_cases(rows)
            for name, rows in sorted(per_dataset.items())
        }
        overall = _summarize_cases(per_case)
        results.append(
            {
                "rank_weight": rank_weight,
                "llm_weight": llm_weight,
                "overall": overall,
                "per_dataset": dataset_summary,
            }
        )
        weight += step

    results.sort(
        key=lambda item: (
            item["overall"]["objective_score"],
            item["overall"]["page_hit_rate"],
            item["overall"]["mean_fragment_recall"],
            item["overall"]["mean_reciprocal_rank"],
        ),
        reverse=True,
    )
    return results


def _save_report(cases: list[dict[str, Any]], sweep_results: list[dict[str, Any]], cache_path: Path) -> Path:
    DEFAULT_REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = DEFAULT_REPORTS_DIR / f"evidence_selector_weight_sweep_{timestamp}.json"
    settings = get_settings()
    current = next(
        (
            item
            for item in sweep_results
            if item["rank_weight"] == round(settings.evidence_selector_rank_weight, 2)
            and item["llm_weight"] == round(settings.evidence_selector_llm_weight, 2)
        ),
        None,
    )
    payload = {
        "cache_path": str(cache_path),
        "case_count": len(cases),
        "best": sweep_results[0] if sweep_results else None,
        "current_default": current,
        "top_results": sweep_results[:5],
        "all_results": sweep_results,
    }
    report_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return report_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Sweep evidence selector weights against labeled retrieval datasets.")
    parser.add_argument("--datasets", nargs="*", help="Dataset filenames under docs/evals to include.")
    parser.add_argument("--cache-path", default=str(DEFAULT_CACHE_PATH))
    parser.add_argument("--step", type=float, default=0.05)
    parser.add_argument("--refresh-cache", action="store_true")
    args = parser.parse_args()

    dataset_paths = (
        [ROOT / "docs" / "evals" / name for name in args.datasets]
        if args.datasets
        else _default_dataset_paths()
    )
    cache_path = Path(args.cache_path)

    cases = build_or_load_cases(dataset_paths, cache_path, refresh=args.refresh_cache)
    sweep_results = sweep_weights(cases, step=args.step)
    report_path = _save_report(cases, sweep_results, cache_path)
    settings = get_settings()

    print(json.dumps({
        "report_path": str(report_path),
        "best": sweep_results[0] if sweep_results else None,
        "current_default": next(
            (
                item
                for item in sweep_results
                if item["rank_weight"] == round(settings.evidence_selector_rank_weight, 2)
                and item["llm_weight"] == round(settings.evidence_selector_llm_weight, 2)
            ),
            None,
        ),
        "case_count": len(cases),
    }, indent=2))


if __name__ == "__main__":
    main()
