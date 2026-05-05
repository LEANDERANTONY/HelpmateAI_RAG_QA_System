from __future__ import annotations

import hashlib
import json
from pathlib import Path
from datetime import datetime
from typing import Any

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
            metadata={"document_fingerprint": fingerprint, "source_file": doc_path.name},
        )
        vector_store_file = self.client.vector_stores.files.create_and_poll(
            uploaded.id,
            vector_store_id=vector_store.id,
            poll_interval_ms=1000,
        )
        if getattr(vector_store_file, "status", "") != "completed":
            raise RuntimeError(
                f"OpenAI vector store indexing did not complete for {doc_path.name}: "
                f"{getattr(vector_store_file, 'status', 'unknown')}"
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

    @staticmethod
    def _extract_response_contexts(response_data: dict[str, Any], *, max_chars: int | None = 4000) -> list[dict[str, Any]]:
        snippets: list[dict[str, Any]] = []
        for item in response_data.get("output", []):
            if item.get("type") != "file_search_call":
                continue
            for index, result in enumerate(item.get("results", []) or [], start=1):
                text = ""
                content = result.get("content", [])
                if isinstance(content, list):
                    text = " ".join(
                        part.get("text", "")
                        for part in content
                        if isinstance(part, dict) and part.get("text")
                    )
                if not text:
                    text = str(result.get("text", "") or "")
                text = text.strip()
                if not text:
                    continue
                snippets.append(
                    {
                        "text": text[:max_chars] if max_chars else text,
                        "rank": len(snippets) + 1,
                        "metadata": {
                            "source": "openai_file_search_native",
                            "page_label": f"OpenAI File Search Result {len(snippets) + 1}",
                            "file_id": result.get("file_id", ""),
                            "filename": result.get("filename", ""),
                            "score": result.get("score"),
                        },
                    }
                )
        return snippets

    @staticmethod
    def _is_abstention(answer: str) -> bool:
        normalized = " ".join(answer.lower().split())
        abstention_markers = (
            "insufficient_evidence",
            "insufficient evidence",
            "not enough evidence",
            "does not contain enough",
            "cannot answer",
            "can't answer",
            "could not determine",
            "does not state",
            "not provided in the file",
            "not specified in the file",
        )
        return any(marker in normalized for marker in abstention_markers)

    def answer(
        self,
        document_path: str | Path,
        question: str,
        *,
        model: str,
        max_num_results: int = 5,
        max_context_chars: int | None = 4000,
    ) -> dict[str, Any]:
        vector_store_id = self.get_or_create_vector_store(document_path)
        response_data: dict[str, Any] = {}
        answer_text = ""
        contexts: list[dict[str, Any]] = []
        attempts = 0
        for attempts in range(1, 4):
            response = self.client.responses.create(
                model=model,
                input=question,
                instructions=(
                    "Answer the user's question using only the uploaded file available through file_search. "
                    "Use file_search before answering. If the file_search evidence is insufficient to answer "
                    "the question completely, say 'INSUFFICIENT_EVIDENCE:' followed by a brief explanation of "
                    "what is missing. Do not use outside knowledge."
                ),
                tools=[
                    {
                        "type": "file_search",
                        "vector_store_ids": [vector_store_id],
                        "max_num_results": max_num_results,
                    }
                ],
                tool_choice={"type": "file_search"},
                include=["file_search_call.results"],
                max_output_tokens=700,
            )
            response_data = response.model_dump()
            answer_text = getattr(response, "output_text", "") or ""
            contexts = self._extract_response_contexts(response_data, max_chars=max_context_chars)
            if contexts or not self._is_abstention(answer_text):
                break
        return {
            "vector_store_id": vector_store_id,
            "answer": answer_text.strip(),
            "supported": not self._is_abstention(answer_text),
            "contexts": contexts,
            "response_id": response_data.get("id"),
            "model": response_data.get("model", model),
            "attempts": attempts,
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
