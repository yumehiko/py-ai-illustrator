"""Licensed Illustrator runtime profile for atomic local edits of modern AI files."""

from __future__ import annotations

import hashlib
import json
import platform
import re
import shutil
import struct
import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ._illustrator_bridge import execute_javascript
from ._illustrator_scripts import (
    build_native_local_apply_javascript,
    build_native_local_inspection_javascript,
)
from ._operation_schema import OperationManifest, OperationRequest
from .format import FileFormat, inspect_file
from .modern import read_modern_ai
from .verification import _read_png_rgba, extract_pdf_display, visual_diff

PROFILE_ID = "illustrator-native-local-edit-v1"
_DOCUMENT_FILE_RE = re.compile(rb"(?:%%DocumentFiles:|<stRef:filePath>)(?P<path>[^\r\n<]+)")


def _environment_error(message: str) -> dict[str, object]:
    return {
        "status": "environment-unavailable",
        "profile": PROFILE_ID,
        "runtime_required": True,
        "error": message,
    }


def _parse_runtime_response(completed: subprocess.CompletedProcess[str]) -> dict[str, Any]:
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or "Illustrator AppleScript failed")
    response = completed.stdout.strip()
    if not response:
        raise RuntimeError("Illustrator returned an empty response")
    try:
        result = json.loads(response)
    except json.JSONDecodeError as error:
        raise RuntimeError("Illustrator returned a non-JSON response") from error
    if not isinstance(result, dict):
        raise RuntimeError("Illustrator result must be a JSON object")
    return result


def _source_hints(data: bytes) -> list[str]:
    return list(
        dict.fromkeys(
            match.group("path").decode("utf-8", errors="replace")
            for match in _DOCUMENT_FILE_RE.finditer(data)
        )
    )


def _selector_bounds(item: dict[str, object]) -> list[float] | None:
    value = item.get("geometric_bounds")
    if not isinstance(value, list) or len(value) != 4:
        return None
    left, top, right, bottom = (float(number) for number in value)
    return [left, bottom, right, top]


def _selectors_from_snapshot(
    snapshot: dict[str, object], *, source_hints: list[str]
) -> list[dict[str, object]]:
    selectors: list[dict[str, object]] = []
    texts = snapshot.get("texts")
    if isinstance(texts, list):
        for raw in texts:
            if not isinstance(raw, dict):
                continue
            item = dict(raw)
            selectors.append(
                {
                    "type": "text",
                    "id": item.get("id"),
                    "name": item.get("name") or None,
                    "selector": {"type": "text", "id": item.get("id")},
                    "before": item.get("contents"),
                    "bounds": _selector_bounds(item),
                    "operations": ["replace_text"],
                    "runtime_evidence": item,
                }
            )
    images = snapshot.get("linked_images")
    if isinstance(images, list):
        for raw in images:
            if not isinstance(raw, dict):
                continue
            item = dict(raw)
            source = item.get("source")
            hint = (
                source
                if isinstance(source, str)
                else source_hints[0]
                if len(source_hints) == 1
                else None
            )
            selectors.append(
                {
                    "type": "linked_image",
                    "id": item.get("id"),
                    "name": item.get("name") or None,
                    "selector": {"type": "linked_image", "id": item.get("id")},
                    "before": hint,
                    "bounds": _selector_bounds(item),
                    "operations": ["replace_linked_image_source"],
                    "runtime_evidence": {**item, "source_hint": hint},
                }
            )
    return selectors


def inspect_illustrator_native_local(
    source: str | Path,
    *,
    timeout: float = 90.0,
    application_name: str = "Adobe Illustrator",
    executor: Callable[..., subprocess.CompletedProcess[str]] = execute_javascript,
) -> dict[str, object]:
    """Inspect live text and linked images through a read-only Illustrator copy."""

    source_path = Path(source).resolve()
    if platform.system() != "Darwin":
        return _environment_error("Illustrator native local editing is supported on macOS only.")
    if not source_path.is_file():
        return {
            "status": "invalid-input",
            "profile": PROFILE_ID,
            "error": f"File does not exist: {source_path}",
        }
    if inspect_file(source_path).format is not FileFormat.PDF_COMPATIBLE_AI:
        return {
            "status": "invalid-input",
            "profile": PROFILE_ID,
            "error": "Illustrator native local editing requires a PDF-compatible AI input.",
        }
    if timeout <= 0:
        return {
            "status": "invalid-input",
            "profile": PROFILE_ID,
            "error": "timeout must be positive",
        }
    data = source_path.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    with tempfile.TemporaryDirectory(prefix="py-ai-native-local-inspect-") as directory:
        root = Path(directory)
        input_copy = root / "input.ai"
        shutil.copy2(source_path, input_copy)
        try:
            completed = executor(
                build_native_local_inspection_javascript(input_copy),
                root,
                timeout=timeout,
                application_name=application_name,
                script_name="native-local-inspect.jsx",
            )
            runtime = _parse_runtime_response(completed)
        except subprocess.TimeoutExpired:
            return _environment_error(f"Illustrator did not answer within {timeout:g} seconds.")
        except (OSError, RuntimeError) as error:
            return _environment_error(str(error))
    if runtime.get("ok") is not True or not isinstance(runtime.get("snapshot"), dict):
        return {
            "status": "failed",
            "profile": PROFILE_ID,
            "runtime_required": True,
            "source_sha256": digest,
            "runtime": runtime,
            "selectors": [],
        }
    snapshot = runtime["snapshot"]
    assert isinstance(snapshot, dict)
    selectors = _selectors_from_snapshot(snapshot, source_hints=_source_hints(data))
    return {
        "status": "passed",
        "profile": PROFILE_ID,
        "runtime_required": True,
        "input": str(source_path),
        "source_sha256": digest,
        "illustrator_version": runtime.get("illustrator_version"),
        "selectors": selectors,
        "snapshot": snapshot,
    }


def _selector_matches(request: OperationRequest, candidate: dict[str, object]) -> bool:
    selector = request.selector
    if candidate.get("type") != selector.type:
        return False
    if selector.id is not None and candidate.get("id") != selector.id:
        return False
    if selector.name is not None and candidate.get("name") != selector.name:
        return False
    if selector.bounds is not None:
        bounds = candidate.get("bounds")
        if not isinstance(bounds, list) or len(bounds) != 4:
            return False
        if any(
            abs(float(actual) - expected) > selector.tolerance
            for actual, expected in zip(bounds, selector.bounds, strict=True)
        ):
            return False
    return not selector.ancestors


def _png_dimensions(path: Path) -> tuple[int, int] | None:
    data = path.read_bytes()[:24]
    if len(data) < 24 or not data.startswith(b"\x89PNG\r\n\x1a\n") or data[12:16] != b"IHDR":
        return None
    return struct.unpack(">II", data[16:24])


def plan_illustrator_native_local(
    source: str | Path,
    manifest_data: object,
    *,
    timeout: float = 90.0,
    application_name: str = "Adobe Illustrator",
    executor: Callable[..., subprocess.CompletedProcess[str]] = execute_javascript,
) -> dict[str, object]:
    """Resolve an atomic native local-edit manifest against a fresh DOM snapshot."""

    manifest = OperationManifest.from_dict(manifest_data)
    inspection = inspect_illustrator_native_local(
        source, timeout=timeout, application_name=application_name, executor=executor
    )
    report: dict[str, object] = {
        "feature_profile": {"id": PROFILE_ID, "licensed_runtime_required": True},
        "input": str(Path(source).resolve()),
        "source_sha256": inspection.get("source_sha256"),
        "runtime_inspection": inspection,
        "operations": [],
        "stop_reasons": [],
        "applicable": False,
        "atomic_policy": "copy-open-edit-save-as-reopen-publish-after-all-checks",
    }
    reasons: list[dict[str, object]] = []
    if inspection.get("status") != "passed":
        reasons.append(
            {
                "code": "runtime-inspection-failed",
                "message": str(inspection.get("error") or inspection.get("runtime")),
            }
        )
    digest = inspection.get("source_sha256")
    if manifest.source_sha256 is None:
        reasons.append(
            {
                "code": "source-precondition-required",
                "message": "source_sha256 is required for native local editing.",
            }
        )
    elif digest is not None and manifest.source_sha256 != digest:
        reasons.append(
            {
                "code": "stale-source",
                "message": "source_sha256 precondition does not match the input.",
            }
        )
    selectors = inspection.get("selectors")
    selectors = selectors if isinstance(selectors, list) else []
    resolved: list[dict[str, object]] = []
    for index, operation in enumerate(manifest.operations):
        if operation.op not in {"replace_text", "replace_linked_image_source"}:
            reasons.append(
                {
                    "code": "operation-unsupported",
                    "operation_index": index,
                    "message": f"{operation.op} is outside {PROFILE_ID}.",
                }
            )
            continue
        matches = [
            candidate
            for candidate in selectors
            if isinstance(candidate, dict) and _selector_matches(operation, candidate)
        ]
        if len(matches) != 1:
            reasons.append(
                {
                    "code": "selector-not-unique",
                    "operation_index": index,
                    "message": (
                        f"selector matched {len(matches)} DOM targets; "
                        "exactly one is required."
                    ),
                }
            )
            continue
        candidate = matches[0]
        evidence = candidate.get("runtime_evidence")
        if not isinstance(evidence, dict):
            reasons.append(
                {
                    "code": "runtime-evidence-missing",
                    "operation_index": index,
                    "message": "DOM target has no runtime fingerprint.",
                }
            )
            continue
        after: str
        asset: dict[str, object] | None = None
        if operation.op == "replace_text":
            assert operation.text is not None
            if not operation.text:
                reasons.append(
                    {
                        "code": "empty-text-unsupported",
                        "operation_index": index,
                        "message": "native local replace_text requires non-empty text.",
                    }
                )
                continue
            after = operation.text
        else:
            assert operation.source is not None
            asset_path = Path(operation.source)
            if not asset_path.is_absolute():
                asset_path = Path(source).resolve().parent / asset_path
            asset_path = asset_path.resolve()
            if not asset_path.is_file():
                reasons.append(
                    {
                        "code": "linked-asset-missing",
                        "operation_index": index,
                        "message": f"replacement linked asset does not exist: {asset_path}",
                    }
                )
                continue
            after = str(asset_path)
            dimensions = _png_dimensions(asset_path)
            asset = {
                "path": after,
                "sha256": hashlib.sha256(asset_path.read_bytes()).hexdigest(),
                "png_dimensions": list(dimensions) if dimensions is not None else None,
            }
        resolved_item = {
            "index": index,
            "request": operation.to_dict(),
            "resolved_target": {
                "type": candidate.get("type"),
                "id": candidate.get("id"),
                "name": candidate.get("name"),
                "bounds": candidate.get("bounds"),
            },
            "before": candidate.get("before"),
            "requested_after": after,
            "runtime_evidence": evidence,
            "replacement_asset": asset,
            "expected_visual_bounds": evidence.get("geometric_bounds"),
        }
        resolved.append(resolved_item)
    report["operations"] = resolved
    report["stop_reasons"] = reasons
    report["applicable"] = not reasons and len(resolved) == len(manifest.operations)
    report["runtime_gates"] = [
        "preconditions_rechecked_on-copy",
        "structure-and-non-targets-preserved-before-save",
        "current-format-pdf-compatible-save-as",
        "reopen-target-identity-style-link-and-editability",
        "font-name-preserved-without-substitution",
    ]
    return report


def _script_request(plan: dict[str, object]) -> dict[str, object]:
    operations: list[dict[str, object]] = []
    for raw in plan.get("operations", []):  # type: ignore[union-attr]
        assert isinstance(raw, dict)
        target = raw["resolved_target"]
        evidence = raw["runtime_evidence"]
        assert isinstance(target, dict) and isinstance(evidence, dict)
        operations.append(
            {
                "type": target["type"],
                "id": target["id"],
                "dom_index": evidence["dom_index"],
                "before": evidence,
                "after": raw["requested_after"],
            }
        )
    return {"operations": operations}


def _visual_impacts_within_targets(
    difference_path: Path,
    runtime: dict[str, Any],
    *,
    dpi: int,
) -> tuple[bool, int, list[list[float]]]:
    before = runtime.get("before")
    after = runtime.get("after_reopen")
    if not isinstance(before, dict) or not isinstance(after, dict):
        return False, -1, []
    structure = before.get("structure")
    artboards = structure.get("artboards") if isinstance(structure, dict) else None
    if not isinstance(artboards, list) or len(artboards) != 1 or not isinstance(artboards[0], dict):
        return False, -1, []
    artboard = artboards[0].get("rect")
    if not isinstance(artboard, list) or len(artboard) != 4:
        return False, -1, []
    scale = dpi / 72
    art_left, art_top = float(artboard[0]), float(artboard[1])
    rectangles: list[list[float]] = []
    for operation in runtime.get("request_operations", []):
        if not isinstance(operation, dict):
            continue
        kind = operation.get("type")
        index = operation.get("dom_index")
        if not isinstance(index, int):
            continue
        collection = "texts" if kind == "text" else "linked_images"
        for snapshot in (before, after):
            items = snapshot.get(collection)
            if (
                not isinstance(items, list)
                or index >= len(items)
                or not isinstance(items[index], dict)
            ):
                continue
            bounds = items[index].get("geometric_bounds")
            if not isinstance(bounds, list) or len(bounds) != 4:
                continue
            left, top, right, bottom = (float(value) for value in bounds)
            margin = 10
            rectangles.append(
                [
                    (min(left, right) - art_left) * scale - margin,
                    (art_top - max(top, bottom)) * scale - margin,
                    (max(left, right) - art_left) * scale + margin,
                    (art_top - min(top, bottom)) * scale + margin,
                ]
            )
    width, height, pixels = _read_png_rgba(difference_path.read_bytes())
    outside = 0
    for y in range(height):
        for x in range(width):
            offset = (y * width + x) * 4
            red, green, blue = pixels[offset : offset + 3]
            if red == green == blue:
                continue
            if not any(
                left <= x < right and top <= y < bottom for left, top, right, bottom in rectangles
            ):
                outside += 1
    return bool(rectangles) and outside == 0, outside, rectangles


def apply_illustrator_native_local(
    source: str | Path,
    manifest_data: object,
    output: str | Path,
    *,
    timeout: float = 120.0,
    application_name: str = "Adobe Illustrator",
    executor: Callable[..., subprocess.CompletedProcess[str]] = execute_javascript,
) -> dict[str, object]:
    """Apply one native local-edit manifest atomically and publish only verified output."""

    source_path = Path(source).resolve()
    destination = Path(output).resolve()
    plan = plan_illustrator_native_local(
        source_path,
        manifest_data,
        timeout=timeout,
        application_name=application_name,
        executor=executor,
    )
    base: dict[str, object] = {
        "profile": PROFILE_ID,
        "input": str(source_path),
        "output": None,
        "source_sha256": plan.get("source_sha256"),
        "plan": plan,
        "applied": False,
    }
    if not plan.get("applicable"):
        return {**base, "status": "failed", "stop_reasons": plan.get("stop_reasons", [])}
    if source_path == destination:
        return {
            **base,
            "status": "failed",
            "stop_reasons": [
                {
                    "code": "input-overwrite-refused",
                    "message": "The input file cannot be overwritten.",
                }
            ],
        }
    diff_path = destination.with_name(destination.stem + "-visual-diff.png")
    if destination.exists() or diff_path.exists():
        existing = destination if destination.exists() else diff_path
        return {
            **base,
            "status": "failed",
            "stop_reasons": [
                {
                    "code": "output-exists",
                    "message": f"Refusing to overwrite existing output: {existing}",
                }
            ],
        }
    original = source_path.read_bytes()
    script_request = _script_request(plan)
    with tempfile.TemporaryDirectory(prefix="py-ai-native-local-apply-") as directory:
        root = Path(directory)
        input_copy = root / "input.ai"
        candidate = root / "candidate.ai"
        temporary_diff = root / "visual-difference.png"
        shutil.copy2(source_path, input_copy)
        try:
            completed = executor(
                build_native_local_apply_javascript(input_copy, candidate, script_request),
                root,
                timeout=timeout,
                application_name=application_name,
                script_name="native-local-apply.jsx",
            )
            runtime = _parse_runtime_response(completed)
        except subprocess.TimeoutExpired:
            return {
                **base,
                "status": "environment-unavailable",
                "stop_reasons": [
                    {
                        "code": "runtime-timeout",
                        "message": f"Illustrator did not answer within {timeout:g} seconds.",
                    }
                ],
            }
        except (OSError, RuntimeError) as error:
            return {
                **base,
                "status": "environment-unavailable",
                "stop_reasons": [{"code": "runtime-failed", "message": str(error)}],
            }
        runtime["request_operations"] = script_request["operations"]
        if runtime.get("ok") is not True or not candidate.is_file():
            return {
                **base,
                "status": "failed",
                "runtime": runtime,
                "stop_reasons": [
                    {
                        "code": "runtime-validation-failed",
                        "message": str(runtime.get("error") or runtime.get("checks")),
                    }
                ],
            }
        modern = read_modern_ai(candidate)
        display = extract_pdf_display(candidate)
        difference = visual_diff(
            source_path,
            candidate,
            temporary_diff,
            dpi=144,
            threshold=8,
        )
        target_limited, outside_pixels, allowed_rectangles = _visual_impacts_within_targets(
            temporary_diff, runtime, dpi=144
        )
        runtime_checks = runtime.get("checks")
        validation = {
            "runtime_checks_passed": isinstance(runtime_checks, dict)
            and all(value is True for value in runtime_checks.values()),
            "output_is_pdf_compatible_ai": inspect_file(candidate).format
            is FileFormat.PDF_COMPATIBLE_AI,
            "private_data_reparsed": modern.private_data_status == "extracted",
            "container_accepted": modern.container_status == "parsed",
            "pdf_display_reparsed": display.valid,
            "pdf_and_private_timestamps_match": display.private_data_freshness
            == "timestamps_match",
            "visual_impact_within_target_bounds": target_limited,
            "source_not_overwritten": source_path.read_bytes() == original,
        }
        if not all(validation.values()):
            return {
                **base,
                "status": "failed",
                "runtime": runtime,
                "validation": validation,
                "visual_diff": difference.to_dict(),
                "visual_bounds_evidence": {
                    "threshold": 8,
                    "outside_changed_pixels": outside_pixels,
                    "allowed_raster_rectangles": allowed_rectangles,
                },
                "stop_reasons": [
                    {"code": "post-apply-validation-failed", "message": str(validation)}
                ],
            }
        output_data = candidate.read_bytes()
        try:
            with destination.open("xb") as stream:
                stream.write(output_data)
            with diff_path.open("xb") as stream:
                stream.write(temporary_diff.read_bytes())
        except OSError:
            destination.unlink(missing_ok=True)
            diff_path.unlink(missing_ok=True)
            raise
    return {
        **base,
        "status": "applied",
        "applied": True,
        "output": str(destination),
        "output_sha256": hashlib.sha256(output_data).hexdigest(),
        "runtime": runtime,
        "validation": validation,
        "visual_diff": {**difference.to_dict(), "artifact": str(diff_path)},
        "visual_bounds_evidence": {
            "threshold": 8,
            "outside_changed_pixels": outside_pixels,
            "allowed_raster_rectangles": allowed_rectangles,
        },
        "stop_reasons": [],
    }


__all__ = [
    "PROFILE_ID",
    "apply_illustrator_native_local",
    "inspect_illustrator_native_local",
    "plan_illustrator_native_local",
]
