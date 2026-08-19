"""Legacy AI fixture export and roundtrip adapters."""

from __future__ import annotations

import platform
import shutil
import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ._illustrator_bridge import execute_javascript
from ._illustrator_dom import compare_roundtrip_semantics
from ._illustrator_scripts import build_roundtrip_javascript
from .format import FileFormat, inspect_file
from .legacy import load_ai7


def run_illustrator_roundtrip_test(
    source: str | Path,
    *,
    output: str | Path | None = None,
    timeout: float = 90.0,
    application_name: str = "Adobe Illustrator",
    executor: Callable[..., subprocess.CompletedProcess[str]] = execute_javascript,
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
            completed = executor(
                build_roundtrip_javascript(input_copy, resaved_path),
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
        checks = compare_roundtrip_semantics(expected, actual)
        advisory_keys = {
            "miter_limits",
            "text_font_names",
            "text_alignments",
            "text_trackings",
            "text_rotations",
        }
        advisory_checks = {key: checks.pop(key) for key in advisory_keys if key in checks}
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
            "advisory_checks": advisory_checks,
            "compatibility_notes": [
                (
                    "AI8 legacy save may normalize inactive/default miter limits, font names, "
                    "and paragraph attributes; geometry and visible paint remain required."
                )
            ]
            if advisory_checks and not all(advisory_checks.values())
            else [],
            "expected_ir": expected.to_dict(),
            "roundtrip_ir": actual.to_dict(),
            "output": str(output_path) if output_path is not None else None,
        }
