"""Run the final Layer 1 patch/reopen/resave matrix against local Illustrator."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from py_ai_illustrator import apply_edit, run_illustrator_modern_roundtrip_test
from py_ai_illustrator.illustrator import run_illustrator_roundtrip_test

ROOT = Path(__file__).resolve().parents[1]


def _request(*operations: dict[str, object]) -> dict[str, object]:
    return {"schema_version": 1, "operations": list(operations)}


def _illustrator_summary(report: dict[str, object]) -> dict[str, object]:
    summary = {
        key: report.get(key)
        for key in (
            "status",
            "illustrator_version",
            "checks",
            "advisory_checks",
            "compatibility_notes",
            "normalization_policy",
        )
        if key in report
    }
    visual = report.get("visual_diff")
    if isinstance(visual, dict):
        summary["visual_diff"] = {
            "equal": visual.get("equal"),
            "changed_pixels": visual.get("changed_pixels"),
            "pages": [
                {
                    "index": page.get("index"),
                    "changed_pixels": page.get("changed_pixels"),
                    "changed_ratio": page.get("changed_ratio"),
                    "changed_bounds": page.get("changed_bounds"),
                }
                for page in visual.get("pages", [])
                if isinstance(page, dict)
            ],
        }
    return summary


def main() -> int:
    cases = (
        (
            "modern-paint-translate-text-batch",
            ROOT / "examples/styled-table.native.ai",
            _request(
                {
                    "op": "set_fill",
                    "selector": {
                        "type": "path",
                        "id": "subscription-comparison.background.header",
                    },
                    "color": {"red": 1.0, "green": 0.0, "blue": 0.0},
                },
                {
                    "op": "translate",
                    "selector": {
                        "type": "path",
                        "id": "subscription-comparison.background.header",
                    },
                    "dx": 2.0,
                    "dy": 3.0,
                },
                {
                    "op": "replace_text",
                    "selector": {
                        "type": "text",
                        "id": "subscription-comparison.header.plan",
                    },
                    "text": "Offer",
                },
            ),
            "modern",
        ),
        (
            "modern-bezier-stroke",
            ROOT / "examples/cmyk-curve.native.ai",
            _request(
                {
                    "op": "set_stroke",
                    "selector": {"type": "path", "id": "cmyk-curve"},
                    "color": {"red": 1.0, "green": 0.0, "blue": 0.0},
                }
            ),
            "modern",
        ),
        (
            "legacy-paint-translate-batch",
            ROOT / "examples/rectangle.ai",
            _request(
                {
                    "op": "set_fill",
                    "selector": {"type": "path", "id": "orange-rectangle"},
                    "color": {"red": 0.0, "green": 0.6, "blue": 0.2},
                },
                {
                    "op": "set_stroke",
                    "selector": {"type": "path", "id": "orange-rectangle"},
                    "color": {"red": 0.1, "green": 0.2, "blue": 0.3},
                },
                {
                    "op": "translate",
                    "selector": {"type": "path", "id": "orange-rectangle"},
                    "dx": 4.0,
                    "dy": -3.0,
                },
            ),
            "legacy",
        ),
    )
    reports: list[dict[str, object]] = []
    with tempfile.TemporaryDirectory(prefix="py-ai-layer1-illustrator-") as directory:
        root = Path(directory)
        for name, source, request, kind in cases:
            patched = root / f"{name}.ai"
            apply_report = apply_edit(source, request, patched)
            if apply_report.get("applied") is not True:
                reports.append(
                    {
                        "case": name,
                        "status": "failed",
                        "stage": "python-apply",
                        "apply": apply_report,
                    }
                )
                continue
            illustrator_report = (
                run_illustrator_modern_roundtrip_test(patched)
                if kind == "modern"
                else run_illustrator_roundtrip_test(patched)
            )
            reports.append(
                {
                    "case": name,
                    "status": illustrator_report.get("status"),
                    "python_validation": apply_report.get("validation"),
                    "illustrator": _illustrator_summary(illustrator_report),
                }
            )
    passed = all(report.get("status") == "passed" for report in reports)
    print(
        json.dumps(
            {
                "profile": "layer1-illustrator-final-matrix-v1",
                "status": "passed" if passed else "failed",
                "case_count": len(reports),
                "cases": reports,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
