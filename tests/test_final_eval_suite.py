from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.evals.final_eval_suite import load_eval_suite, run_final_eval_suite, summarize_rows, validate_eval_suite


def _write_manifest(path: Path, payload: dict) -> Path:
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_manifest_validation_rejects_tuned_docs_and_bad_questions(tmp_path: Path):
    manifest = _write_manifest(
        tmp_path / "suite.json",
        {
            "suite_id": "bad-suite",
            "context_budget": {"top_k": 5, "max_chars_per_context": 600},
            "documents": [
                {
                    "document_id": "doc-1",
                    "path": "missing.pdf",
                    "document_type": "policy",
                    "used_for_tuning": True,
                }
            ],
            "questions": [
                {
                    "question_id": "q-1",
                    "document_id": "unknown-doc",
                    "question": "What is covered?",
                    "intent_type": "bad-intent",
                    "answerable": True,
                },
                {
                    "question_id": "q-2",
                    "document_id": "doc-1",
                    "question": "What does it say about pricing?",
                    "intent_type": "unsupported",
                    "answerable": True,
                },
            ],
        },
    )

    suite = load_eval_suite(manifest, root=tmp_path)
    errors = validate_eval_suite(suite, require_files=True)

    assert any("used_for_tuning=true" in error for error in errors)
    assert any("document path does not exist" in error for error in errors)
    assert any("unknown document_id" in error for error in errors)
    assert any("unknown intent_type" in error for error in errors)
    assert any("unsupported intent must use answerable=false" in error for error in errors)


def test_manifest_validation_accepts_frozen_heldout_schema(tmp_path: Path):
    document_path = tmp_path / "doc.pdf"
    document_path.write_text("fake pdf body", encoding="utf-8")
    manifest = _write_manifest(
        tmp_path / "suite.json",
        {
            "suite_id": "good-suite",
            "frozen": True,
            "context_budget": {"top_k": 4, "max_chars_per_context": 500},
            "documents": [
                {
                    "document_id": "doc-1",
                    "path": str(document_path),
                    "document_type": "research_paper",
                    "used_for_tuning": False,
                }
            ],
            "questions": [
                {
                    "question_id": "q-1",
                    "document_id": "doc-1",
                    "question": "What are the findings?",
                    "intent_type": "broad_summary",
                    "answerable": True,
                    "gold_answer_notes": "Expected answer is recorded separately.",
                },
                {
                    "question_id": "q-2",
                    "document_id": "doc-1",
                    "question": "What subscription price is recommended?",
                    "intent_type": "unsupported",
                    "answerable": False,
                    "unsupported_reason": "No pricing recommendation is present.",
                },
            ],
        },
    )

    suite = load_eval_suite(manifest, root=tmp_path)

    assert suite.frozen is True
    assert suite.context_top_k == 4
    assert validate_eval_suite(suite, require_files=True) == []


def test_summarize_rows_reports_abstention_and_deltas():
    rows = [
        {
            "system": "helpmate",
            "intent_type": "lookup",
            "answerable": True,
            "supported": True,
            "context_count": 4,
            "context_chars": 1000,
            "ragas": {"faithfulness": 0.9, "answer_relevancy": 0.8, "context_precision": 0.7},
        },
        {
            "system": "helpmate",
            "intent_type": "unsupported",
            "answerable": False,
            "supported": False,
            "context_count": 4,
            "context_chars": 900,
            "ragas": {"faithfulness": 1.0, "answer_relevancy": 0.6, "context_precision": 0.8},
        },
        {
            "system": "openai_file_search",
            "intent_type": "lookup",
            "answerable": True,
            "supported": False,
            "context_count": 5,
            "context_chars": 1200,
            "ragas": {"faithfulness": 0.5, "answer_relevancy": 0.4, "context_precision": 0.3},
        },
        {
            "system": "openai_file_search",
            "intent_type": "unsupported",
            "answerable": False,
            "supported": True,
            "context_count": 5,
            "context_chars": 1000,
            "ragas": {"faithfulness": 0.6, "answer_relevancy": 0.5, "context_precision": 0.2},
        },
    ]

    summary = summarize_rows(rows)
    helpmate = summary["overall"]["helpmate"]
    openai = summary["overall"]["openai_file_search"]

    assert helpmate["answerable_supported_rate"] == pytest.approx(1.0)
    assert helpmate["unsupported_abstention_rate"] == pytest.approx(1.0)
    assert helpmate["false_support_rate"] == pytest.approx(0.0)
    assert openai["false_abstention_rate"] == pytest.approx(1.0)
    assert openai["false_support_rate"] == pytest.approx(1.0)
    assert summary["deltas_vs_helpmate"]["openai_file_search"]["ragas_all_faithfulness"] == pytest.approx(0.4)
    assert summary["by_intent"]["helpmate"]["lookup"]["dataset_size"] == 1


def test_run_final_eval_rejects_unknown_context_mode(tmp_path: Path):
    document_path = tmp_path / "doc.pdf"
    document_path.write_text("fake pdf body", encoding="utf-8")
    manifest = _write_manifest(
        tmp_path / "suite.json",
        {
            "suite_id": "context-mode-suite",
            "context_budget": {"top_k": 4, "max_chars_per_context": 500},
            "documents": [
                {
                    "document_id": "doc-1",
                    "path": str(document_path),
                    "document_type": "research_paper",
                    "used_for_tuning": False,
                }
            ],
            "questions": [
                {
                    "question_id": "q-1",
                    "document_id": "doc-1",
                    "question": "What is covered?",
                    "intent_type": "lookup",
                    "answerable": True,
                    "gold_answer_notes": "Expected answer is recorded separately.",
                }
            ],
        },
    )

    with pytest.raises(ValueError, match="context_mode"):
        run_final_eval_suite(manifest, systems=(), context_mode="bad-mode")
