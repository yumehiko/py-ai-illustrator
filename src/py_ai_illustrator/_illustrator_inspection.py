"""Adapter for opening a copy in Illustrator and validating its DOM snapshot."""

from __future__ import annotations

import json
import platform
import shutil
import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ._illustrator_bridge import execute_javascript
from ._illustrator_dom import compare_structure, expected_structure
from ._illustrator_scripts import build_javascript


def run_illustrator_test(
    source: str | Path,
    *,
    timeout: float = 90.0,
    application_name: str = "Adobe Illustrator",
    executor: Callable[..., subprocess.CompletedProcess[str]] = execute_javascript,
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
    expected = expected_structure(source_path)
    with tempfile.TemporaryDirectory(prefix="py-ai-illustrator-") as temp_directory:
        temp_path = Path(temp_directory)
        fixture_path = temp_path / f"fixture{source_path.suffix or '.ai'}"
        shutil.copy2(source_path, fixture_path)
        try:
            completed = executor(
                build_javascript(fixture_path),
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
    checks = compare_structure(expected, actual) if expected is not None else {}
    checks["linked_files_exist"] = all(
        image.get("file_exists") is True for image in actual.get("placed_images", [])
    )
    return {
        "status": "passed" if all(checks.values()) else "mismatch",
        "input": str(source_path),
        "expected": expected,
        "checks": checks,
        "illustrator": actual,
    }
