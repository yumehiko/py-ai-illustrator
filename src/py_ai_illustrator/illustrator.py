"""Opt-in compatibility checks against a locally installed Adobe Illustrator."""

from __future__ import annotations

import json
import platform
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from .format import FileFormat, inspect_file
from .legacy import load_ai7


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
    source_literal = json.dumps(str(source), ensure_ascii=True)
    return f"""#target illustrator
(function () {{
    var source = new File({source_literal});
    var documentRef = null;
    var previousInteractionLevel = app.userInteractionLevel;

    function quoteString(value) {{
        return '"' + value
            .replace(/\\/g, "\\\\")
            .replace(/"/g, '\\"')
            .replace(/\r/g, "\\r")
            .replace(/\n/g, "\\n") + '"';
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
        script_path = temp_path / "inspect.jsx"
        shutil.copy2(source_path, fixture_path)
        script_path.write_text(_build_javascript(fixture_path), encoding="utf-8")

        escaped_app = application_name.replace("\\", "\\\\").replace('"', '\\"')
        escaped_script = str(script_path).replace("\\", "\\\\").replace('"', '\\"')
        apple_script = f"""with timeout of {max(1, int(timeout))} seconds
    set scriptFile to POSIX file "{escaped_script}"
    tell application "{escaped_app}"
        return do javascript scriptFile
    end tell
end timeout
"""
        try:
            completed = subprocess.run(
                ["osascript", "-e", apple_script],
                check=False,
                capture_output=True,
                text=True,
                timeout=timeout + 5,
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
