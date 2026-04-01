from __future__ import annotations

import itertools
import json
from pathlib import Path

from src.evals.retrieval_eval import run_retrieval_eval


def run_grid_search(dataset_path: str | Path, document_path: str | Path) -> list[dict]:
    chunk_sizes = [900, 1200, 1500]
    chunk_overlaps = [120, 180, 240]
    dense_top_ks = [8, 10, 14]
    final_top_ks = [3, 4, 5]
    results: list[dict] = []

    for chunk_size, chunk_overlap, dense_top_k, final_top_k in itertools.product(
        chunk_sizes,
        chunk_overlaps,
        dense_top_ks,
        final_top_ks,
    ):
        summary = run_retrieval_eval(
            dataset_path=dataset_path,
            document_path=document_path,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            dense_top_k=dense_top_k,
            lexical_top_k=dense_top_k,
            fused_top_k=max(dense_top_k + 2, 12),
            final_top_k=final_top_k,
        )
        results.append(summary)

    results.sort(
        key=lambda item: (item["top_k_page_hit_rate"], item["mean_reciprocal_rank"]),
        reverse=True,
    )
    return results


def run_fast_sweep(dataset_path: str | Path, document_path: str | Path) -> list[dict]:
    candidate_configs = [
        {"chunk_size": 900, "chunk_overlap": 120, "dense_top_k": 8, "lexical_top_k": 8, "fused_top_k": 10, "final_top_k": 3},
        {"chunk_size": 900, "chunk_overlap": 180, "dense_top_k": 10, "lexical_top_k": 10, "fused_top_k": 12, "final_top_k": 4},
        {"chunk_size": 1200, "chunk_overlap": 180, "dense_top_k": 10, "lexical_top_k": 10, "fused_top_k": 12, "final_top_k": 4},
        {"chunk_size": 1200, "chunk_overlap": 240, "dense_top_k": 14, "lexical_top_k": 14, "fused_top_k": 16, "final_top_k": 5},
        {"chunk_size": 1500, "chunk_overlap": 180, "dense_top_k": 10, "lexical_top_k": 10, "fused_top_k": 12, "final_top_k": 4},
        {"chunk_size": 1500, "chunk_overlap": 240, "dense_top_k": 14, "lexical_top_k": 14, "fused_top_k": 16, "final_top_k": 5},
    ]
    results = []
    for config in candidate_configs:
        results.append(
            run_retrieval_eval(
                dataset_path=dataset_path,
                document_path=document_path,
                chunk_size=config["chunk_size"],
                chunk_overlap=config["chunk_overlap"],
                dense_top_k=config["dense_top_k"],
                lexical_top_k=config["lexical_top_k"],
                fused_top_k=config["fused_top_k"],
                final_top_k=config["final_top_k"],
            )
        )
    results.sort(
        key=lambda item: (item["top_k_page_hit_rate"], item["mean_reciprocal_rank"]),
        reverse=True,
    )
    return results


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[2]
    results = run_fast_sweep(
        dataset_path=root / "docs" / "evals" / "retrieval_eval_dataset.json",
        document_path=root / "Principal-Sample-Life-Insurance-Policy.pdf",
    )
    print(json.dumps(results[:5], indent=2))
