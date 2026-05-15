"""Nightly evaluation runner for HelpmateAI.

Runs the existing eval suite (RAGAS + FinanceBench + the combined final
eval) and writes a single structured JSON summary so a cron job on the
VPS can stash the result for trend tracking and alerting.

Designed to be invoked as:

    docker exec helpmate-api python -m backend.nightly_eval

A cron entry mirroring the maintenance sweeper is documented in
docs/deployment.md (under "Nightly evaluation cron"). The brief paired
this script with the published cron line — the script ships in this
module, the cron line lives in docs because the agent doesn't have SSH
access to the VPS.

Output JSON shape (written to --output and also dumped to stdout):

    {
      "started_at": "2026-05-15T03:30:00+00:00",
      "ended_at":   "2026-05-15T03:42:18+00:00",
      "duration_seconds": 738,
      "baselines": { ... echoed from the input baselines file ... },
      "thresholds_pct": 5.0,
      "results": {
        "ragas": { ...summary... },
        "financebench": { ...summary... },
        "final_eval": { ...summary... }
      },
      "metrics": {
        "ragas_faithfulness": 0.91,
        "ragas_answer_relevancy": 0.87,
        "financebench_supported_rate": 0.62,
        "final_eval_supported_rate": 0.74
      },
      "regressions": [
        {"metric": "ragas_faithfulness", "baseline": 0.93, "current": 0.85, "drop_pct": 8.6},
        ...
      ],
      "regression_detected": false
    }

When --check-thresholds is supplied AND any tracked metric drops by
more than --regression-threshold-pct relative to the baseline, the
process exits non-zero so cron's mail-on-error path fires. The
regression rows are still written to the JSON summary so the operator
can see what regressed without re-running the eval.

Alerting hook: we deliberately keep this simple — a regression just
logs at WARNING and exits non-zero. A future iteration could write to
a `nightly_eval_alerts` table in Supabase, but for now the cron-mail
loop + the JSON summary on disk are enough to spot drift without
adding a new table and RLS policies to maintain.

The --dry-run flag short-circuits the actual eval invocations — used
by tests and for smoke-checking the wiring on a fresh VPS without
spending model tokens. Dry runs still write the JSON file so the cron
plumbing can be verified end-to-end.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


logger = logging.getLogger(__name__)


# Metrics we track for regression detection. Keep this list focused —
# adding too many will produce noisy alerts. These four cover the
# faithfulness/relevance axes (RAGAS) plus the two abstention-quality
# axes (supported_rate on the final eval + financebench).
TRACKED_METRICS: tuple[str, ...] = (
    "ragas_faithfulness",
    "ragas_answer_relevancy",
    "financebench_supported_rate",
    "final_eval_supported_rate",
)


@dataclass
class NightlyEvalResult:
    started_at: str
    ended_at: str = ""
    duration_seconds: float = 0.0
    baselines: dict[str, float] = field(default_factory=dict)
    thresholds_pct: float = 5.0
    results: dict[str, Any] = field(default_factory=dict)
    metrics: dict[str, float | None] = field(default_factory=dict)
    regressions: list[dict[str, Any]] = field(default_factory=list)
    regression_detected: bool = False
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "duration_seconds": self.duration_seconds,
            "baselines": dict(self.baselines),
            "thresholds_pct": self.thresholds_pct,
            "results": self.results,
            "metrics": self.metrics,
            "regressions": list(self.regressions),
            "regression_detected": self.regression_detected,
            "errors": list(self.errors),
        }


def _load_baselines(path: Path | None) -> dict[str, float]:
    """Read baseline metric values from a JSON file. Missing file or
    a parse error returns an empty dict — regression checks then skip
    the metric instead of crashing the cron job."""
    if path is None:
        return {}
    if not path.exists():
        logger.warning("nightly_eval: baseline file does not exist (%s)", path)
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("nightly_eval: failed to read baselines (%s): %s", path, exc)
        return {}
    cleaned: dict[str, float] = {}
    for key, value in raw.items():
        try:
            cleaned[str(key)] = float(value)
        except (TypeError, ValueError):
            continue
    return cleaned


def _extract_ragas_metrics(payload: dict[str, Any]) -> dict[str, float]:
    """Pull faithfulness + answer_relevancy out of the RAGAS payload
    shape produced by `src.evals.ragas_eval.RagasEvaluator.evaluate`.
    The payload's `summary` key holds the mean per metric — if it's
    missing (e.g. when RAGAS is disabled) we return an empty dict and
    the regression check just skips those metrics."""
    summary = payload.get("summary") or {}
    metrics: dict[str, float] = {}
    for metric in ("faithfulness", "answer_relevancy"):
        value = summary.get(metric)
        if isinstance(value, (int, float)):
            metrics[f"ragas_{metric}"] = float(value)
    return metrics


def _extract_final_eval_metrics(payload: dict[str, Any]) -> dict[str, float]:
    """Pull the helpmate row from the combined eval suite summary.
    `summary.overall.helpmate.supported_rate` is the headline number
    we track for tier-margin validation alongside the RAGAS scores."""
    summary = (payload.get("summary") or {}).get("overall") or {}
    helpmate = summary.get("helpmate") or {}
    metrics: dict[str, float] = {}
    supported_rate = helpmate.get("supported_rate")
    if isinstance(supported_rate, (int, float)):
        metrics["final_eval_supported_rate"] = float(supported_rate)
    answered_rate = helpmate.get("answered_rate")
    if isinstance(answered_rate, (int, float)):
        metrics["final_eval_answered_rate"] = float(answered_rate)
    ragas_all = helpmate.get("ragas_all") or {}
    for metric in ("faithfulness", "answer_relevancy"):
        value = ragas_all.get(metric)
        if isinstance(value, (int, float)):
            # Prefer the final_eval RAGAS numbers when both exist — they're
            # measured under the same context budget as the rest of the suite.
            metrics[f"ragas_{metric}"] = float(value)
    return metrics


def _extract_financebench_metrics(payload: dict[str, Any]) -> dict[str, float]:
    """FinanceBench wraps the final_eval result in a `suite` key when
    --run-suite is set. We dig into the helpmate row the same way."""
    suite_payload = payload.get("suite") or payload
    summary = (suite_payload.get("summary") or {}).get("overall") or {}
    helpmate = summary.get("helpmate") or {}
    metrics: dict[str, float] = {}
    supported_rate = helpmate.get("supported_rate")
    if isinstance(supported_rate, (int, float)):
        metrics["financebench_supported_rate"] = float(supported_rate)
    return metrics


def _detect_regressions(
    metrics: dict[str, float | None],
    baselines: dict[str, float],
    threshold_pct: float,
) -> list[dict[str, Any]]:
    """Compare current vs. baseline for each tracked metric. A regression
    fires when current is at least `threshold_pct` percent *below* the
    baseline. We deliberately don't fire on improvements — the cron
    mail is for "something broke" alerts only."""
    regressions: list[dict[str, Any]] = []
    for metric in TRACKED_METRICS:
        baseline = baselines.get(metric)
        current = metrics.get(metric)
        if baseline is None or current is None:
            continue
        if baseline <= 0:
            continue
        drop_pct = (baseline - current) / baseline * 100.0
        if drop_pct >= threshold_pct:
            regressions.append(
                {
                    "metric": metric,
                    "baseline": float(baseline),
                    "current": float(current),
                    "drop_pct": round(drop_pct, 2),
                }
            )
    return regressions


def _run_step(
    name: str,
    fn: Callable[[], dict[str, Any]],
    *,
    dry_run: bool,
    result: NightlyEvalResult,
) -> dict[str, Any]:
    """Wrap a step in try/except so a single benchmark failing doesn't
    kill the whole nightly run. The error is captured into result.errors
    and the failed step's payload is replaced with a stub so downstream
    metric extraction degrades gracefully (it just won't find the keys
    and the regression check will skip that metric)."""
    if dry_run:
        logger.info("nightly_eval: dry-run skipping %s", name)
        return {"dry_run": True}
    try:
        logger.info("nightly_eval: starting %s", name)
        payload = fn()
        logger.info("nightly_eval: finished %s", name)
        return payload
    except Exception as exc:  # noqa: BLE001 — we genuinely want to swallow here
        logger.exception("nightly_eval: %s failed", name)
        result.errors.append(f"{name}: {exc.__class__.__name__}: {exc}")
        return {"error": str(exc), "error_type": exc.__class__.__name__}


def run_nightly_eval(
    *,
    output_path: Path,
    baselines_path: Path | None = None,
    threshold_pct: float = 5.0,
    dry_run: bool = False,
    skip_ragas: bool = False,
    skip_financebench: bool = False,
    skip_final_eval: bool = False,
    final_eval_manifest: str = "docs/evals/final_eval_manifest.example.json",
    ragas_dataset: str = "docs/evals/retrieval_eval_dataset.json",
    ragas_document: str = "",
    financebench_max_questions: int | None = 25,
) -> NightlyEvalResult:
    """Run the configured eval steps, extract tracked metrics, and
    write a structured JSON summary. Returns the result so callers
    can decide on exit-code semantics; the CLI maps regressions to a
    non-zero exit code so cron mails the operator on drift."""
    started = datetime.now(timezone.utc)
    result = NightlyEvalResult(
        started_at=started.isoformat(),
        thresholds_pct=threshold_pct,
        baselines=_load_baselines(baselines_path),
    )

    # The eval modules import heavy deps (ragas, openai). In a dry run we
    # skip the imports entirely — that's the whole point: a fresh
    # container can validate the cron plumbing without installing the
    # eval extras.
    if not dry_run:
        # Local imports so a dry-run doesn't pay the import cost of ragas,
        # which is slow and chains in additional dependencies.
        from src.evals.financebench_eval import build_financebench_manifest
        from src.evals.final_eval_suite import run_final_eval_suite
        from src.evals.ragas_eval import RagasEvaluator
        from src.config import get_settings

    if not skip_ragas:
        def _ragas_step() -> dict[str, Any]:
            settings = get_settings()  # noqa: F821 — only resolved when not dry_run
            evaluator = RagasEvaluator(settings)  # noqa: F821
            return evaluator.evaluate(ragas_dataset, ragas_document) if ragas_document else {
                "available": evaluator.available,
                "skipped": True,
                "reason": "ragas_document not provided; pass --ragas-document to enable.",
            }

        result.results["ragas"] = _run_step("ragas", _ragas_step, dry_run=dry_run, result=result)

    if not skip_financebench:
        def _financebench_step() -> dict[str, Any]:
            # The financebench evaluator downloads PDFs the first time it
            # runs — we cache them under the project's static dir so
            # subsequent nightly runs are fast.
            root = Path(__file__).resolve().parent.parent
            manifest_path = root / "docs" / "evals" / "financebench_manifest.json"
            data_dir = root / "docs" / "evals" / "financebench"
            pdf_dir = root / "static" / "financebench"
            build_financebench_manifest(  # noqa: F821
                data_dir=data_dir,
                pdf_dir=pdf_dir,
                output_path=manifest_path,
                max_questions=financebench_max_questions,
                download_pdfs=True,
            )
            return run_final_eval_suite(  # noqa: F821
                manifest_path,
                systems=("helpmate",),
                max_questions=financebench_max_questions,
                skip_ragas=False,
            )

        result.results["financebench"] = _run_step(
            "financebench", _financebench_step, dry_run=dry_run, result=result
        )

    if not skip_final_eval:
        def _final_eval_step() -> dict[str, Any]:
            return run_final_eval_suite(  # noqa: F821
                final_eval_manifest,
                systems=("helpmate",),
                skip_ragas=False,
            )

        result.results["final_eval"] = _run_step(
            "final_eval", _final_eval_step, dry_run=dry_run, result=result
        )

    # Extract the metrics we track for regression detection. If a step
    # was skipped or errored the extractor just returns an empty dict
    # and the metric stays absent from `result.metrics` — the regression
    # check then skips that metric.
    metrics: dict[str, float | None] = {}
    if "ragas" in result.results and isinstance(result.results["ragas"], dict):
        metrics.update(_extract_ragas_metrics(result.results["ragas"]))
    if "financebench" in result.results and isinstance(result.results["financebench"], dict):
        metrics.update(_extract_financebench_metrics(result.results["financebench"]))
    if "final_eval" in result.results and isinstance(result.results["final_eval"], dict):
        # final_eval overrides ragas_* because it runs under the same
        # context budget as the rest of the suite, so the numbers are
        # apples-to-apples.
        metrics.update(_extract_final_eval_metrics(result.results["final_eval"]))

    result.metrics = metrics
    result.regressions = _detect_regressions(metrics, result.baselines, threshold_pct)
    result.regression_detected = bool(result.regressions)

    ended = datetime.now(timezone.utc)
    result.ended_at = ended.isoformat()
    result.duration_seconds = max(0.0, (ended - started).total_seconds())

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result.to_dict(), indent=2), encoding="utf-8")
    return result


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Nightly eval runner for HelpmateAI (cron-friendly).",
    )
    parser.add_argument(
        "--output",
        default="data/nightly_eval/latest.json",
        help=(
            "Where to write the structured JSON summary. Cron should send "
            "stdout/stderr to /var/log/helpmate-nightly-eval.log as well, "
            "since transient stack traces won't end up in this file."
        ),
    )
    parser.add_argument(
        "--baselines",
        default=None,
        help=(
            "Path to a JSON file with baseline metric values keyed by "
            "metric name. Missing/unreadable file disables regression "
            "detection but the script still produces a summary."
        ),
    )
    parser.add_argument(
        "--regression-threshold-pct",
        type=float,
        default=5.0,
        help="Percent drop vs. baseline that counts as a regression.",
    )
    parser.add_argument(
        "--check-thresholds",
        action="store_true",
        help=(
            "Exit non-zero when any tracked metric regresses past the "
            "threshold. Default behavior writes the summary but always "
            "exits 0 so cron mail only fires on hard process failures."
        ),
    )
    parser.add_argument("--dry-run", action="store_true", help="Skip eval invocation; write summary only.")
    parser.add_argument("--skip-ragas", action="store_true")
    parser.add_argument("--skip-financebench", action="store_true")
    parser.add_argument("--skip-final-eval", action="store_true")
    parser.add_argument(
        "--final-eval-manifest",
        default="docs/evals/final_eval_manifest.example.json",
    )
    parser.add_argument("--ragas-dataset", default="docs/evals/retrieval_eval_dataset.json")
    parser.add_argument(
        "--ragas-document",
        default="",
        help="PDF to run RAGAS over. Empty disables the RAGAS step.",
    )
    parser.add_argument(
        "--financebench-max-questions",
        type=int,
        default=25,
        help="Cap on FinanceBench questions to keep nightly runtime bounded.",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
    )
    return parser.parse_args(argv)


_CRON_MONITOR_SLUG = "helpmate-nightly-eval"


def _start_sentry_checkin() -> tuple[Any, str | None]:
    """Initialize Sentry + open a Crons check-in for the nightly run.

    Returns ``(sentry_sdk, check_in_id)`` so ``main`` can close the
    check-in with ``status="ok"`` or ``"error"`` at the end. Both
    halves of the return are ``None`` when Sentry isn't configured
    (local dev, ad-hoc invocations) — the caller treats that as a
    no-op.

    Manual-only-mode gate
    ---------------------
    The Crons heartbeat is only opened when
    ``HELPMATE_NIGHTLY_EVAL_MONITOR_ENABLED=true``. Default is OFF
    because the nightly_eval cron is **disabled on the VPS** as of
    2026-05-16 (per-run cost ~\\$1.5-3 in OpenAI calls × daily would
    burn \\$45-90/month at pre-revenue stage). Without a scheduled
    cron, Sentry's missed-heartbeat alert would fire every day —
    that's the HELPMATE-BACKEND-8 false-positive we already chased.

    When the operator re-enables the scheduled cron (see
    ``docs/deployment.md`` for the recommended Mon+Thu line), set
    ``HELPMATE_NIGHTLY_EVAL_MONITOR_ENABLED=true`` in the VPS .env so
    Sentry resumes expecting heartbeats. Until then, manual one-off
    runs (``docker exec helpmate-api python -m backend.nightly_eval``)
    skip Sentry entirely and don't fire alerts.

    Why this lives in nightly_eval rather than backend/observability:
        backend/observability bootstraps the FastAPI app's Sentry init
        from ``backend.main`` at import time. ``python -m backend.nightly_eval``
        runs as a CLI inside the container and doesn't import
        ``backend.main``, so we initialize a dedicated Sentry client
        here. Both inits point at the same DSN so the events land in
        the same project; Sentry de-dupes the SDK init internally if
        somehow both run.

    The ``monitor_config`` block matches the recommended Mon+Thu
    schedule documented in docs/deployment.md so Sentry knows when to
    expect a heartbeat once monitoring is re-enabled. A missed run
    lights up the Crons dashboard.
    """
    import os

    if os.environ.get("HELPMATE_NIGHTLY_EVAL_MONITOR_ENABLED", "").strip().lower() not in {"1", "true", "yes", "on"}:
        # Manual-only mode (current default). Skip Sentry entirely so
        # ad-hoc runs from a shell don't trigger missed-heartbeat
        # alerts on a monitor that has no schedule to keep.
        return None, None

    from src.config import get_settings

    settings = get_settings()
    if not settings.sentry_dsn:
        return None, None
    try:
        import sentry_sdk

        sentry_sdk.init(
            dsn=settings.sentry_dsn,
            environment=settings.observability_environment,
            release=settings.observability_release or settings.retrieval_version,
            traces_sample_rate=0.0,  # the CLI does no FastAPI request work
            profiles_sample_rate=0.0,
        )
        check_in_id = sentry_sdk.crons.capture_checkin(
            monitor_slug=_CRON_MONITOR_SLUG,
            status="in_progress",
            monitor_config={
                # Mon + Thu @ 3:30 UTC — matches the recommended re-enable
                # cron line in docs/deployment.md.
                "schedule": {"type": "crontab", "value": "30 3 * * 1,4"},
                "timezone": "UTC",
                "checkin_margin": 5,    # minutes — alert if late
                "max_runtime": 60,      # minutes — alert if it hangs
                "failure_issue_threshold": 1,
                "recovery_threshold": 1,
            },
        )
        return sentry_sdk, check_in_id
    except Exception as exc:
        logger.warning("sentry crons init failed (%s); continuing without monitoring.", exc)
        return None, None


def _close_sentry_checkin(sentry_sdk: Any, check_in_id: str | None, status: str) -> None:
    if sentry_sdk is None or check_in_id is None:
        return
    try:
        sentry_sdk.crons.capture_checkin(
            monitor_slug=_CRON_MONITOR_SLUG,
            check_in_id=check_in_id,
            status=status,
        )
        sentry_sdk.flush(timeout=5)
    except Exception as exc:
        logger.warning("sentry crons close failed (%s); ignoring.", exc)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    # Open a Sentry Crons check-in BEFORE the work starts so a process
    # crash inside the eval steps still gets a "missing heartbeat"
    # alert via Sentry's monitor watchdog (we'd never reach the close
    # path in that case).
    sentry_module, check_in_id = _start_sentry_checkin()
    final_status = "ok"
    exit_code = 0
    try:
        output_path = Path(args.output)
        baselines_path = Path(args.baselines) if args.baselines else None
        result = run_nightly_eval(
            output_path=output_path,
            baselines_path=baselines_path,
            threshold_pct=args.regression_threshold_pct,
            dry_run=args.dry_run,
            skip_ragas=args.skip_ragas,
            skip_financebench=args.skip_financebench,
            skip_final_eval=args.skip_final_eval,
            final_eval_manifest=args.final_eval_manifest,
            ragas_dataset=args.ragas_dataset,
            ragas_document=args.ragas_document,
            financebench_max_questions=args.financebench_max_questions,
        )

        # Emit a single JSON line on stdout so the cron log file has a
        # parseable trail without having to grep the per-day file.
        print(json.dumps(result.to_dict(), indent=2))

        if result.regression_detected:
            for regression in result.regressions:
                logger.warning(
                    "nightly_eval REGRESSION %s: baseline=%.4f current=%.4f drop=%.2f%%",
                    regression["metric"],
                    regression["baseline"],
                    regression["current"],
                    regression["drop_pct"],
                )
            if args.check_thresholds:
                final_status = "error"
                exit_code = 2

        if result.errors and args.check_thresholds:
            # Hard failure inside a step (eval threw) — only escalate to a
            # nonzero exit when the operator opted into strict checks.
            final_status = "error"
            exit_code = 3
    except Exception:
        # The work itself blew up — close the check-in as failed so
        # Sentry's Crons dashboard surfaces the incident, then re-raise
        # so the operator's cron mail still gets the traceback.
        final_status = "error"
        _close_sentry_checkin(sentry_module, check_in_id, final_status)
        raise
    _close_sentry_checkin(sentry_module, check_in_id, final_status)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
