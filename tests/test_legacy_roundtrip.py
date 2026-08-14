import json
from pathlib import Path

from py_ai_illustrator.legacy import dumps_ai7, load_ai7, loads_ai7
from py_ai_illustrator.model import CmykColor, Color, ControlPoint, Document, Layer, Point
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
    restored = loads_ai7(serialized)
    assert restored.to_dict() == original.to_dict()


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
