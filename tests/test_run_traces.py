from datetime import datetime, timezone
from pathlib import Path

from src.config import Settings
from src.pipeline import HelpmatePipeline
from src.schemas import AnswerResult, CacheStatus, DocumentRecord, IndexRecord, RetrievalCandidate, RetrievalResult


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        data_dir=tmp_path / "data",
        uploads_dir=tmp_path / "data" / "uploads",
        indexes_dir=tmp_path / "data" / "indexes",
        cache_dir=tmp_path / "data" / "cache",
        openai_api_key=None,
    )


def test_pipeline_builds_compact_ephemeral_run_trace(tmp_path):
    settings = _settings(tmp_path)
    pipeline = HelpmatePipeline(settings)
    document = DocumentRecord(
        document_id="doc-1",
        file_name="paper.pdf",
        file_type=".pdf",
        source_path=str(tmp_path / "paper.pdf"),
        fingerprint="fingerprint-1",
        char_count=2000,
        page_count=2,
        metadata={"_workspace_expires_at": "2026-04-18T12:00:00+00:00"},
        extracted_text="full document text should not appear in the trace",
    )
    retrieval = RetrievalResult(
        question="What happened?",
        candidates=[
            RetrievalCandidate(
                chunk_id="chunk-1",
                text="A" * 500,
                metadata={
                    "page_label": "Page 2",
                    "section_id": "results",
                    "section_heading": "Results",
                    "chapter_number": "5",
                    "chapter_title": "Results",
                    "document_section_role": "results",
                },
                dense_score=0.1,
                lexical_score=0.2,
                fused_score=0.3,
            )
        ],
        route_used="synopsis_first",
        evidence_status="strong",
        retrieval_plan={"planner_source": "llm_orchestrator", "scope_strictness": "none"},
        strategy_notes=["Planner selected synopsis_first."],
    )
    answer = AnswerResult(
        question="What happened?",
        answer="The answer text should not be copied into the trace payload.",
        citations=["Page 2"],
        evidence=retrieval.candidates,
        supported=True,
        cache_status=CacheStatus(),
        model_name="gpt",
    )

    trace = pipeline._build_run_trace(document=document, question=answer.question, retrieval_result=retrieval, answer=answer)

    assert trace.document_id == "doc-1"
    assert trace.expires_at == "2026-04-18T12:00:00+00:00"
    assert trace.payload["retrieval"]["retrieval_plan"]["planner_source"] == "llm_orchestrator"
    assert trace.payload["answer"]["support_status"] == "supported"
    candidate_payload = trace.payload["retrieval"]["candidates"][0]
    assert candidate_payload["preview"] == "A" * 240
    assert "full document text should not appear" not in str(trace.payload)
    assert "answer text should not be copied" not in str(trace.payload)


def test_trace_expiry_falls_back_to_retention_window(tmp_path):
    settings = _settings(tmp_path)
    pipeline = HelpmatePipeline(settings)
    document = DocumentRecord(
        document_id="doc-1",
        file_name="paper.pdf",
        file_type=".pdf",
        source_path=str(tmp_path / "paper.pdf"),
        fingerprint="fingerprint-1",
        char_count=100,
        page_count=1,
        metadata={},
        extracted_text="",
    )

    expires_at = pipeline._trace_expires_at(document, datetime(2026, 4, 17, 12, 0, tzinfo=timezone.utc))

    assert expires_at == "2026-04-18T12:00:00+00:00"


def test_recovery_verifier_can_keep_recovered_answer_as_partial(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    pipeline = HelpmatePipeline(settings)
    document = DocumentRecord(
        document_id="doc-1",
        file_name="report.pdf",
        file_type=".pdf",
        source_path=str(tmp_path / "report.pdf"),
        fingerprint="fingerprint-1",
        char_count=100,
        page_count=1,
        metadata={},
        extracted_text="",
    )
    index_record = IndexRecord(
        document_id=document.document_id,
        fingerprint=document.fingerprint,
        collection_name="collection",
        storage_path=str(tmp_path / "index"),
        chunk_count=1,
        section_count=0,
        embedding_model=settings.embedding_model,
        chunk_size=settings.chunk_size,
        chunk_overlap=settings.chunk_overlap,
        created_at="2026-04-18T12:00:00+00:00",
    )
    candidate = RetrievalCandidate(
        chunk_id="chunk-1",
        text="The report gives one side of the comparison, but not the other.",
        metadata={"page_label": "Page 1"},
    )
    initial_retrieval = RetrievalResult(
        question="Compare both scenarios.",
        candidates=[candidate],
        evidence_status="unsupported",
    )
    recovered_retrieval = RetrievalResult(
        question="Compare both scenarios.",
        candidates=[candidate],
        evidence_status="strong",
        retrieval_plan={"abstention_recovery_applied": True},
    )
    recovered_answer = AnswerResult(
        question="Compare both scenarios.",
        answer="The report gives one side of the comparison, but it does not provide the other side [Source 1].",
        citations=["Page 1"],
        evidence=[candidate],
        supported=True,
        support_status="supported",
        cache_status=CacheStatus(),
        model_name="gpt",
    )
    first_answer = AnswerResult(
        question="Compare both scenarios.",
        answer="Unsupported by the retrieved evidence.",
        citations=[],
        evidence=[candidate],
        supported=False,
        support_status="unsupported",
        cache_status=CacheStatus(),
        model_name="gpt",
    )
    generated_answers = [first_answer, recovered_answer]

    monkeypatch.setattr(pipeline.answer_cache, "get", lambda _: None)
    monkeypatch.setattr(pipeline.answer_cache, "set", lambda *_, **__: None)
    monkeypatch.setattr(pipeline, "retrieve_evidence", lambda *_: initial_retrieval)
    monkeypatch.setattr(pipeline.evidence_selector, "select", lambda _question, result: result)
    # generate_answer's signature now accepts a `model_override` keyword
    # arg (Step 4 of tier enforcement). The stub ignores all args so
    # this test stays decoupled from future signature evolution.
    monkeypatch.setattr(
        pipeline,
        "generate_answer",
        lambda *_args, **_kwargs: generated_answers.pop(0),
    )
    monkeypatch.setattr(pipeline.retriever, "should_recover_after_abstention", lambda *_: True)
    monkeypatch.setattr(pipeline.retriever, "recover_after_abstention", lambda **_: recovered_retrieval)
    monkeypatch.setattr(
        pipeline.generator,
        "verify_support_status",
        lambda **_: (
            "partial",
            "Support-status verifier classified the answer as partial. Missing or ambiguous facts: other side.",
        ),
    )

    answer = pipeline.answer_question(document, index_record, "Compare both scenarios.")

    assert answer.supported is False
    assert answer.support_status == "partial"
    assert "Unsupported by the recovered evidence" not in answer.answer
