"""Run the three-fixture direct-native production promotion gate."""

from __future__ import annotations

import argparse
import json
import runpy
from pathlib import Path
from typing import Any

from py_ai_illustrator.model import Document
from py_ai_illustrator.native import compile_native_ai

ROOT = Path(__file__).parents[1]
FIXTURES = (
    ("quarterly-kpi-report", ROOT / "examples" / "quarterly_kpi_report.py"),
    ("editorial-brochure", ROOT / "examples" / "editorial_brochure.py"),
    ("product-catalog", ROOT / "examples" / "product_catalog.py"),
)


def _build_document(script: Path) -> Document:
    namespace = runpy.run_path(str(script))
    builder = namespace.get("build_document")
    if not callable(builder):
        raise ValueError(f"Fixture script has no build_document(): {script}")
    document = builder()
    if not isinstance(document, Document):
        raise TypeError(f"Fixture script returned {type(document).__name__}, not Document")
    return document


def run_gate(
    output_directory: str | Path,
    *,
    timeout: float = 180.0,
    application_name: str = "Adobe Illustrator",
) -> dict[str, Any]:
    output_root = Path(output_directory).resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    fixture_results: dict[str, dict[str, Any]] = {}
    for name, script in FIXTURES:
        output = output_root / f"{name}.direct.ai"
        fixture_results[name] = compile_native_ai(
            _build_document(script),
            output,
            source_base=ROOT,
            timeout=timeout,
            application_name=application_name,
        )
    passed = all(result["status"] == "passed" for result in fixture_results.values())
    return {
        "status": "passed" if passed else "failed",
        "fixture_count": len(FIXTURES),
        "fixtures": fixture_results,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output_directory")
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument("--application", default="Adobe Illustrator")
    args = parser.parse_args()
    result = run_gate(
        args.output_directory,
        timeout=args.timeout,
        application_name=args.application,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
