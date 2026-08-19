"""Run the three-fixture direct-native production promotion gate."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from py_ai_illustrator.legacy import read_ai7
from py_ai_illustrator.model import Document
from py_ai_illustrator.native import compile_native_ai

ROOT = Path(__file__).parents[1]
FIXTURES = (
    ("quarterly-kpi-report", ROOT / "examples" / "quarterly-kpi-report.ai"),
    ("editorial-brochure", ROOT / "examples" / "editorial-brochure.ai"),
    ("product-catalog", ROOT / "examples" / "product-catalog.ai"),
)


def _load_fixture_document(source: Path) -> Document:
    document = read_ai7(source).document
    if not isinstance(document, Document):  # pragma: no cover - defensive API guard
        raise TypeError(f"Fixture reader returned {type(document).__name__}, not Document")
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
    for name, source in FIXTURES:
        output = output_root / f"{name}.direct.ai"
        fixture_results[name] = compile_native_ai(
            _load_fixture_document(source),
            output,
            source_base=source.parent,
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
