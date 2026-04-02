from __future__ import annotations

import hashlib
import json
from pathlib import Path
from datetime import datetime

from dotenv import load_dotenv
from openai import OpenAI


load_dotenv()


class OpenAIFileSearchBenchmark:
    def __init__(self):
        self.client = OpenAI()

    @staticmethod
    def _fingerprint(path: str | Path) -> str:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()

    def _registry_path(self, root: Path) -> Path:
        return root / "data" / "openai_vector_store_registry.json"

    def _load_registry(self, root: Path) -> dict:
        path = self._registry_path(root)
        if not path.exists():
            return {}
        return json.loads(path.read_text(encoding="utf-8"))

    def _save_registry(self, root: Path, payload: dict) -> None:
        path = self._registry_path(root)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def get_or_create_vector_store(self, document_path: str | Path) -> str:
        root = Path(__file__).resolve().parents[2]
        doc_path = Path(document_path)
        fingerprint = self._fingerprint(doc_path)
        registry = self._load_registry(root)
        existing = registry.get(fingerprint)
        if existing:
            return existing["vector_store_id"]

        uploaded = self.client.files.create(file=doc_path.open("rb"), purpose="assistants")
        vector_store = self.client.vector_stores.create(
            name=f"Helpmate Benchmark {doc_path.name}",
            file_ids=[uploaded.id],
            metadata={"document_fingerprint": fingerprint, "source_file": doc_path.name},
        )
        registry[fingerprint] = {
            "vector_store_id": vector_store.id,
            "file_id": uploaded.id,
            "document_path": str(doc_path),
        }
        self._save_registry(root, registry)
        return vector_store.id

    def search(self, document_path: str | Path, question: str, *, max_num_results: int = 5) -> dict:
        vector_store_id = self.get_or_create_vector_store(document_path)
        response = self.client.vector_stores.search(
            vector_store_id,
            query=question,
            max_num_results=max_num_results,
            rewrite_query=True,
        )
        response_data = response.model_dump()
        outputs = response_data.get("data", [])
        snippets = []
        for index, output in enumerate(outputs, start=1):
            content = output.get("content", [])
            snippet = " ".join(part.get("text", "") for part in content if isinstance(part, dict))
            snippets.append(
                {
                    "text": snippet[:400],
                    "rank": index,
                    "metadata": {
                        "source": "openai_file_search",
                        "page_label": f"OpenAI Result {index}",
                    },
                }
            )
        return {
            "vector_store_id": vector_store_id,
            "results": snippets,
        }

    def benchmark(self, dataset_path: str | Path, document_path: str | Path) -> dict:
        dataset = json.loads(Path(dataset_path).read_text(encoding="utf-8"))
        vector_store_id = self.get_or_create_vector_store(document_path)
        results = []
        for item in dataset:
            search_result = self.search(document_path, item["question"], max_num_results=5)
            snippets = [result["text"] for result in search_result["results"]]
            matched = any(
                expected_fragment.lower() in " ".join(snippets).lower()
                for expected_fragment in item.get("expected_fragments", [])
            )
            results.append(
                {
                    "question": item["question"],
                    "matched_fragment": matched,
                    "snippets": snippets,
                }
            )

        match_rate = sum(1 for item in results if item["matched_fragment"]) / max(len(results), 1)
        return {
            "dataset_size": len(dataset),
            "vector_store_id": vector_store_id,
            "snippet_fragment_match_rate": match_rate,
            "results": results,
        }


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[2]
    benchmark = OpenAIFileSearchBenchmark()
    summary = benchmark.benchmark(
        dataset_path=root / "docs" / "evals" / "retrieval_eval_dataset.json",
        document_path=root / "Principal-Sample-Life-Insurance-Policy.pdf",
    )
    reports_dir = root / "docs" / "evals" / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    report_path = reports_dir / f"openai_file_search_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    report_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    summary["report_path"] = str(report_path)
    print(json.dumps(summary, indent=2))
