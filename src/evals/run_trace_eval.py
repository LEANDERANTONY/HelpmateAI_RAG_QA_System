from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from src.config import Settings
from src.evals.evidence_selector_weight_sweep import ROOT
from src.pipeline import HelpmatePipeline
from src.schemas import AnswerResult, CacheStatus, DocumentRecord, RetrievalCandidate, RetrievalResult


REPORTS_DIR = ROOT / "docs" / "evals" / "reports"
LOCAL_STORE_DIR = ROOT / "tmp" / "run_trace_eval"


def _sample_document() -> DocumentRecord:
    return DocumentRecord(
        document_id="trace-doc",
        file_name="trace-sample.pdf",
        file_type=".pdf",
        source_path=str(LOCAL_STORE_DIR / "uploads" / "trace-sample.pdf"),
        fingerprint="trace-fingerprint",
        char_count=5000,
        page_count=4,
        metadata={"_workspace_expires_at": "2026-04-28T12:00:00+00:00"},
        extracted_text="SENSITIVE FULL DOCUMENT TEXT SHOULD NOT BE COPIED",
    )


def run_eval() -> dict[str, Any]:
    settings = Settings(
        data_dir=LOCAL_STORE_DIR / "data",
        uploads_dir=LOCAL_STORE_DIR / "data" / "uploads",
        indexes_dir=LOCAL_STORE_DIR / "data" / "indexes",
        cache_dir=LOCAL_STORE_DIR / "data" / "cache",
        state_store_backend="local",
        vector_store_backend="local",
        openai_api_key=None,
    )
    settings.ensure_dirs()
    pipeline = HelpmatePipeline(settings)
    document = _sample_document()
    candidate = RetrievalCandidate(
        chunk_id="chunk-1",
        text="Evidence preview text. " * 40,
        metadata={
            "page_label": "Page 3",
            "section_id": "results",
            "section_heading": "Results",
            "chapter_number": "5",
            "chapter_title": "Results And Discussion",
            "document_section_role": "results",
        },
        dense_score=0.1,
        lexical_score=0.2,
        fused_score=0.3,
    )
    retrieval = RetrievalResult(
        question="What are the findings?",
        candidates=[candidate],
        route_used="synopsis_first",
        evidence_status="strong",
        retrieval_plan={
            "planner_source": "llm_structured",
            "constraint_mode": "soft_multi_region",
            "scope_strictness": "none",
        },
        strategy_notes=["Planner selected broad retrieval."],
    )
    answer = AnswerResult(
        question=retrieval.question,
        answer="SENSITIVE ANSWER BODY SHOULD NOT BE COPIED",
        citations=["Page 3"],
        evidence=[candidate],
        supported=True,
        cache_status=CacheStatus(),
        model_name="gpt",
    )
    trace = pipeline._build_run_trace(document=document, question=retrieval.question, retrieval_result=retrieval, answer=answer)
    pipeline.run_trace_store.save_trace(trace)
    stored = pipeline.run_trace_store.list_traces(document.document_id)

    payload_text = json.dumps(trace.payload)
    checks = {
        "trace_saved": len(stored) == 1,
        "expires_at_matches_workspace": trace.expires_at == document.metadata["_workspace_expires_at"],
        "preview_limited": len(trace.payload["retrieval"]["candidates"][0]["preview"]) <= 240,
        "no_full_document_text": "SENSITIVE FULL DOCUMENT TEXT" not in payload_text,
        "no_answer_body": "SENSITIVE ANSWER BODY" not in payload_text,
        "has_retrieval_plan": trace.payload["retrieval"]["retrieval_plan"]["planner_source"] == "llm_structured",
    }
    return {
        "created_at": datetime.now().isoformat(),
        "trace_id": trace.trace_id,
        "checks": checks,
        "passed": all(checks.values()),
    }


def _save_report(payload: dict[str, Any]) -> Path:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = REPORTS_DIR / f"run_trace_eval_{timestamp}.json"
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def main() -> None:
    payload = run_eval()
    report_path = _save_report(payload)
    print(json.dumps({"report_path": str(report_path), **payload}, indent=2))


if __name__ == "__main__":
    main()
