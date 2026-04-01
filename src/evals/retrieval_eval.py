from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from datetime import datetime

from src.config import get_settings
from src.pipeline import HelpmatePipeline


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
    chunk_size: int | None = None,
    chunk_overlap: int | None = None,
    dense_top_k: int | None = None,
    lexical_top_k: int | None = None,
    fused_top_k: int | None = None,
    final_top_k: int | None = None,
) -> dict:
    dataset = json.loads(Path(dataset_path).read_text(encoding="utf-8"))
    settings = get_settings()
    settings = replace(
        settings,
        chunk_size=chunk_size or settings.chunk_size,
        chunk_overlap=chunk_overlap or settings.chunk_overlap,
        dense_top_k=dense_top_k or settings.dense_top_k,
        lexical_top_k=lexical_top_k or settings.lexical_top_k,
        fused_top_k=fused_top_k or settings.fused_top_k,
        final_top_k=final_top_k or settings.final_top_k,
    )
    pipeline = HelpmatePipeline(settings)
    document = pipeline.ingest_document(document_path)
    pipeline.build_or_load_index(document)

    results: list[dict] = []
    hits = 0
    mrr_total = 0.0
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
        results.append(
            {
                "question": item["question"],
                "expected_pages": expected_pages,
                "found_pages": found_pages,
                "matched": matched,
                "reciprocal_rank": reciprocal_rank,
                "query_used": retrieval.query_used,
                "strategy_notes": retrieval.strategy_notes,
            }
        )

    return {
        "dataset_size": len(dataset),
        "top_k_page_hit_rate": hits / max(len(dataset), 1),
        "mean_reciprocal_rank": mrr_total / max(len(dataset), 1),
        "settings": {
            "chunk_size": settings.chunk_size,
            "chunk_overlap": settings.chunk_overlap,
            "dense_top_k": settings.dense_top_k,
            "lexical_top_k": settings.lexical_top_k,
            "fused_top_k": settings.fused_top_k,
            "final_top_k": settings.final_top_k,
        },
        "results": results,
    }


def run_negative_eval(dataset_path: str | Path, document_path: str | Path) -> dict:
    dataset = json.loads(Path(dataset_path).read_text(encoding="utf-8"))
    pipeline = HelpmatePipeline()
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
