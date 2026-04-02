from src.evals.vectara_eval import _safe_mean


def test_vectara_eval_safe_mean_handles_values() -> None:
    assert _safe_mean([0.5, 1.0]) == 0.75


def test_vectara_eval_safe_mean_none_for_empty() -> None:
    assert _safe_mean([]) is None
