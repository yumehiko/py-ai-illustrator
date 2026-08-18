from __future__ import annotations

import hashlib
import json
import shutil
from datetime import UTC, datetime
from pathlib import Path

import pytest

from py_ai_illustrator import (
    Color,
    ModernWriteError,
    apply_edit_plan,
    extract_pdf_display,
    inspect_modern_container_translate_targets,
    inspect_modern_representation_consistency,
    patch_modern_path_fill,
    patch_modern_path_stroke,
    patch_modern_path_translate,
    patch_modern_text,
    plan_edit,
    read_modern_ai,
    visual_diff,
)
from py_ai_illustrator.cli import main
from py_ai_illustrator.modern_writing import _pdf_paint_matches

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "examples/styled-table.native.ai"
TARGET_ID = "subscription-comparison.background.header"
MODIFICATION_TIME = datetime(2026, 8, 18, 12, 34, 56, tzinfo=UTC)


def test_modern_fill_patch_updates_private_data_and_pdf_display_atomically(
    tmp_path: Path,
) -> None:
    output = tmp_path / "updated.ai"
    source_bytes = SOURCE.read_bytes()

    report = patch_modern_path_fill(
        SOURCE,
        output,
        path_id=TARGET_ID,
        color=Color(1, 0, 0),
        modification_time=MODIFICATION_TIME,
    )

    assert SOURCE.read_bytes() == source_bytes
    assert output.read_bytes().startswith(source_bytes)
    assert report.private_data_object == "17 0 R"
    assert report.pdf_content_object == "11 0 R"
    assert report.modification_date == "D:20260818123456Z"
    assert all(report.validation.values())

    reread = read_modern_ai(output)
    assert reread.semantic is not None and reread.semantic.document is not None
    path = reread.semantic.document.layers[0].paths[0]
    assert path.id == TARGET_ID
    assert path.unknown["modern_style_spans"]["fill"]["alternate_rgb"] == [1.0, 0.0, 0.0]
    display = extract_pdf_display(output)
    assert display.valid is True
    assert display.private_data_freshness == "timestamps_match"


def test_cross_representation_consistency_checks_proven_paint_and_geometry() -> None:
    report = inspect_modern_representation_consistency(SOURCE)

    assert report["status"] == "consistent_for_proven_targets"
    assert report["checked_count"] > 0
    assert report["mismatch_count"] == 0
    assert _pdf_paint_matches(b"1 0 0 rg", Color(1, 0, 0), stroke=False) is True
    assert _pdf_paint_matches(b"0 1 0 rg", Color(1, 0, 0), stroke=False) is False


def test_modern_fill_patch_is_deterministic_with_explicit_timestamp(tmp_path: Path) -> None:
    first = tmp_path / "first.ai"
    second = tmp_path / "second.ai"
    arguments = {
        "path_id": TARGET_ID,
        "color": Color(0.2, 0.4, 0.6),
        "modification_time": MODIFICATION_TIME,
    }

    first_report = patch_modern_path_fill(SOURCE, first, **arguments)
    second_report = patch_modern_path_fill(SOURCE, second, **arguments)

    assert first.read_bytes() == second.read_bytes()
    assert first_report.output_sha256 == second_report.output_sha256


def test_modern_fill_patch_honors_source_precondition_and_never_creates_output(
    tmp_path: Path,
) -> None:
    output = tmp_path / "stale.ai"

    with pytest.raises(ModernWriteError, match="source_sha256 precondition"):
        patch_modern_path_fill(
            SOURCE,
            output,
            path_id=TARGET_ID,
            color=Color(1, 0, 0),
            source_sha256="0" * 64,
            modification_time=MODIFICATION_TIME,
        )

    assert not output.exists()


def test_modern_fill_patch_stops_on_unknown_or_unfilled_target(tmp_path: Path) -> None:
    with pytest.raises(ModernWriteError, match="matched 0 paths"):
        patch_modern_path_fill(
            SOURCE,
            tmp_path / "missing.ai",
            path_id="missing",
            color=Color(1, 0, 0),
            modification_time=MODIFICATION_TIME,
        )

    curve = ROOT / "examples/cmyk-curve.native.ai"
    curve_id = "cmyk-curve"
    with pytest.raises(ModernWriteError, match="cannot add paint"):
        patch_modern_path_fill(
            curve,
            tmp_path / "curve.ai",
            path_id=curve_id,
            color=Color(1, 0, 0),
            modification_time=MODIFICATION_TIME,
        )

    assert sorted(tmp_path.iterdir()) == []


def test_modern_fill_patch_refuses_existing_output(tmp_path: Path) -> None:
    output = tmp_path / "existing.ai"
    output.write_bytes(b"keep")

    with pytest.raises(FileExistsError, match="refusing to overwrite"):
        patch_modern_path_fill(
            SOURCE,
            output,
            path_id=TARGET_ID,
            color=Color(1, 0, 0),
            modification_time=MODIFICATION_TIME,
        )

    assert output.read_bytes() == b"keep"


def test_modern_stroke_patch_synchronizes_curved_path(tmp_path: Path) -> None:
    source = ROOT / "examples/cmyk-curve.native.ai"
    output = tmp_path / "curve.ai"

    report = patch_modern_path_stroke(
        source,
        output,
        path_id="cmyk-curve",
        color=Color(1, 0, 0),
        modification_time=MODIFICATION_TIME,
    )

    assert report.operation == "set_stroke"
    assert all(report.validation.values())
    reread = read_modern_ai(output)
    assert reread.semantic is not None and reread.semantic.document is not None
    curve = reread.semantic.document.layers[0].paths[0]
    assert curve.unknown["modern_style_spans"]["stroke"]["alternate_rgb"] == [1.0, 0.0, 0.0]


def test_modern_text_patch_synchronizes_ai11_story_and_pdf_text(tmp_path: Path) -> None:
    output = tmp_path / "text.ai"

    report = patch_modern_text(
        SOURCE,
        output,
        text_id="subscription-comparison.header.plan",
        text="Offer",
        modification_time=MODIFICATION_TIME,
    )

    assert report.operation == "replace_text"
    assert report.before == "Plan"
    assert report.after == "Offer"
    assert report.metadata_objects == ("2 0 R",)
    assert all(report.validation.values())
    reread = read_modern_ai(output)
    assert reread.semantic is not None
    target = next(
        item
        for item in reread.semantic.partial_nodes
        if item.id == "subscription-comparison.header.plan"
    )
    assert target.known_fields["text"] == "Offer"


def test_modern_text_patch_stops_when_display_or_story_value_is_not_unique(
    tmp_path: Path,
) -> None:
    with pytest.raises(ModernWriteError, match="exact story values"):
        patch_modern_text(
            SOURCE,
            tmp_path / "duplicate.ai",
            text_id="subscription-comparison.row-1.plan",
            text="Team",
            modification_time=MODIFICATION_TIME,
        )

    assert not (tmp_path / "duplicate.ai").exists()


def test_modern_rectangle_translation_updates_both_geometries(tmp_path: Path) -> None:
    output = tmp_path / "translated.ai"

    report = patch_modern_path_translate(
        SOURCE,
        output,
        path_id=TARGET_ID,
        dx=10,
        dy=-5,
        modification_time=MODIFICATION_TIME,
    )

    assert report.operation == "translate"
    assert all(report.validation.values())
    reread = read_modern_ai(output)
    assert reread.semantic is not None and reread.semantic.document is not None
    path = reread.semantic.document.layers[0].paths[0]
    assert [(point.x, point.y) for point in path.points] == [
        (58.0, 257.0),
        (574.0, 257.0),
        (574.0, 295.0),
        (58.0, 295.0),
        (58.0, 257.0),
    ]


@pytest.mark.skipif(shutil.which("pdftocairo") is None, reason="Poppler is not installed")
def test_safe_edit_plan_applies_modern_translation_with_union_impact_bounds(
    tmp_path: Path,
) -> None:
    output = tmp_path / "translated.ai"
    request = {
        "schema_version": 1,
        "operations": [
            {
                "op": "translate",
                "selector": {"type": "path", "id": TARGET_ID},
                "dx": 10,
                "dy": -5,
            }
        ],
    }

    plan = plan_edit(SOURCE, request)
    assert plan.applicable is True
    assert plan.report["expected_visual_impact"]["bounds"] == [48.0, 257.0, 526.0, 43.0]
    result = apply_edit_plan(plan, output)

    assert result["applied"] is True
    assert result["validation"]["visual_impact_within_target_bounds"] is True


@pytest.mark.skipif(shutil.which("pdftocairo") is None, reason="Poppler is not installed")
def test_modern_atomic_batch_replans_and_validates_each_distinct_target(
    tmp_path: Path,
) -> None:
    output = tmp_path / "batch.ai"
    request = {
        "schema_version": 1,
        "operations": [
            {
                "op": "set_fill",
                "selector": {"type": "path", "id": TARGET_ID},
                "color": {"red": 1, "green": 0, "blue": 0},
            },
            {
                "op": "replace_text",
                "selector": {
                    "type": "text",
                    "id": "subscription-comparison.row-0.plan",
                },
                "text": "Entry",
            },
        ],
    }

    plan = plan_edit(SOURCE, request)
    assert plan.applicable is True
    result = apply_edit_plan(plan, output)

    assert result["applied"] is True
    assert result["operation"] == "batch"
    assert result["operation_count"] == 2
    assert all(result["validation"].values())
    assert all(
        all(operation["validation"].values())
        for operation in result["operation_results"]
    )
    reread = read_modern_ai(output)
    assert reread.semantic is not None and reread.semantic.document is not None
    assert reread.semantic.document.layers[0].paths[0].unknown[
        "modern_style_spans"
    ]["fill"]["alternate_rgb"] == [1.0, 0.0, 0.0]
    text = next(
        item
        for item in reread.semantic.partial_nodes
        if item.id == "subscription-comparison.row-0.plan"
    )
    assert text.known_fields["text"] == "Entry"


@pytest.mark.skipif(shutil.which("pdftocairo") is None, reason="Poppler is not installed")
def test_modern_batch_replans_repeated_target_in_manifest_order(tmp_path: Path) -> None:
    request = {
        "schema_version": 1,
        "operations": [
            {
                "op": "set_fill",
                "selector": {"type": "path", "id": TARGET_ID},
                "color": {"red": 1, "green": 0, "blue": 0},
            },
            {
                "op": "translate",
                "selector": {"type": "path", "id": TARGET_ID},
                "dx": 2,
                "dy": 3,
            },
        ],
    }

    output = tmp_path / "repeated-target.ai"
    plan = plan_edit(SOURCE, request)
    result = apply_edit_plan(plan, output)

    assert plan.applicable is True
    assert plan.report["batch_policy"]["ordering"] == "manifest-order"
    assert result["applied"] is True
    assert result["operation_count"] == 2
    reread = read_modern_ai(output)
    assert reread.semantic is not None and reread.semantic.document is not None
    path = reread.semantic.document.layers[0].paths[0]
    assert path.points[0].x == pytest.approx(50.0)
    assert path.points[0].y == pytest.approx(265.0)
    assert path.unknown["modern_style_spans"]["fill"]["alternate_rgb"] == [
        1.0,
        0.0,
        0.0,
    ]


def test_modern_root_to_parent_hierarchy_selector_resolves_path() -> None:
    request = {
        "schema_version": 1,
        "operations": [
            {
                "op": "set_fill",
                "selector": {
                    "type": "path",
                    "name": TARGET_ID,
                    "ancestors": [
                        {"type": "layer", "id": "Subscription_table"}
                    ],
                },
                "color": {"red": 1, "green": 0, "blue": 0},
            }
        ],
    }

    plan = plan_edit(SOURCE, request)

    assert plan.applicable is True
    assert plan.report["operations"][0]["resolved_target"]["id"] == TARGET_ID


def test_modern_container_translate_stops_when_any_descendant_is_unproven() -> None:
    source = ROOT / "examples/packaging-labels.native.ai"
    inventory = inspect_modern_container_translate_targets(source)
    group = next(
        item for item in inventory["selectors"] if item["id"] == "modern-0-group-0"
    )
    request = {
        "schema_version": 1,
        "operations": [
            {
                "op": "translate",
                "selector": {"type": "group", "id": "modern-0-group-0"},
                "dx": 5,
                "dy": 5,
            }
        ],
    }

    plan = plan_edit(source, request)

    assert group["writable"] is False
    assert any("partial descendants" in reason for reason in group["stop_reasons"])
    assert plan.applicable is False
    assert plan.report["stop_reasons"][0]["code"] == "operation-not-applicable"


@pytest.mark.skipif(shutil.which("pdftocairo") is None, reason="Poppler is not installed")
def test_safe_edit_plan_applies_modern_stroke_with_bounded_visual_impact(
    tmp_path: Path,
) -> None:
    source = ROOT / "examples/cmyk-curve.native.ai"
    output = tmp_path / "curve.ai"
    request = {
        "schema_version": 1,
        "operations": [
            {
                "op": "set_stroke",
                "selector": {"type": "path", "id": "cmyk-curve"},
                "color": {"red": 1, "green": 0, "blue": 0},
            }
        ],
    }

    plan = plan_edit(source, request)
    assert plan.applicable is True
    result = apply_edit_plan(plan, output)

    assert result["applied"] is True
    assert result["operation"] == "set_stroke"
    assert result["validation"]["visual_impact_within_target_bounds"] is True


@pytest.mark.skipif(shutil.which("pdftocairo") is None, reason="Poppler is not installed")
def test_safe_edit_plan_applies_uniquely_placed_modern_text(tmp_path: Path) -> None:
    output = tmp_path / "text.ai"
    request = {
        "schema_version": 1,
        "operations": [
            {
                "op": "replace_text",
                "selector": {
                    "type": "text",
                    "name": "Header: Plan",
                    "bounds": [59.9, 277.3, 96.1, 289.5],
                    "tolerance": 0.11,
                },
                "text": "Offer",
            }
        ],
    }

    plan = plan_edit(SOURCE, request)
    assert plan.applicable is True
    result = apply_edit_plan(plan, output)

    assert result["applied"] is True
    assert result["operation"] == "replace_text"
    assert result["validation"]["private_data_value_matches"] is True
    assert result["validation"]["visual_impact_within_target_bounds"] is True


@pytest.mark.skipif(shutil.which("pdftocairo") is None, reason="Poppler is not installed")
def test_cli_plan_apply_validate_preview_completes_modern_editing_workflow(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    operations = tmp_path / "operations.json"
    output = tmp_path / "updated.ai"
    preview = tmp_path / "updated.png"
    operations.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "operations": [
                    {
                        "op": "set_fill",
                        "selector": {"type": "path", "id": TARGET_ID},
                        "color": {"red": 1, "green": 0, "blue": 0},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    assert main(["plan", str(SOURCE), str(operations)]) == 0
    planned = json.loads(capsys.readouterr().out)
    assert planned["applicable"] is True
    assert planned["feature_profile"]["id"] == "modern-ai-synchronized-patch-v1"
    assert planned["operations"][0]["representations"] == [
        "illustrator-private-data",
        "pdf-display",
    ]

    assert main(["apply", str(SOURCE), str(operations), "-o", str(output)]) == 0
    applied = json.loads(capsys.readouterr().out)
    assert applied["applied"] is True
    assert applied["validation"]["private_data_value_matches"] is True
    assert applied["validation"]["visual_impact_within_target_bounds"] is True
    assert applied["visual_diff"]["equal"] is False
    assert output.exists()

    assert main(["validate", str(output)]) == 0
    validated = json.loads(capsys.readouterr().out)
    assert validated["valid"] is True
    assert validated["pdf_display"]["private_data_freshness"] == "timestamps_match"

    assert main(["preview", str(output), "-o", str(preview), "--dpi", "72"]) == 0
    previewed = json.loads(capsys.readouterr().out)
    assert previewed["page_count"] == 1
    assert preview.exists()


@pytest.mark.skipif(shutil.which("pdftocairo") is None, reason="Poppler is not installed")
def test_modern_fill_patch_has_the_expected_visual_impact(tmp_path: Path) -> None:
    output = tmp_path / "updated.ai"
    difference = tmp_path / "difference.png"
    patch_modern_path_fill(
        SOURCE,
        output,
        path_id=TARGET_ID,
        color=Color(1, 0, 0),
        modification_time=MODIFICATION_TIME,
    )

    result = visual_diff(SOURCE, output, difference, dpi=72)

    assert result.equal is False
    assert result.pages[0].changed_pixels > 0
    assert result.pages[0].changed_bounds == (48, 60, 564, 98)
    assert hashlib.sha256(difference.read_bytes()).hexdigest() == result.pages[0].png_sha256
