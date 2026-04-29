from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


load_dotenv()


@dataclass(frozen=True)
class VectaraSearchProfile:
    name: str
    request_limit: int
    return_limit: int
    lexical_interpolation: float | None = None
    reranker: dict | None = None

    def search_payload(self, limit: int) -> dict:
        request_limit = max(self.request_limit, limit)
        search: dict = {"limit": request_limit}
        if self.lexical_interpolation is not None:
            search["lexical_interpolation"] = self.lexical_interpolation
        if self.reranker:
            reranker = dict(self.reranker)
            reranker["limit"] = limit
            search["reranker"] = reranker
        return search


VECTARA_SEARCH_PROFILES: dict[str, VectaraSearchProfile] = {
    "baseline": VectaraSearchProfile(
        name="baseline",
        request_limit=5,
        return_limit=5,
    ),
    "hybrid_rerank": VectaraSearchProfile(
        name="hybrid_rerank",
        request_limit=20,
        return_limit=5,
        lexical_interpolation=0.025,
        reranker={
            "type": "customer_reranker",
            "reranker_name": "Rerank_Multilingual_v1",
        },
    ),
}


def get_vectara_search_profile(name: str | None = None) -> VectaraSearchProfile:
    profile_name = (name or os.getenv("HELPMATE_VECTARA_SEARCH_PROFILE") or "hybrid_rerank").strip().lower()
    if profile_name not in VECTARA_SEARCH_PROFILES:
        allowed = ", ".join(sorted(VECTARA_SEARCH_PROFILES))
        raise ValueError(f"Unknown Vectara search profile '{profile_name}'. Expected one of: {allowed}")
    return VECTARA_SEARCH_PROFILES[profile_name]


class VectaraBenchmark:
    def __init__(self, api_key: str | None = None, *, search_profile: str | VectaraSearchProfile | None = None) -> None:
        self.api_key = api_key or os.getenv("VECTARA_API_KEY")
        self.base_url = "https://api.vectara.io/v2"
        self.available = bool(self.api_key)
        self.search_profile = (
            search_profile
            if isinstance(search_profile, VectaraSearchProfile)
            else get_vectara_search_profile(search_profile)
        )

    @staticmethod
    def _fingerprint(path: str | Path) -> str:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()

    @staticmethod
    def _safe_key_fragment(value: str) -> str:
        cleaned = "".join(char if char.isalnum() or char in {"_", "-", "="} else "-" for char in value.lower())
        while "--" in cleaned:
            cleaned = cleaned.replace("--", "-")
        cleaned = cleaned.strip("-") or "document"
        return cleaned[:32].rstrip("-") or "document"

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

        corpus_key = f"helpmate-{self._safe_key_fragment(doc_path.stem)}-{fingerprint[:8]}"
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
            search_result = self.search(document_path, item["question"], limit=self.search_profile.return_limit)
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
            "available": True,
            "dataset_size": len(dataset),
            "corpus_key": corpus_key,
            "search_profile": self.search_profile.name,
            "search_config": self.search_profile.search_payload(self.search_profile.return_limit),
            "snippet_fragment_match_rate": match_rate,
            "results": results,
        }

    def search(self, document_path: str | Path, question: str, *, limit: int = 5) -> dict:
        if not self.available:
            raise RuntimeError("VECTARA_API_KEY is not configured.")

        corpus_key = self.get_or_create_corpus(document_path)
        payload = {
            "query": question,
            "search": self.search_profile.search_payload(limit),
            "stream_response": False,
        }
        response = self._request(
            f"/corpora/{corpus_key}/query",
            method="POST",
            body=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        outputs = response.get("search_results", [])
        results = []
        for index, output in enumerate(outputs, start=1):
            part_metadata = output.get("part_metadata", {})
            page = part_metadata.get("page")
            title = part_metadata.get("title")
            page_label = f"Vectara Result {index}"
            if page is not None:
                page_label = f"Page {page}"
            results.append(
                {
                    "text": str(output.get("text", ""))[:400],
                    "rank": index,
                    "metadata": {
                        "source": "vectara",
                        "page_label": page_label,
                        "title": title,
                    },
                }
            )
        return {
            "corpus_key": corpus_key,
            "search_profile": self.search_profile.name,
            "search_config": payload["search"],
            "results": results,
        }
