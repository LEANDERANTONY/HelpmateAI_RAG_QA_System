from __future__ import annotations

import json
from pathlib import Path


def get_latest_benchmark_report() -> tuple[dict | None, Path | None]:
    reports_dir = Path(__file__).resolve().parents[2] / "docs" / "evals" / "reports"
    if not reports_dir.exists():
        return None, None
    candidates = sorted(
        reports_dir.glob("benchmark_comparison_*.json"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    if not candidates:
        return None, None
    latest = candidates[0]
    return json.loads(latest.read_text(encoding="utf-8")), latest
