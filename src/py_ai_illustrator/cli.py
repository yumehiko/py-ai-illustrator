"""Inspect, safely edit, verify supported AI files, and compile Document IR via Illustrator 2026."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

from .editing import (
    apply_edit_plan,
    inspect_editable_legacy,
    inspect_editable_modern,
    plan_edit,
)
from .format import FileFormat, inspect_file
from .illustrator import (
    list_illustrator_fonts,
    materialize_native_ai,
    run_illustrator_export_test,
    run_illustrator_modern_roundtrip_test,
    run_illustrator_roundtrip_test,
    run_illustrator_test,
)
from .legacy import UnsupportedLegacyFeature, dump_ai7, read_ai7
from .model import Document
from .modern import read_modern_ai
from .modern_writing import inspect_modern_representation_consistency
from .native import NativeCompileProfile, compile_native_ai
from .semantic import semantic_diff
from .verification import extract_pdf_display, render_preview, visual_diff


def _write_json(data: Any, destination: Path | None) -> None:
    payload = json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if destination is None:
        sys.stdout.write(payload)
    else:
        destination.write_text(payload, encoding="utf-8")


def _inspect(args: argparse.Namespace) -> int:
    report = inspect_file(args.input)
    if args.json:
        output = report.to_dict()
        if report.format is FileFormat.LEGACY_AI:
            output.update(inspect_editable_legacy(args.input))
        elif report.format in {FileFormat.PDF_COMPATIBLE_AI, FileFormat.PDF}:
            output["modern_ai"] = read_modern_ai(args.input).to_dict()
            output["pdf_display"] = extract_pdf_display(args.input).to_dict()
            if report.format is FileFormat.PDF_COMPATIBLE_AI:
                editing = inspect_editable_modern(args.input)
                output["modern_editing"] = editing
                output["selectors"] = editing["selectors"]
        _write_json(output, None)
    else:
        print(f"format: {report.format.value}")
        print(f"size: {report.size_bytes} bytes")
        print(f"confidence: {report.confidence}")
        if report.illustrator_markers:
            print("markers: " + ", ".join(report.illustrator_markers))
        if report.format in {FileFormat.PDF_COMPATIBLE_AI, FileFormat.PDF}:
            modern = read_modern_ai(args.input)
            display = extract_pdf_display(args.input)
            print(f"container-read: {modern.container_status}")
            print(f"private-data: {modern.private_data_status}")
            print(f"semantic: {modern.semantic_status}")
            print(f"pdf-display: {display.status} ({len(display.pages)} pages)")
            print(f"private-data-freshness: {display.private_data_freshness}")
            if modern.semantic is not None:
                coverage = modern.semantic.coverage
                print("semantic-profile: modern-ai-semantic-read-only-v2")
                print(
                    "artwork: "
                    f"layers={coverage.projected_layer_count} "
                    f"paths={coverage.projected_path_count} "
                    f"groups={coverage.projected_group_count} "
                    f"compound-paths={coverage.projected_compound_path_count} "
                    f"clipping-groups={coverage.projected_clipping_group_count} "
                    f"text={coverage.projected_text_count}"
                )
                print(
                    f"partial-nodes: {coverage.partial_node_count} "
                    f"(text={coverage.partial_text_count})"
                )
        for note in report.notes:
            print(f"note: {note}")
    return 0


def _read_request(path: str) -> object:
    with Path(path).open(encoding="utf-8") as stream:
        return json.load(stream)


def _plan(args: argparse.Namespace) -> int:
    plan = plan_edit(args.input, _read_request(args.operations))
    _write_json(plan.to_dict(), None)
    return 0 if plan.applicable else 1


def _apply(args: argparse.Namespace) -> int:
    plan = plan_edit(args.input, _read_request(args.operations))
    result = apply_edit_plan(plan, args.output)
    result["plan"] = plan.to_dict()
    _write_json(result, None)
    return 0 if result["applied"] else 1


def _semantic_diff(args: argparse.Namespace) -> int:
    before_report = inspect_file(args.before)
    after_report = inspect_file(args.after)
    if before_report.format is not FileFormat.LEGACY_AI:
        raise UnsupportedLegacyFeature(
            f"Semantic diff supports legacy_ai only; before is {before_report.format.value}."
        )
    if after_report.format is not FileFormat.LEGACY_AI:
        raise UnsupportedLegacyFeature(
            f"Semantic diff supports legacy_ai only; after is {after_report.format.value}."
        )
    before = read_ai7(args.before)
    after = read_ai7(args.after)
    difference = semantic_diff(before.document, after.document)
    _write_json(
        {
            "before": {
                "path": args.before,
                "format": before_report.format.value,
                "source_sha256": hashlib.sha256(before.source.data).hexdigest(),
            },
            "after": {
                "path": args.after,
                "format": after_report.format.value,
                "source_sha256": hashlib.sha256(after.source.data).hexdigest(),
            },
            "semantic_diff": difference.to_dict(),
        },
        None,
    )
    return 0


def _diff(args: argparse.Namespace) -> int:
    if args.visual:
        if not args.output:
            raise ValueError("--output is required with --visual")
        difference = visual_diff(
            args.before,
            args.after,
            args.output,
            dpi=args.dpi,
            threshold=args.threshold,
            renderer=args.renderer,
            timeout=args.timeout,
            overwrite=args.force,
        )
        _write_json(difference.to_dict(), None)
        return 0
    return _semantic_diff(args)


def _preview(args: argparse.Namespace) -> int:
    result = render_preview(
        args.input,
        args.output,
        dpi=args.dpi,
        page=args.page,
        renderer=args.renderer,
        timeout=args.timeout,
        overwrite=args.force,
    )
    _write_json(result.to_dict(), None)
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
    modern_read: dict[str, object] | None = None
    pdf_display: dict[str, object] | None = None
    modern_valid: bool | None = None
    representation_consistency: dict[str, object] | None = None
    classification = "unconvertible"
    if report.format is FileFormat.LEGACY_AI:
        try:
            parsed = read_ai7(args.input)
            compatibility = parsed.compatibility_report()
            warnings.extend(diagnostic.message for diagnostic in parsed.diagnostics)
            if not parsed.document.layers:
                warnings.append("No layers were parsed by the Phase 0 reader.")
            classification = str(compatibility["classification"])
        except (ValueError, UnicodeError) as error:
            errors.append(str(error))
    elif report.format in {FileFormat.PDF_COMPATIBLE_AI, FileFormat.PDF}:
        modern = read_modern_ai(args.input)
        display = extract_pdf_display(args.input)
        modern_read = modern.to_dict()
        pdf_display = display.to_dict()
        if report.format is FileFormat.PDF_COMPATIBLE_AI:
            representation_consistency = inspect_modern_representation_consistency(
                args.input
            )
        errors.extend(
            diagnostic.message
            for diagnostic in modern.diagnostics
            if diagnostic.severity == "error"
        )
        warnings.extend(
            diagnostic.message
            for diagnostic in modern.diagnostics
            if diagnostic.severity in {"warning", "info"}
        )
        errors.extend(
            diagnostic.message
            for diagnostic in display.diagnostics
            if diagnostic.severity == "error"
        )
        warnings.extend(
            diagnostic.message
            for diagnostic in display.diagnostics
            if diagnostic.severity in {"warning", "info"}
        )
        if display.private_data_freshness == "timestamp_mismatch":
            errors.append(
                "PDF display and Illustrator PrivateData timestamps disagree; "
                "the two representations may be stale."
            )
        if (
            representation_consistency is not None
            and representation_consistency["status"] == "inconsistent"
        ):
            errors.append(
                "PDF display and Illustrator PrivateData disagree for one or more "
                "source-local paint targets."
            )
        if modern.private_data_status == "extracted":
            classification = (
                f"read_only_semantic_{modern.semantic_status}"
                if modern.semantic_status in {"supported", "partial"}
                else "read_only_private_data"
            )
            modern_valid = modern.container_status == "parsed" and not errors
        elif modern.private_data_status == "absent":
            classification = "ordinary_pdf"
            modern_valid = modern.container_status == "parsed" and not errors
        else:
            modern_valid = False
        if modern.semantic_status == "partial":
            warnings.append(
                "Modern AI semantic projection is partial and read-only; unknown bytes and "
                "unsupported nodes remain preserved in the read result."
            )
        elif modern.semantic_status == "unsupported":
            warnings.append(
                "Modern AI semantic projection is unsupported; no Document IR was produced."
            )
    else:
        errors.append(f"Unsupported input format: {report.format.value}")

    valid = modern_valid if modern_valid is not None else not errors
    result = {
        "valid": valid,
        "safe_to_reserialize": bool(
            not errors
            and compatibility is not None
            and compatibility["safe_to_reserialize"]
        ),
        "classification": classification if valid else "unconvertible",
        "format": report.format.value,
        "errors": errors,
        "warnings": warnings,
        "compatibility": compatibility,
        "modern_ai": modern_read,
        "pdf_display": pdf_display,
        "representation_consistency": representation_consistency,
    }
    _write_json(result, None)
    if modern_valid is not None:
        return 0 if result["valid"] else 1
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


def _compile_native(args: argparse.Namespace) -> int:
    result = compile_native_ai(
        args.input,
        args.output,
        profile=NativeCompileProfile(color_space=args.color_space),
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


def _test_illustrator_modern_roundtrip(args: argparse.Namespace) -> int:
    result = run_illustrator_modern_roundtrip_test(
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

    inspect_parser = subparsers.add_parser(
        "inspect", help="detect the container and read bounded format evidence"
    )
    inspect_parser.add_argument("input")
    inspect_parser.add_argument("--json", action="store_true")
    inspect_parser.set_defaults(handler=_inspect)

    export_parser = subparsers.add_parser(
        "export", help="convert the supported legacy AI subset and Document IR JSON"
    )
    export_parser.add_argument("input")
    export_parser.add_argument("--to", choices=("json", "ai7"), required=True)
    export_parser.add_argument("-o", "--output")
    export_parser.add_argument(
        "--allow-partial",
        action="store_true",
        help="allow JSON export that omits diagnosed unsupported source features",
    )
    export_parser.set_defaults(handler=_export)

    plan_parser = subparsers.add_parser(
        "plan", help="resolve and dry-run a safe edit without writing a file"
    )
    plan_parser.add_argument("input")
    plan_parser.add_argument("operations")
    plan_parser.set_defaults(handler=_plan)

    apply_parser = subparsers.add_parser(
        "apply", help="apply a verified safe edit to a new output file"
    )
    apply_parser.add_argument("input")
    apply_parser.add_argument("operations")
    apply_parser.add_argument("-o", "--output", required=True)
    apply_parser.set_defaults(handler=_apply)

    diff_parser = subparsers.add_parser(
        "diff", help="compare two supported AI documents semantically or visually"
    )
    diff_parser.add_argument("before")
    diff_parser.add_argument("after")
    diff_mode = diff_parser.add_mutually_exclusive_group(required=True)
    diff_mode.add_argument("--semantic", action="store_true")
    diff_mode.add_argument("--visual", action="store_true")
    diff_parser.add_argument("-o", "--output")
    diff_parser.add_argument("--dpi", type=int, default=144)
    diff_parser.add_argument("--threshold", type=int, default=0)
    diff_parser.add_argument("--renderer", default="pdftocairo")
    diff_parser.add_argument("--timeout", type=float, default=60.0)
    diff_parser.add_argument("--force", action="store_true")
    diff_parser.set_defaults(handler=_diff)

    preview_parser = subparsers.add_parser(
        "preview", help="render a PDF display or legacy IR reference PNG preview"
    )
    preview_parser.add_argument("input")
    preview_parser.add_argument("-o", "--output", required=True)
    preview_parser.add_argument("--dpi", type=int, default=144)
    preview_parser.add_argument("--page", type=int)
    preview_parser.add_argument("--renderer", default="pdftocairo")
    preview_parser.add_argument("--timeout", type=float, default=60.0)
    preview_parser.add_argument("--force", action="store_true")
    preview_parser.set_defaults(handler=_preview)

    validate_parser = subparsers.add_parser(
        "validate", help="run available container, compatibility, and structural checks"
    )
    validate_parser.add_argument("input")
    validate_parser.set_defaults(handler=_validate)

    illustrator_parser = subparsers.add_parser(
        "test-illustrator",
        help="use Illustrator to inspect a temporary copy without saving the input",
    )
    illustrator_parser.add_argument("input")
    illustrator_parser.add_argument("--timeout", type=float, default=90.0)
    illustrator_parser.add_argument("--application", default="Adobe Illustrator")
    illustrator_parser.add_argument("-o", "--output")
    illustrator_parser.set_defaults(handler=_test_illustrator)

    native_parser = subparsers.add_parser(
        "materialize-native",
        help="use Illustrator to convert legacy AI text to a new native editable AI",
    )
    native_parser.add_argument("input")
    native_parser.add_argument("-o", "--output", required=True)
    native_parser.add_argument("--timeout", type=float, default=90.0)
    native_parser.add_argument("--application", default="Adobe Illustrator")
    native_parser.set_defaults(handler=_materialize_native)

    compile_native_parser = subparsers.add_parser(
        "compile-native",
        help="use Illustrator to compile Document IR JSON to a verified native AI",
    )
    compile_native_parser.add_argument("input")
    compile_native_parser.add_argument("-o", "--output", required=True)
    compile_native_parser.add_argument("--color-space", choices=("rgb", "cmyk"), default="rgb")
    compile_native_parser.add_argument("--timeout", type=float, default=120.0)
    compile_native_parser.add_argument("--application", default="Adobe Illustrator")
    compile_native_parser.set_defaults(handler=_compile_native)

    fonts_parser = subparsers.add_parser(
        "illustrator-fonts",
        help="use Illustrator to list fonts and validate PostScript names",
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

    modern_roundtrip_parser = subparsers.add_parser(
        "test-illustrator-modern-roundtrip",
        help="resave a PDF-compatible AI in Illustrator and verify native reopen/editability",
    )
    modern_roundtrip_parser.add_argument("input")
    modern_roundtrip_parser.add_argument("--timeout", type=float, default=90.0)
    modern_roundtrip_parser.add_argument("--application", default="Adobe Illustrator")
    modern_roundtrip_parser.add_argument("--ai-output")
    modern_roundtrip_parser.add_argument("-o", "--output")
    modern_roundtrip_parser.set_defaults(handler=_test_illustrator_modern_roundtrip)
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
