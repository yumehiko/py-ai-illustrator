"""Run the Illustrator 30.7 area-text overflow regression gate."""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from py_ai_illustrator._illustrator_inspection import run_illustrator_test
from py_ai_illustrator.native import (
    NativeCompileProfile,
    _document_spec,
    _load_document,
    _validate_document,
)
from py_ai_illustrator.native_bridge import (
    NativeCompileRequest,
    NativeContractError,
    NativeRuntimeBridge,
    parse_native_compile_result,
)

ROOT = Path(__file__).parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "native-area-text-overflow.json"
EXPECTED = {
    "fit-area-text": False,
    "overset-area-text": True,
    "point-text-control": None,
}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _by_id(entries: object) -> dict[str, dict[str, Any]]:
    if not isinstance(entries, list):
        return {}
    result: dict[str, dict[str, Any]] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        item_id = entry.get("id")
        if isinstance(item_id, str):
            result[item_id] = entry
            continue
        note = entry.get("note")
        if not isinstance(note, str) or not note.startswith("py-ai-text:"):
            continue
        try:
            payload = json.loads(note.removeprefix("py-ai-text:"))
        except (TypeError, ValueError):
            continue
        if isinstance(payload, dict) and isinstance(payload.get("id"), str):
            result[payload["id"]] = entry
    return result


def run_gate(
    output_directory: str | Path,
    *,
    timeout: float = 180.0,
    application_name: str = "Adobe Illustrator",
) -> dict[str, Any]:
    if platform.system() != "Darwin":
        return {
            "status": "environment-unavailable",
            "error": "Illustrator overflow testing is currently supported on macOS only.",
        }
    output_root = Path(output_directory).resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    output = output_root / "native-area-text-overflow.direct.ai"
    if output.exists():
        return {"status": "invalid-input", "error": f"Output already exists: {output}"}

    document, _ = _load_document(FIXTURE)
    _validate_document(document)
    spec = _document_spec(document, output_root, NativeCompileProfile())
    request = NativeCompileRequest(document=spec, destination=str(output))
    try:
        with tempfile.TemporaryDirectory(prefix="py-ai-overflow-runtime-") as directory:
            completed = NativeRuntimeBridge().execute(
                request,
                Path(directory),
                timeout=timeout,
                application_name=application_name,
            )
    except subprocess.TimeoutExpired:
        return {
            "status": "environment-unavailable",
            "error": f"Illustrator did not answer within {timeout:g} seconds.",
        }
    except (NativeContractError, OSError) as error:
        return {"status": "failed", "error": str(error)}
    if completed.returncode != 0:
        return {
            "status": "environment-unavailable",
            "error": completed.stderr.strip() or "Illustrator AppleScript failed.",
        }
    try:
        runtime = parse_native_compile_result(completed.stdout)
    except NativeContractError as error:
        return {
            "status": "failed",
            "error": str(error),
            "illustrator_response": completed.stdout.strip(),
        }
    if not output.is_file():
        return {"status": "failed", "error": "Runtime did not create the native fixture."}

    digest_before = _sha256(output)
    inspection = run_illustrator_test(
        output,
        timeout=timeout,
        application_name=application_name,
    )
    digest_after = _sha256(output)
    runtime_by_id = _by_id(runtime.get("text_overflows"))
    illustrator = inspection.get("illustrator", {})
    inspection_by_id = _by_id(
        illustrator.get("text_frames") if isinstance(illustrator, dict) else None
    )

    runtime_values = {
        item_id: runtime_by_id.get(item_id, {}).get("overflows") for item_id in EXPECTED
    }
    inspection_values = {
        item_id: inspection_by_id.get(item_id, {}).get("overflows") for item_id in EXPECTED
    }
    runtime_preserved = all(
        runtime_by_id.get(item_id, {}).get("inspection_preserved") is True for item_id in EXPECTED
    )
    inspection_preserved = all(
        inspection_by_id.get(item_id, {}).get("overflow_inspection_preserved") is True
        for item_id in EXPECTED
    )
    errors = runtime.get("errors", [])
    expected_rejection = (
        runtime.get("ok") is False
        and isinstance(errors, list)
        and any(
            "overset-area-text" in str(error) and "area text overflow true" in str(error)
            for error in errors
        )
        and not any("fit-area-text" in str(error) for error in errors)
        and not any("point-text-control" in str(error) for error in errors)
    )
    checks = {
        "illustrator_30_7": runtime.get("illustrator_version") == "30.7.0",
        "direct_verification_rejects_overset": expected_rejection,
        "direct_overflow_values": runtime_values == EXPECTED,
        "inspection_overflow_values": inspection_values == EXPECTED,
        "direct_inspection_preserved": runtime_preserved,
        "dom_inspection_preserved": inspection_preserved,
        "fixture_file_unchanged": digest_before == digest_after,
        "dom_inspection_passed": inspection.get("status") == "passed",
    }
    return {
        "status": "passed" if all(checks.values()) else "failed",
        "fixture": str(FIXTURE),
        "native_output": str(output),
        "checks": checks,
        "runtime_overflows": runtime_values,
        "inspection_overflows": inspection_values,
        "runtime": runtime,
        "inspection": inspection,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output_directory")
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument("--application", default="Adobe Illustrator")
    args = parser.parse_args()
    result = run_gate(
        args.output_directory,
        timeout=args.timeout,
        application_name=args.application,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
