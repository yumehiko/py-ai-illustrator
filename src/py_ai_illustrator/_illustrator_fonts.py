"""Font catalog adapter for an installed Illustrator."""

from __future__ import annotations

import platform
import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ._illustrator_bridge import execute_javascript
from ._illustrator_scripts import build_font_catalog_javascript


def list_illustrator_fonts(
    *,
    query: str | None = None,
    required: tuple[str, ...] = (),
    timeout: float = 30.0,
    application_name: str = "Adobe Illustrator",
    executor: Callable[..., subprocess.CompletedProcess[str]] = execute_javascript,
) -> dict[str, Any]:
    """List installed Illustrator fonts and validate exact PostScript names."""

    if platform.system() != "Darwin":
        return {
            "status": "environment-unavailable",
            "error": "Illustrator font discovery is currently supported on macOS only.",
        }
    if timeout <= 0:
        return {"status": "invalid-input", "error": "timeout must be positive"}
    with tempfile.TemporaryDirectory(prefix="py-ai-illustrator-fonts-") as directory:
        try:
            completed = executor(
                build_font_catalog_javascript(),
                Path(directory),
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
    lines = completed.stdout.rstrip("\r\n").splitlines()
    if not lines or not lines[0].startswith("ok\t"):
        return {"status": "failed", "illustrator_response": completed.stdout.strip()}
    header = lines[0].split("\t")
    if len(header) != 3:
        return {"status": "failed", "illustrator_response": lines[0]}
    fonts = []
    for line in lines[1:]:
        values = line.split("\t")
        if len(values) == 3:
            fonts.append({"postscript_name": values[0], "family": values[1], "style": values[2]})
    installed_names = {font["postscript_name"] for font in fonts}
    missing = [name for name in required if name not in installed_names]
    if query:
        folded_query = query.casefold()
        fonts = [
            font
            for font in fonts
            if folded_query
            in " ".join((font["postscript_name"], font["family"], font["style"])).casefold()
        ]
    return {
        "status": "passed" if not missing else "mismatch",
        "illustrator_version": header[1],
        "total_font_count": int(header[2]),
        "match_count": len(fonts),
        "query": query,
        "required": list(required),
        "missing": missing,
        "fonts": fonts,
    }
