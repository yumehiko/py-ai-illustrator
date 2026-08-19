import json
import runpy
from pathlib import Path
from subprocess import CompletedProcess
from types import SimpleNamespace

import pytest

from py_ai_illustrator import native
from py_ai_illustrator.cli import build_parser
from py_ai_illustrator.model import Color, Document, Group, Layer, Point, TextFrame
from py_ai_illustrator.model import Path as AIPath
from py_ai_illustrator.native import (
    NativeCompileProfile,
    _build_direct_native_javascript,
    _document_spec,
    _javascript_literal,
    _load_document,
    _validate_document,
)

ROOT = Path(__file__).parents[1]


def sample_document() -> Document:
    nested = Group(
        id="card",
        name="Card",
        text_frames=[
            TextFrame(
                id="title",
                name="Title",
                text="日本語 title",
                x=20,
                y=80,
                font_name="Helvetica",
            )
        ],
    )
    return Document(
        width=100,
        height=100,
        title="Native test",
        layers=[
            Layer(
                id="artwork",
                name="Artwork",
                paths=[
                    AIPath(
                        id="background",
                        name="Background",
                        points=[Point(0, 100), Point(100, 100), Point(100, 0), Point(0, 0)],
                        fill=Color(1, 1, 1),
                    )
                ],
                groups=[nested],
            )
        ],
    )


def test_compile_native_cli_exposes_profile_and_runtime_options() -> None:
    args = build_parser().parse_args(
        [
            "compile-native",
            "document.json",
            "-o",
            "result.ai",
            "--color-space",
            "cmyk",
            "--timeout",
            "45",
            "--application",
            "Illustrator Test",
        ]
    )

    assert args.input == "document.json"
    assert args.output == "result.ai"
    assert args.color_space == "cmyk"
    assert args.timeout == 45.0
    assert args.application == "Illustrator Test"
    assert args.handler.__name__ == "_compile_native"


def test_load_document_json_uses_its_directory_as_the_asset_base(
    tmp_path: Path,
) -> None:
    source = tmp_path / "document.json"
    source.write_text(
        json.dumps(sample_document().to_dict(), ensure_ascii=False),
        encoding="utf-8",
    )

    document, source_base = _load_document(source)

    assert document == sample_document()
    assert source_base == tmp_path


def test_javascript_literal_uses_character_codes_for_strings() -> None:
    literal = _javascript_literal({"value": "日本語"})

    assert "日本語" not in literal
    assert "String.fromCharCode(26085,26412,35486)" in literal


def test_document_spec_preserves_ir_order_and_native_identity(tmp_path: Path) -> None:
    spec = _document_spec(sample_document(), tmp_path, NativeCompileProfile())

    assert spec["color_space"] == "rgb"
    assert spec["artboards"] == [
        {"id": "artboard-1", "name": "Artboard 1", "rect": [0.0, 100, 100, 0.0]}
    ]
    items = spec["layers"][0]["items"]
    assert [item["id"] for item in items] == ["background", "card"]
    assert items[0]["note"].startswith("py-ai-path:")
    assert items[1]["items"][0]["note"].startswith("py-ai-text:")


def test_direct_javascript_owns_and_reopens_only_its_document(tmp_path: Path) -> None:
    output = tmp_path / 'native "quoted".ai'
    spec = _document_spec(sample_document(), tmp_path, NativeCompileProfile())

    javascript = _build_direct_native_javascript(spec, output)

    assert "app.documents.add(colorSpace, spec.width, spec.height)" in javascript
    assert "documentRef.saveAs(destination, options)" in javascript
    assert "documentRef = app.open(destination)" in javascript
    assert "documentRef.close(SaveOptions.DONOTSAVECHANGES)" in javascript
    assert "app.activeDocument" not in javascript
    assert "previousCoordinateSystem" in javascript
    assert "options.pdfCompatible = spec.pdf_compatible" in javascript
    assert "layerIndex = spec.layers.length - 1; layerIndex >= 0; layerIndex--" in javascript
    assert str(tmp_path) not in javascript


def test_validation_rejects_duplicate_ids() -> None:
    document = sample_document()
    document.layers[0].groups[0].text_frames[0].id = "background"

    with pytest.raises(ValueError, match="Duplicate stable id"):
        _validate_document(document)


def test_validation_rejects_unmodeled_unknown_data() -> None:
    document = sample_document()
    document.layers[0].paths[0].unknown["operator"] = "opaque"

    with pytest.raises(ValueError, match="unsupported unknown data"):
        _validate_document(document)


def test_validation_requires_native_font_name_for_legacy_composite_font() -> None:
    document = sample_document()
    document.layers[0].groups[0].text_frames[0].font_name = (
        "_KozGoPr6N-Regular-83pv-RKSJ-H"
    )

    with pytest.raises(ValueError, match="native PostScript font name"):
        _validate_document(document)


def test_compile_profile_rejects_non_native_output_modes() -> None:
    with pytest.raises(ValueError, match="PDF-compatible"):
        NativeCompileProfile(pdf_compatible=False)
    with pytest.raises(ValueError, match="external links"):
        NativeCompileProfile(embed_linked_files=True)


def test_compile_promotes_only_a_verified_pdf_compatible_ai(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    destination = tmp_path / "result.ai"
    temporary_output: Path | None = None

    def fake_builder(spec: dict[str, object], output: Path) -> str:
        nonlocal temporary_output
        temporary_output = output
        return "direct-script"

    def fake_execute(*args: object, **kwargs: object) -> CompletedProcess[str]:
        assert temporary_output is not None
        temporary_output.write_bytes(b"compiled")
        return CompletedProcess(
            [],
            0,
            stdout=json.dumps(
                {
                    "ok": True,
                    "illustrator_version": "30.7.0",
                    "checks": {"native_editability": True},
                }
            ),
            stderr="",
        )

    monkeypatch.setattr(native.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(native, "_build_direct_native_javascript", fake_builder)
    monkeypatch.setattr(native, "_execute_javascript", fake_execute)
    monkeypatch.setattr(
        native,
        "inspect_file",
        lambda path: SimpleNamespace(
            format=native.FileFormat.PDF_COMPATIBLE_AI,
            to_dict=lambda: {"format": "pdf-compatible-ai"},
        ),
    )

    result = native.compile_native_ai(sample_document(), destination)

    assert result["status"] == "passed"
    assert destination.read_bytes() == b"compiled"


def test_compile_keeps_destination_absent_on_dom_mismatch(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    destination = tmp_path / "result.ai"

    monkeypatch.setattr(native.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(
        native,
        "_execute_javascript",
        lambda *args, **kwargs: CompletedProcess(
            [],
            0,
            stdout=json.dumps(
                {
                    "ok": False,
                    "checks": {"structure_and_order": False},
                    "errors": ["item order mismatch"],
                }
            ),
            stderr="",
        ),
    )

    result = native.compile_native_ai(sample_document(), destination)

    assert result["status"] == "mismatch"
    assert not destination.exists()


@pytest.mark.parametrize(
    ("script_name", "expected"),
    (
        (
            "quarterly_kpi_report.py",
            {"paths": 17, "texts": 24, "groups": 4, "area_texts": 0, "images": 0},
        ),
        (
            "editorial_brochure.py",
            {"paths": 4, "texts": 7, "groups": 0, "area_texts": 4, "images": 0},
        ),
        (
            "product_catalog.py",
            {"paths": 3, "texts": 5, "groups": 0, "area_texts": 1, "images": 1},
        ),
    ),
)
def test_promotion_fixture_specs_cover_the_direct_backend(
    script_name: str,
    expected: dict[str, int],
    tmp_path: Path,
) -> None:
    namespace = runpy.run_path(str(ROOT / "examples" / script_name))
    document = namespace["build_document"]()
    spec = _document_spec(document, tmp_path, NativeCompileProfile())

    def flatten(items: list[dict[str, object]]) -> list[dict[str, object]]:
        result: list[dict[str, object]] = []
        for item in items:
            result.append(item)
            if item["kind"] == "group":
                result.extend(flatten(item["items"]))
        return result

    items = flatten(
        [
            item
            for layer in spec["layers"]
            for item in layer["items"]
        ]
    )
    assert sum(item["kind"] == "path" for item in items) == expected["paths"]
    assert sum(item["kind"] == "text" for item in items) == expected["texts"]
    assert sum(item["kind"] == "group" for item in items) == expected["groups"]
    assert sum(
        item["kind"] == "text" and item["area_width"] is not None for item in items
    ) == expected["area_texts"]
    assert sum(item["kind"] == "image" for item in items) == expected["images"]
