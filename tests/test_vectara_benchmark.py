from pathlib import Path

from src.evals.vectara_benchmark import VectaraBenchmark


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
