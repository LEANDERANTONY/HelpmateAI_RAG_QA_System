from __future__ import annotations

import json
from pathlib import Path

from src.evals.openai_file_search_benchmark import OpenAIFileSearchBenchmark
from src.evals.ragas_eval import RagasEvaluator
from src.evals.retrieval_eval import _save_report, run_negative_eval, run_retrieval_eval
from src.evals.vendor_answer_eval import VendorAnswerEvaluator
from src.evals.vectara_benchmark import VectaraBenchmark


def compare(document_path: str | Path, positive_dataset_path: str | Path, negative_dataset_path: str | Path) -> dict:
    local_summary = run_retrieval_eval(positive_dataset_path, document_path)
    local_negative = run_negative_eval(negative_dataset_path, document_path)
    ragas_summary = RagasEvaluator().evaluate(positive_dataset_path, document_path)
    openai_summary = OpenAIFileSearchBenchmark().benchmark(positive_dataset_path, document_path)
    vectara_summary = VectaraBenchmark().benchmark(positive_dataset_path, document_path)
    vendor_answer_summary = VendorAnswerEvaluator().evaluate(positive_dataset_path, document_path)

    payload = {
        "local_retrieval": local_summary,
        "local_negative": local_negative,
        "ragas": ragas_summary,
        "openai_file_search": openai_summary,
        "vectara": vectara_summary,
        "vendor_answer_eval": vendor_answer_summary,
    }
    report_path = _save_report("benchmark_comparison", payload)
    payload["report_path"] = str(report_path)
    return payload


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[2]
    result = compare(
        document_path=root / "Principal-Sample-Life-Insurance-Policy.pdf",
        positive_dataset_path=root / "docs" / "evals" / "retrieval_eval_dataset.json",
        negative_dataset_path=root / "docs" / "evals" / "negative_eval_dataset.json",
    )
    print(json.dumps(result, indent=2))
