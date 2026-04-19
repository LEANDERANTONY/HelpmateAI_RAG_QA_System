from __future__ import annotations

import argparse
import json
import re
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from statistics import mean

from src.config import Settings, get_settings
from src.evals.ragas_eval import RagasEvaluator
from src.evals.retrieval_eval import _save_report
from src.evals.vendor_answer_eval import VendorAnswerEvaluator
from src.ingest import ingest_document
from src.question_starters import get_question_starters
from src.sections import build_sections


def _slugify(value: str) -> str:
    lowered = value.lower()
    lowered = re.sub(r"[^a-z0-9]+", "-", lowered)
    lowered = re.sub(r"-{2,}", "-", lowered).strip("-")
    return lowered or "document"


def _short_eval_slug(value: str, index: int) -> str:
    slug = _slugify(value)
    return f"d{index:02d}-{slug[:16].rstrip('-') or 'document'}"


def _safe_mean(values: list[float | None]) -> float | None:
    numeric = [float(value) for value in values if isinstance(value, (int, float))]
    if not numeric:
        return None
    return float(mean(numeric))


def _save_json_report(stem: str, payload: dict) -> Path:
    reports_dir = Path(__file__).resolve().parents[2] / "docs" / "evals" / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    path = reports_dir / f"{stem}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def _looks_like_noise_title(title: str) -> bool:
    lowered = title.strip().lower()
    if not lowered:
        return True
    noise_tokens = (
        "references",
        "bibliography",
        "acknowledg",
        "appendix",
        "list of tables",
        "list of figures",
        "certificate",
        "declaration",
        "table of contents",
        "contents",
        "author",
        "copyright",
    )
    if any(token in lowered for token in noise_tokens):
        return True
    if re.search(r"\[\d+\]", title):
        return True
    if re.search(r"\b(19|20)\d{2}\b", title):
        return True
    digits = sum(char.isdigit() for char in title)
    if digits >= 4:
        return True
    word_count = len(title.split())
    useful_singletons = {"abstract", "introduction", "background", "results", "discussion", "conclusion", "limitations"}
    if word_count == 1 and lowered not in useful_singletons:
        return True
    return False


def _ranked_section_titles(document_path: str | Path) -> tuple[str, list[dict[str, str]]]:
    document = ingest_document(document_path)
    sections = build_sections(document)
    style = str(document.metadata.get("document_style", "generic_longform"))
    ranked: list[dict[str, str | int]] = []
    for index, section in enumerate(sections):
        title = section.title.strip()
        if not title or _looks_like_noise_title(title):
            continue
        kind = str(section.metadata.get("section_kind", "")).strip().lower()
        score = 0
        if kind in {"overview", "abstract", "introduction", "background"}:
            score += 5
        if kind in {"methodology", "methods", "results", "discussion", "conclusion", "future work", "future directions"}:
            score += 4
        if index < 6:
            score += 2
        if len(section.page_labels) > 1:
            score += 1
        ranked.append({"title": title, "kind": kind, "score": score})

    deduped: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in sorted(ranked, key=lambda row: (-int(row["score"]), str(row["title"]).lower())):
        title = str(item["title"])
        if title.lower() == "document overview":
            continue
        key = title.lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append({"title": title, "kind": str(item["kind"])})
        if len(deduped) >= 6:
            break
    return style, deduped


def _section_question(title: str, kind: str) -> str:
    lowered_kind = kind.lower()
    if lowered_kind in {"overview", "abstract", "introduction", "background"}:
        return f"What does the document say in the {title} section?"
    if lowered_kind in {"methodology", "methods"}:
        return f"How does the document describe the approach or methods in {title}?"
    if lowered_kind == "results":
        return f"What findings or outcomes are reported in {title}?"
    if lowered_kind in {"discussion", "conclusion", "conclusions"}:
        return f"What conclusions or implications are discussed in {title}?"
    if lowered_kind in {"future work", "future directions", "limitations"}:
        return f"What limitations or future directions are discussed in {title}?"
    return f"What does the document say about {title}?"


def build_question_set(document_path: str | Path, *, max_questions: int = 8) -> dict:
    doc_path = Path(document_path)
    style, ranked_sections = _ranked_section_titles(doc_path)
    questions: list[str] = []
    seen: set[str] = set()

    def add(question: str) -> None:
        normalized = question.strip()
        if not normalized or normalized.lower() in seen:
            return
        seen.add(normalized.lower())
        questions.append(normalized)

    for starter in get_question_starters(style):
        add(starter)

    for section in ranked_sections:
        add(_section_question(section["title"], section["kind"]))
        if len(questions) >= max_questions:
            break

    if len(ranked_sections) >= 2 and len(questions) < max_questions:
        add(
            f"How do the sections {ranked_sections[0]['title']} and {ranked_sections[1]['title']} connect in the document?"
        )

    if len(questions) < max_questions:
        generic_fillers = [
            "Which sections should I read first to understand this document quickly?",
            "What are the most important claims or takeaways in this document?",
            "What challenges, constraints, or limitations are discussed in this document?",
        ]
        for filler in generic_fillers:
            add(filler)
            if len(questions) >= max_questions:
                break

    dataset = {
        "document_path": str(doc_path),
        "document_style": style,
        "question_generation_method": "deterministic starters plus section-title prompts",
        "questions": [{"question": question} for question in questions[:max_questions]],
        "section_basis": ranked_sections,
    }
    return dataset


def _unseen_eval_settings(base: Settings, timestamp_slug: str) -> Settings:
    data_dir = base.data_dir / "unseen_vendor_ragas" / timestamp_slug
    settings = replace(
        base,
        data_dir=data_dir,
        uploads_dir=data_dir / "uploads",
        indexes_dir=data_dir / "indexes",
        cache_dir=data_dir / "cache",
        state_store_backend="local",
        vector_store_backend="local",
        index_schema_version=f"{base.index_schema_version}-unseen",
        retrieval_version=f"{base.retrieval_version}-unseen",
        generation_version=f"{base.generation_version}-unseen",
    )
    settings.ensure_dirs()
    return settings


def evaluate_document(document_path: str | Path, *, settings: Settings, max_questions: int = 8) -> dict:
    doc_path = Path(document_path)
    dataset = build_question_set(doc_path, max_questions=max_questions)
    dataset_payload = dataset["questions"]
    question_path = _save_json_report(f"unseen_questions_{_slugify(doc_path.stem)}", dataset_payload)

    local_summary = RagasEvaluator(settings).evaluate(question_path, doc_path)
    vendor_summary = VendorAnswerEvaluator().evaluate(question_path, doc_path)
    openai_summary = vendor_summary["openai"]
    vectara_summary = vendor_summary["vectara"]

    document_report = {
        "document_path": str(doc_path),
        "document_style": dataset["document_style"],
        "question_generation_method": dataset["question_generation_method"],
        "question_count": len(dataset_payload),
        "questions_path": str(question_path),
        "questions": dataset_payload,
        "section_basis": dataset["section_basis"],
        "local_ragas": {
            "faithfulness_mean": local_summary.get("faithfulness_mean"),
            "answer_relevancy_mean": local_summary.get("answer_relevancy_mean"),
            "context_precision_mean": local_summary.get("context_precision_mean"),
        },
        "openai_ragas": {
            "faithfulness_mean": openai_summary.get("ragas_faithfulness_mean"),
            "answer_relevancy_mean": openai_summary.get("ragas_answer_relevancy_mean"),
            "context_precision_mean": openai_summary.get("ragas_context_precision_mean"),
        },
        "vectara_ragas": {
            "faithfulness_mean": vectara_summary.get("ragas_faithfulness_mean"),
            "answer_relevancy_mean": vectara_summary.get("ragas_answer_relevancy_mean"),
            "context_precision_mean": vectara_summary.get("ragas_context_precision_mean"),
        },
    }
    return document_report


def _margin(local_value: float | None, vendor_value: float | None) -> float | None:
    if local_value is None or vendor_value is None:
        return None
    return float(local_value - vendor_value)


def summarize_reports(reports: list[dict]) -> dict:
    overall = {
        "local": {
            "faithfulness_mean": _safe_mean([report["local_ragas"]["faithfulness_mean"] for report in reports]),
            "answer_relevancy_mean": _safe_mean([report["local_ragas"]["answer_relevancy_mean"] for report in reports]),
            "context_precision_mean": _safe_mean([report["local_ragas"]["context_precision_mean"] for report in reports]),
        },
        "openai": {
            "faithfulness_mean": _safe_mean([report["openai_ragas"]["faithfulness_mean"] for report in reports]),
            "answer_relevancy_mean": _safe_mean([report["openai_ragas"]["answer_relevancy_mean"] for report in reports]),
            "context_precision_mean": _safe_mean([report["openai_ragas"]["context_precision_mean"] for report in reports]),
        },
        "vectara": {
            "faithfulness_mean": _safe_mean([report["vectara_ragas"]["faithfulness_mean"] for report in reports]),
            "answer_relevancy_mean": _safe_mean([report["vectara_ragas"]["answer_relevancy_mean"] for report in reports]),
            "context_precision_mean": _safe_mean([report["vectara_ragas"]["context_precision_mean"] for report in reports]),
        },
    }
    overall["margin_vs_openai"] = {
        "faithfulness": _margin(overall["local"]["faithfulness_mean"], overall["openai"]["faithfulness_mean"]),
        "answer_relevancy": _margin(overall["local"]["answer_relevancy_mean"], overall["openai"]["answer_relevancy_mean"]),
        "context_precision": _margin(overall["local"]["context_precision_mean"], overall["openai"]["context_precision_mean"]),
    }
    overall["margin_vs_vectara"] = {
        "faithfulness": _margin(overall["local"]["faithfulness_mean"], overall["vectara"]["faithfulness_mean"]),
        "answer_relevancy": _margin(overall["local"]["answer_relevancy_mean"], overall["vectara"]["answer_relevancy_mean"]),
        "context_precision": _margin(overall["local"]["context_precision_mean"], overall["vectara"]["context_precision_mean"]),
    }
    return overall


def run_unseen_vendor_ragas_compare(documents: list[str | Path], *, max_questions: int = 8) -> dict:
    timestamp_slug = datetime.now().strftime("%Y%m%d_%H%M%S")
    base_settings = get_settings()
    reports = []
    for index, document_path in enumerate(documents, start=1):
        doc_path = Path(document_path)
        doc_settings = _unseen_eval_settings(base_settings, f"{timestamp_slug}_{_short_eval_slug(doc_path.stem, index)}")
        reports.append(evaluate_document(doc_path, settings=doc_settings, max_questions=max_questions))
    payload = {
        "documents": reports,
        "overall_summary": summarize_reports(reports),
        "question_generation_method": "deterministic starters plus section-title prompts",
        "notes": [
            "These are unseen-document vendor answer-quality comparisons, not retrieval-labeled benchmark runs.",
            "The question sets are auto-generated from document structure and built-in starter templates; they are useful as an out-of-sample stress check, but they are not a substitute for hand-labeled held-out eval sets.",
        ],
    }
    report_path = _save_report("unseen_vendor_ragas_compare", payload)
    payload["report_path"] = str(report_path)
    return payload


def _default_documents(root: Path) -> list[Path]:
    return sorted((root / "static" / "sample_files" / "test").glob("*.pdf"))


def main() -> None:
    root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser()
    parser.add_argument("--documents", nargs="*", help="PDF paths to evaluate. Defaults to static/sample_files/test/*.pdf")
    parser.add_argument("--max-questions", type=int, default=8)
    args = parser.parse_args()

    documents = [Path(path) for path in args.documents] if args.documents else _default_documents(root)
    result = run_unseen_vendor_ragas_compare(documents, max_questions=args.max_questions)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
