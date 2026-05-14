"""Smoke tests for backend.nightly_eval.

The full nightly script invokes the eval suite and burns OpenAI tokens,
so the unit suite only covers the parts that can be exercised without
network: argument parsing, baseline loading, regression detection,
metric extraction from canned payloads, and the --dry-run wiring.

The cron line itself is documented in docs/deployment.md and is
verified by humans on the VPS, not in this test file.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend import nightly_eval


# ─── baseline loading ──────────────────────────────────────────────────


def test_load_baselines_returns_empty_dict_when_path_is_none():
    assert nightly_eval._load_baselines(None) == {}


def test_load_baselines_returns_empty_dict_when_file_missing(tmp_path):
    assert nightly_eval._load_baselines(tmp_path / "does-not-exist.json") == {}


def test_load_baselines_parses_floats(tmp_path):
    path = tmp_path / "baselines.json"
    path.write_text(
        json.dumps(
            {
                "ragas_faithfulness": 0.91,
                "ragas_answer_relevancy": 0.87,
                "garbage": "not-a-number",
            }
        ),
        encoding="utf-8",
    )
    baselines = nightly_eval._load_baselines(path)
    assert baselines["ragas_faithfulness"] == pytest.approx(0.91)
    assert baselines["ragas_answer_relevancy"] == pytest.approx(0.87)
    # Non-numeric values are silently dropped — we never want a bad
    # baselines file to crash the cron job.
    assert "garbage" not in baselines


def test_load_baselines_returns_empty_dict_for_unparseable_json(tmp_path):
    path = tmp_path / "broken.json"
    path.write_text("not valid json {{{", encoding="utf-8")
    assert nightly_eval._load_baselines(path) == {}


# ─── metric extraction ────────────────────────────────────────────────


def test_extract_ragas_metrics_pulls_summary_means():
    payload = {
        "summary": {
            "faithfulness": 0.88,
            "answer_relevancy": 0.92,
            "context_precision": 0.71,  # not tracked, should be skipped
        }
    }
    metrics = nightly_eval._extract_ragas_metrics(payload)
    assert metrics == {
        "ragas_faithfulness": pytest.approx(0.88),
        "ragas_answer_relevancy": pytest.approx(0.92),
    }


def test_extract_ragas_metrics_handles_missing_summary():
    assert nightly_eval._extract_ragas_metrics({}) == {}
    assert nightly_eval._extract_ragas_metrics({"available": False}) == {}


def test_extract_final_eval_metrics_reads_helpmate_row():
    payload = {
        "summary": {
            "overall": {
                "helpmate": {
                    "supported_rate": 0.74,
                    "answered_rate": 0.82,
                    "ragas_all": {
                        "faithfulness": 0.93,
                        "answer_relevancy": 0.86,
                    },
                },
                "vectara": {"supported_rate": 0.61},
            }
        }
    }
    metrics = nightly_eval._extract_final_eval_metrics(payload)
    assert metrics["final_eval_supported_rate"] == pytest.approx(0.74)
    assert metrics["final_eval_answered_rate"] == pytest.approx(0.82)
    assert metrics["ragas_faithfulness"] == pytest.approx(0.93)
    assert metrics["ragas_answer_relevancy"] == pytest.approx(0.86)


def test_extract_financebench_metrics_reads_suite_or_root():
    direct = {"summary": {"overall": {"helpmate": {"supported_rate": 0.55}}}}
    nested = {"suite": direct}
    assert nightly_eval._extract_financebench_metrics(direct) == {
        "financebench_supported_rate": pytest.approx(0.55)
    }
    assert nightly_eval._extract_financebench_metrics(nested) == {
        "financebench_supported_rate": pytest.approx(0.55)
    }


# ─── regression detection ─────────────────────────────────────────────


def test_detect_regressions_flags_drop_past_threshold():
    metrics = {
        "ragas_faithfulness": 0.85,  # baseline 0.95 → ~10.5% drop → flagged
        "ragas_answer_relevancy": 0.93,  # baseline 0.95 → ~2.1% drop → safe
    }
    baselines = {"ragas_faithfulness": 0.95, "ragas_answer_relevancy": 0.95}
    regressions = nightly_eval._detect_regressions(metrics, baselines, 5.0)
    assert len(regressions) == 1
    assert regressions[0]["metric"] == "ragas_faithfulness"
    assert regressions[0]["drop_pct"] > 5.0


def test_detect_regressions_ignores_improvements():
    metrics = {"ragas_faithfulness": 0.99}
    baselines = {"ragas_faithfulness": 0.90}
    assert nightly_eval._detect_regressions(metrics, baselines, 5.0) == []


def test_detect_regressions_skips_metrics_without_baseline():
    metrics = {"ragas_faithfulness": 0.5}
    assert nightly_eval._detect_regressions(metrics, {}, 5.0) == []


def test_detect_regressions_ignores_missing_current_metric():
    baselines = {"ragas_faithfulness": 0.95}
    # Current metrics dict is empty — caller might have a step error.
    assert nightly_eval._detect_regressions({}, baselines, 5.0) == []


def test_detect_regressions_skips_zero_baseline():
    """A zero baseline would divide by zero. We treat it as "no baseline"
    rather than crash the cron run."""
    metrics = {"ragas_faithfulness": 0.5}
    baselines = {"ragas_faithfulness": 0.0}
    assert nightly_eval._detect_regressions(metrics, baselines, 5.0) == []


# ─── dry-run end-to-end ───────────────────────────────────────────────


def test_dry_run_writes_summary_without_invoking_steps(tmp_path):
    output_path = tmp_path / "summary.json"
    result = nightly_eval.run_nightly_eval(
        output_path=output_path,
        baselines_path=None,
        threshold_pct=5.0,
        dry_run=True,
    )

    assert output_path.exists()
    # Each step's payload is the dry-run sentinel — proves we didn't
    # actually call into the real eval modules.
    for step in ("ragas", "financebench", "final_eval"):
        assert result.results[step] == {"dry_run": True}

    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["regression_detected"] is False
    assert payload["thresholds_pct"] == 5.0
    assert "started_at" in payload
    assert "ended_at" in payload


def test_main_dry_run_exits_zero(tmp_path):
    output_path = tmp_path / "summary.json"
    exit_code = nightly_eval.main(
        [
            "--dry-run",
            "--output",
            str(output_path),
        ]
    )
    assert exit_code == 0
    assert output_path.exists()


def test_main_check_thresholds_exits_nonzero_on_regression(tmp_path):
    """End-to-end: a dry run with baselines plus pre-seeded metrics
    should NOT trigger an exit-nonzero because dry run sets no metrics.
    This guards against a regression where the threshold check fires
    on absent metrics (the empty-dict case)."""
    baselines_path = tmp_path / "baselines.json"
    baselines_path.write_text(json.dumps({"ragas_faithfulness": 0.95}), encoding="utf-8")

    output_path = tmp_path / "summary.json"
    exit_code = nightly_eval.main(
        [
            "--dry-run",
            "--check-thresholds",
            "--baselines",
            str(baselines_path),
            "--output",
            str(output_path),
        ]
    )
    # No metrics → no regressions even with --check-thresholds, exit 0.
    assert exit_code == 0


def test_main_check_thresholds_returns_2_when_regression_detected(monkeypatch, tmp_path):
    """Patch the runner to inject metrics, then verify the threshold
    flag converts that into a nonzero exit so cron mails the operator.
    """
    output_path = tmp_path / "summary.json"
    baselines_path = tmp_path / "baselines.json"
    baselines_path.write_text(json.dumps({"ragas_faithfulness": 0.95}), encoding="utf-8")

    real_runner = nightly_eval.run_nightly_eval

    def _patched_runner(**kwargs):
        # Invoke the real runner so output_path side-effects still happen,
        # then mutate the result to simulate a regression.
        result = real_runner(**kwargs)
        result.metrics["ragas_faithfulness"] = 0.80
        result.regressions = nightly_eval._detect_regressions(
            result.metrics, result.baselines, result.thresholds_pct
        )
        result.regression_detected = bool(result.regressions)
        return result

    monkeypatch.setattr(nightly_eval, "run_nightly_eval", _patched_runner)
    exit_code = nightly_eval.main(
        [
            "--dry-run",
            "--check-thresholds",
            "--baselines",
            str(baselines_path),
            "--output",
            str(output_path),
        ]
    )
    assert exit_code == 2


def test_tracked_metrics_list_is_stable():
    """Brief locks in four metrics. If a future refactor narrows or
    widens this list, baseline files on the VPS need updating too.
    Pin the contents so the change has to come through a code review.
    """
    assert nightly_eval.TRACKED_METRICS == (
        "ragas_faithfulness",
        "ragas_answer_relevancy",
        "financebench_supported_rate",
        "final_eval_supported_rate",
    )
