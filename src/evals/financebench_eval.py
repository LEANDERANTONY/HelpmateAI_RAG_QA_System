from __future__ import annotations

import argparse
import json
import urllib.request
from pathlib import Path
from typing import Any

from src.evals.final_eval_suite import DEFAULT_SYSTEMS, run_final_eval_suite


ROOT = Path(__file__).resolve().parents[2]
FINANCEBENCH_RAW_BASE = "https://raw.githubusercontent.com/patronus-ai/financebench/main"
QUESTIONS_URL = f"{FINANCEBENCH_RAW_BASE}/data/financebench_open_source.jsonl"
DOCUMENTS_URL = f"{FINANCEBENCH_RAW_BASE}/data/financebench_document_information.jsonl"


def _download(url: str, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(url, headers={"User-Agent": "HelpmateAI FinanceBench eval"})
    with urllib.request.urlopen(request, timeout=300) as response:
        path.write_bytes(response.read())


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _safe_filename(value: str) -> str:
    cleaned = "".join(char if char.isalnum() or char in {"-", "_", "."} else "-" for char in value)
    while "--" in cleaned:
        cleaned = cleaned.replace("--", "-")
    return cleaned.strip("-") or "document"


def _intent_from_financebench(row: dict[str, Any]) -> str:
    question_type = str(row.get("question_type", "")).lower()
    reasoning = str(row.get("question_reasoning", "")).lower()
    question = str(row.get("question", "")).lower()
    if "calculation" in reasoning or "generated" in question_type or any(token in question for token in ("how much", "amount", "percentage", "ratio")):
        return "numeric_procedure"
    if "comparison" in reasoning or "compare" in question or "difference" in question:
        return "comparison_synthesis"
    return "lookup"


def prepare_financebench_assets(
    *,
    data_dir: Path,
    pdf_dir: Path,
    max_questions: int | None = None,
    download_pdfs: bool = True,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    questions_path = data_dir / "financebench_open_source.jsonl"
    documents_path = data_dir / "financebench_document_information.jsonl"
    if not questions_path.exists():
        _download(QUESTIONS_URL, questions_path)
    if not documents_path.exists():
        _download(DOCUMENTS_URL, documents_path)

    questions = _read_jsonl(questions_path)
    if max_questions:
        questions = questions[:max_questions]
    document_rows = {row["doc_name"]: row for row in _read_jsonl(documents_path)}
    needed_documents = [document_rows[question["doc_name"]] for question in questions]
    unique_documents = {document["doc_name"]: document for document in needed_documents}

    if download_pdfs:
        pdf_dir.mkdir(parents=True, exist_ok=True)
        for document in unique_documents.values():
            target = pdf_dir / f"{_safe_filename(document['doc_name'])}.pdf"
            if target.exists() and target.stat().st_size > 0:
                continue
            _download(str(document["doc_link"]), target)
    return questions, list(unique_documents.values())


def build_financebench_manifest(
    *,
    data_dir: Path,
    pdf_dir: Path,
    output_path: Path,
    max_questions: int | None = None,
    download_pdfs: bool = True,
) -> dict:
    questions, documents = prepare_financebench_assets(
        data_dir=data_dir,
        pdf_dir=pdf_dir,
        max_questions=max_questions,
        download_pdfs=download_pdfs,
    )
    document_payload = []
    for document in sorted(documents, key=lambda item: item["doc_name"]):
        doc_name = str(document["doc_name"])
        document_payload.append(
            {
                "document_id": doc_name,
                "path": str((pdf_dir / f"{_safe_filename(doc_name)}.pdf").as_posix()),
                "document_type": str(document.get("doc_type", "financial_filing")).lower(),
                "source": str(document.get("doc_link", "")),
                "license_notes": "FinanceBench open-source sample; see PatronusAI/financebench and dataset license.",
                "used_for_tuning": False,
            }
        )

    question_payload = []
    for row in questions:
        evidence = row.get("evidence", [])
        expected_regions = []
        if isinstance(evidence, list):
            for item in evidence:
                page = item.get("evidence_page_num") if isinstance(item, dict) else None
                expected_regions.append(
                    {
                        "section": "FinanceBench annotated evidence",
                        "page_label": f"Page {int(page) + 1}" if isinstance(page, int) else "",
                    }
                )
        question_payload.append(
            {
                "question_id": str(row["financebench_id"]),
                "document_id": str(row["doc_name"]),
                "question": str(row["question"]),
                "intent_type": _intent_from_financebench(row),
                "answerable": True,
                "expected_regions": expected_regions,
                "gold_answer": str(row.get("answer", "")),
                "gold_answer_notes": str(row.get("justification", "")),
                "unsupported_reason": "",
            }
        )

    manifest = {
        "suite_id": "financebench-open-source-150",
        "description": "FinanceBench open-source 150-question sample converted into HelpmateAI final-eval manifest format.",
        "frozen": True,
        "context_budget": {
            "top_k": 5,
            "max_chars_per_context": 600,
        },
        "documents": document_payload,
        "questions": question_payload,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", default="docs/evals/financebench")
    parser.add_argument("--pdf-dir", default="static/financebench")
    parser.add_argument("--output", default="docs/evals/financebench_manifest.json")
    parser.add_argument("--max-questions", type=int, default=None)
    parser.add_argument("--skip-pdf-download", action="store_true")
    parser.add_argument("--run-suite", action="store_true")
    parser.add_argument("--systems", nargs="*", default=list(DEFAULT_SYSTEMS), choices=list(DEFAULT_SYSTEMS))
    parser.add_argument("--skip-ragas", action="store_true")
    args = parser.parse_args()

    output_path = ROOT / args.output
    manifest = build_financebench_manifest(
        data_dir=ROOT / args.data_dir,
        pdf_dir=ROOT / args.pdf_dir,
        output_path=output_path,
        max_questions=args.max_questions,
        download_pdfs=not args.skip_pdf_download,
    )
    result = {
        "output": str(output_path.resolve()),
        "document_count": len(manifest["documents"]),
        "question_count": len(manifest["questions"]),
    }
    if args.run_suite:
        if args.skip_pdf_download:
            raise SystemExit("--run-suite requires PDFs. Remove --skip-pdf-download.")
        result["suite"] = run_final_eval_suite(
            output_path,
            systems=tuple(args.systems),
            max_questions=args.max_questions,
            skip_ragas=args.skip_ragas,
        )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
