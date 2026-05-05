"""Re-run the 22 originally-abstaining held-out questions through the live pipeline.

This is a *local pass only* — no RAGAS scoring. Mirrors the indexing / retrieval /
selection, abstention recovery, support verification, and run-trace behavior
used by ``HelpmatePipeline.answer_question``.

Usage::

    uv run python -m src.evals.abstention_22_rerun

The 22 questions live in
``docs/evals/reports/abstention_22_current_check_20260501.json`` (each entry has
``question_id``, ``document_id``, ``intent_type``, ``question``, plus the prior
``supported`` / ``abstained`` labels). Source PDFs are resolved via the manifest
at ``docs/evals/final_eval_manifest.draft.json``.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any

from src.config import Settings, get_settings
from src.evals.evidence_selector_weight_sweep import ROOT
from src.pipeline import HelpmatePipeline


REPORTS_DIR = ROOT / "docs" / "evals" / "reports"
MANIFEST_PATH = ROOT / "docs" / "evals" / "final_eval_manifest.draft.json"
DEFAULT_DATASET_PATH = REPORTS_DIR / "abstention_22_current_check_20260501.json"
LOCAL_STORE_DIR = ROOT / "tmp" / "abstention_22_rerun"


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _build_settings(base: Settings) -> Settings:
    """Same settings recipe used by ``support_guardrail_eval`` so behavior matches."""

    settings = replace(
        base,
        data_dir=LOCAL_STORE_DIR / "data",
        cache_dir=LOCAL_STORE_DIR / "data" / "cache",
        state_store_backend="local",
        vector_store_backend="local",
        reranker_enabled=True,
        evidence_selector_enabled=True,
        evidence_selector_prune=False,
        router_llm_enabled=True,
        retrieval_version=f"{base.retrieval_version}-abstention-22-rerun",
        generation_version=f"{base.generation_version}-abstention-22-rerun",
    )
    settings.ensure_dirs()
    return settings


def _resolve_documents() -> dict[str, Path]:
    """Map ``document_id`` → absolute PDF path using the held-out manifest."""

    manifest = _load_json(MANIFEST_PATH)
    mapping: dict[str, Path] = {}
    for entry in manifest["documents"]:
        document_id = entry["document_id"]
        path = (ROOT / entry["path"]).resolve()
        if not path.exists():
            raise FileNotFoundError(f"Manifest entry missing on disk: {path}")
        mapping[document_id] = path
    return mapping


def _index_document(pipeline: HelpmatePipeline, document_path: Path):
    document = pipeline.ingest_document(document_path)
    index_record = pipeline.build_or_load_index(document)
    return document, index_record


def _run_answer(pipeline: HelpmatePipeline, document_record, index_record, question: str):
    return pipeline.answer_question(document_record, index_record, question)


def _trace_payload(pipeline: HelpmatePipeline, answer) -> dict[str, Any]:
    trace_id = getattr(answer, "run_trace_id", None)
    if not trace_id:
        return {}
    for trace in pipeline.run_trace_store.list_traces():
        if trace.trace_id == trace_id:
            return trace.payload or {}
    return {}


def _row(item: dict[str, Any], answer, trace_payload: dict[str, Any]) -> dict[str, Any]:
    retrieval = trace_payload.get("retrieval", {}) if isinstance(trace_payload, dict) else {}
    retrieval_plan = retrieval.get("retrieval_plan", {}) if isinstance(retrieval, dict) else {}
    strategy_notes = list(retrieval.get("strategy_notes", [])) if isinstance(retrieval, dict) else []
    return {
        "question_id": item["question_id"],
        "document_id": item["document_id"],
        "intent_type": item.get("intent_type"),
        "question": item["question"],
        "previously_supported": bool(item.get("supported", False)),
        "previously_abstained": bool(item.get("abstained", True)),
        "now_supported": bool(answer.supported),
        "now_abstained": not bool(answer.supported),
        "support_status": answer.support_status,
        "evidence_status": retrieval.get("evidence_status"),
        "route_used": retrieval.get("route_used"),
        "best_score": retrieval.get("best_score"),
        "selector_triggered": any("Evidence selector reviewed" in str(note) for note in strategy_notes),
        "abstention_recovery_applied": bool(retrieval_plan.get("abstention_recovery_applied")),
        "retrieval_plan": retrieval_plan,
        "retrieval_notes": strategy_notes,
        "citations": list(answer.citations),
        "note": answer.note,
        "answer": answer.answer,
    }


def _summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(rows)
    now_supported = sum(1 for r in rows if r["now_supported"])
    prev_supported = sum(1 for r in rows if r["previously_supported"])

    by_intent: dict[str, dict[str, int]] = {}
    by_doc: dict[str, dict[str, int]] = {}
    for r in rows:
        for key, bucket in (
            (r.get("intent_type") or "unknown", by_intent),
            (r["document_id"], by_doc),
        ):
            slot = bucket.setdefault(key, {"total": 0, "now_supported": 0, "prev_supported": 0})
            slot["total"] += 1
            slot["now_supported"] += int(r["now_supported"])
            slot["prev_supported"] += int(r["previously_supported"])

    transitions = {
        "stayed_supported": sum(1 for r in rows if r["previously_supported"] and r["now_supported"]),
        "newly_supported": sum(1 for r in rows if not r["previously_supported"] and r["now_supported"]),
        "regressed": sum(1 for r in rows if r["previously_supported"] and not r["now_supported"]),
        "still_abstaining": sum(1 for r in rows if not r["previously_supported"] and not r["now_supported"]),
    }
    return {
        "total": total,
        "now_supported": now_supported,
        "now_abstained": total - now_supported,
        "previously_supported": prev_supported,
        "previously_abstained": total - prev_supported,
        "transitions": transitions,
        "by_intent": by_intent,
        "by_doc": by_doc,
    }


def run_abstention_22_rerun(dataset_path: Path = DEFAULT_DATASET_PATH) -> dict[str, Any]:
    settings = _build_settings(get_settings())
    pipeline = HelpmatePipeline(settings)
    documents = _resolve_documents()

    dataset = _load_json(dataset_path)
    questions: list[dict[str, Any]] = dataset["results"] if isinstance(dataset, dict) else dataset

    # Index each unique document once.
    indexed: dict[str, tuple[Any, Any]] = {}
    for item in questions:
        document_id = item["document_id"]
        if document_id in indexed:
            continue
        if document_id not in documents:
            raise KeyError(f"document_id {document_id!r} not present in manifest")
        indexed[document_id] = _index_document(pipeline, documents[document_id])

    rows: list[dict[str, Any]] = []
    for item in questions:
        document_record, index_record = indexed[item["document_id"]]
        answer = _run_answer(pipeline, document_record, index_record, item["question"])
        rows.append(_row(item, answer, _trace_payload(pipeline, answer)))

    return {
        "created_at": datetime.now().isoformat(),
        "dataset_path": str(dataset_path),
        "manifest_path": str(MANIFEST_PATH),
        "settings": {
            "retrieval_version": settings.retrieval_version,
            "generation_version": settings.generation_version,
            "answer_model": settings.answer_model,
            "weak_evidence_score_threshold": settings.weak_evidence_score_threshold,
            "unsupported_evidence_score_threshold": settings.unsupported_evidence_score_threshold,
            "evidence_selector_enabled": settings.evidence_selector_enabled,
            "evidence_selector_gap_threshold": settings.evidence_selector_gap_threshold,
        },
        "summary": _summarize(rows),
        "results": rows,
    }


def _save_report(payload: dict[str, Any]) -> Path:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = REPORTS_DIR / f"abstention_22_rerun_{timestamp}.json"
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def _print_table(rows: list[dict[str, Any]]) -> None:
    header = f"{'question_id':<60}  {'prev':<10}  {'now':<10}  {'evidence':<10}  {'route'}"
    print(header)
    print("-" * len(header))
    for r in rows:
        prev = "supported" if r["previously_supported"] else "abstained"
        now = "supported" if r["now_supported"] else "abstained"
        print(
            f"{r['question_id'][:60]:<60}  {prev:<10}  {now:<10}  "
            f"{(r['evidence_status'] or '-'): <10}  {r['route_used']}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dataset",
        type=Path,
        default=DEFAULT_DATASET_PATH,
        help="Path to the 22-question abstention dataset JSON.",
    )
    parser.add_argument(
        "--no-save",
        action="store_true",
        help="Skip writing the JSON report (still prints summary).",
    )
    args = parser.parse_args()

    payload = run_abstention_22_rerun(args.dataset)

    print()
    _print_table(payload["results"])
    print()

    summary = payload["summary"]
    headline = {
        "total": summary["total"],
        "now_supported": summary["now_supported"],
        "now_abstained": summary["now_abstained"],
        "previously_supported": summary["previously_supported"],
        "previously_abstained": summary["previously_abstained"],
        "transitions": summary["transitions"],
        "by_intent": summary["by_intent"],
        "by_doc": summary["by_doc"],
    }
    print(json.dumps(headline, indent=2))

    if not args.no_save:
        path = _save_report(payload)
        print(f"\nReport written to: {path}")


if __name__ == "__main__":
    main()
