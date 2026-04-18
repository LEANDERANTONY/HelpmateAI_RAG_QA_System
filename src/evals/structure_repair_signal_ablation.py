from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from src.config import get_settings
from src.evals.evidence_selector_weight_sweep import ROOT
from src.ingest import ingest_document
from src.sections import build_sections
from src.sections.repair import _DEFAULT_CONFIDENCE_PENALTIES, StructureRepairService


REPORTS_DIR = ROOT / "docs" / "evals" / "reports"
TARGET_DOCUMENTS = {
    "health": {
        "path": ROOT / "static" / "sample_files" / "HealthInsurance_Policy.pdf",
        "expected_should_repair": False,
    },
    "thesis": {
        "path": ROOT / "static" / "sample_files" / "Final_Thesis_Leander_Antony_A.pdf",
        "expected_should_repair": False,
    },
    "reportgeneration": {
        "path": ROOT / "static" / "sample_files" / "reportgeneration.pdf",
        "expected_should_repair": True,
    },
    "reportgeneration2": {
        "path": ROOT / "static" / "sample_files" / "reportgeneration2.pdf",
        "expected_should_repair": True,
    },
}


def _variant_overrides() -> dict[str, dict[str, float]]:
    variants = {"baseline": {}}
    for signal_name in _DEFAULT_CONFIDENCE_PENALTIES:
        variants[f"no_{signal_name}"] = {signal_name: 0.0}
    return variants


def run_ablation() -> dict[str, Any]:
    settings = get_settings()
    service = StructureRepairService(settings)
    payload: dict[str, Any] = {}

    for variant_name, overrides in _variant_overrides().items():
        per_document: dict[str, Any] = {}
        false_positives = 0
        false_negatives = 0

        for label, doc_info in TARGET_DOCUMENTS.items():
            document = ingest_document(doc_info["path"])
            sections = build_sections(document)
            decision = service.assess(
                document,
                sections,
                threshold=settings.structure_repair_confidence_threshold,
                penalty_overrides=overrides,
            )
            expected = bool(doc_info["expected_should_repair"])
            false_positives += int(decision.should_repair and not expected)
            false_negatives += int((not decision.should_repair) and expected)
            per_document[label] = {
                "document_path": str(doc_info["path"]),
                "page_count": document.page_count,
                "section_count": len(sections),
                "expected_should_repair": expected,
                "confidence": decision.confidence,
                "should_repair": decision.should_repair,
                "reasons": list(decision.reasons),
            }

        payload[variant_name] = {
            "penalty_overrides": overrides,
            "false_positives": false_positives,
            "false_negatives": false_negatives,
            "per_document": per_document,
        }

    ordered = sorted(
        payload.items(),
        key=lambda item: (
            item[1]["false_positives"] + item[1]["false_negatives"],
            item[1]["false_positives"],
            item[1]["false_negatives"],
        ),
    )
    return {
        "variant_order": [name for name, _ in ordered],
        "variants": payload,
    }


def _save_report(payload: dict[str, Any]) -> Path:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = REPORTS_DIR / f"structure_repair_signal_ablation_{timestamp}.json"
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description="Ablate deterministic structure-repair confidence penalties.")
    parser.parse_args()
    payload = run_ablation()
    report_path = _save_report(payload)
    best = payload["variant_order"][0]
    print(
        json.dumps(
            {
                "report_path": str(report_path),
                "best_variant": best,
                "best_summary": payload["variants"][best],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
