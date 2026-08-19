"""Illustrator-authored fixture generation and legacy IR validation."""

from __future__ import annotations

import platform
import shutil
import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ._illustrator_bridge import execute_javascript
from ._illustrator_dom import document_paths, document_text_frames
from ._illustrator_scripts import build_export_javascript
from .format import FileFormat, inspect_file
from .legacy import load_ai7
from .model import CmykColor, Color

FIXTURES = {
    "rgb-rectangle",
    "cmyk-curve",
    "stroke-style",
    "compound-path",
    "clipping-group",
    "group",
    "point-text",
    "unicode-text",
}


def run_illustrator_export_test(
    *,
    fixture: str = "rgb-rectangle",
    output: str | Path | None = None,
    timeout: float = 90.0,
    application_name: str = "Adobe Illustrator",
    executor: Callable[..., subprocess.CompletedProcess[str]] = execute_javascript,
) -> dict[str, Any]:
    """Create an AI8 fixture in Illustrator and read it back through Python IR."""

    if platform.system() != "Darwin":
        return {
            "status": "environment-unavailable",
            "error": "Illustrator export testing is currently supported on macOS only.",
        }
    if timeout <= 0:
        return {"status": "invalid-input", "error": "timeout must be positive"}
    if fixture not in FIXTURES:
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
            completed = executor(
                build_export_javascript(fixture_path, fixture),
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
        paths = document_paths(document)
        path = paths[0] if paths else None
        expected_layer_name = {
            "rgb-rectangle": "Illustrator Native",
            "cmyk-curve": "Illustrator Native Curves",
            "stroke-style": "Illustrator Native Stroke Style",
            "compound-path": "Illustrator Native Compound",
            "clipping-group": "Illustrator Native Clipping",
            "group": "Illustrator Native Group",
            "point-text": "Illustrator Native Text",
            "unicode-text": "Illustrator Native Unicode",
        }[fixture]
        checks: dict[str, bool] = {
            "legacy_ai_detected": format_report.format is FileFormat.LEGACY_AI,
            "artwork_bounds": document.width > 0 and document.height > 0,
            "layer_count": len(document.layers) == 1,
            "layer_name": bool(document.layers) and document.layers[0].name == expected_layer_name,
            "path_item_count": len(paths)
            == (
                2
                if fixture in {"compound-path", "clipping-group", "group"}
                else 0
                if fixture in {"point-text", "unicode-text"}
                else 1
            ),
        }
        if fixture == "rgb-rectangle":
            checks.update(
                {
                    "stroked": path is not None and path.stroke is not None,
                    "point_count": path is not None and len(path.points) == 4,
                    "closed": path is not None and path.closed,
                    "filled": path is not None and path.fill is not None,
                    "stroke_width": path is not None and path.stroke_width == 3.0,
                    "rgb_fill": path is not None and isinstance(path.fill, Color),
                    "rgb_stroke": path is not None and isinstance(path.stroke, Color),
                }
            )
        elif fixture == "cmyk-curve":
            checks.update(
                {
                    "stroked": path is not None and path.stroke is not None,
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
        elif fixture == "stroke-style":
            checks.update(
                {
                    "dash_pattern": path is not None and path.dash_pattern == [18.0, 8.0, 4.0, 8.0],
                    "dash_offset": path is not None and path.dash_offset == 3.0,
                    "line_cap": path is not None and path.line_cap == "round",
                    "line_join": path is not None and path.line_join == "bevel",
                    "miter_limit": path is not None and path.miter_limit == 7.0,
                }
            )
        elif fixture == "compound-path":
            compound_paths = document.layers[0].compound_paths if document.layers else []
            compound = compound_paths[0] if compound_paths else None
            checks.update(
                {
                    "compound_path_count": len(compound_paths) == 1,
                    "component_count": compound is not None and len(compound.paths) == 2,
                    "component_polarities": compound is not None
                    and [item.polarity for item in compound.paths] == ["positive", "negative"],
                    "filled": compound is not None
                    and all(item.fill is not None for item in compound.paths),
                    "unstroked": compound is not None
                    and all(item.stroke is None for item in compound.paths),
                }
            )
        elif fixture == "clipping-group":
            clipping_groups = document.layers[0].clipping_groups if document.layers else []
            group = clipping_groups[0] if clipping_groups else None
            checks.update(
                {
                    "clipping_group_count": len(clipping_groups) == 1,
                    "content_path_count": group is not None and len(group.paths) == 1,
                    "mask_closed": group is not None and group.clipping_path.closed,
                    "mask_unpainted": group is not None
                    and group.clipping_path.fill is None
                    and group.clipping_path.stroke is None,
                    "content_filled": group is not None and group.paths[0].fill is not None,
                }
            )
        elif fixture == "group":
            groups = document.layers[0].groups if document.layers else []
            group = groups[0] if groups else None
            checks.update(
                {
                    "group_item_count": len(groups) == 1,
                    "group_child_count": group is not None and len(group.item_order) == 2,
                    "group_path_count": group is not None and len(group.paths) == 2,
                }
            )
        else:
            text_frames = document_text_frames(document)
            expected_text = "Table Header" if fixture == "point-text" else "日本語の表見出し"
            expected_size = 14.0 if fixture == "point-text" else 16.0
            checks.update(
                {
                    "text_frame_count": bool(text_frames),
                    "text_contents": "".join(text.text for text in text_frames) == expected_text,
                    "font_size": all(text.font_size == expected_size for text in text_frames),
                    "rgb_fill": all(isinstance(text.fill, Color) for text in text_frames),
                }
            )
        return {
            "status": "passed" if all(checks.values()) else "mismatch",
            "fixture": fixture,
            "illustrator_version": response[3:],
            "format": format_details,
            "checks": checks,
            "python_ir": document.to_dict(),
            "output": str(output_path) if output_path is not None else None,
        }
