from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import urllib.error
import urllib.request
import uuid
from pathlib import Path

from dotenv import load_dotenv


load_dotenv()


class VectaraBenchmark:
    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = api_key or os.getenv("VECTARA_API_KEY")
        self.base_url = "https://api.vectara.io/v2"
        self.available = bool(self.api_key)

    @staticmethod
    def _fingerprint(path: str | Path) -> str:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()

    def _registry_path(self, root: Path) -> Path:
        return root / "data" / "vectara_corpus_registry.json"

    def _load_registry(self, root: Path) -> dict:
        path = self._registry_path(root)
        if not path.exists():
            return {}
        return json.loads(path.read_text(encoding="utf-8"))

    def _save_registry(self, root: Path, payload: dict) -> None:
        path = self._registry_path(root)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def _request(
        self,
        path: str,
        *,
        method: str = "GET",
        body: bytes | None = None,
        headers: dict[str, str] | None = None,
        timeout: int = 120,
    ) -> dict:
        if not self.api_key:
            raise RuntimeError("VECTARA_API_KEY is not configured.")

        request_headers = {
            "x-api-key": self.api_key,
            "Accept": "application/json",
        }
        if headers:
            request_headers.update(headers)

        request = urllib.request.Request(
            f"{self.base_url}{path}",
            data=body,
            headers=request_headers,
            method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                payload = response.read().decode("utf-8")
                return json.loads(payload) if payload else {}
        except urllib.error.HTTPError as exc:
            message = exc.read().decode("utf-8")
            raise RuntimeError(f"Vectara API error {exc.code}: {message}") from exc

    def _create_corpus(self, corpus_key: str) -> dict:
        payload = {
            "key": corpus_key,
            "name": corpus_key,
            "description": "Helpmate benchmark corpus",
        }
        return self._request(
            "/corpora",
            method="POST",
            body=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )

    @staticmethod
    def _multipart_body(file_path: Path) -> tuple[bytes, str]:
        boundary = f"----HelpmateBoundary{uuid.uuid4().hex}"
        content_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
        parts = [
            f"--{boundary}\r\n".encode(),
            f'Content-Disposition: form-data; name="file"; filename="{file_path.name}"\r\n'.encode(),
            f"Content-Type: {content_type}\r\n\r\n".encode(),
            file_path.read_bytes(),
            b"\r\n",
            f"--{boundary}--\r\n".encode(),
        ]
        return b"".join(parts), boundary

    def _upload_file(self, corpus_key: str, file_path: Path) -> dict:
        body, boundary = self._multipart_body(file_path)
        return self._request(
            f"/corpora/{corpus_key}/upload_file",
            method="POST",
            body=body,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
            timeout=300,
        )

    def get_or_create_corpus(self, document_path: str | Path) -> str:
        root = Path(__file__).resolve().parents[2]
        doc_path = Path(document_path)
        fingerprint = self._fingerprint(doc_path)
        registry = self._load_registry(root)
        existing = registry.get(fingerprint)
        if existing:
            return existing["corpus_key"]

        corpus_key = f"helpmate-{doc_path.stem.lower()}-{fingerprint[:8]}"
        self._create_corpus(corpus_key)
        self._upload_file(corpus_key, doc_path)
        registry[fingerprint] = {
            "corpus_key": corpus_key,
            "document_path": str(doc_path),
        }
        self._save_registry(root, registry)
        return corpus_key

    def benchmark(self, dataset_path: str | Path, document_path: str | Path) -> dict:
        dataset = json.loads(Path(dataset_path).read_text(encoding="utf-8"))
        if not self.available:
            return {
                "available": False,
                "reason": "VECTARA_API_KEY is not configured.",
                "dataset_size": len(dataset),
            }

        corpus_key = self.get_or_create_corpus(document_path)
        results = []
        for item in dataset:
            payload = {
                "query": item["question"],
                "search": {"limit": 5},
                "stream_response": False,
            }
            response = self._request(
                f"/corpora/{corpus_key}/query",
                method="POST",
                body=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
            )
            outputs = response.get("search_results", [])
            snippets = [str(output.get("text", ""))[:400] for output in outputs]
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
            "available": True,
            "dataset_size": len(dataset),
            "corpus_key": corpus_key,
            "snippet_fragment_match_rate": match_rate,
            "results": results,
        }

