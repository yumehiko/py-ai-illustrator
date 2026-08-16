from pathlib import Path
from subprocess import CompletedProcess, TimeoutExpired

from py_ai_illustrator import illustrator
from py_ai_illustrator.illustrator import (
    _build_export_javascript,
    _build_font_catalog_javascript,
    _build_javascript,
    _build_native_materialization_javascript,
    _build_roundtrip_javascript,
    _compare_roundtrip_semantics,
    _compare_structure,
    _document_text_frames_dom_order,
    _expected_structure,
)
from py_ai_illustrator.model import Color, ControlPoint, Document, Point


def test_javascript_closes_only_its_document_without_saving(tmp_path: Path) -> None:
    source = tmp_path / 'fixture "quoted".ai'
    javascript = _build_javascript(source)
    assert "documentRef = app.open(source)" in javascript
    assert "documentRef.close(SaveOptions.DONOTSAVECHANGES)" in javascript
    assert "current document" not in javascript
    assert "String.fromCharCode(92)" in javascript
    assert "String.fromCharCode(34)" in javascript
    assert "\\" not in javascript


def test_export_javascript_creates_and_closes_only_its_cmyk_fixture(tmp_path: Path) -> None:
    javascript = _build_export_javascript(tmp_path / 'native "curve".ai', "cmyk-curve")
    assert "app.documents.add(DocumentColorSpace.CMYK, 200, 200)" in javascript
    assert "Compatibility.ILLUSTRATOR8" in javascript
    assert "documentRef.saveAs(destination, options)" in javascript
    assert "documentRef.close(SaveOptions.DONOTSAVECHANGES)" in javascript
    assert "current document" not in javascript
    assert "\\" not in javascript


def test_roundtrip_javascript_resaves_and_closes_only_its_document(tmp_path: Path) -> None:
    javascript = _build_roundtrip_javascript(
        tmp_path / 'source "quoted".ai',
        tmp_path / 'resaved "quoted".ai',
    )
    assert "documentRef = app.open(source)" in javascript
    assert "Compatibility.ILLUSTRATOR8" in javascript
    assert "documentRef.saveAs(destination, options)" in javascript
    assert "documentRef.close(SaveOptions.DONOTSAVECHANGES)" in javascript
    assert "current document" not in javascript
    assert "\\" not in javascript


def test_native_materialization_converts_legacy_text_and_closes_its_copy(
    tmp_path: Path,
) -> None:
    javascript = _build_native_materialization_javascript(
        tmp_path / 'source "quoted".ai',
        tmp_path / 'native "quoted".ai',
    )
    assert "documentRef.legacyTextItems.convertToNative()" in javascript
    assert "options.pdfCompatible = true" in javascript
    assert "documentRef.saveAs(destination, options)" in javascript
    assert "documentRef.close(SaveOptions.DONOTSAVECHANGES)" in javascript
    assert "current document" not in javascript
    assert "\\" not in javascript


def test_text_identity_notes_follow_recursive_illustrator_dom_order() -> None:
    source = Path(__file__).parents[1] / "examples" / "quarterly-kpi-report.ai"
    document = illustrator.load_ai7(source)

    texts = _document_text_frames_dom_order(document)

    assert [text.id for text in texts[:3]] == [
        "report.source.line-0",
        "operating-index.label-5.line-0",
        "operating-index.label-4.line-0",
    ]


def test_native_materialization_assigns_identity_notes_after_conversion(
    tmp_path: Path,
) -> None:
    javascript = _build_native_materialization_javascript(
        tmp_path / "source.ai",
        tmp_path / "native.ai",
        text_notes=('py-ai-text:{"id":"price","name":"Price"}',),
        text_contents=("Price",),
        desired_font_names=("Helvetica-Bold",),
        desired_trackings=(160,),
        desired_rotations=(-12,),
    )

    conversion = javascript.index("legacyTextItems.convertToNative()")
    assignment = javascript.index("documentRef.textFrames[noteIndex].note = textNotes[noteIndex]")
    assert conversion < assignment
    assert 'py-ai-text:{"id":"price","name":"Price"}' not in javascript
    assert "var textContents = [String.fromCharCode(80,114,105,99,101)]" in javascript
    assert "String.fromCharCode" in javascript
    assert "app.textFonts.getByName" in javascript
    assert "characterAttributes.textFont" in javascript
    assert "characterAttributes.tracking" in javascript
    assert "textFrames[noteIndex].rotate(rotationDelta)" in javascript
    assert "position = positionBeforeRotation" in javascript
    assert "itemRotation" in javascript
    assert "String.fromCharCode(72,101,108,118,101,116,105,99,97,45,66,111,108,100)" in javascript


def test_font_catalog_javascript_does_not_touch_documents() -> None:
    javascript = _build_font_catalog_javascript()

    assert "app.textFonts" in javascript
    assert 'lines.join("\\n")' in javascript
    assert "app.open" not in javascript
    assert "saveAs" not in javascript


def test_font_catalog_filters_and_validates_exact_names(monkeypatch) -> None:
    response = "\n".join(
        (
            "ok\t30.7.0\t3",
            "Helvetica\tHelvetica\tRegular",
            "KozGoPr6N-Regular\t小塚ゴシック Pr6N\tR",
            "NotoSansJP-Regular\tNoto Sans JP\tRegular",
        )
    )

    monkeypatch.setattr(illustrator.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(
        illustrator,
        "_execute_javascript",
        lambda *args, **kwargs: CompletedProcess([], 0, stdout=response, stderr=""),
    )

    result = illustrator.list_illustrator_fonts(
        query="小塚", required=("KozGoPr6N-Regular", "Missing-Bold")
    )

    assert result["status"] == "mismatch"
    assert result["missing"] == ["Missing-Bold"]
    assert [font["postscript_name"] for font in result["fonts"]] == ["KozGoPr6N-Regular"]


def test_expected_structure_comes_from_legacy_reader() -> None:
    source = Path(__file__).parents[1] / "examples" / "rectangle.ai"
    expected = _expected_structure(source)
    assert expected == {
        "layer_count": 1,
        "layer_names": ["Artwork"],
        "layer_page_item_types": [["PathItem"]],
        "path_item_count": 1,
        "text_frame_count": 0,
        "point_counts": [4],
        "closed_count": 1,
        "filled_count": 1,
        "stroked_count": 1,
        "compound_path_item_count": 0,
        "clipping_group_count": 0,
        "group_item_count": 0,
    }


def test_structure_comparison_reports_individual_mismatches() -> None:
    expected = {
        "layer_count": 1,
        "layer_names": ["Artwork"],
        "layer_page_item_types": [["PathItem"]],
        "path_item_count": 1,
        "text_frame_count": 0,
        "point_counts": [4],
        "closed_count": 1,
        "filled_count": 1,
        "stroked_count": 1,
        "compound_path_item_count": 0,
        "clipping_group_count": 0,
        "group_item_count": 0,
    }
    actual = dict(expected, point_counts=[3])
    checks = _compare_structure(expected, actual)
    assert checks["layer_count"] is True
    assert checks["point_counts"] is False


def test_runner_reports_a_successful_illustrator_import(monkeypatch) -> None:
    source = Path(__file__).parents[1] / "examples" / "rectangle.ai"
    actual = {
        "ok": True,
        "illustrator_version": "30.7.0",
        "layer_count": 1,
        "layer_names": ["Artwork"],
        "layer_page_item_types": [["PathItem"]],
        "path_item_count": 1,
        "text_frame_count": 0,
        "point_counts": [4],
        "closed_count": 1,
        "filled_count": 1,
        "stroked_count": 1,
        "compound_path_item_count": 0,
        "clipping_group_count": 0,
        "group_item_count": 0,
    }

    def fake_run(command, **kwargs):
        assert command[0] == "osascript"
        assert "do javascript scriptFile" in command[2]
        assert kwargs["timeout"] == 95
        return CompletedProcess(command, 0, stdout=illustrator.json.dumps(actual), stderr="")

    monkeypatch.setattr(illustrator.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(illustrator.subprocess, "run", fake_run)
    result = illustrator.run_illustrator_test(source)
    assert result["status"] == "passed"
    assert all(result["checks"].values())


def test_runner_distinguishes_an_unready_environment(monkeypatch) -> None:
    source = Path(__file__).parents[1] / "examples" / "rectangle.ai"

    def fake_timeout(command, **kwargs):
        raise TimeoutExpired(command, kwargs["timeout"])

    monkeypatch.setattr(illustrator.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(illustrator.subprocess, "run", fake_timeout)
    result = illustrator.run_illustrator_test(source, timeout=5)
    assert result["status"] == "environment-unavailable"
    assert "sign in" in result["next_action"]


def test_export_runner_refuses_to_overwrite_existing_output(monkeypatch, tmp_path: Path) -> None:
    output = tmp_path / "existing.ai"
    output.write_bytes(b"user data")
    monkeypatch.setattr(illustrator.platform, "system", lambda: "Darwin")
    result = illustrator.run_illustrator_export_test(output=output)
    assert result["status"] == "invalid-input"
    assert output.read_bytes() == b"user data"


def test_native_materialization_refuses_to_overwrite_existing_output(
    monkeypatch, tmp_path: Path
) -> None:
    source = Path(__file__).parents[1] / "examples" / "styled-table.ai"
    output = tmp_path / "existing.ai"
    output.write_bytes(b"user data")
    monkeypatch.setattr(illustrator.platform, "system", lambda: "Darwin")

    result = illustrator.materialize_native_ai(source, output)

    assert result["status"] == "invalid-input"
    assert output.read_bytes() == b"user data"


def test_export_runner_rejects_unknown_fixture(monkeypatch) -> None:
    monkeypatch.setattr(illustrator.platform, "system", lambda: "Darwin")
    result = illustrator.run_illustrator_export_test(fixture="unknown")
    assert result == {"status": "invalid-input", "error": "Unknown fixture: unknown"}


def test_roundtrip_comparison_allows_document_translation() -> None:
    source = Path(__file__).parents[1] / "examples" / "cmyk-curve.ai"
    expected = illustrator.load_ai7(source)
    actual = Document.from_dict(expected.to_dict())
    for path in [path for layer in actual.layers for path in layer.paths]:
        path.points = [
            Point(
                point.x + 100,
                point.y - 50,
                in_handle=(
                    ControlPoint(point.in_handle.x + 100, point.in_handle.y - 50)
                    if point.in_handle is not None
                    else None
                ),
                out_handle=(
                    ControlPoint(point.out_handle.x + 100, point.out_handle.y - 50)
                    if point.out_handle is not None
                    else None
                ),
                smooth=point.smooth,
            )
            for point in path.points
        ]
    checks = _compare_roundtrip_semantics(expected, actual)
    assert all(checks.values())


def test_roundtrip_comparison_reports_color_changes() -> None:
    source = Path(__file__).parents[1] / "examples" / "rectangle.ai"
    expected = illustrator.load_ai7(source)
    actual = Document.from_dict(expected.to_dict())
    actual.layers[0].paths[0].fill = Color(0.0, 0.0, 0.0)
    checks = _compare_roundtrip_semantics(expected, actual)
    assert checks["fill_colors"] is False
    assert checks["path_geometry"] is True


def test_roundtrip_comparison_reports_path_identity_changes() -> None:
    source = Path(__file__).parents[1] / "examples" / "rectangle.ai"
    expected = illustrator.load_ai7(source)
    actual = Document.from_dict(expected.to_dict())
    actual.layers[0].paths[0].id = "changed"
    actual.layers[0].paths[0].name = "Changed"
    checks = _compare_roundtrip_semantics(expected, actual)
    assert checks["path_ids"] is False
    assert checks["path_names"] is False
    assert checks["path_geometry"] is True


def test_roundtrip_comparison_reports_compound_path_polarity_changes() -> None:
    source = Path(__file__).parents[1] / "examples" / "compound-path.ai"
    expected = illustrator.load_ai7(source)
    actual = Document.from_dict(expected.to_dict())
    actual.layers[0].compound_paths[0].paths[1].polarity = "positive"
    checks = _compare_roundtrip_semantics(expected, actual)
    assert checks["compound_path_count"] is True
    assert checks["compound_component_counts"] is True
    assert checks["path_polarities"] is False


def test_roundtrip_comparison_reports_clipping_group_removal() -> None:
    source = Path(__file__).parents[1] / "examples" / "clipping-group.ai"
    expected = illustrator.load_ai7(source)
    actual = Document.from_dict(expected.to_dict())
    group = actual.layers[0].clipping_groups.pop()
    actual.layers[0].paths.extend([group.clipping_path, *group.paths])
    checks = _compare_roundtrip_semantics(expected, actual)
    assert checks["path_item_count"] is True
    assert checks["clipping_group_count"] is False


def test_roundtrip_comparison_reports_mixed_item_type_reordering() -> None:
    source = Path(__file__).parents[1] / "examples" / "mixed-stack.ai"
    expected = illustrator.load_ai7(source)
    actual = Document.from_dict(expected.to_dict())
    actual.layers[0].item_order.reverse()
    checks = _compare_roundtrip_semantics(expected, actual)
    assert checks["layer_item_types"] is False
    assert checks["path_geometry"] is True


def test_roundtrip_runner_refuses_to_overwrite_existing_output(monkeypatch, tmp_path: Path) -> None:
    source = Path(__file__).parents[1] / "examples" / "rectangle.ai"
    output = tmp_path / "existing.ai"
    output.write_bytes(b"user data")
    monkeypatch.setattr(illustrator.platform, "system", lambda: "Darwin")
    result = illustrator.run_illustrator_roundtrip_test(source, output=output)
    assert result["status"] == "invalid-input"
    assert output.read_bytes() == b"user data"
