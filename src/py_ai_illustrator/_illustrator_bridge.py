"""Process boundary for invoking Illustrator ExtendScript through AppleScript."""

from __future__ import annotations

import subprocess
from pathlib import Path


def execute_javascript(
    javascript: str,
    directory: Path,
    *,
    timeout: float,
    application_name: str,
    script_name: str = "illustrator-test.jsx",
) -> subprocess.CompletedProcess[str]:
    """Write a temporary JSX file and execute it in the named Illustrator app.

    This module owns only the process/AppleScript boundary. It deliberately does
    not parse Illustrator results or know anything about the Python document IR.
    """

    script_path = directory / script_name
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
