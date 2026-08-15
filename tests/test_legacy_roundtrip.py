import json
from pathlib import Path

import pytest

from py_ai_illustrator.legacy import (
    UnsupportedLegacyFeature,
    dumps_ai7,
    load_ai7,
    loads_ai7,
)
from py_ai_illustrator.model import (
    ClippingGroup,
    CmykColor,
    Color,
    CompoundPath,
    ControlPoint,
    Document,
    Layer,
    LayerItemRef,
    Point,
    TextFrame,
)
from py_ai_illustrator.model import Path as AIPath


def sample_document() -> Document:
    return Document(
        width=320,
        height=240,
        title="Roundtrip (sample)",
        metadata={"profile": "phase-0", "locale": "日本語"},
        layers=[
            Layer(
                id="artwork",
                name="Artwork",
                paths=[
                    AIPath(
                        id="rectangle",
                        name="Primary (shape)",
                        points=[Point(40, 40), Point(280, 40), Point(280, 200), Point(40, 200)],
                        fill=Color(1.0, 0.3, 0.0),
                        stroke=Color(0.1, 0.1, 0.1),
                        stroke_width=3,
                    )
                ],
            )
        ],
    )


def test_ai7_roundtrip_preserves_supported_semantics() -> None:
    original = sample_document()
    serialized = dumps_ai7(original)
    assert b"40 200 L\n40 40 L\nb" in serialized
    assert b"%AI3_Note:py-ai:" in serialized
    assert b"%AI5_ArtSize: 320 240" in serialized
    assert b"%AI3_TemplateBox: 160 360 160 360" in serialized
    restored = loads_ai7(serialized)
    assert restored.to_dict() == original.to_dict()


def test_text_alignment_is_written_as_native_paragraph_attribute() -> None:
    document = Document(
        width=200,
        height=100,
        layers=[
            Layer(
                id="text",
                name="Text",
                text_frames=[
                    TextFrame(
                        id="centered",
                        text="Centered",
                        x=100,
                        y=50,
                        alignment="center",
                    ),
                    TextFrame(
                        id="right-aligned",
                        text="Right",
                        x=180,
                        y=30,
                        alignment="right",
                    ),
                ],
            )
        ],
    )

    serialized = dumps_ai7(document)

    assert b"1 Ta\n(Centered) Tx" in serialized
    assert b"2 Ta\n(Right) Tx" in serialized
    assert loads_ai7(serialized).to_dict() == document.to_dict()


def test_standard_path_note_recovers_id_and_name_without_private_comments() -> None:
    serialized = dumps_ai7(sample_document())
    illustrator_style = b"\n".join(
        line
        for line in serialized.splitlines()
        if not line.startswith((b"%AI7_Tag:", b"%%py-ai-path-name:"))
    )
    restored_path = loads_ai7(illustrator_style).layers[0].paths[0]
    assert restored_path.id == "rectangle"
    assert restored_path.name == "Primary (shape)"


def test_example_json_is_a_valid_document() -> None:
    example = Path(__file__).parents[1] / "examples" / "rectangle.json"
    document = Document.from_dict(json.loads(example.read_text(encoding="utf-8")))
    assert document.layers[0].paths[0].id == "orange-rectangle"


def test_generated_example_matches_its_json_source() -> None:
    examples = Path(__file__).parents[1] / "examples"
    source = Document.from_dict(
        json.loads((examples / "rectangle.json").read_text(encoding="utf-8"))
    )
    generated = load_ai7(examples / "rectangle.ai")
    assert generated.to_dict() == source.to_dict()


def test_generated_cmyk_curve_matches_its_json_source() -> None:
    examples = Path(__file__).parents[1] / "examples"
    source = Document.from_dict(
        json.loads((examples / "cmyk-curve.json").read_text(encoding="utf-8"))
    )
    generated = load_ai7(examples / "cmyk-curve.ai")
    assert generated.to_dict() == source.to_dict()


def test_generated_compound_path_matches_its_json_source() -> None:
    examples = Path(__file__).parents[1] / "examples"
    source = Document.from_dict(
        json.loads((examples / "compound-path.json").read_text(encoding="utf-8"))
    )
    generated = load_ai7(examples / "compound-path.ai")
    assert generated.to_dict() == source.to_dict()


def test_generated_clipping_group_matches_its_json_source() -> None:
    examples = Path(__file__).parents[1] / "examples"
    source = Document.from_dict(
        json.loads((examples / "clipping-group.json").read_text(encoding="utf-8"))
    )
    generated = load_ai7(examples / "clipping-group.ai")
    assert generated.to_dict() == source.to_dict()


def test_generated_mixed_stack_matches_its_json_source() -> None:
    examples = Path(__file__).parents[1] / "examples"
    source = Document.from_dict(
        json.loads((examples / "mixed-stack.json").read_text(encoding="utf-8"))
    )
    generated = load_ai7(examples / "mixed-stack.ai")
    assert generated.to_dict() == source.to_dict()


def test_bezier_and_cmyk_roundtrip() -> None:
    original = Document(
        width=200,
        height=200,
        layers=[
            Layer(
                id="curves",
                name="Curves",
                paths=[
                    AIPath(
                        id="cmyk-curve",
                        points=[
                            Point(20, 20, out_handle=ControlPoint(20, 120)),
                            Point(
                                180,
                                180,
                                in_handle=ControlPoint(180, 80),
                                smooth=True,
                            ),
                        ],
                        closed=False,
                        fill=None,
                        stroke=CmykColor(1.0, 0.25, 0.0, 0.1),
                        stroke_width=4,
                    )
                ],
            )
        ],
    )
    restored = loads_ai7(dumps_ai7(original))
    assert restored.to_dict() == original.to_dict()


def test_closed_bezier_roundtrip_preserves_closing_handles() -> None:
    original = Document(
        width=200,
        height=200,
        layers=[
            Layer(
                id="curves",
                name="Curves",
                paths=[
                    AIPath(
                        id="closed-curve",
                        points=[
                            Point(
                                20,
                                20,
                                in_handle=ControlPoint(10, 40),
                                out_handle=ControlPoint(40, 10),
                                smooth=True,
                            ),
                            Point(
                                180,
                                20,
                                in_handle=ControlPoint(160, 10),
                                out_handle=ControlPoint(190, 40),
                            ),
                            Point(
                                100,
                                180,
                                in_handle=ControlPoint(140, 180),
                                out_handle=ControlPoint(60, 180),
                            ),
                        ],
                        closed=True,
                        fill=None,
                        stroke=CmykColor(1.0, 0.25, 0.0, 0.1),
                    )
                ],
            )
        ],
    )
    restored = loads_ai7(dumps_ai7(original))
    assert restored.to_dict() == original.to_dict()


def test_reads_compact_v_and_y_curve_operators() -> None:
    source = b"""%!PS-Adobe-3.0
%%BoundingBox: 0 0 100 100
%AI5_FileFormat 3.0
0 0 0 1 k
10 10 m
20 30 40 50 v
60 70 80 90 y
S
%%EOF
"""
    document = loads_ai7(source)
    points = document.layers[0].paths[0].points
    assert points[0].out_handle is None
    assert points[1].in_handle == ControlPoint(20, 30)
    assert points[1].out_handle == ControlPoint(60, 70)
    assert points[2].in_handle is None


def test_reads_illustrator_native_ai8_paint_style_and_top_level_metadata() -> None:
    source = b"""%!PS-Adobe-3.0
%%Title: (Native Fixture)
%%BoundingBox: 38 38 282 202
%AI5_FileFormat 3.0
%%BeginProlog
%%Title: (Embedded Procset)
%%BoundingBox: 0 0 5 5
%%EndProlog
%AI5_BeginLayer
1 1 1 1 0 0 1 0 79 128 255 0 50 Lb
(Illustrator Native) Ln
0 0.82324 0.932219 0 1 0.301961 0 Xa
0.76434 0.800351 0.929503 0.666529 0.14902 0.101961 0.05098 XA
0 J 0 j 3 w 10 M []0 d
40 40 m
280 40 L
280 200 L
40 200 L
40 40 L
b
LB
%AI5_EndLayer
%%EOF
"""
    document = loads_ai7(source)
    path = document.layers[0].paths[0]
    assert document.title == "Native Fixture"
    assert (document.width, document.height) == (244.0, 164.0)
    assert path.fill == Color(1.0, 0.301961, 0.0)
    assert path.stroke == Color(0.14902, 0.101961, 0.05098)
    assert path.stroke_width == 3.0
    assert len(path.points) == 4


def test_compound_path_roundtrip_preserves_components_and_polarity() -> None:
    fill = Color(0.25, 0.5, 1.0)
    original = Document(
        width=300,
        height=300,
        layers=[
            Layer(
                id="compound-layer",
                name="Compound",
                compound_paths=[
                    CompoundPath(
                        id="compound-1",
                        name="Frame",
                        paths=[
                            AIPath(
                                id="outer",
                                points=[
                                    Point(20, 20),
                                    Point(280, 20),
                                    Point(280, 280),
                                    Point(20, 280),
                                ],
                                fill=fill,
                                stroke=None,
                                polarity="positive",
                            ),
                            AIPath(
                                id="inner",
                                points=[
                                    Point(90, 90),
                                    Point(90, 210),
                                    Point(210, 210),
                                    Point(210, 90),
                                ],
                                fill=fill,
                                stroke=None,
                                polarity="negative",
                            ),
                        ],
                    )
                ],
            )
        ],
    )
    serialized = dumps_ai7(original)
    assert b"*u" in serialized
    assert b"1 D" in serialized
    assert b"0 D" in serialized
    assert b"*U" in serialized
    restored = loads_ai7(serialized)
    assert restored.to_dict() == original.to_dict()


def test_clipping_group_roundtrip_preserves_mask_and_content() -> None:
    original = Document(
        width=300,
        height=300,
        layers=[
            Layer(
                id="clipping-layer",
                name="Clipping",
                clipping_groups=[
                    ClippingGroup(
                        id="clip-1",
                        name="Square crop",
                        clipping_path=AIPath(
                            id="mask",
                            points=[
                                Point(80, 80),
                                Point(220, 80),
                                Point(220, 220),
                                Point(80, 220),
                            ],
                            fill=None,
                            stroke=None,
                        ),
                        paths=[
                            AIPath(
                                id="content",
                                points=[
                                    Point(20, 20),
                                    Point(280, 20),
                                    Point(280, 280),
                                    Point(20, 280),
                                ],
                                fill=Color(1.0, 0.25, 0.5),
                                stroke=None,
                            )
                        ],
                    )
                ],
            )
        ],
    )
    serialized = dumps_ai7(original)
    assert b"q" in serialized
    assert b"h\nW\nn" in serialized
    assert b"Q" in serialized
    restored = loads_ai7(serialized)
    assert restored.to_dict() == original.to_dict()


def test_mixed_layer_item_order_is_serialized_and_read_in_file_order() -> None:
    examples = Path(__file__).parents[1] / "examples"
    compound = Document.from_dict(
        json.loads((examples / "compound-path.json").read_text(encoding="utf-8"))
    ).layers[0].compound_paths[0]
    clipping = Document.from_dict(
        json.loads((examples / "clipping-group.json").read_text(encoding="utf-8"))
    ).layers[0].clipping_groups[0]
    path = sample_document().layers[0].paths[0]
    original = Document(
        width=320,
        height=300,
        layers=[
            Layer(
                id="mixed",
                name="Mixed",
                paths=[path],
                compound_paths=[compound],
                clipping_groups=[clipping],
                item_order=[
                    LayerItemRef("clipping_group", clipping.id),
                    LayerItemRef("path", path.id),
                    LayerItemRef("compound_path", compound.id),
                ],
            )
        ],
    )

    serialized = dumps_ai7(original)
    assert serialized.index(b"%%py-ai-clipping-id:") < serialized.index(
        b"%AI7_Tag: (rectangle)"
    ) < serialized.index(b"%%py-ai-compound-id:")
    restored = loads_ai7(serialized)
    assert restored.to_dict() == original.to_dict()


def test_layer_derives_legacy_grouped_item_order_when_field_is_missing() -> None:
    data = sample_document().to_dict()
    del data["layers"][0]["item_order"]
    document = Document.from_dict(data)
    assert document.layers[0].item_order == [LayerItemRef("path", "rectangle")]


def test_point_text_roundtrip_preserves_editable_text_semantics() -> None:
    original = Document(
        width=320,
        height=240,
        layers=[
            Layer(
                id="table",
                name="Table",
                text_frames=[
                    TextFrame(
                        id="header",
                        name="Header label",
                        text="Table (Header)",
                        x=40,
                        y=180,
                        font_name="Helvetica-Bold",
                        font_size=14,
                        fill=Color(0.1, 0.2, 0.3),
                        alignment="center",
                    )
                ],
            )
        ],
    )

    serialized = dumps_ai7(original)
    assert b"0 To" in serialized
    assert b"/Helvetica-Bold 14 0 0 Tf" in serialized
    assert b"(Table \\(Header\\)) Tx" in serialized
    assert loads_ai7(serialized).to_dict() == original.to_dict()


def test_reads_illustrator_native_octal_text_body() -> None:
    source = br"""%!PS-Adobe-3.0
%%BoundingBox: 0 0 320 240
%AI5_FileFormat 3.0
%AI5_BeginLayer
1 1 1 1 0 0 1 0 79 128 255 0 50 Lb
(Native Text) Ln
0 To
1 0 0 1 40 180 0 Tp
TP
0.1 0.2 0.3 Xa
/Helvetica 14 0 0 Tf
(\124) Tx 1 20 Tk
TO
0 To
1 0 0 1 47.1 180 0 Tp
TP
(\141\142\154\145\040\110\145\141\144\145\162) Tx 1 0 Tk
TO
LB
%AI5_EndLayer
%%EOF
"""

    texts = loads_ai7(source).layers[0].text_frames
    assert "".join(text.text for text in texts) == "Table Header"
    assert (texts[0].x, texts[0].y, texts[0].font_size) == (40.0, 180.0, 14.0)
    assert texts[1].font_size == 14.0
    assert texts[0].fill == Color(0.1, 0.2, 0.3)


def test_japanese_rksj_point_text_roundtrip() -> None:
    font_name = "_KozGoPr6N-Regular-83pv-RKSJ-H"
    original = Document(
        width=420,
        height=240,
        layers=[
            Layer(
                id="japanese",
                name="Japanese",
                text_frames=[
                    TextFrame(
                        id="見出し",
                        name="日本語見出し",
                        text="日本語の表見出し",
                        x=40,
                        y=180,
                        font_name=font_name,
                        font_size=16,
                    )
                ],
            )
        ],
    )

    serialized = dumps_ai7(original)
    assert f"%AI3_BeginEncoding: {font_name} ".encode() in serialized
    assert br"(\223\372\226{\214\352\202\314" in serialized
    assert loads_ai7(serialized).to_dict() == original.to_dict()


def test_non_ascii_text_requires_a_compatible_legacy_font() -> None:
    document = Document(
        width=100,
        height=100,
        layers=[
            Layer(
                id="text",
                name="Text",
                text_frames=[
                    TextFrame(id="unicode", text="日本語", x=10, y=50)
                ],
            )
        ],
    )

    with pytest.raises(UnsupportedLegacyFeature, match="RKSJ"):
        dumps_ai7(document)
