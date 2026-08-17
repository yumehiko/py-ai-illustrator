from dataclasses import replace
from pathlib import Path

import pytest

from py_ai_illustrator.cli import main
from py_ai_illustrator.legacy import (
    ReplaceText,
    SetPathFill,
    SetPathStroke,
    TranslatePath,
    UnsupportedLegacyFeature,
    dumps_ai7,
    patch_path_fill,
    patch_path_stroke,
    patch_path_translate,
    patch_text,
    reads_ai7,
    reserialize_ai7,
)
from py_ai_illustrator.lossless import SourceReplacement
from py_ai_illustrator.model import CmykColor, Color, Document, Layer, Point, TextFrame
from py_ai_illustrator.model import Path as AIPath


def supported_document() -> Document:
    return Document(
        width=100,
        height=80,
        layers=[
            Layer(
                id="artwork",
                name="Artwork",
                paths=[
                    AIPath(
                        id="shape",
                        points=[Point(10, 10), Point(90, 10), Point(90, 70), Point(10, 70)],
                        fill=Color(1, 0, 0),
                    )
                ],
            )
        ],
    )


def text_document(*, text_id: str = "headline", text: str = "Original (copy)") -> Document:
    return Document(
        width=100,
        height=80,
        layers=[
            Layer(
                id="artwork",
                name="Artwork",
                text_frames=[TextFrame(id=text_id, text=text, x=10, y=50)],
            )
        ],
    )


def stroked_document(*, path_id: str = "rule") -> Document:
    return Document(
        width=100,
        height=80,
        layers=[
            Layer(
                id="artwork",
                name="Artwork",
                paths=[
                    AIPath(
                        id=path_id,
                        points=[Point(10, 40), Point(90, 40)],
                        closed=False,
                        stroke=Color(0.1, 0.2, 0.3),
                        stroke_width=2,
                    )
                ],
            )
        ],
    )


def test_reader_returns_source_coverage_and_recognized_inventory() -> None:
    data = dumps_ai7(supported_document())
    result = reads_ai7(data)

    assert result.document.to_dict() == supported_document().to_dict()
    assert result.source.to_bytes() == data
    assert result.coverage.complete is True
    assert result.safe_to_reserialize is True
    assert result.classification == "convertible"
    assert {entry.name for entry in result.coverage.operators} >= {"m", "L", "f"}
    assert {entry.name for entry in result.coverage.resources} >= {
        "%AI5_FileFormat",
        "%%BoundingBox",
    }
    origin = next(origin for origin in result.origins if origin.node_id == "shape")
    assert origin.node_type == "path"
    assert origin.field("fill") is not None
    assert origin.start < origin.end


def test_unknown_operator_and_resource_are_source_located_and_make_result_partial() -> None:
    data = dumps_ai7(supported_document()).replace(
        b"%%EndSetup\n",
        b"%%EndSetup\n%%AIFutureResource: opaque\n12 34 FutureOperator\n",
    )
    result = reads_ai7(data)

    assert result.source.to_bytes() == data
    assert result.coverage.unsupported_statement_count == 1
    assert result.coverage.unsupported_resource_count == 1
    assert result.safe_to_reserialize is False
    assert result.classification == "partially_parsed"
    assert [(item.code, item.feature_name) for item in result.diagnostics] == [
        ("unsupported-resource", "%%AIFutureResource"),
        ("unsupported-operator", "FutureOperator"),
    ]
    assert all(item.start < item.end for item in result.diagnostics)
    report = result.compatibility_report()
    assert report["safe_to_reserialize"] is False
    assert report["coverage"]["unsupported_statement_count"] == 1


def test_reserialize_rejects_unknown_features_unless_loss_is_explicit() -> None:
    data = dumps_ai7(supported_document()).replace(
        b"%%EndSetup\n",
        b"%%EndSetup\n12 34 FutureOperator\n",
    )
    result = reads_ai7(data)

    with pytest.raises(UnsupportedLegacyFeature, match="Refusing to reserialize"):
        reserialize_ai7(result)

    discarded = reserialize_ai7(result, loss_policy="discard")
    assert b"FutureOperator" not in discarded
    assert reads_ai7(discarded).safe_to_reserialize is True


def test_json_export_is_strict_by_default_and_validate_reports_partial(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source = tmp_path / "future.ai"
    destination = tmp_path / "future.json"
    source.write_bytes(
        dumps_ai7(supported_document()).replace(
            b"%%EndSetup\n",
            b"%%EndSetup\n12 34 FutureOperator\n",
        )
    )

    with pytest.raises(SystemExit):
        main(["export", str(source), "--to", "json", "-o", str(destination)])
    assert not destination.exists()

    assert main(["validate", str(source)]) == 1
    output = capsys.readouterr().out
    assert '"classification": "partially_parsed"' in output
    assert '"safe_to_reserialize": false' in output

    assert (
        main(
            [
                "export",
                str(source),
                "--to",
                "json",
                "-o",
                str(destination),
                "--allow-partial",
            ]
        )
        == 0
    )
    assert destination.exists()


def test_typed_fill_patch_changes_only_its_field_span_and_keeps_unknown_bytes() -> None:
    data = dumps_ai7(supported_document()).replace(
        b"%%EndSetup\n",
        b"%%EndSetup\n12 34 FutureOperator % opaque \xff\n",
    )
    result = reads_ai7(data)
    fill_origin = next(origin for origin in result.origins if origin.node_id == "shape").field(
        "fill"
    )
    assert fill_origin is not None

    replacement = b"0.1 0.2 0.3 0.4 k"
    patched = patch_path_fill(
        result,
        SetPathFill(
            path_id="shape",
            expected_fill=Color(1, 0, 0),
            fill=CmykColor(0.1, 0.2, 0.3, 0.4),
        ),
    )

    assert patched.data[: fill_origin.start] == data[: fill_origin.start]
    assert patched.data[fill_origin.start : fill_origin.start + len(replacement)] == replacement
    assert patched.data[fill_origin.start + len(replacement) :] == data[fill_origin.end :]
    assert b"FutureOperator % opaque \xff" in patched.data
    restored = reads_ai7(patched.data)
    assert restored.document.layers[0].paths[0].fill == CmykColor(0.1, 0.2, 0.3, 0.4)


def test_typed_fill_patch_requires_matching_semantic_precondition() -> None:
    result = reads_ai7(dumps_ai7(supported_document()))

    with pytest.raises(UnsupportedLegacyFeature, match="fill precondition failed"):
        patch_path_fill(
            result,
            SetPathFill(
                path_id="shape",
                expected_fill=Color(0, 0, 0),
                fill=Color(0, 1, 0),
            ),
        )


@pytest.mark.parametrize("path_id", ["missing", "duplicate"])
def test_typed_fill_patch_stops_for_zero_or_multiple_selector_matches(path_id: str) -> None:
    document = supported_document()
    if path_id == "duplicate":
        document.layers[0].paths.append(
            AIPath(
                id="duplicate",
                points=[Point(10, 20), Point(90, 20)],
                closed=False,
                stroke=Color(0, 0, 0),
            )
        )
        document.layers[0].paths[0].id = "duplicate"
    result = reads_ai7(dumps_ai7(document))

    with pytest.raises(UnsupportedLegacyFeature, match="matched"):
        patch_path_fill(
            result,
            SetPathFill(
                path_id=path_id,
                expected_fill=Color(1, 0, 0),
                fill=Color(0, 1, 0),
            ),
        )


def test_typed_fill_patch_rejects_a_source_color_shared_by_multiple_paths() -> None:
    data = b"""%!PS-Adobe-3.0
%%BoundingBox: 0 0 100 100
%AI5_FileFormat 3.0
1 0 0 Xa
10 10 m
40 10 L
40 40 L
f
60 60 m
90 60 L
90 90 L
f
%%EOF
"""
    result = reads_ai7(data)
    assert all(origin.field("fill") is None for origin in result.origins)

    with pytest.raises(UnsupportedLegacyFeature, match="exclusive source fill span"):
        patch_path_fill(
            result,
            SetPathFill(
                path_id="path-1",
                expected_fill=Color(1, 0, 0),
                fill=Color(0, 1, 0),
            ),
        )


def test_typed_stroke_patch_changes_only_its_field_span_and_keeps_unknown_bytes() -> None:
    data = dumps_ai7(stroked_document()).replace(b"\n", b"\r\n").replace(
        b"%%EndSetup\r\n",
        b"%%EndSetup\r\n12 34 FutureOperator % opaque \xff\r\n",
    )
    result = reads_ai7(data)
    stroke_origin = next(origin for origin in result.origins if origin.node_id == "rule").field(
        "stroke"
    )
    assert stroke_origin is not None

    replacement = b"0.4 0.3 0.2 0.1 K"
    patched = patch_path_stroke(
        result,
        SetPathStroke(
            path_id="rule",
            expected_stroke=Color(0.1, 0.2, 0.3),
            stroke=CmykColor(0.4, 0.3, 0.2, 0.1),
        ),
    )

    assert patched.data[: stroke_origin.start] == data[: stroke_origin.start]
    assert (
        patched.data[stroke_origin.start : stroke_origin.start + len(replacement)] == replacement
    )
    assert patched.data[stroke_origin.start + len(replacement) :] == data[stroke_origin.end :]
    assert b"FutureOperator % opaque \xff" in patched.data
    restored = reads_ai7(patched.data)
    assert restored.document.layers[0].paths[0].stroke == CmykColor(0.4, 0.3, 0.2, 0.1)


def test_typed_stroke_patch_requires_matching_semantic_precondition() -> None:
    result = reads_ai7(dumps_ai7(stroked_document()))

    with pytest.raises(UnsupportedLegacyFeature, match="stroke precondition failed"):
        patch_path_stroke(
            result,
            SetPathStroke(
                path_id="rule",
                expected_stroke=Color(0, 0, 0),
                stroke=Color(0, 1, 0),
            ),
        )


@pytest.mark.parametrize("path_id", ["missing", "duplicate"])
def test_typed_stroke_patch_stops_for_zero_or_multiple_selector_matches(path_id: str) -> None:
    document = stroked_document(path_id="duplicate" if path_id == "duplicate" else "rule")
    if path_id == "duplicate":
        document.layers[0].paths.append(
            AIPath(
                id="duplicate",
                points=[Point(10, 20), Point(90, 20)],
                closed=False,
                stroke=Color(0, 0, 0),
            )
        )
    result = reads_ai7(dumps_ai7(document))

    with pytest.raises(UnsupportedLegacyFeature, match="matched"):
        patch_path_stroke(
            result,
            SetPathStroke(
                path_id=path_id,
                expected_stroke=Color(0.1, 0.2, 0.3),
                stroke=Color(0, 1, 0),
            ),
        )


def test_typed_stroke_patch_rejects_a_source_color_shared_by_multiple_paths() -> None:
    data = b"""%!PS-Adobe-3.0
%%BoundingBox: 0 0 100 100
%AI5_FileFormat 3.0
0.1 0.2 0.3 XA
10 10 m
40 10 L
S
60 60 m
90 60 L
S
%%EOF
"""
    result = reads_ai7(data)
    assert all(origin.field("stroke") is None for origin in result.origins)

    with pytest.raises(UnsupportedLegacyFeature, match="exclusive source stroke span"):
        patch_path_stroke(
            result,
            SetPathStroke(
                path_id="path-1",
                expected_stroke=Color(0.1, 0.2, 0.3),
                stroke=Color(0, 1, 0),
            ),
        )


def test_path_translate_patches_only_geometry_statements() -> None:
    data = dumps_ai7(stroked_document()).replace(b"\n", b"\r\n").replace(
        b"10 40 m\r\n90 40 L",
        b"10 40 m\r\n\r\n90 40 L",
    )
    result = reads_ai7(data)
    path = result.document.layers[0].paths[0]
    origin = next(origin for origin in result.origins if origin.node_id == "rule")
    assert [field.field for field in origin.fields_with_prefix("geometry.")] == [
        "geometry.0",
        "geometry.1",
    ]

    patched = patch_path_translate(
        result,
        TranslatePath(
            path_id="rule",
            dx=5,
            dy=-3,
            expected_points=tuple(path.points),
        ),
    )

    expected = data.replace(
        b"10 40 m\r\n\r\n90 40 L",
        b"15 37 m\r\n\r\n95 37 L",
    )
    assert patched.data == expected
    restored = reads_ai7(patched.data).document.layers[0].paths[0]
    assert restored.points == [Point(15, 37), Point(95, 37)]


def test_path_translate_moves_cubic_and_short_cubic_coordinates() -> None:
    data = b"""%!PS-Adobe-3.0
%%BoundingBox: 0 0 100 100
%AI5_FileFormat 3.0
0 0 0 XA
10 10 m
12 13 14 15 20 20 c
22 23 30 30 v
32 33 40 40 y
S
%%EOF
"""
    result = reads_ai7(data)
    path = result.document.layers[0].paths[0]

    patched = patch_path_translate(
        result,
        TranslatePath(
            path_id="path-1",
            dx=5,
            dy=-2,
            expected_points=tuple(path.points),
        ),
    )

    assert b"15 8 m\n" in patched.data
    assert b"17 11 19 13 25 18 c\n" in patched.data
    assert b"27 21 35 28 v\n" in patched.data
    assert b"37 31 45 38 y\n" in patched.data
    restored = reads_ai7(patched.data).document.layers[0].paths[0]
    for before, after in zip(path.points, restored.points, strict=True):
        assert after.x == before.x + 5
        assert after.y == before.y - 2
        if before.in_handle is not None and after.in_handle is not None:
            assert after.in_handle.x == before.in_handle.x + 5
            assert after.in_handle.y == before.in_handle.y - 2
        if before.out_handle is not None and after.out_handle is not None:
            assert after.out_handle.x == before.out_handle.x + 5
            assert after.out_handle.y == before.out_handle.y - 2


def test_path_translate_requires_matching_geometry_precondition() -> None:
    result = reads_ai7(dumps_ai7(stroked_document()))

    with pytest.raises(UnsupportedLegacyFeature, match="geometry precondition failed"):
        patch_path_translate(
            result,
            TranslatePath(
                path_id="rule",
                dx=5,
                dy=5,
                expected_points=(Point(0, 0),),
            ),
        )


@pytest.mark.parametrize("path_id", ["missing", "duplicate"])
def test_path_translate_stops_for_zero_or_multiple_selector_matches(path_id: str) -> None:
    document = stroked_document(path_id="duplicate" if path_id == "duplicate" else "rule")
    if path_id == "duplicate":
        document.layers[0].paths.append(
            AIPath(
                id="duplicate",
                points=[Point(10, 20), Point(90, 20)],
                closed=False,
                stroke=Color(0, 0, 0),
            )
        )
    result = reads_ai7(dumps_ai7(document))

    with pytest.raises(UnsupportedLegacyFeature, match="matched"):
        patch_path_translate(
            result,
            TranslatePath(
                path_id=path_id,
                dx=5,
                dy=5,
                expected_points=tuple(result.document.layers[0].paths[0].points),
            ),
        )


def test_path_translate_rejects_changed_source_geometry() -> None:
    result = reads_ai7(dumps_ai7(stroked_document()))
    path = result.document.layers[0].paths[0]
    origin = next(origin for origin in result.origins if origin.node_id == "rule")
    first_geometry = origin.fields_with_prefix("geometry.")[0]
    changed_source = result.source.patched(
        [SourceReplacement(first_geometry.start, first_geometry.end, b"11 40 m")]
    )
    stale_result = replace(result, source=changed_source)

    with pytest.raises(UnsupportedLegacyFeature, match="source precondition failed"):
        patch_path_translate(
            stale_result,
            TranslatePath(
                path_id="rule",
                dx=5,
                dy=5,
                expected_points=tuple(path.points),
            ),
        )


def test_path_translate_rejects_unsupported_syntax_inside_the_path_span() -> None:
    data = dumps_ai7(stroked_document()).replace(
        b"10 40 m\n90 40 L",
        b"10 40 m\n12 34 FutureOperator\n90 40 L",
    )
    result = reads_ai7(data)
    path = result.document.layers[0].paths[0]

    with pytest.raises(UnsupportedLegacyFeature, match="intersects unsupported"):
        patch_path_translate(
            result,
            TranslatePath(
                path_id="rule",
                dx=5,
                dy=5,
                expected_points=tuple(path.points),
            ),
        )


def test_zero_path_translation_is_byte_preserving() -> None:
    data = dumps_ai7(stroked_document()).replace(b"10 40 m", b"10.000 40.0 m")
    result = reads_ai7(data)
    path = result.document.layers[0].paths[0]

    patched = patch_path_translate(
        result,
        TranslatePath(
            path_id="rule",
            dx=0,
            dy=0,
            expected_points=tuple(path.points),
        ),
    )

    assert patched.data == data


def test_text_origin_and_typed_patch_preserve_every_byte_outside_content() -> None:
    data = dumps_ai7(text_document()).replace(b"\n", b"\r\n").replace(
        b"%%EndSetup\r\n",
        b"%%EndSetup\r\n12 34 FutureOperator % opaque \xff\r\n",
    )
    result = reads_ai7(data)
    origin = next(
        origin
        for origin in result.origins
        if origin.node_type == "text" and origin.node_id == "headline"
    )
    text_origin = origin.field("text")
    assert text_origin is not None
    assert data[text_origin.start : text_origin.end] == rb"Original \(copy\)"

    replacement = rb"Revised \(draft\)"
    patched = patch_text(
        result,
        ReplaceText(
            text_id="headline",
            expected_text="Original (copy)",
            text="Revised (draft)",
        ),
    )

    assert patched.data[: text_origin.start] == data[: text_origin.start]
    assert patched.data[text_origin.start : text_origin.start + len(replacement)] == replacement
    assert patched.data[text_origin.start + len(replacement) :] == data[text_origin.end :]
    assert b"FutureOperator % opaque \xff" in patched.data
    assert reads_ai7(patched.data).document.layers[0].text_frames[0].text == "Revised (draft)"


def test_typed_text_patch_requires_matching_semantic_precondition() -> None:
    result = reads_ai7(dumps_ai7(text_document()))

    with pytest.raises(UnsupportedLegacyFeature, match="content precondition failed"):
        patch_text(
            result,
            ReplaceText(text_id="headline", expected_text="Stale", text="New"),
        )


@pytest.mark.parametrize("text_id", ["missing", "duplicate"])
def test_typed_text_patch_stops_for_zero_or_multiple_selector_matches(text_id: str) -> None:
    document = text_document(text_id="duplicate" if text_id == "duplicate" else "headline")
    if text_id == "duplicate":
        document.layers[0].text_frames.append(
            TextFrame(id="duplicate", text="Second", x=10, y=30)
        )
    result = reads_ai7(dumps_ai7(document))

    with pytest.raises(UnsupportedLegacyFeature, match="matched"):
        patch_text(
            result,
            ReplaceText(text_id=text_id, expected_text="Original (copy)", text="New"),
        )


def test_typed_text_patch_rejects_multi_statement_text_without_a_local_field_span() -> None:
    data = dumps_ai7(text_document(text="FirstSecond")).replace(
        b"(FirstSecond) Tx",
        b"(First) Tx\n(Second) Tx",
    )
    result = reads_ai7(data)
    origin = next(origin for origin in result.origins if origin.node_id == "headline")
    assert origin.field("text") is None

    with pytest.raises(UnsupportedLegacyFeature, match="exclusive source text span"):
        patch_text(
            result,
            ReplaceText(text_id="headline", expected_text="FirstSecond", text="New"),
        )


def test_typed_text_patch_uses_the_existing_font_encoding_profile() -> None:
    font_name = "_KozGoPr6N-Regular-83pv-RKSJ-H"
    document = text_document(text="日本語")
    document.layers[0].text_frames[0].font_name = font_name
    result = reads_ai7(dumps_ai7(document))

    patched = patch_text(
        result,
        ReplaceText(text_id="headline", expected_text="日本語", text="差し替え"),
    )

    assert b"\\215\\267\\202\\265\\221\\326\\202\\246" in patched.data
    assert reads_ai7(patched.data).document.layers[0].text_frames[0].text == "差し替え"


def test_typed_text_patch_rejects_text_outside_the_existing_font_encoding() -> None:
    result = reads_ai7(dumps_ai7(text_document(text="ASCII")))

    with pytest.raises(UnsupportedLegacyFeature, match="RKSJ"):
        patch_text(
            result,
            ReplaceText(text_id="headline", expected_text="ASCII", text="日本語"),
        )


def test_typed_text_patch_can_insert_into_an_empty_text_statement() -> None:
    result = reads_ai7(dumps_ai7(text_document(text="")))
    origin = next(origin for origin in result.origins if origin.node_id == "headline")
    text_origin = origin.field("text")
    assert text_origin is not None
    assert text_origin.start == text_origin.end

    patched = patch_text(
        result,
        ReplaceText(text_id="headline", expected_text="", text="Inserted"),
    )

    assert reads_ai7(patched.data).document.layers[0].text_frames[0].text == "Inserted"
