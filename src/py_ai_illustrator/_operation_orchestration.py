"""Apply orchestration for validated operation plans.

Planning and selector resolution live in _operation_plan. This module is the
only operation boundary that invokes mutation backends and commits output.
"""

from __future__ import annotations

import hashlib
import tempfile
from pathlib import Path as FilePath

from ._modern_patch import (
    ModernWriteError,
    patch_modern_path_fill,
    patch_modern_path_stroke,
    patch_modern_path_translate,
    patch_modern_text,
)
from ._operation_plan import (
    LegacyEditPlan,
    ModernEditPlan,
    _base_report,
    _legacy_visual_impact_allowed,
    _outside_replacements_equal,
    _plan_modern_edit,
    _stop,
    plan_edit,
    unexpected_semantic_differences,
)
from .legacy import (
    UnsupportedLegacyFeature,
    apply_legacy_patch,
    read_ai7,
    reads_ai7,
)
from .semantic import semantic_diff
from .verification import extract_pdf_display, visual_diff


def _failed_apply(plan: LegacyEditPlan, code: str, message: str) -> dict[str, object]:
    return {
        "status": "failed",
        "applied": False,
        "input": str(plan.input_path),
        "output": None,
        "source_sha256": plan.report.get("source_sha256"),
        "output_sha256": None,
        "compatibility": {"before": plan.report.get("compatibility"), "after": None},
        "validation": {
            "output_reparsed": False,
            "bytes_outside_replacement_spans_identical": False,
            "semantic_impact_allowed": False,
            "semantic_diff_matches_plan": False,
            "visual_impact_within_target_bounds": False,
        },
        "semantic_diff": None,
        "warnings": plan.report.get("warnings", []),
        "stop_reasons": [_stop(code, message)],
    }


def _apply_modern_edit_plan(plan: ModernEditPlan, output: str | FilePath) -> dict[str, object]:
    if len(plan.resolved_operations) > 1 or (
        plan.request is not None and plan.request.selector.type in {"group", "layer"}
    ):
        return _apply_modern_batch(plan, output)
    destination = FilePath(output)
    if not plan.applicable or plan.request is None or plan.capability is None:
        return {
            "status": "failed",
            "applied": False,
            "input": str(plan.input_path),
            "output": None,
            "source_sha256": plan.report.get("source_sha256"),
            "output_sha256": None,
            "validation": {},
            "stop_reasons": plan.report.get("stop_reasons")
            or [_stop("plan-not-applicable", "The edit plan is not applicable.")],
        }
    if plan.input_path.resolve() == destination.resolve():
        return {
            "status": "failed",
            "applied": False,
            "output": None,
            "stop_reasons": [
                _stop("input-overwrite-refused", "The input file cannot be overwritten.")
            ],
        }
    if destination.exists():
        return {
            "status": "failed",
            "applied": False,
            "output": None,
            "stop_reasons": [
                _stop("output-exists", f"Output already exists: {destination}")
            ],
        }
    try:
        if plan.request.op == "replace_text":
            assert plan.request.text is not None
            result = patch_modern_text(
                plan.input_path,
                destination,
                text_id=str(plan.capability["id"]),
                text=plan.request.text,
                source_sha256=str(plan.report["source_sha256"]),
            )
        elif plan.request.op == "translate":
            assert plan.request.dx is not None and plan.request.dy is not None
            result = patch_modern_path_translate(
                plan.input_path,
                destination,
                path_id=str(plan.capability["id"]),
                dx=plan.request.dx,
                dy=plan.request.dy,
                source_sha256=str(plan.report["source_sha256"]),
            )
        else:
            assert plan.request.color is not None
            patcher = (
                patch_modern_path_fill
                if plan.request.op == "set_fill"
                else patch_modern_path_stroke
            )
            result = patcher(
                plan.input_path,
                destination,
                path_id=str(plan.capability["id"]),
                color=plan.request.color,
                source_sha256=str(plan.report["source_sha256"]),
            )
        with tempfile.TemporaryDirectory(prefix="py-ai-operation-impact-") as directory:
            difference = visual_diff(
                plan.input_path,
                destination,
                FilePath(directory) / "difference.png",
                dpi=144,
            )
        bounds_value = plan.capability.get("pdf_impact_bounds") if plan.capability else None
        display = extract_pdf_display(plan.input_path)
        impact_allowed = False
        if (
            isinstance(bounds_value, list)
            and len(bounds_value) == 4
            and display.pages
            and display.pages[0].crop_box is not None
            and len(difference.pages) == 1
        ):
            x, y, width, height = (float(value) for value in bounds_value)
            crop = display.pages[0].crop_box
            scale = 144 / 72
            expected = (
                (x - crop[0]) * scale,
                (crop[3] - (y + height)) * scale,
                (x + width - crop[0]) * scale,
                (crop[3] - y) * scale,
            )
            actual = difference.pages[0].changed_bounds
            # Stroke width and antialiasing can extend several raster pixels beyond path geometry.
            margin = 8
            impact_allowed = actual is None or (
                actual[0] >= expected[0] - margin
                and actual[1] >= expected[1] - margin
                and actual[2] <= expected[2] + margin
                and actual[3] <= expected[3] + margin
            )
        if not impact_allowed:
            destination.unlink(missing_ok=True)
            raise ModernWriteError("visual diff escaped the requested path bounds")
    except (OSError, ValueError, RuntimeError) as error:
        destination.unlink(missing_ok=True)
        return {
            "status": "failed",
            "applied": False,
            "input": str(plan.input_path),
            "output": None,
            "source_sha256": plan.report.get("source_sha256"),
            "output_sha256": None,
            "validation": {},
            "stop_reasons": [_stop("apply-validation-failed", str(error))],
        }
    report = result.to_dict()
    report.update(
        {
            "status": "applied",
            "applied": True,
            "visual_diff": difference.to_dict(),
            "validation": {
                **result.validation,
                "visual_impact_within_target_bounds": impact_allowed,
            },
            "stop_reasons": [],
        }
    )
    return report


def _apply_modern_batch(plan: ModernEditPlan, output: str | FilePath) -> dict[str, object]:
    """Apply independently re-planned modern operations to temporary incremental revisions."""

    destination = FilePath(output)
    if not plan.applicable:
        return {
            "status": "failed",
            "applied": False,
            "input": str(plan.input_path),
            "output": None,
            "stop_reasons": plan.report.get("stop_reasons")
            or [_stop("plan-not-applicable", "The edit plan is not applicable.")],
        }
    if plan.input_path.resolve() == destination.resolve():
        return {
            "status": "failed",
            "applied": False,
            "output": None,
            "stop_reasons": [
                _stop("input-overwrite-refused", "The input file cannot be overwritten.")
            ],
        }
    if destination.exists():
        return {
            "status": "failed",
            "applied": False,
            "output": None,
            "stop_reasons": [
                _stop("output-exists", f"Output already exists: {destination}")
            ],
        }
    original = plan.input_path.read_bytes()
    operation_results: list[dict[str, object]] = []
    final_data: bytes | None = None
    try:
        with tempfile.TemporaryDirectory(prefix="py-ai-modern-batch-") as directory:
            current = plan.input_path
            for index, (request, _capability) in enumerate(plan.resolved_operations):
                current_digest = hashlib.sha256(current.read_bytes()).hexdigest()
                subreport, _input_format = _base_report(current)
                subplan = _plan_modern_edit(
                    current,
                    {
                        "schema_version": 1,
                        "source_sha256": current_digest,
                        "operations": [request.to_dict()],
                    },
                    subreport,
                )
                if not subplan.applicable:
                    raise ModernWriteError(
                        f"operation {index} no longer applies after prior operations: "
                        f"{subplan.report.get('stop_reasons')}"
                    )
                step_output = FilePath(directory) / f"step-{index}.ai"
                step_result = _apply_modern_edit_plan(subplan, step_output)
                if not step_result.get("applied"):
                    raise ModernWriteError(
                        f"operation {index} failed: {step_result.get('stop_reasons')}"
                    )
                visual = step_result.get("visual_diff")
                visual_summary = None
                if isinstance(visual, dict):
                    visual_summary = {
                        "profile": visual.get("profile"),
                        "equal": visual.get("equal"),
                        "changed_pixels": visual.get("changed_pixels"),
                        "pages": [
                            {
                                "index": page.get("index"),
                                "changed_pixels": page.get("changed_pixels"),
                                "changed_ratio": page.get("changed_ratio"),
                                "changed_bounds": page.get("changed_bounds"),
                            }
                            for page in visual.get("pages", [])
                            if isinstance(page, dict)
                        ],
                    }
                operation_results.append(
                    {
                        "index": index,
                        "operation": step_result.get("operation"),
                        "selector": step_result.get("selector"),
                        "validation": step_result.get("validation"),
                        "visual_diff": visual_summary,
                    }
                )
                current = step_output
            final_data = current.read_bytes()
        with destination.open("xb") as stream:
            stream.write(final_data)
    except (OSError, ValueError, RuntimeError) as error:
        destination.unlink(missing_ok=True)
        return {
            "status": "failed",
            "applied": False,
            "input": str(plan.input_path),
            "output": None,
            "source_sha256": hashlib.sha256(original).hexdigest(),
            "output_sha256": None,
            "operation_results": operation_results,
            "stop_reasons": [_stop("atomic-batch-failed", str(error))],
        }
    assert final_data is not None
    return {
        "status": "applied",
        "applied": True,
        "operation": "batch",
        "input": str(plan.input_path),
        "output": str(destination),
        "source_sha256": hashlib.sha256(original).hexdigest(),
        "output_sha256": hashlib.sha256(final_data).hexdigest(),
        "operation_count": len(operation_results),
        "operation_results": operation_results,
        "validation": {
            "all_operations_validated": all(
                all(result["validation"].values())  # type: ignore[union-attr]
                for result in operation_results
            ),
            "atomic_destination_created": destination.read_bytes() == final_data,
            "source_not_overwritten": plan.input_path.read_bytes() == original,
            "original_source_prefix_preserved": final_data.startswith(original),
        },
        "stop_reasons": [],
    }


def apply_edit_plan(
    plan: LegacyEditPlan | ModernEditPlan, output: str | FilePath
) -> dict[str, object]:
    """Atomically apply a prepared plan to a distinct, non-existing output path."""

    if isinstance(plan, ModernEditPlan):
        return _apply_modern_edit_plan(plan, output)
    destination = FilePath(output)
    if not plan.applicable or plan.read_result is None or plan.patch_plan is None:
        result = _failed_apply(plan, "plan-not-applicable", "The edit plan is not applicable.")
        result["stop_reasons"] = plan.report.get("stop_reasons") or result["stop_reasons"]
        return result
    if plan.input_path.resolve() == destination.resolve():
        return _failed_apply(
            plan, "input-overwrite-refused", "The input file cannot be overwritten."
        )
    if destination.exists():
        return _failed_apply(
            plan,
            "output-exists",
            f"Output already exists and will not be overwritten: {destination}",
        )
    try:
        current = read_ai7(plan.input_path)
        candidate = apply_legacy_patch(current, plan.patch_plan)
        candidate_after = reads_ai7(candidate.data)
        candidate_diff = semantic_diff(current.document, candidate_after.document)
        unexpected = unexpected_semantic_differences(candidate_diff, plan.resolved_operations)
        matches_plan = candidate_diff == plan.expected_diff
        bytes_preserved = _outside_replacements_equal(
            current.source.data, candidate.data, plan.patch_plan.replacements
        )
        if unexpected:
            raise UnsupportedLegacyFeature(
                "Output would contain semantic changes outside the requested impact: "
                + ", ".join(item.path for item in unexpected)
            )
        if not matches_plan:
            raise UnsupportedLegacyFeature("Output semantic diff does not match the dry-run plan.")
        if not bytes_preserved:
            raise UnsupportedLegacyFeature("Output would change bytes outside replacement spans.")
    except (OSError, ValueError, UnicodeError) as error:
        return _failed_apply(plan, "apply-validation-failed", str(error))

    created = False
    try:
        with destination.open("xb") as stream:
            stream.write(candidate.data)
        created = True
        disk_data = destination.read_bytes()
        disk_after = reads_ai7(disk_data)
        disk_diff = semantic_diff(current.document, disk_after.document)
        disk_unexpected = unexpected_semantic_differences(disk_diff, plan.resolved_operations)
        disk_matches_plan = disk_diff == plan.expected_diff
        disk_bytes_preserved = _outside_replacements_equal(
            current.source.data, disk_data, plan.patch_plan.replacements
        )
        if disk_unexpected or not disk_matches_plan or not disk_bytes_preserved:
            raise UnsupportedLegacyFeature("Written output failed post-write validation.")
        with tempfile.TemporaryDirectory(prefix="py-ai-legacy-operation-impact-") as directory:
            difference = visual_diff(
                plan.input_path,
                destination,
                FilePath(directory) / "difference.png",
                dpi=144,
            )
        impact_allowed = len(difference.pages) == 1 and _legacy_visual_impact_allowed(
            plan.resolved_operations,
            difference.pages[0].changed_bounds,
            document_height=current.document.height,
            dpi=144,
        )
        if not impact_allowed:
            raise UnsupportedLegacyFeature(
                "Reference-raster diff escaped the requested operation bounds."
            )
    except (OSError, ValueError, UnicodeError) as error:
        if created:
            destination.unlink(missing_ok=True)
        return _failed_apply(plan, "output-write-or-validation-failed", str(error))

    return {
        "status": "applied",
        "applied": True,
        "input": str(plan.input_path),
        "output": str(destination),
        "source_sha256": plan.patch_plan.source_sha256,
        "output_sha256": hashlib.sha256(disk_data).hexdigest(),
        "replacement_count": len(plan.patch_plan.replacements),
        "compatibility": {
            "before": current.compatibility_report(),
            "after": disk_after.compatibility_report(),
        },
        "validation": {
            "output_reparsed": True,
            "bytes_outside_replacement_spans_identical": disk_bytes_preserved,
            "semantic_impact_allowed": not disk_unexpected,
            "semantic_diff_matches_plan": disk_matches_plan,
            "visual_impact_within_target_bounds": impact_allowed,
        },
        "visual_diff": difference.to_dict(),
        "semantic_diff": disk_diff.to_dict(),
        "warnings": [diagnostic.message for diagnostic in disk_after.diagnostics],
        "stop_reasons": [],
    }


def apply_edit(
    source: str | FilePath, request_data: object, output: str | FilePath
) -> dict[str, object]:
    """Plan and apply an operation manifest through the same validated workflow."""

    return apply_edit_plan(plan_edit(source, request_data), output)
__all__ = ["apply_edit", "apply_edit_plan"]
