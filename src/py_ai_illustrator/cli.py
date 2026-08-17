"""Command-line boundary shared by humans and future agent adapters."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .format import FileFormat, inspect_file
from .illustrator import (
    list_illustrator_fonts,
    materialize_native_ai,
    run_illustrator_export_test,
    run_illustrator_roundtrip_test,
    run_illustrator_test,
)
from .legacy import UnsupportedLegacyFeature, dump_ai7, read_ai7
from .model import Document


def _write_json(data: Any, destination: Path | None) -> None:
    payload = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    if destination is None:
        sys.stdout.write(payload)
    else:
        destination.write_text(payload, encoding="utf-8")


def _inspect(args: argparse.Namespace) -> int:
    report = inspect_file(args.input)
    if args.json:
        _write_json(report.to_dict(), None)
    else:
        print(f"format: {report.format.value}")
        print(f"size: {report.size_bytes} bytes")
        print(f"confidence: {report.confidence}")
        if report.illustrator_markers:
            print("markers: " + ", ".join(report.illustrator_markers))
        for note in report.notes:
            print(f"note: {note}")
    return 0


def _export(args: argparse.Namespace) -> int:
    source = Path(args.input)
    destination = Path(args.output) if args.output else None
    if args.to == "json":
        report = inspect_file(source)
        if report.format is not FileFormat.LEGACY_AI:
            raise UnsupportedLegacyFeature(
                "Phase 0 JSON export currently supports legacy Illustrator files only; "
                f"detected {report.format.value}."
            )
        result = read_ai7(source)
        if not result.safe_to_reserialize and not args.allow_partial:
            features = sorted({item.feature_name for item in result.diagnostics})
            summary = ", ".join(repr(feature) for feature in features[:5])
            raise UnsupportedLegacyFeature(
                "Refusing partial JSON export because unsupported source features would not be "
                f"represented in the IR: {summary}. Use --allow-partial after reviewing validate."
            )
        _write_json(result.document.to_dict(), destination)
        return 0

    if args.to == "ai7":
        if destination is None:
            raise ValueError("--output is required when writing binary/file output")
        data = json.loads(source.read_text(encoding="utf-8"))
        dump_ai7(Document.from_dict(data), destination, source_base=source.parent)
        return 0
    raise ValueError(f"Unsupported target: {args.to}")


def _validate(args: argparse.Namespace) -> int:
    report = inspect_file(args.input)
    errors: list[str] = []
    warnings = list(report.notes)
    compatibility: dict[str, object] | None = None
    if report.format is FileFormat.LEGACY_AI:
        try:
            parsed = read_ai7(args.input)
            compatibility = parsed.compatibility_report()
            warnings.extend(diagnostic.message for diagnostic in parsed.diagnostics)
            if not parsed.document.layers:
                warnings.append("No layers were parsed by the Phase 0 reader.")
        except (ValueError, UnicodeError) as error:
            errors.append(str(error))
    elif report.format is FileFormat.PDF_COMPATIBLE_AI:
        warnings.append(
            "Modern AI semantic parsing is not implemented yet; container only checked."
        )
    else:
        errors.append(f"Unsupported input format: {report.format.value}")

    result = {
        "valid": not errors,
        "safe_to_reserialize": bool(
            not errors
            and compatibility is not None
            and compatibility["safe_to_reserialize"]
        ),
        "classification": (
            compatibility["classification"]
            if compatibility is not None and not errors
            else "unconvertible"
        ),
        "format": report.format.value,
        "errors": errors,
        "warnings": warnings,
        "compatibility": compatibility,
    }
    _write_json(result, None)
    return 0 if result["safe_to_reserialize"] else 1


def _test_illustrator(args: argparse.Namespace) -> int:
    result = run_illustrator_test(
        args.input,
        timeout=args.timeout,
        application_name=args.application,
    )
    destination = Path(args.output) if args.output else None
    _write_json(result, destination)
    return 0 if result["status"] == "passed" else 1


def _materialize_native(args: argparse.Namespace) -> int:
    result = materialize_native_ai(
        args.input,
        args.output,
        timeout=args.timeout,
        application_name=args.application,
    )
    _write_json(result, None)
    return 0 if result["status"] == "passed" else 1


def _illustrator_fonts(args: argparse.Namespace) -> int:
    result = list_illustrator_fonts(
        query=args.query,
        required=tuple(args.require),
        timeout=args.timeout,
        application_name=args.application,
    )
    destination = Path(args.output) if args.output else None
    _write_json(result, destination)
    return 0 if result["status"] == "passed" else 1


def _test_illustrator_export(args: argparse.Namespace) -> int:
    result = run_illustrator_export_test(
        fixture=args.fixture,
        output=args.ai_output,
        timeout=args.timeout,
        application_name=args.application,
    )
    destination = Path(args.output) if args.output else None
    _write_json(result, destination)
    return 0 if result["status"] == "passed" else 1


def _test_illustrator_roundtrip(args: argparse.Namespace) -> int:
    result = run_illustrator_roundtrip_test(
        args.input,
        output=args.ai_output,
        timeout=args.timeout,
        application_name=args.application,
    )
    destination = Path(args.output) if args.output else None
    _write_json(result, destination)
    return 0 if result["status"] == "passed" else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="py-ai", description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    inspect_parser = subparsers.add_parser("inspect", help="detect the file container")
    inspect_parser.add_argument("input")
    inspect_parser.add_argument("--json", action="store_true")
    inspect_parser.set_defaults(handler=_inspect)

    export_parser = subparsers.add_parser("export", help="translate between AI and the JSON IR")
    export_parser.add_argument("input")
    export_parser.add_argument("--to", choices=("json", "ai7"), required=True)
    export_parser.add_argument("-o", "--output")
    export_parser.add_argument(
        "--allow-partial",
        action="store_true",
        help="allow JSON export that omits diagnosed unsupported source features",
    )
    export_parser.set_defaults(handler=_export)

    validate_parser = subparsers.add_parser("validate", help="run available structural checks")
    validate_parser.add_argument("input")
    validate_parser.set_defaults(handler=_validate)

    illustrator_parser = subparsers.add_parser(
        "test-illustrator",
        help="open a temporary copy in Illustrator and inspect the imported structure",
    )
    illustrator_parser.add_argument("input")
    illustrator_parser.add_argument("--timeout", type=float, default=90.0)
    illustrator_parser.add_argument("--application", default="Adobe Illustrator")
    illustrator_parser.add_argument("-o", "--output")
    illustrator_parser.set_defaults(handler=_test_illustrator)

    native_parser = subparsers.add_parser(
        "materialize-native",
        help="convert legacy AI text to native editable text and save a modern AI",
    )
    native_parser.add_argument("input")
    native_parser.add_argument("-o", "--output", required=True)
    native_parser.add_argument("--timeout", type=float, default=90.0)
    native_parser.add_argument("--application", default="Adobe Illustrator")
    native_parser.set_defaults(handler=_materialize_native)

    fonts_parser = subparsers.add_parser(
        "illustrator-fonts",
        help="list installed Illustrator fonts and validate PostScript names",
    )
    fonts_parser.add_argument("--query")
    fonts_parser.add_argument("--require", action="append", default=[])
    fonts_parser.add_argument("--timeout", type=float, default=30.0)
    fonts_parser.add_argument("--application", default="Adobe Illustrator")
    fonts_parser.add_argument("-o", "--output")
    fonts_parser.set_defaults(handler=_illustrator_fonts)

    illustrator_export_parser = subparsers.add_parser(
        "test-illustrator-export",
        help="create an AI8 fixture in Illustrator and parse it through the Python IR",
    )
    illustrator_export_parser.add_argument("--timeout", type=float, default=90.0)
    illustrator_export_parser.add_argument("--application", default="Adobe Illustrator")
    illustrator_export_parser.add_argument(
        "--fixture",
        choices=(
            "rgb-rectangle",
            "cmyk-curve",
            "stroke-style",
            "compound-path",
            "clipping-group",
            "group",
            "point-text",
            "unicode-text",
        ),
        default="rgb-rectangle",
    )
    illustrator_export_parser.add_argument("--ai-output")
    illustrator_export_parser.add_argument("-o", "--output")
    illustrator_export_parser.set_defaults(handler=_test_illustrator_export)

    illustrator_roundtrip_parser = subparsers.add_parser(
        "test-illustrator-roundtrip",
        help="resave a legacy AI fixture in Illustrator and compare the Python IR",
    )
    illustrator_roundtrip_parser.add_argument("input")
    illustrator_roundtrip_parser.add_argument("--timeout", type=float, default=90.0)
    illustrator_roundtrip_parser.add_argument("--application", default="Adobe Illustrator")
    illustrator_roundtrip_parser.add_argument("--ai-output")
    illustrator_roundtrip_parser.add_argument("-o", "--output")
    illustrator_roundtrip_parser.set_defaults(handler=_test_illustrator_roundtrip)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.handler(args))
    except (OSError, ValueError, json.JSONDecodeError) as error:
        parser.error(str(error))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
