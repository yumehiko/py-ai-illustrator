"""Modern PDF-compatible AI roundtrip adapter."""

from __future__ import annotations

import platform
import shutil
import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ._illustrator_bridge import execute_javascript
from ._illustrator_inspection import run_illustrator_test
from ._illustrator_scripts import build_modern_roundtrip_javascript
from .format import FileFormat, inspect_file
from .modern import read_modern_ai
from .verification import extract_pdf_display, visual_diff


def run_illustrator_modern_roundtrip_test(
    source: str | Path,
    *,
    output: str | Path | None = None,
    timeout: float = 90.0,
    application_name: str = "Adobe Illustrator",
    executor: Callable[..., subprocess.CompletedProcess[str]] = execute_javascript,
) -> dict[str, Any]:
    """Open, current-format resave, reopen, and verify a PDF-compatible AI file."""

    source_path = Path(source).resolve()
    output_path = Path(output).resolve() if output is not None else None
    if platform.system() != "Darwin":
        return {
            "status": "environment-unavailable",
            "error": "Illustrator modern roundtrip testing is supported on macOS only.",
        }
    if not source_path.is_file():
        return {"status": "invalid-input", "error": f"File does not exist: {source_path}"}
    if inspect_file(source_path).format is not FileFormat.PDF_COMPATIBLE_AI:
        return {
            "status": "invalid-input",
            "error": "Modern roundtrip testing requires a PDF-compatible AI input.",
        }
    if output_path is not None and output_path.exists():
        return {
            "status": "invalid-input",
            "error": f"Refusing to overwrite existing output: {output_path}",
        }
    if timeout <= 0:
        return {"status": "invalid-input", "error": "timeout must be positive"}
    before = run_illustrator_test(
        source_path, timeout=timeout, application_name=application_name, executor=executor
    )
    if before.get("status") != "passed":
        return {
            "status": before.get("status", "failed"),
            "stage": "open-before-resave",
            "before": before,
        }
    with tempfile.TemporaryDirectory(prefix="py-ai-modern-roundtrip-") as directory:
        root = Path(directory)
        input_copy = root / "input.ai"
        resaved = root / "resaved.ai"
        shutil.copy2(source_path, input_copy)
        try:
            completed = executor(
                build_modern_roundtrip_javascript(input_copy, resaved),
                root,
                timeout=timeout,
                application_name=application_name,
            )
        except subprocess.TimeoutExpired:
            return {
                "status": "environment-unavailable",
                "stage": "resave",
                "error": f"Illustrator did not answer within {timeout:g} seconds.",
            }
        if completed.returncode != 0:
            return {
                "status": "environment-unavailable",
                "stage": "resave",
                "error": completed.stderr.strip() or "Illustrator AppleScript failed.",
            }
        response = completed.stdout.strip()
        if not response.startswith("ok:") or not resaved.is_file():
            return {"status": "failed", "stage": "resave", "illustrator_response": response}
        after = run_illustrator_test(
            resaved, timeout=timeout, application_name=application_name, executor=executor
        )
        if after.get("status") != "passed":
            return {
                "status": after.get("status", "failed"),
                "stage": "reopen-after-resave",
                "before": before,
                "after": after,
            }
        structural_keys = (
            "layer_count",
            "layer_names",
            "layer_page_item_types",
            "path_item_count",
            "text_frame_count",
            "placed_item_count",
            "page_item_count",
            "artboard_count",
            "compound_path_item_count",
            "clipping_group_count",
            "group_item_count",
            "point_counts",
            "closed_count",
            "filled_count",
            "stroked_count",
        )
        before_dom = before.get("illustrator", {})
        after_dom = after.get("illustrator", {})
        structure_preserved = all(
            isinstance(before_dom, dict)
            and isinstance(after_dom, dict)
            and before_dom.get(key) == after_dom.get(key)
            for key in structural_keys
        )
        text_identity_preserved = (
            isinstance(before_dom, dict)
            and isinstance(after_dom, dict)
            and [
                (item.get("note"), item.get("contents"))
                for item in before_dom.get("text_frames", [])
                if isinstance(item, dict)
            ]
            == [
                (item.get("note"), item.get("contents"))
                for item in after_dom.get("text_frames", [])
                if isinstance(item, dict)
            ]
        )
        modern = read_modern_ai(resaved)
        display = extract_pdf_display(resaved)
        difference = visual_diff(source_path, resaved, root / "visual-difference.png", dpi=144)
        visual_change_within_tolerance = (
            bool(difference.before.pages)
            and len(difference.before.pages) == len(difference.after.pages)
            and all(
                before_page.width == after_page.width
                and before_page.height == after_page.height
                and diff_page.changed_ratio <= 0.001
                for before_page, after_page, diff_page in zip(
                    difference.before.pages, difference.after.pages, difference.pages, strict=True
                )
            )
        )
        checks = {
            "resave_created_pdf_compatible_ai": inspect_file(resaved).format
            is FileFormat.PDF_COMPATIBLE_AI,
            "reopen_structure_preserved": structure_preserved,
            "text_identity_and_content_preserved": text_identity_preserved,
            "private_data_reparsed": modern.private_data_status == "extracted",
            "semantic_projection_available": modern.semantic is not None,
            "pdf_display_reparsed": display.valid,
            "pdf_and_private_timestamps_match": display.private_data_freshness
            != "timestamp_mismatch",
            "visual_change_within_resave_tolerance": visual_change_within_tolerance,
        }
        if output_path is not None:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(resaved, output_path)
        return {
            "status": "passed" if all(checks.values()) else "mismatch",
            "input": str(source_path),
            "output": str(output_path) if output_path is not None else None,
            "illustrator_version": response[3:],
            "checks": checks,
            "advisory_checks": {"visual_pixels_exact": difference.equal},
            "normalization_policy": {
                "maximum_changed_pixel_ratio_per_page": 0.001,
                "page_dimensions_must_match": True,
            },
            "visual_diff": difference.to_dict(),
            "before": before,
            "after": after,
        }
