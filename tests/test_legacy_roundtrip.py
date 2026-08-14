import json
from pathlib import Path

from py_ai_illustrator.legacy import dumps_ai7, load_ai7, loads_ai7
from py_ai_illustrator.model import Color, Document, Layer, Point
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
    restored = loads_ai7(dumps_ai7(original))
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
