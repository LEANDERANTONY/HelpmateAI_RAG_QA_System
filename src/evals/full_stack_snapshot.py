from __future__ import annotations

import argparse
import json
import warnings
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from statistics import mean
from typing import Any

from dotenv import load_dotenv
from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from ragas import SingleTurnSample
from ragas.embeddings import LangchainEmbeddingsWrapper
from ragas.llms import LangchainLLMWrapper
from ragas.metrics import Faithfulness, LLMContextPrecisionWithoutReference, ResponseRelevancy

from src.config import Settings, get_settings
from src.evals.answer_stack_ablation import NEGATIVE_DATASETS, POSITIVE_DATASETS, _load_dataset
from src.evals.evidence_selector_weight_sweep import ROOT
from src.pipeline import HelpmatePipeline


load_dotenv()


REPORTS_DIR = ROOT / "docs" / "evals" / "reports"
LOCAL_STORE_DIR = ROOT / "tmp" / "full_stack_snapshot"
RAGAS_DEFAULT_DATASETS = [
    "health_retrieval_eval_dataset.json",
    "thesis_retrieval_eval_dataset.json",
    "pancreas7_retrieval_eval_dataset.json",
]


def _safe_mean(values: list[float]) -> float | None:
    numeric = [value for value in values if isinstance(value, (int, float))]
    if not numeric:
        return None
    return float(mean(numeric))


def _snapshot_settings(base: Settings) -> Settings:
    settings = replace(
        base,
        data_dir=LOCAL_STORE_DIR / "data",
        cache_dir=LOCAL_STORE_DIR / "data" / "cache",
        state_store_backend="local",
        vector_store_backend="local",
        reranker_enabled=True,
        evidence_selector_enabled=True,
        evidence_selector_prune=False,
        planner_llm_enabled=True,
        router_llm_enabled=False,
        retrieval_version=f"{base.retrieval_version}-llm-planner-snapshot",
        generation_version=f"{base.generation_version}-llm-planner-snapshot",
    )
    settings.ensure_dirs()
    return settings


def _build_indexes(pipeline: HelpmatePipeline) -> dict[str, dict[str, str]]:
    documents: dict[str, dict[str, str]] = {}
    for dataset_name, document_path in POSITIVE_DATASETS.items():
        resolved_path = Path(document_path)
        if not resolved_path.exists():
            fallback = ROOT / "static" / "sample_files" / "test" / resolved_path.name
            if fallback.exists():
                resolved_path = fallback
        document = pipeline.ingest_document(resolved_path)
        pipeline.build_or_load_index(document)
        documents[dataset_name] = {
            "document_id": document.document_id,
            "fingerprint": document.fingerprint,
            "document_path": str(resolved_path),
        }
    return documents


def _retrieval_row(question: str, expected_pages: list[str], expected_fragments: list[str], retrieval) -> dict[str, Any]:
    selected_pages = [candidate.metadata.get("page_label", "Document") for candidate in retrieval.candidates]
    selected_text = " ".join(candidate.text.lower() for candidate in retrieval.candidates)
    fragment_hits = sum(1 for fragment in expected_fragments if fragment.lower() in selected_text)
    fragment_recall = fragment_hits / max(len(expected_fragments), 1)
    reciprocal_rank = 0.0
    for rank, page in enumerate(selected_pages, start=1):
        if page in expected_pages:
            reciprocal_rank = 1.0 / rank
            break
    plan = retrieval.retrieval_plan or {}
    return {
        "question": question,
        "page_hit": int(any(page in selected_pages for page in expected_pages)),
        "reciprocal_rank": reciprocal_rank,
        "fragment_recall": fragment_recall,
        "route_used": retrieval.route_used,
        "planner_source": str(plan.get("planner_source", "deterministic")),
        "evidence_spread": str(plan.get("evidence_spread", "")),
        "selected_pages": selected_pages,
    }


def _summarize_retrieval(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {
            "dataset_size": 0,
            "page_hit_rate": 0.0,
            "mean_reciprocal_rank": 0.0,
            "mean_fragment_recall": 0.0,
            "objective_score": 0.0,
            "llm_structured_rate": 0.0,
            "planner_source_counts": {},
        }
    page_hit_rate = sum(row["page_hit"] for row in rows) / len(rows)
    mean_reciprocal_rank = sum(row["reciprocal_rank"] for row in rows) / len(rows)
    mean_fragment_recall = sum(row["fragment_recall"] for row in rows) / len(rows)
    planner_source_counts: dict[str, int] = {}
    for row in rows:
        source = str(row["planner_source"])
        planner_source_counts[source] = planner_source_counts.get(source, 0) + 1
    return {
        "dataset_size": len(rows),
        "page_hit_rate": page_hit_rate,
        "mean_reciprocal_rank": mean_reciprocal_rank,
        "mean_fragment_recall": mean_fragment_recall,
        "objective_score": 0.45 * page_hit_rate + 0.35 * mean_fragment_recall + 0.20 * mean_reciprocal_rank,
        "llm_structured_rate": planner_source_counts.get("llm_structured", 0) / len(rows),
        "planner_source_counts": planner_source_counts,
    }


def _positive_row(question: str, expected_pages: list[str], expected_fragments: list[str], answer, planner_source: str) -> dict[str, Any]:
    evidence_pages = [candidate.metadata.get("page_label", "Document") for candidate in answer.evidence]
    evidence_text = " ".join(candidate.text.lower() for candidate in answer.evidence)
    fragment_hits = sum(1 for fragment in expected_fragments if fragment.lower() in evidence_text)
    return {
        "question": question,
        "supported": bool(answer.supported),
        "citation_page_hit": int(any(page in evidence_pages for page in expected_pages)),
        "evidence_fragment_recall": fragment_hits / max(len(expected_fragments), 1),
        "planner_source": planner_source,
        "model_name": answer.model_name,
    }


def _negative_row(question: str, answer, planner_source: str) -> dict[str, Any]:
    return {
        "question": question,
        "abstained": int(not answer.supported),
        "supported": bool(answer.supported),
        "planner_source": planner_source,
        "model_name": answer.model_name,
    }


def _summarize_positive(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {
            "dataset_size": 0,
            "supported_rate": 0.0,
            "citation_page_hit_rate": 0.0,
            "evidence_fragment_recall_mean": 0.0,
            "llm_structured_rate": 0.0,
            "planner_source_counts": {},
        }
    planner_source_counts: dict[str, int] = {}
    for row in rows:
        source = str(row["planner_source"])
        planner_source_counts[source] = planner_source_counts.get(source, 0) + 1
    return {
        "dataset_size": len(rows),
        "supported_rate": sum(row["supported"] for row in rows) / len(rows),
        "citation_page_hit_rate": sum(row["citation_page_hit"] for row in rows) / len(rows),
        "evidence_fragment_recall_mean": sum(row["evidence_fragment_recall"] for row in rows) / len(rows),
        "llm_structured_rate": planner_source_counts.get("llm_structured", 0) / len(rows),
        "planner_source_counts": planner_source_counts,
    }


def _summarize_negative(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {
            "dataset_size": 0,
            "abstention_rate": 0.0,
            "false_support_rate": 0.0,
            "llm_structured_rate": 0.0,
            "planner_source_counts": {},
        }
    planner_source_counts: dict[str, int] = {}
    for row in rows:
        source = str(row["planner_source"])
        planner_source_counts[source] = planner_source_counts.get(source, 0) + 1
    return {
        "dataset_size": len(rows),
        "abstention_rate": sum(row["abstained"] for row in rows) / len(rows),
        "false_support_rate": sum(1 for row in rows if row["supported"]) / len(rows),
        "llm_structured_rate": planner_source_counts.get("llm_structured", 0) / len(rows),
        "planner_source_counts": planner_source_counts,
    }


class _RagasRunner:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.available = bool(settings.openai_api_key)
        self._metrics: dict[str, Any] = {}
        if not self.available:
            return
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", category=DeprecationWarning)
            llm = LangchainLLMWrapper(ChatOpenAI(model=settings.answer_model, api_key=settings.openai_api_key))
            embeddings = LangchainEmbeddingsWrapper(
                OpenAIEmbeddings(model=settings.embedding_model, api_key=settings.openai_api_key)
            )
        self._metrics = {
            "faithfulness": Faithfulness(llm=llm),
            "answer_relevancy": ResponseRelevancy(llm=llm, embeddings=embeddings),
            "context_precision": LLMContextPrecisionWithoutReference(llm=llm),
        }

    @staticmethod
    def _sample(question: str, answer: dict[str, Any]) -> SingleTurnSample:
        contexts = [
            str(candidate.get("text", "")).strip()
            for candidate in answer.get("evidence", [])
            if str(candidate.get("text", "")).strip()
        ]
        return SingleTurnSample(
            user_input=question,
            response=answer.get("answer", ""),
            retrieved_contexts=contexts,
        )

    def evaluate_dataset(self, pipeline: HelpmatePipeline, document, dataset_name: str) -> dict[str, Any]:
        dataset_path = ROOT / "docs" / "evals" / dataset_name
        items = _load_dataset(dataset_path)
        if not self.available:
            return {"dataset_size": len(items), "available": False}

        rows: list[dict[str, Any]] = []
        faithfulness_scores: list[float] = []
        answer_relevancy_scores: list[float] = []
        context_precision_scores: list[float] = []
        planner_source_counts: dict[str, int] = {}

        for item in items:
            question = item["question"]
            retrieval = pipeline.retrieve_evidence(document.document_id, document.fingerprint, question)
            planner_source = str((retrieval.retrieval_plan or {}).get("planner_source", "deterministic"))
            planner_source_counts[planner_source] = planner_source_counts.get(planner_source, 0) + 1
            retrieval = pipeline.evidence_selector.select(question, retrieval)
            answer = pipeline.generate_answer(document.document_id, question, retrieval).to_dict()
            sample = self._sample(question, answer)
            row: dict[str, Any] = {
                "question": question,
                "supported": answer.get("supported", False),
                "planner_source": planner_source,
            }
            for metric_name, metric in self._metrics.items():
                try:
                    score = float(metric.single_turn_score(sample))
                    row[metric_name] = score
                    if metric_name == "faithfulness":
                        faithfulness_scores.append(score)
                    elif metric_name == "answer_relevancy":
                        answer_relevancy_scores.append(score)
                    elif metric_name == "context_precision":
                        context_precision_scores.append(score)
                except Exception as exc:
                    row[f"{metric_name}_error"] = str(exc)
            rows.append(row)

        return {
            "dataset_size": len(rows),
            "available": True,
            "supported_rate": sum(1 for row in rows if row.get("supported")) / len(rows) if rows else 0.0,
            "faithfulness_mean": _safe_mean(faithfulness_scores),
            "answer_relevancy_mean": _safe_mean(answer_relevancy_scores),
            "context_precision_mean": _safe_mean(context_precision_scores),
            "llm_structured_rate": planner_source_counts.get("llm_structured", 0) / len(rows) if rows else 0.0,
            "planner_source_counts": planner_source_counts,
            "results": rows,
        }


def run_snapshot(ragas_datasets: list[str]) -> dict[str, Any]:
    base_settings = get_settings()
    settings = _snapshot_settings(base_settings)
    pipeline = HelpmatePipeline(settings)
    documents = _build_indexes(pipeline)

    retrieval_payload: dict[str, Any] = {}
    retrieval_rows_all: list[dict[str, Any]] = []
    for dataset_name, doc_info in documents.items():
        document = pipeline.ingest_document(doc_info["document_path"])
        items = _load_dataset(ROOT / "docs" / "evals" / dataset_name)
        rows: list[dict[str, Any]] = []
        for item in items:
            retrieval = pipeline.retrieve_evidence(document.document_id, document.fingerprint, item["question"])
            row = _retrieval_row(item["question"], item.get("expected_pages", []), item.get("expected_fragments", []), retrieval)
            rows.append(row)
            retrieval_rows_all.append(row)
        retrieval_payload[dataset_name] = _summarize_retrieval(rows)

    answer_payload: dict[str, Any] = {}
    positive_all: list[dict[str, Any]] = []
    negative_all: list[dict[str, Any]] = []
    for dataset_name, doc_info in documents.items():
        document = pipeline.ingest_document(doc_info["document_path"])
        positive_items = _load_dataset(ROOT / "docs" / "evals" / dataset_name)
        negative_dataset_name = dataset_name.replace("_retrieval_eval_dataset.json", "_negative_eval_dataset.json")
        if dataset_name == "retrieval_eval_dataset.json":
            negative_dataset_name = "negative_eval_dataset.json"
        negative_items = _load_dataset(ROOT / "docs" / "evals" / negative_dataset_name)

        positive_rows: list[dict[str, Any]] = []
        negative_rows: list[dict[str, Any]] = []
        for item in positive_items:
            retrieval = pipeline.retrieve_evidence(document.document_id, document.fingerprint, item["question"])
            planner_source = str((retrieval.retrieval_plan or {}).get("planner_source", "deterministic"))
            retrieval = pipeline.evidence_selector.select(item["question"], retrieval)
            answer = pipeline.generate_answer(document.document_id, item["question"], retrieval)
            row = _positive_row(item["question"], item.get("expected_pages", []), item.get("expected_fragments", []), answer, planner_source)
            positive_rows.append(row)
            positive_all.append(row)
        for item in negative_items:
            retrieval = pipeline.retrieve_evidence(document.document_id, document.fingerprint, item["question"])
            planner_source = str((retrieval.retrieval_plan or {}).get("planner_source", "deterministic"))
            retrieval = pipeline.evidence_selector.select(item["question"], retrieval)
            answer = pipeline.generate_answer(document.document_id, item["question"], retrieval)
            row = _negative_row(item["question"], answer, planner_source)
            negative_rows.append(row)
            negative_all.append(row)

        answer_payload[dataset_name] = {
            "positive": _summarize_positive(positive_rows),
            "negative": _summarize_negative(negative_rows),
        }

    ragas_runner = _RagasRunner(settings)
    ragas_payload: dict[str, Any] = {
        "available": ragas_runner.available,
        "dataset_names": ragas_datasets,
        "datasets": {},
    }
    ragas_results_all: list[dict[str, Any]] = []
    for dataset_name in ragas_datasets:
        document = pipeline.ingest_document(documents[dataset_name]["document_path"])
        dataset_payload = ragas_runner.evaluate_dataset(pipeline, document, dataset_name)
        ragas_payload["datasets"][dataset_name] = dataset_payload
        ragas_results_all.extend(dataset_payload.get("results", []))

    ragas_payload["overall"] = {
        "supported_rate": sum(1 for row in ragas_results_all if row.get("supported")) / len(ragas_results_all)
        if ragas_results_all
        else 0.0,
        "faithfulness_mean": _safe_mean([float(row["faithfulness"]) for row in ragas_results_all if "faithfulness" in row]),
        "answer_relevancy_mean": _safe_mean([float(row["answer_relevancy"]) for row in ragas_results_all if "answer_relevancy" in row]),
        "context_precision_mean": _safe_mean([float(row["context_precision"]) for row in ragas_results_all if "context_precision" in row]),
        "llm_structured_rate": (
            sum(1 for row in ragas_results_all if row.get("planner_source") == "llm_structured") / len(ragas_results_all)
            if ragas_results_all
            else 0.0
        ),
    }

    return {
        "settings": {
            "planner_llm_enabled": settings.planner_llm_enabled,
            "planner_model": settings.planner_model,
            "router_llm_enabled": settings.router_llm_enabled,
            "reranker_enabled": settings.reranker_enabled,
            "evidence_selector_enabled": settings.evidence_selector_enabled,
            "evidence_selector_prune": settings.evidence_selector_prune,
        },
        "retrieval": {
            "overall": _summarize_retrieval(retrieval_rows_all),
            "per_dataset": retrieval_payload,
        },
        "answer": {
            "positive_overall": _summarize_positive(positive_all),
            "negative_overall": _summarize_negative(negative_all),
            "per_dataset": answer_payload,
        },
        "ragas": ragas_payload,
    }


def _save_report(payload: dict[str, Any]) -> Path:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = REPORTS_DIR / f"full_stack_snapshot_{timestamp}.json"
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate the current full stack with the LLM planner enabled.")
    parser.add_argument("--ragas-datasets", nargs="*", default=RAGAS_DEFAULT_DATASETS)
    args = parser.parse_args()

    payload = run_snapshot(args.ragas_datasets)
    report_path = _save_report(payload)
    summary = {
        "report_path": str(report_path),
        "retrieval_overall": payload["retrieval"]["overall"],
        "answer_positive_overall": payload["answer"]["positive_overall"],
        "answer_negative_overall": payload["answer"]["negative_overall"],
        "ragas_overall": payload["ragas"]["overall"],
    }
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
