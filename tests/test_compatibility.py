from dataclasses import replace
from pathlib import Path

import pytest

from py_ai_illustrator.cli import main
from py_ai_illustrator.legacy import (
    ReplaceLinkedImageSource,
    ReplaceText,
    SetPathFill,
    SetPathStroke,
    TranslateContainer,
    TranslatePath,
    UnsupportedLegacyFeature,
    apply_legacy_patch,
    dumps_ai7,
    patch_container_translate,
    patch_legacy,
    patch_linked_image_source,
    patch_path_fill,
    patch_path_stroke,
    patch_path_translate,
    patch_text,
    plan_legacy_patch,
    reads_ai7,
    reserialize_ai7,
)
from py_ai_illustrator.lossless import SourceReplacement, tokenize_legacy
from py_ai_illustrator.model import (
    Artboard,
    ClippingGroup,
    CmykColor,
    Color,
    CompoundPath,
    Document,
    Group,
    Layer,
    LayerItemRef,
    LinkedImage,
    Point,
    TextFrame,
)
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


def linked_image_document(*, image_id: str = "hero") -> Document:
    return Document(
        width=100,
        height=80,
        layers=[
            Layer(
                id="artwork",
                name="Artwork",
                linked_images=[
                    LinkedImage(
                        id=image_id,
                        name="Hero image",
                        source="Links/hero.png",
                        x=10,
                        y=70,
                        width=80,
                        height=60,
                        rotation=-5,
                    )
                ],
            )
        ],
    )


def translatable_group_document() -> Document:
    path = AIPath(
        id="shape",
        points=[Point(10, 10), Point(30, 10), Point(30, 30), Point(10, 30)],
        fill=Color(1, 0, 0),
    )
    text = TextFrame(id="label", text="Label", x=12, y=24)
    image = LinkedImage(
        id="photo",
        source="Links/photo.png",
        x=40,
        y=60,
        width=30,
        height=20,
    )
    group = Group(
        id="card",
        paths=[path],
        text_frames=[text],
        linked_images=[image],
        item_order=[
            LayerItemRef("path", path.id),
            LayerItemRef("text", text.id),
            LayerItemRef("image", image.id),
        ],
    )
    return Document(
        width=100,
        height=80,
        layers=[
            Layer(
                id="artwork",
                name="Artwork",
                groups=[group],
                item_order=[LayerItemRef("group", group.id)],
            )
        ],
    )


def translatable_path_containers_document() -> Document:
    ordinary = AIPath(id="ordinary", points=[Point(5, 5), Point(15, 5)])
    compound = CompoundPath(
        id="compound",
        paths=[
            AIPath(id="compound-a", points=[Point(20, 20), Point(30, 20)]),
            AIPath(id="compound-b", points=[Point(20, 30), Point(30, 30)]),
        ],
    )
    clipping = ClippingGroup(
        id="clipping",
        clipping_path=AIPath(
            id="mask",
            points=[Point(40, 40), Point(60, 40), Point(60, 60), Point(40, 60)],
        ),
        paths=[AIPath(id="clipped", points=[Point(45, 45), Point(55, 55)])],
    )
    return Document(
        width=100,
        height=80,
        layers=[
            Layer(
                id="artwork",
                name="Artwork",
                paths=[ordinary],
                compound_paths=[compound],
                clipping_groups=[clipping],
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
    assert data[origin.start : origin.end].startswith(b"%AI7_Tag: (shape)")


def test_reader_connects_every_ir_node_kind_to_a_source_span() -> None:
    group_path = AIPath(id="group-path", points=[Point(5, 5), Point(15, 5)])
    group = Group(id="group", paths=[group_path])
    compound = CompoundPath(
        id="compound",
        paths=[
            AIPath(id="compound-a", points=[Point(20, 20), Point(30, 20)]),
            AIPath(id="compound-b", points=[Point(20, 30), Point(30, 30)]),
        ],
    )
    clipping = ClippingGroup(
        id="clipping",
        clipping_path=AIPath(
            id="mask",
            points=[Point(40, 40), Point(60, 40), Point(60, 60), Point(40, 60)],
        ),
        paths=[AIPath(id="clipped", points=[Point(45, 45), Point(55, 55)])],
    )
    image = LinkedImage(
        id="image",
        source="Links/image.png",
        x=65,
        y=70,
        width=20,
        height=20,
    )
    document = Document(
        width=100,
        height=80,
        artboards=[Artboard(id="board", name="Board", left=0, top=80, width=100, height=80)],
        layers=[
            Layer(
                id="layer",
                name="Layer",
                groups=[group],
                compound_paths=[compound],
                clipping_groups=[clipping],
                text_frames=[TextFrame(id="text", text="Text", x=10, y=70)],
                linked_images=[image],
            )
        ],
    )

    data = dumps_ai7(document)
    result = reads_ai7(data)
    origin_keys = {(origin.node_type, origin.node_id) for origin in result.origins}

    assert origin_keys >= {
        ("document", "document"),
        ("artboard", "board"),
        ("layer", "layer"),
        ("group", "group"),
        ("compound_path", "compound"),
        ("clipping_group", "clipping"),
        ("path", "group-path"),
        ("path", "compound-a"),
        ("path", "compound-b"),
        ("path", "mask"),
        ("path", "clipped"),
        ("text", "text"),
        ("linked_image", "image"),
    }
    for origin in result.origins:
        assert data[origin.start : origin.end]

    by_key = {(origin.node_type, origin.node_id): origin for origin in result.origins}
    layer_origin = by_key[("layer", "layer")]
    for node_type, node_id in origin_keys - {("document", "document"), ("artboard", "board")}:
        origin = by_key[(node_type, node_id)]
        assert layer_origin.start <= origin.start < origin.end <= layer_origin.end


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


@pytest.mark.parametrize(
    ("old", "new", "feature_name"),
    [
        (b"0 Ta", b"3 Ta", "Ta"),
        (b"1 0 0 Xa", b"1 0 0 0 Xa", "Xa"),
        (b"0 A", b"1 A", "A"),
    ],
)
def test_known_operator_with_unmodeled_semantics_is_not_classified_as_convertible(
    old: bytes, new: bytes, feature_name: str
) -> None:
    data = dumps_ai7(text_document() if feature_name == "Ta" else supported_document()).replace(
        old,
        new,
        1,
    )

    result = reads_ai7(data)

    assert result.coverage.complete is True
    assert result.safe_to_reserialize is False
    assert result.classification == "partially_parsed"
    assert [
        (diagnostic.code, diagnostic.feature_name) for diagnostic in result.diagnostics
    ] == [("unmodeled-operator-semantics", feature_name)]
    with pytest.raises(UnsupportedLegacyFeature, match="Refusing to reserialize"):
        reserialize_ai7(result)


def test_modeled_text_operator_outside_a_text_object_is_diagnosed() -> None:
    data = dumps_ai7(supported_document()).replace(
        b"%%EndSetup\n",
        b"%%EndSetup\n(orphan) Tx\n",
    )

    result = reads_ai7(data)

    assert result.coverage.complete is True
    assert result.safe_to_reserialize is False
    assert [(item.code, item.feature_name) for item in result.diagnostics] == [
        ("unmodeled-operator-semantics", "Tx")
    ]


def test_multiple_tx_runs_with_different_styles_are_diagnosed_as_lossy() -> None:
    data = dumps_ai7(text_document(text="FirstSecond")).replace(
        b"(FirstSecond) Tx",
        b"(First) Tx\n/Helvetica-Bold 12 0 0 Tf\n(Second) Tx",
    )

    result = reads_ai7(data)

    assert result.document.layers[0].text_frames[0].text == "FirstSecond"
    assert result.safe_to_reserialize is False
    assert [(item.code, item.feature_name) for item in result.diagnostics] == [
        ("unmodeled-operator-semantics", "TO")
    ]


def test_malformed_recognized_private_metadata_is_not_silently_discarded() -> None:
    initial = reads_ai7(dumps_ai7(linked_image_document()))
    image_origin = next(origin for origin in initial.origins if origin.node_id == "hero")
    metadata = image_origin.field("metadata")
    assert metadata is not None
    data = initial.source.patched(
        [SourceReplacement(metadata.start, metadata.end, b"not-base64")]
    ).data

    result = reads_ai7(data)

    assert result.coverage.complete is True
    assert result.safe_to_reserialize is False
    assert [(item.code, item.feature_name) for item in result.diagnostics] == [
        ("unmodeled-resource-semantics", "%%py-ai-linked-image"),
        ("unmodeled-resource-semantics", "%AI3_Note"),
    ]


def test_arbitrary_standard_path_note_is_reported_as_unmodeled_resource_data() -> None:
    data = dumps_ai7(supported_document())
    note_line = next(line for line in data.splitlines() if line.startswith(b"%AI3_Note:"))
    data = data.replace(note_line, b"%AI3_Note:user-authored-note")

    result = reads_ai7(data)

    assert result.coverage.complete is True
    assert result.safe_to_reserialize is False
    assert [(item.code, item.feature_name) for item in result.diagnostics] == [
        ("unmodeled-resource-semantics", "%AI3_Note")
    ]


def test_valid_private_metadata_without_a_modeled_node_is_diagnosed() -> None:
    data = dumps_ai7(supported_document()).replace(
        b"%%EndSetup\n",
        b"%%EndSetup\n%%py-ai-text-alignment: (left)\n",
    )

    result = reads_ai7(data)

    assert result.coverage.complete is True
    assert result.safe_to_reserialize is False
    assert [(item.code, item.feature_name) for item in result.diagnostics] == [
        ("unmodeled-resource-semantics", "%%py-ai-text-alignment")
    ]


@pytest.mark.parametrize(
    ("document", "old", "feature_name"),
    [
        (text_document(), b"TO\n", "To"),
        (supported_document(), b"f\nLB", "m"),
        (translatable_group_document(), b"U\nLB", "u"),
    ],
)
def test_unterminated_modeled_construct_is_not_classified_as_convertible(
    document: Document, old: bytes, feature_name: str
) -> None:
    data = dumps_ai7(document).replace(old, b"LB" if old.endswith(b"LB") else b"", 1)

    result = reads_ai7(data)

    assert result.coverage.complete is True
    assert result.safe_to_reserialize is False
    assert ("unmodeled-operator-semantics", feature_name) in [
        (item.code, item.feature_name) for item in result.diagnostics
    ]


def test_isolated_modeled_close_operator_is_diagnosed() -> None:
    data = dumps_ai7(supported_document()).replace(b"%%EndSetup\n", b"%%EndSetup\nU\n")

    result = reads_ai7(data)

    assert result.coverage.complete is True
    assert result.safe_to_reserialize is False
    assert [(item.code, item.feature_name) for item in result.diagnostics] == [
        ("unmodeled-operator-semantics", "U")
    ]


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


def test_group_translate_moves_all_leaf_types_and_preserves_bytes_outside_container() -> None:
    data = dumps_ai7(translatable_group_document()).replace(
        b"%%EndSetup\n",
        b"%%EndSetup\n12 34 FutureOperator % outside \xff\n",
    )
    result = reads_ai7(data)
    group_origin = next(
        origin
        for origin in result.origins
        if origin.node_type == "group" and origin.node_id == "card"
    )

    patched = patch_container_translate(
        result,
        TranslateContainer(
            container_type="group",
            container_id="card",
            dx=5,
            dy=-3,
            expected_members=frozenset(
                {("path", "shape"), ("text", "label"), ("linked_image", "photo")}
            ),
        ),
    )

    assert patched.data[: group_origin.start] == data[: group_origin.start]
    suffix_length = len(data) - group_origin.end
    assert patched.data[-suffix_length:] == data[group_origin.end :]
    assert b"FutureOperator % outside \xff" in patched.data
    assert b"1 0 0 1 17 21 Tm" in patched.data
    assert b"45 37 m" in patched.data
    assert b"75 57 L" in patched.data
    restored_group = reads_ai7(patched.data).document.layers[0].groups[0]
    assert restored_group.paths[0].points == [
        Point(15, 7),
        Point(35, 7),
        Point(35, 27),
        Point(15, 27),
    ]
    assert (restored_group.text_frames[0].x, restored_group.text_frames[0].y) == (17, 21)
    assert (restored_group.linked_images[0].x, restored_group.linked_images[0].y) == (
        45,
        57,
    )


@pytest.mark.parametrize(
    ("container_type", "container_id", "expected_ids", "moved_ids"),
    [
        (
            "compound_path",
            "compound",
            {"compound-a", "compound-b"},
            {"compound-a", "compound-b"},
        ),
        ("clipping_group", "clipping", {"mask", "clipped"}, {"mask", "clipped"}),
        (
            "layer",
            "artwork",
            {"ordinary", "compound-a", "compound-b", "mask", "clipped"},
            {"ordinary", "compound-a", "compound-b", "mask", "clipped"},
        ),
    ],
)
def test_path_container_types_translate_exactly_their_descendants(
    container_type: str,
    container_id: str,
    expected_ids: set[str],
    moved_ids: set[str],
) -> None:
    result = reads_ai7(dumps_ai7(translatable_path_containers_document()))
    layer = result.document.layers[0]
    before_paths = {
        path.id: tuple(path.points)
        for path in [
            *layer.paths,
            *layer.compound_paths[0].paths,
            layer.clipping_groups[0].clipping_path,
            *layer.clipping_groups[0].paths,
        ]
    }

    patched = patch_container_translate(
        result,
        TranslateContainer(
            container_type=container_type,
            container_id=container_id,
            dx=3,
            dy=4,
            expected_members=frozenset(("path", path_id) for path_id in expected_ids),
        ),
    )

    restored_layer = reads_ai7(patched.data).document.layers[0]
    after_paths = {
        path.id: tuple(path.points)
        for path in [
            *restored_layer.paths,
            *restored_layer.compound_paths[0].paths,
            restored_layer.clipping_groups[0].clipping_path,
            *restored_layer.clipping_groups[0].paths,
        ]
    }
    for path_id, before in before_paths.items():
        if path_id in moved_ids:
            assert after_paths[path_id] == tuple(
                Point(point.x + 3, point.y + 4, point.in_handle, point.out_handle, point.smooth)
                for point in before
            )
        else:
            assert after_paths[path_id] == before


def test_container_translate_requires_an_exact_member_precondition() -> None:
    result = reads_ai7(dumps_ai7(translatable_group_document()))

    with pytest.raises(UnsupportedLegacyFeature, match="members precondition failed"):
        patch_container_translate(
            result,
            TranslateContainer(
                container_type="group",
                container_id="card",
                dx=5,
                dy=5,
                expected_members=frozenset({("path", "shape"), ("text", "label")}),
            ),
        )


def test_container_translate_rejects_unsupported_syntax_inside_container() -> None:
    data = dumps_ai7(translatable_group_document()).replace(
        b"10 10 m",
        b"12 34 FutureOperator\n10 10 m",
    )
    result = reads_ai7(data)

    with pytest.raises(UnsupportedLegacyFeature, match="intersects unsupported"):
        patch_container_translate(
            result,
            TranslateContainer(
                container_type="group",
                container_id="card",
                dx=5,
                dy=5,
                expected_members=frozenset(
                    {("path", "shape"), ("text", "label"), ("linked_image", "photo")}
                ),
            ),
        )


def test_container_translate_rejects_a_member_origin_outside_the_container() -> None:
    result = reads_ai7(dumps_ai7(translatable_group_document()))
    path_origin = next(
        origin
        for origin in result.origins
        if origin.node_type == "path" and origin.node_id == "shape"
    )
    stale_origins = tuple(
        replace(origin, start=0) if origin is path_origin else origin for origin in result.origins
    )

    with pytest.raises(UnsupportedLegacyFeature, match="out-of-range source span"):
        patch_container_translate(
            replace(result, origins=stale_origins),
            TranslateContainer(
                container_type="group",
                container_id="card",
                dx=5,
                dy=5,
                expected_members=frozenset(
                    {("path", "shape"), ("text", "label"), ("linked_image", "photo")}
                ),
            ),
        )


def test_zero_container_translation_is_byte_preserving() -> None:
    data = dumps_ai7(translatable_group_document()).replace(b"10 10 m", b"10.000 10.0 m")
    result = reads_ai7(data)

    patched = patch_container_translate(
        result,
        TranslateContainer(
            container_type="group",
            container_id="card",
            dx=0,
            dy=0,
            expected_members=frozenset(
                {("path", "shape"), ("text", "label"), ("linked_image", "photo")}
            ),
        ),
    )

    assert patched.data == data


def test_batch_patch_applies_disjoint_typed_operations_atomically() -> None:
    data = dumps_ai7(translatable_group_document()).replace(
        b"%%EndSetup\n",
        b"%%EndSetup\n12 34 FutureOperator % outside \xff\n",
    )
    result = reads_ai7(data)

    plan = plan_legacy_patch(
        result,
        (
            SetPathFill(
                path_id="shape",
                expected_fill=Color(1, 0, 0),
                fill=Color(0, 1, 0),
            ),
            ReplaceText(text_id="label", expected_text="Label", text="Updated"),
        ),
    )
    patched = apply_legacy_patch(result, plan)

    assert plan.operation_count == 2
    assert plan.to_dict()["replacement_count"] == 2
    assert b"FutureOperator % outside \xff" in patched.data
    source_cursor = 0
    patched_cursor = 0
    for replacement in plan.replacements:
        unchanged_size = replacement.start - source_cursor
        assert (
            patched.data[patched_cursor : patched_cursor + unchanged_size]
            == data[source_cursor : replacement.start]
        )
        source_cursor = replacement.end
        patched_cursor += unchanged_size + len(replacement.data)
    assert patched.data[patched_cursor:] == data[source_cursor:]
    restored_group = reads_ai7(patched.data).document.layers[0].groups[0]
    assert restored_group.paths[0].fill == Color(0, 1, 0)
    assert restored_group.text_frames[0].text == "Updated"


def test_batch_patch_rejects_two_operations_for_the_same_field() -> None:
    result = reads_ai7(dumps_ai7(supported_document()))

    with pytest.raises(UnsupportedLegacyFeature, match="operations conflict"):
        plan_legacy_patch(
            result,
            (
                SetPathFill(
                    path_id="shape",
                    expected_fill=Color(1, 0, 0),
                    fill=Color(0, 1, 0),
                ),
                SetPathFill(
                    path_id="shape",
                    expected_fill=Color(1, 0, 0),
                    fill=Color(0, 0, 1),
                ),
            ),
        )


def test_batch_patch_treats_two_empty_span_insertions_as_a_conflict() -> None:
    result = reads_ai7(dumps_ai7(text_document(text="")))

    with pytest.raises(UnsupportedLegacyFeature, match="operations conflict"):
        plan_legacy_patch(
            result,
            (
                ReplaceText(text_id="headline", expected_text="", text="First"),
                ReplaceText(text_id="headline", expected_text="", text="Second"),
            ),
        )


def test_patch_plan_requires_the_complete_source_to_remain_unchanged() -> None:
    result = reads_ai7(dumps_ai7(supported_document()))
    plan = plan_legacy_patch(
        result,
        (
            SetPathFill(
                path_id="shape",
                expected_fill=Color(1, 0, 0),
                fill=Color(0, 1, 0),
            ),
        ),
    )
    changed_source = tokenize_legacy(result.source.data + b"% changed after planning\n")

    with pytest.raises(UnsupportedLegacyFeature, match="complete source changed"):
        apply_legacy_patch(replace(result, source=changed_source), plan)


def test_patch_plan_revalidates_replacement_ranges_before_apply() -> None:
    result = reads_ai7(dumps_ai7(supported_document()))
    plan = plan_legacy_patch(
        result,
        (
            SetPathFill(
                path_id="shape",
                expected_fill=Color(1, 0, 0),
                fill=Color(0, 1, 0),
            ),
        ),
    )
    invalid_plan = replace(
        plan,
        replacements=(SourceReplacement(0, len(result.source.data) + 1, b"invalid"),),
    )

    with pytest.raises(UnsupportedLegacyFeature, match="exceeds source size"):
        apply_legacy_patch(result, invalid_plan)


def test_patch_legacy_is_the_atomic_plan_and_apply_convenience() -> None:
    result = reads_ai7(dumps_ai7(text_document()))

    patched = patch_legacy(
        result,
        (ReplaceText(text_id="headline", expected_text="Original (copy)", text="New"),),
    )

    assert reads_ai7(patched.data).document.layers[0].text_frames[0].text == "New"


def test_linked_image_source_patch_changes_only_the_metadata_payload() -> None:
    data = dumps_ai7(linked_image_document()).replace(b"\n", b"\r\n").replace(
        b"%%EndSetup\r\n",
        b"%%EndSetup\r\n12 34 FutureOperator % opaque \xff\r\n",
    )
    result = reads_ai7(data)
    origin = next(
        origin
        for origin in result.origins
        if origin.node_type == "linked_image" and origin.node_id == "hero"
    )
    metadata_origin = origin.field("metadata")
    assert metadata_origin is not None
    assert [field.field for field in origin.fields_with_prefix("geometry.")] == [
        "geometry.0",
        "geometry.1",
        "geometry.2",
        "geometry.3",
        "geometry.4",
    ]

    patched = patch_linked_image_source(
        result,
        ReplaceLinkedImageSource(
            image_id="hero",
            expected_source="Links/hero.png",
            source="Links/replacement image.png",
        ),
    )

    replacement_length = len(patched.data) - (len(data) - len(metadata_origin.expected))
    assert patched.data[: metadata_origin.start] == data[: metadata_origin.start]
    assert patched.data[metadata_origin.start + replacement_length :] == data[metadata_origin.end :]
    assert b"FutureOperator % opaque \xff" in patched.data
    restored = reads_ai7(patched.data).document.layers[0].linked_images[0]
    assert restored.source == "Links/replacement image.png"
    assert restored.id == "hero"
    assert restored.name == "Hero image"
    assert (restored.x, restored.y, restored.width, restored.height, restored.rotation) == (
        10,
        70,
        80,
        60,
        -5,
    )


def test_linked_image_source_patch_requires_matching_semantic_precondition() -> None:
    result = reads_ai7(dumps_ai7(linked_image_document()))

    with pytest.raises(UnsupportedLegacyFeature, match="source precondition failed"):
        patch_linked_image_source(
            result,
            ReplaceLinkedImageSource(
                image_id="hero",
                expected_source="Links/stale.png",
                source="Links/new.png",
            ),
        )


@pytest.mark.parametrize("image_id", ["missing", "duplicate"])
def test_linked_image_source_patch_stops_for_zero_or_multiple_matches(image_id: str) -> None:
    document = linked_image_document(image_id="duplicate" if image_id == "duplicate" else "hero")
    if image_id == "duplicate":
        document.layers[0].linked_images.append(
            LinkedImage(
                id="duplicate",
                source="Links/second.png",
                x=10,
                y=70,
                width=40,
                height=30,
            )
        )
    result = reads_ai7(dumps_ai7(document))

    with pytest.raises(UnsupportedLegacyFeature, match="matched"):
        patch_linked_image_source(
            result,
            ReplaceLinkedImageSource(
                image_id=image_id,
                expected_source="Links/hero.png",
                source="Links/new.png",
            ),
        )


def test_linked_image_source_patch_rejects_changed_metadata_source() -> None:
    result = reads_ai7(dumps_ai7(linked_image_document()))
    origin = next(origin for origin in result.origins if origin.node_id == "hero")
    metadata_origin = origin.field("metadata")
    assert metadata_origin is not None
    changed_source = result.source.patched(
        [
            SourceReplacement(
                metadata_origin.start,
                metadata_origin.end,
                b"A" * len(metadata_origin.expected),
            )
        ]
    )
    stale_result = replace(result, source=changed_source)

    with pytest.raises(UnsupportedLegacyFeature, match="source precondition failed"):
        patch_linked_image_source(
            stale_result,
            ReplaceLinkedImageSource(
                image_id="hero",
                expected_source="Links/hero.png",
                source="Links/new.png",
            ),
        )


def test_linked_image_source_operation_rejects_an_invalid_new_path() -> None:
    with pytest.raises(ValueError, match="non-empty path"):
        ReplaceLinkedImageSource(
            image_id="hero",
            expected_source="Links/hero.png",
            source="",
        )


def test_linked_image_source_patch_selects_images_in_nested_groups() -> None:
    image = LinkedImage(
        id="nested-image",
        source="Links/nested.png",
        x=10,
        y=70,
        width=40,
        height=30,
    )
    group = Group(
        id="nested-group",
        linked_images=[image],
        item_order=[LayerItemRef("image", image.id)],
    )
    document = Document(
        width=100,
        height=80,
        layers=[
            Layer(
                id="artwork",
                name="Artwork",
                groups=[group],
                item_order=[LayerItemRef("group", group.id)],
            )
        ],
    )
    result = reads_ai7(dumps_ai7(document))

    patched = patch_linked_image_source(
        result,
        ReplaceLinkedImageSource(
            image_id="nested-image",
            expected_source="Links/nested.png",
            source="Links/new.png",
        ),
    )

    restored_group = reads_ai7(patched.data).document.layers[0].groups[0]
    assert restored_group.linked_images[0].source == "Links/new.png"


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
    position_origin = origin.field("position")
    matrix_origin = origin.field("matrix")
    assert text_origin is not None
    assert position_origin is not None
    assert matrix_origin is not None
    assert data[text_origin.start : text_origin.end] == rb"Original \(copy\)"
    assert data[origin.start : origin.end].startswith(b"%%py-ai-text-id: (headline)")
    assert data[position_origin.start : position_origin.end].endswith(b"10 50 0 Tp")
    assert data[matrix_origin.start : matrix_origin.end].endswith(b"10 50 Tm")

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


def test_multi_statement_text_origin_retains_each_content_span() -> None:
    data = dumps_ai7(text_document(text="FirstSecond")).replace(
        b"(FirstSecond) Tx",
        b"(First) Tx\n(Second) Tx",
    )
    result = reads_ai7(data)
    origin = next(origin for origin in result.origins if origin.node_id == "headline")
    text_origins = origin.fields_with_prefix("text.")

    assert origin.field("text") is None
    assert [field.field for field in text_origins] == ["text.0", "text.1"]
    assert [data[field.start : field.end] for field in text_origins] == [b"First", b"Second"]
    assert result.document.layers[0].text_frames[0].text == "FirstSecond"
    assert result.safe_to_reserialize is True

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
