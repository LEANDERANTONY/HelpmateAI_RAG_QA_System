from src.evals.ragas_eval import _safe_mean


def test_safe_mean_ignores_non_numeric_values() -> None:
    assert _safe_mean([1.0, "x", 3, None]) == 2.0


def test_safe_mean_returns_none_for_empty_numeric_values() -> None:
    assert _safe_mean(["x", None]) is None
