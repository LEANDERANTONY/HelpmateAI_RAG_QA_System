from __future__ import annotations

from pathlib import Path

from src.evals.financebench_eval import build_financebench_manifest, _intent_from_financebench, _safe_filename


def test_financebench_safe_filename_removes_path_punctuation():
    assert _safe_filename("3M/2018 10-K") == "3M-2018-10-K"


def test_financebench_intent_maps_numeric_questions():
    row = {
        "question_type": "metrics-generated",
        "question_reasoning": "Information extraction",
        "question": "What is the FY2018 capital expenditure amount?",
    }

    assert _intent_from_financebench(row) == "numeric_procedure"


def test_financebench_intent_maps_comparison_questions():
    row = {
        "question_type": "domain-relevant",
        "question_reasoning": "Comparison",
        "question": "How did revenue compare across the two years?",
    }

    assert _intent_from_financebench(row) == "comparison_synthesis"


def test_financebench_manifest_builder_uses_local_pdf_paths(monkeypatch, tmp_path: Path):
    questions = [
        {
            "financebench_id": "financebench_id_test",
            "doc_name": "ACME_2024_10K",
            "question": "What was ACME revenue?",
            "answer": "$10",
            "justification": "Revenue line item.",
            "evidence": [{"evidence_page_num": 4}],
        }
    ]
    documents = [
        {
            "doc_name": "ACME_2024_10K",
            "doc_type": "10K",
            "doc_link": "https://example.com/acme.pdf",
        }
    ]

    monkeypatch.setattr("src.evals.financebench_eval.prepare_financebench_assets", lambda **kwargs: (questions, documents))

    manifest = build_financebench_manifest(
        data_dir=tmp_path / "data",
        pdf_dir=tmp_path / "pdfs",
        output_path=tmp_path / "manifest.json",
        download_pdfs=False,
    )

    assert manifest["documents"][0]["path"].endswith("ACME_2024_10K.pdf")
    assert manifest["questions"][0]["question_id"] == "financebench_id_test"
    assert manifest["questions"][0]["expected_regions"][0]["page_label"] == "Page 5"
