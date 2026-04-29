from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from statistics import mean
from typing import Any

from ragas import SingleTurnSample

from src.config import Settings, get_settings
from src.evals.openai_file_search_benchmark import OpenAIFileSearchBenchmark
from src.evals.ragas_judge import build_ragas_metrics
from src.evals.ragas_retry import call_with_ragas_retry
from src.evals.retrieval_eval import _save_report
from src.evals.vendor_answer_eval import _strip_references
from src.evals.vectara_benchmark import VectaraBenchmark, get_vectara_search_profile
from src.generation import AnswerGenerator
from src.pipeline import HelpmatePipeline
from src.schemas import AnswerResult, RetrievalCandidate, RetrievalResult


ALLOWED_INTENTS = {
    "lookup",
    "local_scope",
    "broad_summary",
    "comparison_synthesis",
    "numeric_procedure",
    "unsupported",
}

DEFAULT_SYSTEMS = ("helpmate", "openai_file_search", "vectara")
CONTEXT_MODES = ("native", "equalized")


@dataclass(frozen=True)
class EvalDocument:
    document_id: str
    path: Path
    document_type: str
    source: str = ""
    license_notes: str = ""
    used_for_tuning: bool = False


@dataclass(frozen=True)
class EvalQuestion:
    question_id: str
    document_id: str
    question: str
    intent_type: str
    answerable: bool
    gold_answer: str = ""
    gold_answer_notes: str = ""
    expected_regions: tuple[dict[str, Any], ...] = ()
    unsupported_reason: str = ""


@dataclass(frozen=True)
class EvalSuite:
    suite_id: str
    description: str
    frozen: bool
    context_top_k: int
    max_context_chars: int
    documents: tuple[EvalDocument, ...]
    questions: tuple[EvalQuestion, ...]


def _safe_mean(values: list[float | None]) -> float | None:
    numeric = [float(value) for value in values if isinstance(value, (int, float))]
    if not numeric:
        return None
    return float(mean(numeric))


def _as_bool(value: Any, *, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _resolve_suite_path(raw_path: str | Path, *, suite_path: Path, root: Path) -> Path:
    path = Path(raw_path)
    if path.is_absolute():
        return path
    suite_relative = (suite_path.parent / path).resolve()
    if suite_relative.exists():
        return suite_relative
    return (root / path).resolve()


def load_eval_suite(manifest_path: str | Path, *, root: Path | None = None) -> EvalSuite:
    root = root or Path(__file__).resolve().parents[2]
    manifest = Path(manifest_path).resolve()
    payload = json.loads(manifest.read_text(encoding="utf-8-sig"))
    budget = payload.get("context_budget", {})
    documents = tuple(
        EvalDocument(
            document_id=str(item["document_id"]),
            path=_resolve_suite_path(item["path"], suite_path=manifest, root=root),
            document_type=str(item.get("document_type", item.get("type", "unknown"))),
            source=str(item.get("source", "")),
            license_notes=str(item.get("license_notes", "")),
            used_for_tuning=_as_bool(item.get("used_for_tuning"), default=False),
        )
        for item in payload.get("documents", [])
    )
    questions = tuple(
        EvalQuestion(
            question_id=str(item["question_id"]),
            document_id=str(item["document_id"]),
            question=str(item["question"]),
            intent_type=str(item.get("intent_type", "")).strip(),
            answerable=_as_bool(item.get("answerable"), default=True),
            gold_answer=str(item.get("gold_answer", "")),
            gold_answer_notes=str(item.get("gold_answer_notes", "")),
            expected_regions=tuple(item.get("expected_regions", [])),
            unsupported_reason=str(item.get("unsupported_reason", "")),
        )
        for item in payload.get("questions", [])
    )
    return EvalSuite(
        suite_id=str(payload.get("suite_id", manifest.stem)),
        description=str(payload.get("description", "")),
        frozen=_as_bool(payload.get("frozen"), default=False),
        context_top_k=int(budget.get("top_k", 5)),
        max_context_chars=int(budget.get("max_chars_per_context", 600)),
        documents=documents,
        questions=questions,
    )


def validate_eval_suite(suite: EvalSuite, *, require_files: bool = True) -> list[str]:
    errors: list[str] = []
    if not suite.documents:
        errors.append("manifest must include at least one document")
    if not suite.questions:
        errors.append("manifest must include at least one question")
    if suite.context_top_k <= 0:
        errors.append("context_budget.top_k must be positive")
    if suite.max_context_chars <= 0:
        errors.append("context_budget.max_chars_per_context must be positive")

    document_ids = [document.document_id for document in suite.documents]
    duplicate_document_ids = sorted({item for item in document_ids if document_ids.count(item) > 1})
    for document_id in duplicate_document_ids:
        errors.append(f"duplicate document_id: {document_id}")

    known_documents = set(document_ids)
    for document in suite.documents:
        if document.used_for_tuning:
            errors.append(f"document was marked used_for_tuning=true: {document.document_id}")
        if require_files and not document.path.exists():
            errors.append(f"document path does not exist for {document.document_id}: {document.path}")

    question_ids = [question.question_id for question in suite.questions]
    duplicate_question_ids = sorted({item for item in question_ids if question_ids.count(item) > 1})
    for question_id in duplicate_question_ids:
        errors.append(f"duplicate question_id: {question_id}")

    for question in suite.questions:
        if question.document_id not in known_documents:
            errors.append(f"question references unknown document_id: {question.question_id}")
        if question.intent_type not in ALLOWED_INTENTS:
            errors.append(f"unknown intent_type for {question.question_id}: {question.intent_type}")
        if question.intent_type == "unsupported" and question.answerable:
            errors.append(f"unsupported intent must use answerable=false: {question.question_id}")
        if not question.answerable and not question.unsupported_reason:
            errors.append(f"unsupported question should include unsupported_reason: {question.question_id}")
        if question.answerable and not (question.gold_answer or question.gold_answer_notes or question.expected_regions):
            errors.append(f"answerable question needs gold answer, notes, or expected regions: {question.question_id}")
    return errors


def _limited_contexts(snippets: list[dict], *, top_k: int, max_chars: int) -> list[dict]:
    limited: list[dict] = []
    for index, snippet in enumerate(snippets[:top_k], start=1):
        text = str(snippet.get("text", "")).strip()
        if not text:
            continue
        metadata = dict(snippet.get("metadata", {}))
        metadata.setdefault("source", "external")
        metadata.setdefault("page_label", f"Result {index}")
        limited.append(
            {
                "text": text[:max_chars],
                "rank": int(snippet.get("rank", index)),
                "metadata": metadata,
            }
        )
    return limited


def _candidates_from_contexts(system_name: str, contexts: list[dict]) -> list[RetrievalCandidate]:
    candidates: list[RetrievalCandidate] = []
    for index, context in enumerate(contexts, start=1):
        metadata = dict(context.get("metadata", {}))
        page_label = str(metadata.get("page_label", f"{system_name} Result {index}"))
        candidates.append(
            RetrievalCandidate(
                chunk_id=f"{system_name}-{index}",
                text=str(context.get("text", "")),
                metadata=metadata,
                fused_score=float(max(0.0, 1.0 - (index * 0.1))),
                citation_label=page_label,
            )
        )
    return candidates


def _answer_from_contexts(
    *,
    settings: Settings,
    system_name: str,
    question: str,
    contexts: list[dict],
) -> AnswerResult:
    generator = AnswerGenerator(settings)
    retrieval = RetrievalResult(
        question=question,
        candidates=_candidates_from_contexts(system_name, contexts),
        route_used=f"{system_name}_retrieval",
        query_used=question,
        strategy_notes=[f"Answer generated from {system_name} retrieval context."],
    )
    return generator.generate(question, retrieval)


class RagasScorer:
    def __init__(self, settings: Settings, *, enabled: bool = True) -> None:
        self._metrics: dict[str, Any] = {}
        self.judge_info: dict[str, str] = {"reason": "RAGAS scoring disabled."}
        if not enabled:
            self.available = False
            return
        self._metrics, self.judge_info = build_ragas_metrics(settings)
        self.available = bool(self._metrics)

    def score(self, *, question: str, answer_text: str, contexts: list[str]) -> dict[str, float | str]:
        if not self.available:
            return {}
        sample = SingleTurnSample(user_input=question, response=answer_text, retrieved_contexts=contexts)
        scores: dict[str, float | str] = {}
        for metric_name, metric in self._metrics.items():
            try:
                scores[metric_name] = float(call_with_ragas_retry(lambda: metric.single_turn_score(sample)))
            except Exception as exc:
                scores[f"{metric_name}_error"] = str(exc)
        return scores


def _row_from_answer(
    *,
    suite: EvalSuite,
    system_name: str,
    document: EvalDocument,
    question: EvalQuestion,
    answer: AnswerResult,
    contexts: list[str],
    ragas_scores: dict[str, float | str],
) -> dict:
    cleaned_answer = _strip_references(answer.answer)
    return {
        "suite_id": suite.suite_id,
        "system": system_name,
        "document_id": document.document_id,
        "document_type": document.document_type,
        "question_id": question.question_id,
        "question": question.question,
        "intent_type": question.intent_type,
        "answerable": question.answerable,
        "supported": bool(answer.supported),
        "abstained": not bool(answer.supported),
        "answer_preview": cleaned_answer[:500],
        "citations": list(answer.citations),
        "context_count": len(contexts),
        "context_chars": sum(len(context) for context in contexts),
        "ragas": ragas_scores,
        "note": answer.note,
    }


def summarize_rows(rows: list[dict]) -> dict:
    by_system: dict[str, list[dict]] = {}
    for row in rows:
        by_system.setdefault(str(row["system"]), []).append(row)

    summaries = {system: _summarize_group(system_rows) for system, system_rows in sorted(by_system.items())}
    return {
        "overall": summaries,
        "by_intent": {
            system: {
                intent: _summarize_group([row for row in system_rows if row["intent_type"] == intent])
                for intent in sorted({str(row["intent_type"]) for row in system_rows})
            }
            for system, system_rows in sorted(by_system.items())
        },
        "deltas_vs_helpmate": _deltas_vs_helpmate(summaries),
    }


def _summarize_group(rows: list[dict]) -> dict:
    total = len(rows)
    if not total:
        return {"dataset_size": 0}
    answerable_rows = [row for row in rows if row.get("answerable")]
    unsupported_rows = [row for row in rows if not row.get("answerable")]
    supported_rows = [row for row in rows if row.get("supported")]
    attempted_answerable_rows = [row for row in answerable_rows if row.get("supported")]
    ragas = {
        metric: _safe_mean(
            [
                row.get("ragas", {}).get(metric)
                for row in rows
                if isinstance(row.get("ragas", {}).get(metric), (int, float))
            ]
        )
        for metric in ("faithfulness", "answer_relevancy", "context_precision")
    }
    attempted_ragas = {
        metric: _safe_mean(
            [
                row.get("ragas", {}).get(metric)
                for row in supported_rows
                if isinstance(row.get("ragas", {}).get(metric), (int, float))
            ]
        )
        for metric in ("faithfulness", "answer_relevancy", "context_precision")
    }
    return {
        "dataset_size": total,
        "answerable_size": len(answerable_rows),
        "unsupported_size": len(unsupported_rows),
        "supported_rate": len(supported_rows) / max(total, 1),
        "answerable_supported_rate": len(attempted_answerable_rows) / max(len(answerable_rows), 1),
        "unsupported_abstention_rate": sum(1 for row in unsupported_rows if not row.get("supported")) / max(len(unsupported_rows), 1),
        "false_support_rate": sum(1 for row in unsupported_rows if row.get("supported")) / max(len(unsupported_rows), 1),
        "false_abstention_rate": sum(1 for row in answerable_rows if not row.get("supported")) / max(len(answerable_rows), 1),
        "avg_context_count": _safe_mean([row.get("context_count") for row in rows]),
        "avg_context_chars": _safe_mean([row.get("context_chars") for row in rows]),
        "ragas_all": ragas,
        "ragas_attempted": attempted_ragas,
    }


def _deltas_vs_helpmate(summaries: dict[str, dict]) -> dict:
    baseline = summaries.get("helpmate")
    if not baseline:
        return {}
    deltas: dict[str, dict] = {}
    for system, summary in summaries.items():
        if system == "helpmate":
            continue
        deltas[system] = {}
        for metric in ("faithfulness", "answer_relevancy", "context_precision"):
            local = baseline.get("ragas_all", {}).get(metric)
            other = summary.get("ragas_all", {}).get(metric)
            deltas[system][f"ragas_all_{metric}"] = None if local is None or other is None else float(local - other)
        for metric in ("supported_rate", "false_support_rate", "false_abstention_rate"):
            local = baseline.get(metric)
            other = summary.get(metric)
            deltas[system][metric] = None if local is None or other is None else float(local - other)
    return deltas


def _final_eval_settings(base: Settings, suite_id: str) -> Settings:
    slug = "".join(char if char.isalnum() or char in {"-", "_"} else "-" for char in suite_id.lower()).strip("-")
    slug = slug or datetime.now().strftime("%Y%m%d_%H%M%S")
    data_dir = base.data_dir / "final_eval_suite" / slug
    settings = replace(
        base,
        data_dir=data_dir,
        uploads_dir=data_dir / "uploads",
        indexes_dir=data_dir / "indexes",
        cache_dir=data_dir / "cache",
        state_store_backend="local",
        vector_store_backend="local",
        index_schema_version=f"{base.index_schema_version}-final-eval",
        retrieval_version=f"{base.retrieval_version}-final-eval",
        generation_version=f"{base.generation_version}-final-eval",
    )
    settings.ensure_dirs()
    return settings


def run_final_eval_suite(
    manifest_path: str | Path,
    *,
    systems: tuple[str, ...] = DEFAULT_SYSTEMS,
    max_questions: int | None = None,
    context_mode: str = "native",
    skip_ragas: bool = False,
    require_files: bool = True,
    settings: Settings | None = None,
) -> dict:
    suite = load_eval_suite(manifest_path)
    errors = validate_eval_suite(suite, require_files=require_files)
    if errors:
        raise ValueError("Invalid final eval suite:\n" + "\n".join(f"- {error}" for error in errors))
    if context_mode not in CONTEXT_MODES:
        raise ValueError(f"context_mode must be one of {', '.join(CONTEXT_MODES)}")

    base_settings = settings or get_settings()
    runtime_settings = _final_eval_settings(base_settings, suite.suite_id)
    scorer = RagasScorer(runtime_settings, enabled=not skip_ragas)
    documents = {document.document_id: document for document in suite.documents}
    questions = list(suite.questions[:max_questions]) if max_questions else list(suite.questions)

    rows: list[dict] = []
    if "helpmate" in systems:
        rows.extend(_run_helpmate(suite, documents, questions, runtime_settings, scorer, context_mode=context_mode))
    if "openai_file_search" in systems:
        rows.extend(_run_openai_file_search(suite, documents, questions, runtime_settings, scorer))
    if "vectara" in systems:
        rows.extend(_run_vectara(suite, documents, questions, runtime_settings, scorer))

    vectara_profile = get_vectara_search_profile()
    payload = {
        "suite": {
            "suite_id": suite.suite_id,
            "description": suite.description,
            "frozen": suite.frozen,
            "manifest_path": str(Path(manifest_path).resolve()),
            "context_budget": {
                "top_k": suite.context_top_k,
                "max_chars_per_context": suite.max_context_chars,
            },
            "context_mode": context_mode,
        },
        "systems": list(systems),
        "ragas_enabled": scorer.available,
        "ragas_judge": scorer.judge_info,
        "settings": {
            "answer_model": runtime_settings.answer_model,
            "embedding_model": runtime_settings.embedding_model,
            "retrieval_version": runtime_settings.retrieval_version,
            "generation_version": runtime_settings.generation_version,
            "ragas_context_policy": {
                "native": "Score each system against the context it generated from. Helpmate uses full selected evidence; vendor answers use the capped context passed into their generator.",
                "equalized": "Score all systems against the same top-k and max-character context cap.",
                "active": context_mode,
            },
            "openai_file_search": {
                "rewrite_query": True,
                "max_num_results": suite.context_top_k,
            },
            "vectara": {
                "search_profile": vectara_profile.name,
                "search_config": vectara_profile.search_payload(suite.context_top_k),
            },
        },
        "summary": summarize_rows(rows),
        "results": rows,
    }
    report_path = _save_report("final_eval_suite", payload)
    payload["report_path"] = str(report_path)
    return payload


def validate_manifest_file(manifest_path: str | Path, *, require_files: bool = True) -> dict:
    suite = load_eval_suite(manifest_path)
    errors = validate_eval_suite(suite, require_files=require_files)
    return {
        "suite_id": suite.suite_id,
        "valid": not errors,
        "errors": errors,
        "document_count": len(suite.documents),
        "question_count": len(suite.questions),
        "frozen": suite.frozen,
    }


def _run_helpmate(
    suite: EvalSuite,
    documents: dict[str, EvalDocument],
    questions: list[EvalQuestion],
    settings: Settings,
    scorer: RagasScorer,
    *,
    context_mode: str,
) -> list[dict]:
    rows: list[dict] = []
    pipeline = HelpmatePipeline(settings)
    index_cache = {}
    for question in questions:
        eval_document = documents[question.document_id]
        if eval_document.document_id not in index_cache:
            document_record = pipeline.ingest_document(eval_document.path)
            index_record = pipeline.build_or_load_index(document_record)
            index_cache[eval_document.document_id] = (document_record, index_record)
        document_record, index_record = index_cache[eval_document.document_id]
        answer = pipeline.answer_question(document_record, index_record, question.question)
        contexts = [candidate.text for candidate in answer.evidence if candidate.text]
        if context_mode == "equalized":
            contexts = [context[: suite.max_context_chars] for context in contexts[: suite.context_top_k]]
        ragas_scores = scorer.score(question=question.question, answer_text=_strip_references(answer.answer), contexts=contexts)
        rows.append(
            _row_from_answer(
                suite=suite,
                system_name="helpmate",
                document=eval_document,
                question=question,
                answer=answer,
                contexts=contexts,
                ragas_scores=ragas_scores,
            )
        )
    return rows


def _run_openai_file_search(
    suite: EvalSuite,
    documents: dict[str, EvalDocument],
    questions: list[EvalQuestion],
    settings: Settings,
    scorer: RagasScorer,
) -> list[dict]:
    rows: list[dict] = []
    provider = OpenAIFileSearchBenchmark()
    for question in questions:
        eval_document = documents[question.document_id]
        search = provider.search(eval_document.path, question.question, max_num_results=suite.context_top_k)
        contexts_payload = _limited_contexts(search["results"], top_k=suite.context_top_k, max_chars=suite.max_context_chars)
        answer = _answer_from_contexts(
            settings=settings,
            system_name="openai_file_search",
            question=question.question,
            contexts=contexts_payload,
        )
        contexts = [context["text"] for context in contexts_payload]
        ragas_scores = scorer.score(question=question.question, answer_text=_strip_references(answer.answer), contexts=contexts)
        rows.append(
            _row_from_answer(
                suite=suite,
                system_name="openai_file_search",
                document=eval_document,
                question=question,
                answer=answer,
                contexts=contexts,
                ragas_scores=ragas_scores,
            )
        )
    return rows


def _run_vectara(
    suite: EvalSuite,
    documents: dict[str, EvalDocument],
    questions: list[EvalQuestion],
    settings: Settings,
    scorer: RagasScorer,
) -> list[dict]:
    rows: list[dict] = []
    provider = VectaraBenchmark()
    if not provider.available:
        return [
            {
                "suite_id": suite.suite_id,
                "system": "vectara",
                "error": "VECTARA_API_KEY is not configured.",
                "document_id": question.document_id,
                "question_id": question.question_id,
                "question": question.question,
                "intent_type": question.intent_type,
                "answerable": question.answerable,
                "supported": False,
                "abstained": True,
                "context_count": 0,
                "context_chars": 0,
                "ragas": {},
            }
            for question in questions
        ]
    for question in questions:
        eval_document = documents[question.document_id]
        search = provider.search(eval_document.path, question.question, limit=suite.context_top_k)
        contexts_payload = _limited_contexts(search["results"], top_k=suite.context_top_k, max_chars=suite.max_context_chars)
        answer = _answer_from_contexts(
            settings=settings,
            system_name="vectara",
            question=question.question,
            contexts=contexts_payload,
        )
        contexts = [context["text"] for context in contexts_payload]
        ragas_scores = scorer.score(question=question.question, answer_text=_strip_references(answer.answer), contexts=contexts)
        rows.append(
            _row_from_answer(
                suite=suite,
                system_name="vectara",
                document=eval_document,
                question=question,
                answer=answer,
                contexts=contexts,
                ragas_scores=ragas_scores,
            )
        )
    return rows


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", default="docs/evals/final_eval_manifest.example.json")
    parser.add_argument("--systems", nargs="*", default=list(DEFAULT_SYSTEMS), choices=list(DEFAULT_SYSTEMS))
    parser.add_argument("--max-questions", type=int, default=None)
    parser.add_argument("--context-mode", choices=list(CONTEXT_MODES), default="native")
    parser.add_argument("--skip-ragas", action="store_true")
    parser.add_argument("--allow-missing-files", action="store_true")
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args()

    if args.validate_only:
        result = validate_manifest_file(args.manifest, require_files=not args.allow_missing_files)
        print(json.dumps(result, indent=2))
        if not result["valid"]:
            raise SystemExit(1)
        return

    result = run_final_eval_suite(
        args.manifest,
        systems=tuple(args.systems),
        max_questions=args.max_questions,
        context_mode=args.context_mode,
        skip_ragas=args.skip_ragas,
        require_files=not args.allow_missing_files,
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
