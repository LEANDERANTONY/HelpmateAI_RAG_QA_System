from src.evals.ragas_eval import _safe_mean
from src.evals.ragas_retry import call_with_ragas_retry, is_transient_ragas_error


def test_safe_mean_ignores_non_numeric_values() -> None:
    assert _safe_mean([1.0, "x", 3, None]) == 2.0


def test_safe_mean_returns_none_for_empty_numeric_values() -> None:
    assert _safe_mean(["x", None]) is None


def test_ragas_retry_retries_transient_errors() -> None:
    attempts = {"count": 0}

    def flaky_call() -> float:
        attempts["count"] += 1
        if attempts["count"] < 3:
            raise RuntimeError("503 model overloaded")
        return 0.75

    result = call_with_ragas_retry(flaky_call, initial_delay_seconds=0, jitter_seconds=0)

    assert result == 0.75
    assert attempts["count"] == 3


def test_ragas_retry_does_not_retry_deterministic_errors() -> None:
    attempts = {"count": 0}

    def bad_call() -> float:
        attempts["count"] += 1
        raise ValueError("invalid sample")

    try:
        call_with_ragas_retry(bad_call, initial_delay_seconds=0, jitter_seconds=0)
    except ValueError:
        pass

    assert attempts["count"] == 1
    assert is_transient_ragas_error(RuntimeError("rate limit exceeded")) is True
