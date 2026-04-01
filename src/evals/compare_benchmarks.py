from __future__ import annotations

import json
from pathlib import Path

from src.evals.openai_file_search_benchmark import OpenAIFileSearchBenchmark
from src.evals.retrieval_eval import _save_report, run_negative_eval, run_retrieval_eval


def compare(document_path: str | Path, positive_dataset_path: str | Path, negative_dataset_path: str | Path) -> dict:
    local_summary = run_retrieval_eval(positive_dataset_path, document_path)
    local_negative = run_negative_eval(negative_dataset_path, document_path)
    openai_summary = OpenAIFileSearchBenchmark().benchmark(positive_dataset_path, document_path)

    payload = {
        "local_retrieval": local_summary,
        "local_negative": local_negative,
        "openai_file_search": openai_summary,
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
