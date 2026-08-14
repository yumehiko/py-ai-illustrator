"""Opt-in compatibility checks against a locally installed Adobe Illustrator."""

from __future__ import annotations

import json
import math
import platform
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from .format import FileFormat, inspect_file
from .legacy import load_ai7
from .model import CmykColor, Color, Document, ProcessColor
from .model import Path as AIPath


def _character_code_expression(value: str | Path) -> str:
    codepoints = ",".join(str(ord(character)) for character in str(value))
    return f"String.fromCharCode({codepoints})"


def _expected_structure(source: Path) -> dict[str, Any] | None:
    report = inspect_file(source)
    if report.format is not FileFormat.LEGACY_AI:
        return None
    try:
        document = load_ai7(source)
    except ValueError:
        return None
    paths = [path for layer in document.layers for path in layer.paths]
    return {
        "layer_count": len(document.layers),
        "layer_names": [layer.name for layer in document.layers],
        "path_item_count": len(paths),
        "point_counts": sorted(len(path.points) for path in paths),
        "closed_count": sum(path.closed for path in paths),
        "filled_count": sum(path.fill is not None for path in paths),
        "stroked_count": sum(path.stroke is not None for path in paths),
    }


def _build_javascript(source: Path) -> str:
    # Illustrator's Japanese ExtendScript parser can display and parse the ASCII
    # reverse solidus as a yen sign.  Build both the path and JSON escapes from
    # character codes so the generated JSX contains no ambiguous character.
    source_literal = _character_code_expression(source)
    return f"""#target illustrator
(function () {{
    var source = new File({source_literal});
    var documentRef = null;
    var previousInteractionLevel = app.userInteractionLevel;

    function quoteString(value) {{
        var slash = String.fromCharCode(92);
        var quote = String.fromCharCode(34);
        var carriageReturn = String.fromCharCode(13);
        var lineFeed = String.fromCharCode(10);
        return quote + String(value)
            .split(slash).join(slash + slash)
            .split(quote).join(slash + quote)
            .split(carriageReturn).join(slash + "r")
            .split(lineFeed).join(slash + "n") + quote;
    }}

    function toJson(value) {{
        if (value === null) return "null";
        if (typeof value === "string") return quoteString(value);
        if (typeof value === "number" || typeof value === "boolean") return String(value);
        var parts = [];
        var index;
        if (value instanceof Array) {{
            for (index = 0; index < value.length; index++) parts.push(toJson(value[index]));
            return "[" + parts.join(",") + "]";
        }}
        for (var key in value) {{
            if (value.hasOwnProperty(key)) parts.push(quoteString(key) + ":" + toJson(value[key]));
        }}
        return "{{" + parts.join(",") + "}}";
    }}

    function colorToObject(color) {{
        if (!color) return null;
        var value = {{type: color.typename}};
        if (color.typename === "RGBColor") {{
            value.red = color.red;
            value.green = color.green;
            value.blue = color.blue;
        }} else if (color.typename === "CMYKColor") {{
            value.cyan = color.cyan;
            value.magenta = color.magenta;
            value.yellow = color.yellow;
            value.black = color.black;
        }} else if (color.typename === "GrayColor") {{
            value.gray = color.gray;
        }}
        return value;
    }}

    try {{
        app.userInteractionLevel = UserInteractionLevel.DONTDISPLAYALERTS;
        if (!source.exists) throw new Error("Temporary fixture does not exist");
        documentRef = app.open(source);

        var layers = [];
        for (var layerIndex = 0; layerIndex < documentRef.layers.length; layerIndex++) {{
            var layer = documentRef.layers[layerIndex];
            layers.push({{
                name: layer.name,
                visible: layer.visible,
                locked: layer.locked,
                page_item_count: layer.pageItems.length
            }});
        }}

        var paths = [];
        for (var pathIndex = 0; pathIndex < documentRef.pathItems.length; pathIndex++) {{
            var path = documentRef.pathItems[pathIndex];
            var anchors = [];
            for (var pointIndex = 0; pointIndex < path.pathPoints.length; pointIndex++) {{
                var point = path.pathPoints[pointIndex];
                anchors.push({{
                    anchor: [point.anchor[0], point.anchor[1]],
                    left_direction: [point.leftDirection[0], point.leftDirection[1]],
                    right_direction: [point.rightDirection[0], point.rightDirection[1]],
                    point_type: String(point.pointType)
                }});
            }}
            paths.push({{
                name: path.name,
                closed: path.closed,
                filled: path.filled,
                stroked: path.stroked,
                stroke_width: path.strokeWidth,
                fill_color: path.filled ? colorToObject(path.fillColor) : null,
                stroke_color: path.stroked ? colorToObject(path.strokeColor) : null,
                anchors: anchors
            }});
        }}

        var artboards = [];
        for (
            var artboardIndex = 0;
            artboardIndex < documentRef.artboards.length;
            artboardIndex++
        ) {{
            var artboard = documentRef.artboards[artboardIndex];
            artboards.push({{
                name: artboard.name,
                rect: [
                    artboard.artboardRect[0],
                    artboard.artboardRect[1],
                    artboard.artboardRect[2],
                    artboard.artboardRect[3]
                ]
            }});
        }}

        var layerNames = [];
        for (var layerNameIndex = 0; layerNameIndex < layers.length; layerNameIndex++) {{
            layerNames.push(layers[layerNameIndex].name);
        }}
        var pointCounts = [];
        var closedCount = 0;
        var filledCount = 0;
        var strokedCount = 0;
        for (var pathCountIndex = 0; pathCountIndex < paths.length; pathCountIndex++) {{
            pointCounts.push(paths[pathCountIndex].anchors.length);
            if (paths[pathCountIndex].closed) closedCount++;
            if (paths[pathCountIndex].filled) filledCount++;
            if (paths[pathCountIndex].stroked) strokedCount++;
        }}
        pointCounts.sort(function (left, right) {{ return left - right; }});

        return toJson({{
            ok: true,
            illustrator_version: app.version,
            document_name: documentRef.name,
            document_color_space: String(documentRef.documentColorSpace),
            layer_count: documentRef.layers.length,
            path_item_count: documentRef.pathItems.length,
            page_item_count: documentRef.pageItems.length,
            artboard_count: documentRef.artboards.length,
            layer_names: layerNames,
            point_counts: pointCounts,
            closed_count: closedCount,
            filled_count: filledCount,
            stroked_count: strokedCount,
            layers: layers,
            paths: paths,
            artboards: artboards
        }});
    }} catch (error) {{
        return toJson({{
            ok: false,
            error: String(error),
            line: error.line || null
        }});
    }} finally {{
        if (documentRef !== null) {{
            documentRef.close(SaveOptions.DONOTSAVECHANGES);
        }}
        app.userInteractionLevel = previousInteractionLevel;
    }}
}}());
"""


def _build_export_javascript(destination: Path, fixture: str) -> str:
    destination_literal = _character_code_expression(destination)
    if fixture == "rgb-rectangle":
        fixture_javascript = """
        documentRef = app.documents.add(DocumentColorSpace.RGB, 320, 240);
        var layer = documentRef.layers[0];
        layer.name = "Illustrator Native";

        var path = layer.pathItems.add();
        path.name = "Native Rectangle";
        path.setEntirePath([[40, 40], [280, 40], [280, 200], [40, 200]]);
        path.closed = true;
        path.filled = true;
        path.stroked = true;
        path.strokeWidth = 3;

        var fill = new RGBColor();
        fill.red = 255;
        fill.green = 77;
        fill.blue = 0;
        path.fillColor = fill;

        var stroke = new RGBColor();
        stroke.red = 38;
        stroke.green = 26;
        stroke.blue = 13;
        path.strokeColor = stroke;
"""
    elif fixture == "cmyk-curve":
        fixture_javascript = """
        documentRef = app.documents.add(DocumentColorSpace.CMYK, 200, 200);
        var layer = documentRef.layers[0];
        layer.name = "Illustrator Native Curves";

        var path = layer.pathItems.add();
        path.name = "Native CMYK Curve";
        var first = path.pathPoints.add();
        first.anchor = [20, 20];
        first.leftDirection = [20, 20];
        first.rightDirection = [20, 150];
        first.pointType = PointType.SMOOTH;
        var second = path.pathPoints.add();
        second.anchor = [180, 180];
        second.leftDirection = [180, 50];
        second.rightDirection = [180, 180];
        second.pointType = PointType.SMOOTH;
        path.closed = false;
        path.filled = false;
        path.stroked = true;
        path.strokeWidth = 4;

        var stroke = new CMYKColor();
        stroke.cyan = 100;
        stroke.magenta = 25;
        stroke.yellow = 0;
        stroke.black = 10;
        path.strokeColor = stroke;
"""
    else:
        raise ValueError(f"Unknown Illustrator fixture: {fixture}")
    return f"""#target illustrator
(function () {{
    var destination = new File({destination_literal});
    var documentRef = null;
    var previousInteractionLevel = app.userInteractionLevel;

    try {{
        app.userInteractionLevel = UserInteractionLevel.DONTDISPLAYALERTS;
{fixture_javascript}

        var options = new IllustratorSaveOptions();
        options.compatibility = Compatibility.ILLUSTRATOR8;
        options.pdfCompatible = false;
        options.embedLinkedFiles = false;
        options.flattenOutput = OutputFlattening.PRESERVEAPPEARANCE;
        documentRef.saveAs(destination, options);
        return "ok:" + app.version;
    }} catch (error) {{
        return "error:" + String(error) + ":line:" + String(error.line || "");
    }} finally {{
        if (documentRef !== null) {{
            documentRef.close(SaveOptions.DONOTSAVECHANGES);
        }}
        app.userInteractionLevel = previousInteractionLevel;
    }}
}}());
"""


def _build_roundtrip_javascript(source: Path, destination: Path) -> str:
    source_literal = _character_code_expression(source)
    destination_literal = _character_code_expression(destination)
    return f"""#target illustrator
(function () {{
    var source = new File({source_literal});
    var destination = new File({destination_literal});
    var documentRef = null;
    var previousInteractionLevel = app.userInteractionLevel;

    try {{
        app.userInteractionLevel = UserInteractionLevel.DONTDISPLAYALERTS;
        if (!source.exists) throw new Error("Temporary fixture does not exist");
        documentRef = app.open(source);
        var options = new IllustratorSaveOptions();
        options.compatibility = Compatibility.ILLUSTRATOR8;
        options.pdfCompatible = false;
        options.embedLinkedFiles = false;
        options.flattenOutput = OutputFlattening.PRESERVEAPPEARANCE;
        documentRef.saveAs(destination, options);
        return "ok:" + app.version;
    }} catch (error) {{
        return "error:" + String(error) + ":line:" + String(error.line || "");
    }} finally {{
        if (documentRef !== null) {{
            documentRef.close(SaveOptions.DONOTSAVECHANGES);
        }}
        app.userInteractionLevel = previousInteractionLevel;
    }}
}}());
"""


def _execute_javascript(
    javascript: str,
    directory: Path,
    *,
    timeout: float,
    application_name: str,
) -> subprocess.CompletedProcess[str]:
    script_path = directory / "illustrator-test.jsx"
    script_path.write_text(javascript, encoding="utf-8")
    escaped_app = application_name.replace("\\", "\\\\").replace('"', '\\"')
    escaped_script = str(script_path).replace("\\", "\\\\").replace('"', '\\"')
    apple_script = f"""with timeout of {max(1, int(timeout))} seconds
    set scriptFile to POSIX file "{escaped_script}"
    tell application "{escaped_app}"
        return do javascript scriptFile
    end tell
end timeout
"""
    return subprocess.run(
        ["osascript", "-e", apple_script],
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout + 5,
    )


def _compare_structure(expected: dict[str, Any], actual: dict[str, Any]) -> dict[str, bool]:
    keys = (
        "layer_count",
        "layer_names",
        "path_item_count",
        "point_counts",
        "closed_count",
        "filled_count",
        "stroked_count",
    )
    return {key: actual.get(key) == expected[key] for key in keys}


def _color_close(
    expected: ProcessColor | None,
    actual: ProcessColor | None,
    *,
    tolerance: float,
) -> bool:
    if expected is None or actual is None:
        return expected is actual
    if type(expected) is not type(actual):
        return False
    if isinstance(expected, Color) and isinstance(actual, Color):
        expected_values = (expected.red, expected.green, expected.blue)
        actual_values = (actual.red, actual.green, actual.blue)
    elif isinstance(expected, CmykColor) and isinstance(actual, CmykColor):
        expected_values = (expected.cyan, expected.magenta, expected.yellow, expected.black)
        actual_values = (actual.cyan, actual.magenta, actual.yellow, actual.black)
    else:
        return False
    return all(
        math.isclose(left, right, rel_tol=0.0, abs_tol=tolerance)
        for left, right in zip(expected_values, actual_values, strict=True)
    )


def _path_geometry_close(expected: AIPath, actual: AIPath, *, tolerance: float) -> bool:
    if len(expected.points) != len(actual.points):
        return False
    expected_origin = expected.points[0]
    actual_origin = actual.points[0]
    for expected_point, actual_point in zip(expected.points, actual.points, strict=True):
        coordinates = (
            (expected_point.x - expected_origin.x, actual_point.x - actual_origin.x),
            (expected_point.y - expected_origin.y, actual_point.y - actual_origin.y),
        )
        if not all(
            math.isclose(left, right, rel_tol=0.0, abs_tol=tolerance)
            for left, right in coordinates
        ):
            return False
        for expected_handle, actual_handle in (
            (expected_point.in_handle, actual_point.in_handle),
            (expected_point.out_handle, actual_point.out_handle),
        ):
            if expected_handle is None or actual_handle is None:
                if expected_handle is not actual_handle:
                    return False
                continue
            handle_coordinates = (
                (expected_handle.x - expected_point.x, actual_handle.x - actual_point.x),
                (expected_handle.y - expected_point.y, actual_handle.y - actual_point.y),
            )
            if not all(
                math.isclose(left, right, rel_tol=0.0, abs_tol=tolerance)
                for left, right in handle_coordinates
            ):
                return False
        if expected_point.smooth != actual_point.smooth:
            return False
    return True


def _compare_roundtrip_semantics(
    expected: Document,
    actual: Document,
    *,
    tolerance: float = 1 / 255 + 1e-6,
) -> dict[str, bool]:
    expected_paths = [path for layer in expected.layers for path in layer.paths]
    actual_paths = [path for layer in actual.layers for path in layer.paths]
    paired_paths = list(zip(expected_paths, actual_paths, strict=False))
    same_path_count = len(expected_paths) == len(actual_paths)
    return {
        "layer_count": len(expected.layers) == len(actual.layers),
        "layer_names": [layer.name for layer in expected.layers]
        == [layer.name for layer in actual.layers],
        "layer_visibility": [layer.visible for layer in expected.layers]
        == [layer.visible for layer in actual.layers],
        "path_item_count": same_path_count,
        "point_counts": same_path_count
        and all(len(left.points) == len(right.points) for left, right in paired_paths),
        "path_flags": same_path_count
        and all(
            (left.closed, left.fill is not None, left.stroke is not None)
            == (right.closed, right.fill is not None, right.stroke is not None)
            for left, right in paired_paths
        ),
        "stroke_widths": same_path_count
        and all(
            math.isclose(
                left.stroke_width,
                right.stroke_width,
                rel_tol=0.0,
                abs_tol=tolerance,
            )
            for left, right in paired_paths
        ),
        "fill_colors": same_path_count
        and all(
            _color_close(left.fill, right.fill, tolerance=tolerance)
            for left, right in paired_paths
        ),
        "stroke_colors": same_path_count
        and all(
            _color_close(left.stroke, right.stroke, tolerance=tolerance)
            for left, right in paired_paths
        ),
        "path_geometry": same_path_count
        and all(
            _path_geometry_close(left, right, tolerance=tolerance)
            for left, right in paired_paths
        ),
    }


def run_illustrator_test(
    source: str | Path,
    *,
    timeout: float = 90.0,
    application_name: str = "Adobe Illustrator",
) -> dict[str, Any]:
    """Open a temporary copy in Illustrator, inspect it, and close only that copy."""

    source_path = Path(source).resolve()
    if platform.system() != "Darwin":
        return {
            "status": "environment-unavailable",
            "error": "Illustrator compatibility testing is currently supported on macOS only.",
        }
    if not source_path.is_file():
        return {"status": "invalid-input", "error": f"File does not exist: {source_path}"}
    if timeout <= 0:
        return {"status": "invalid-input", "error": "timeout must be positive"}

    expected = _expected_structure(source_path)
    with tempfile.TemporaryDirectory(prefix="py-ai-illustrator-") as temp_directory:
        temp_path = Path(temp_directory)
        fixture_path = temp_path / f"fixture{source_path.suffix or '.ai'}"
        shutil.copy2(source_path, fixture_path)
        try:
            completed = _execute_javascript(
                _build_javascript(fixture_path),
                temp_path,
                timeout=timeout,
                application_name=application_name,
            )
        except subprocess.TimeoutExpired:
            return {
                "status": "environment-unavailable",
                "error": f"Illustrator did not answer within {timeout:g} seconds.",
                "next_action": (
                    "Open Illustrator manually, sign in to Creative Cloud, finish onboarding, "
                    "and rerun this command after the Home screen is responsive."
                ),
            }

    if completed.returncode != 0:
        return {
            "status": "environment-unavailable",
            "error": completed.stderr.strip() or "Illustrator AppleScript failed.",
            "next_action": (
                "Confirm Illustrator is open, signed in, and responds to its Home screen."
            ),
        }

    try:
        actual = json.loads(completed.stdout.strip())
    except json.JSONDecodeError:
        return {
            "status": "error",
            "error": "Illustrator returned a non-JSON response.",
            "response": completed.stdout.strip(),
        }
    if not actual.get("ok"):
        return {"status": "failed", "illustrator": actual, "expected": expected}

    checks = _compare_structure(expected, actual) if expected is not None else {}
    passed = bool(actual.get("ok")) and all(checks.values())
    return {
        "status": "passed" if passed else "mismatch",
        "input": str(source_path),
        "expected": expected,
        "checks": checks,
        "illustrator": actual,
    }


def run_illustrator_export_test(
    *,
    fixture: str = "rgb-rectangle",
    output: str | Path | None = None,
    timeout: float = 90.0,
    application_name: str = "Adobe Illustrator",
) -> dict[str, Any]:
    """Create an AI8 fixture in Illustrator and read it back through the Python IR."""

    if platform.system() != "Darwin":
        return {
            "status": "environment-unavailable",
            "error": "Illustrator export testing is currently supported on macOS only.",
        }
    if timeout <= 0:
        return {"status": "invalid-input", "error": "timeout must be positive"}
    if fixture not in {"rgb-rectangle", "cmyk-curve"}:
        return {"status": "invalid-input", "error": f"Unknown fixture: {fixture}"}

    output_path = Path(output).resolve() if output is not None else None
    if output_path is not None and output_path.exists():
        return {
            "status": "invalid-input",
            "error": f"Refusing to overwrite existing output: {output_path}",
        }

    with tempfile.TemporaryDirectory(prefix="py-ai-illustrator-export-") as temp_directory:
        temp_path = Path(temp_directory)
        fixture_path = temp_path / "illustrator-native.ai"
        try:
            completed = _execute_javascript(
                _build_export_javascript(fixture_path, fixture),
                temp_path,
                timeout=timeout,
                application_name=application_name,
            )
        except subprocess.TimeoutExpired:
            return {
                "status": "environment-unavailable",
                "error": f"Illustrator did not answer within {timeout:g} seconds.",
            }
        if completed.returncode != 0:
            return {
                "status": "environment-unavailable",
                "error": completed.stderr.strip() or "Illustrator AppleScript failed.",
            }

        response = completed.stdout.strip()
        if not response.startswith("ok:"):
            return {"status": "failed", "illustrator_response": response}
        if not fixture_path.is_file():
            return {
                "status": "failed",
                "error": "Illustrator reported success but did not create the AI fixture.",
            }
        if output_path is not None:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(fixture_path, output_path)

        format_report = inspect_file(fixture_path)
        format_details = format_report.to_dict()
        format_details["path"] = str(output_path) if output_path is not None else None
        try:
            document = load_ai7(fixture_path)
        except (ValueError, UnicodeError) as error:
            return {
                "status": "failed",
                "illustrator_version": response[3:],
                "format": format_details,
                "reader_error": str(error),
            }

        paths = [path for layer in document.layers for path in layer.paths]
        path = paths[0] if paths else None
        expected_layer_name = (
            "Illustrator Native" if fixture == "rgb-rectangle" else "Illustrator Native Curves"
        )
        checks: dict[str, bool] = {
            "legacy_ai_detected": format_report.format is FileFormat.LEGACY_AI,
            "artwork_bounds": document.width > 0 and document.height > 0,
            "layer_count": len(document.layers) == 1,
            "layer_name": bool(document.layers)
            and document.layers[0].name == expected_layer_name,
            "path_item_count": len(paths) == 1,
            "stroked": path is not None and path.stroke is not None,
        }
        if fixture == "rgb-rectangle":
            checks.update(
                {
                    "point_count": path is not None and len(path.points) == 4,
                    "closed": path is not None and path.closed,
                    "filled": path is not None and path.fill is not None,
                    "stroke_width": path is not None and path.stroke_width == 3.0,
                    "rgb_fill": path is not None and isinstance(path.fill, Color),
                    "rgb_stroke": path is not None and isinstance(path.stroke, Color),
                }
            )
        else:
            checks.update(
                {
                    "point_count": path is not None and len(path.points) == 2,
                    "open": path is not None and not path.closed,
                    "unfilled": path is not None and path.fill is None,
                    "stroke_width": path is not None and path.stroke_width == 4.0,
                    "cmyk_stroke": path is not None and isinstance(path.stroke, CmykColor),
                    "bezier_handles": path is not None
                    and path.points[0].out_handle is not None
                    and path.points[1].in_handle is not None,
                }
            )
        passed = all(checks.values())

        return {
            "status": "passed" if passed else "mismatch",
            "fixture": fixture,
            "illustrator_version": response[3:],
            "format": format_details,
            "checks": checks,
            "python_ir": document.to_dict(),
            "output": str(output_path) if output_path is not None else None,
        }


def run_illustrator_roundtrip_test(
    source: str | Path,
    *,
    output: str | Path | None = None,
    timeout: float = 90.0,
    application_name: str = "Adobe Illustrator",
) -> dict[str, Any]:
    """Resave a Python-readable AI file in Illustrator and compare semantic IRs."""

    source_path = Path(source).resolve()
    if platform.system() != "Darwin":
        return {
            "status": "environment-unavailable",
            "error": "Illustrator round-trip testing is currently supported on macOS only.",
        }
    if not source_path.is_file():
        return {"status": "invalid-input", "error": f"File does not exist: {source_path}"}
    if timeout <= 0:
        return {"status": "invalid-input", "error": "timeout must be positive"}
    if inspect_file(source_path).format is not FileFormat.LEGACY_AI:
        return {
            "status": "invalid-input",
            "error": "Round-trip testing currently requires a legacy AI input.",
        }
    try:
        expected = load_ai7(source_path)
    except (ValueError, UnicodeError) as error:
        return {"status": "invalid-input", "error": str(error)}

    output_path = Path(output).resolve() if output is not None else None
    if output_path is not None and output_path.exists():
        return {
            "status": "invalid-input",
            "error": f"Refusing to overwrite existing output: {output_path}",
        }

    with tempfile.TemporaryDirectory(prefix="py-ai-illustrator-roundtrip-") as temp_directory:
        temp_path = Path(temp_directory)
        input_copy = temp_path / "python-generated.ai"
        resaved_path = temp_path / "illustrator-resaved.ai"
        shutil.copy2(source_path, input_copy)
        try:
            completed = _execute_javascript(
                _build_roundtrip_javascript(input_copy, resaved_path),
                temp_path,
                timeout=timeout,
                application_name=application_name,
            )
        except subprocess.TimeoutExpired:
            return {
                "status": "environment-unavailable",
                "error": f"Illustrator did not answer within {timeout:g} seconds.",
            }
        if completed.returncode != 0:
            return {
                "status": "environment-unavailable",
                "error": completed.stderr.strip() or "Illustrator AppleScript failed.",
            }

        response = completed.stdout.strip()
        if not response.startswith("ok:"):
            return {"status": "failed", "illustrator_response": response}
        if not resaved_path.is_file():
            return {
                "status": "failed",
                "error": "Illustrator reported success but did not create the resaved AI file.",
            }
        if output_path is not None:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(resaved_path, output_path)

        format_report = inspect_file(resaved_path)
        format_details = format_report.to_dict()
        format_details["path"] = str(output_path) if output_path is not None else None
        try:
            actual = load_ai7(resaved_path)
        except (ValueError, UnicodeError) as error:
            return {
                "status": "failed",
                "illustrator_version": response[3:],
                "format": format_details,
                "reader_error": str(error),
                "output": str(output_path) if output_path is not None else None,
            }

        checks = _compare_roundtrip_semantics(expected, actual)
        passed = format_report.format is FileFormat.LEGACY_AI and all(checks.values())
        return {
            "status": "passed" if passed else "mismatch",
            "input": str(source_path),
            "illustrator_version": response[3:],
            "format": format_details,
            "checks": {
                "legacy_ai_detected": format_report.format is FileFormat.LEGACY_AI,
                **checks,
            },
            "expected_ir": expected.to_dict(),
            "roundtrip_ir": actual.to_dict(),
            "output": str(output_path) if output_path is not None else None,
        }
