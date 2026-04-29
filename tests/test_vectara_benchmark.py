from pathlib import Path

import pytest

from src.evals.vectara_benchmark import VectaraBenchmark, get_vectara_search_profile


def test_vectara_fingerprint_is_stable(tmp_path: Path) -> None:
    file_path = tmp_path / "sample.txt"
    file_path.write_text("hello world", encoding="utf-8")

    assert VectaraBenchmark._fingerprint(file_path) == VectaraBenchmark._fingerprint(file_path)


def test_vectara_multipart_body_contains_filename(tmp_path: Path) -> None:
    file_path = tmp_path / "sample.txt"
    file_path.write_text("hello world", encoding="utf-8")

    body, boundary = VectaraBenchmark._multipart_body(file_path)

    assert boundary in body.decode("utf-8", errors="ignore")
    assert 'filename="sample.txt"' in body.decode("utf-8", errors="ignore")


def test_vectara_hybrid_rerank_profile_payload() -> None:
    profile = get_vectara_search_profile("hybrid_rerank")
    payload = profile.search_payload(5)

    assert payload["limit"] == 20
    assert payload["lexical_interpolation"] == pytest.approx(0.025)
    assert payload["reranker"]["type"] == "customer_reranker"
    assert payload["reranker"]["reranker_name"] == "Rerank_Multilingual_v1"
    assert payload["reranker"]["limit"] == 5


def test_vectara_baseline_profile_payload() -> None:
    profile = get_vectara_search_profile("baseline")

    assert profile.search_payload(5) == {"limit": 5}
