import json
from pathlib import Path
from subprocess import CompletedProcess, TimeoutExpired
from types import SimpleNamespace

import pytest

from py_ai_illustrator import native
from py_ai_illustrator.cli import build_parser
from py_ai_illustrator.legacy import read_ai7
from py_ai_illustrator.model import Color, Document, Group, Layer, Point, TextFrame
from py_ai_illustrator.model import Path as AIPath
from py_ai_illustrator.native import (
    NativeCompileProfile,
    _build_direct_native_javascript,
    _document_spec,
    _load_document,
    _validate_document,
)
from py_ai_illustrator.native_bridge import (
    NATIVE_REQUIRED_CHECKS,
    NativeCompileRequest,
    NativeContractError,
    NativeRuntimeBridge,
    parse_native_compile_result,
    serialize_native_compile_request,
)

ROOT = Path(__file__).parents[1]
GROUP_PARENTING_FIXTURE = ROOT / "tests" / "fixtures" / "native-group-parenting.json"
AREA_TEXT_OVERFLOW_FIXTURE = ROOT / "tests" / "fixtures" / "native-area-text-overflow.json"


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


def test_native_request_contract_preserves_json_boundary_types() -> None:
    request = NativeCompileRequest(
        document={
            "unicode": "日本語\\\"\\\\",
            "number": 12.5,
            "null": None,
            "array": [True, 0, {"nested": "値"}],
            "object": {"key": "value"},
        },
        destination="/tmp/出力.ai",
    )

    payload = json.loads(serialize_native_compile_request(request))

    assert payload["contract"] == "py-ai-illustrator.native-compile"
    assert payload["version"] == 1
    assert payload["operation"] == "compile"
    assert payload["destination"] == "/tmp/出力.ai"
    assert payload["document"]["unicode"] == "日本語\\\"\\\\"
    assert payload["document"]["number"] == 12.5
    assert payload["document"]["null"] is None
    assert payload["document"]["array"] == [True, 0, {"nested": "値"}]


def test_native_result_contract_rejects_unversioned_or_non_json_responses() -> None:
    with pytest.raises(NativeContractError, match="non-JSON"):
        parse_native_compile_result("error from Illustrator")
    with pytest.raises(NativeContractError, match="unsupported result contract"):
        parse_native_compile_result(json.dumps({"ok": False}))


def test_native_result_contract_requires_all_success_checks() -> None:
    valid_checks = {name: True for name in NATIVE_REQUIRED_CHECKS}
    invalid_checks = (
        {},
        {**valid_checks, "native_editability": False},
        {**valid_checks, "native_editability": "true"},
        {name: True for name in NATIVE_REQUIRED_CHECKS[:-1]},
    )

    for checks in invalid_checks:
        with pytest.raises(NativeContractError):
            parse_native_compile_result(
                json.dumps(
                    {
                        "contract": "py-ai-illustrator.native-compile-result",
                        "version": 1,
                        "operation": "compile",
                        "ok": True,
                        "checks": checks,
                    }
                )
            )


def test_native_runtime_bridge_places_request_and_runtime_independently(
    tmp_path: Path,
) -> None:
    request = NativeCompileRequest(
        document={"title": "日本語", "items": [None, 1.25]},
        destination=str(tmp_path / "temporary.ai"),
    )
    calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def fake_executor(*args: object, **kwargs: object) -> CompletedProcess[str]:
        calls.append((args, kwargs))
        return CompletedProcess([], 0, stdout="{}", stderr="")

    NativeRuntimeBridge(runtime_loader=lambda: "#target illustrator\\n// runtime").execute(
        request,
        tmp_path,
        timeout=30,
        application_name="Illustrator Test",
        script_executor=fake_executor,
    )

    request_payload = json.loads(
        (tmp_path / "py-ai-native-request.json").read_text(encoding="utf-8")
    )
    assert request_payload["document"]["title"] == "日本語"
    assert calls[0][0][0] == "#target illustrator\\n// runtime"
    assert calls[0][1]["script_name"] == "py-ai-native-runtime.jsx"


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


def test_group_parenting_fixture_request_preserves_nested_area_text_and_image(
    tmp_path: Path,
) -> None:
    document, source_base = _load_document(GROUP_PARENTING_FIXTURE)
    assert source_base == GROUP_PARENTING_FIXTURE.parent

    spec = _document_spec(document, tmp_path, NativeCompileProfile())
    layer_items = spec["layers"][0]["items"]

    assert [item["id"] for item in layer_items] == ["image-group", "area-text-group"]
    assert [[child["kind"] for child in item["items"]] for item in layer_items] == [
        ["image"],
        ["text"],
    ]
    image = layer_items[0]["items"][0]
    area_text = layer_items[1]["items"][0]
    assert image["note"].startswith("py-ai-image:")
    assert (image["x"], image["y"], image["width"], image["height"], image["rotation"]) == (
        18.0,
        142.0,
        36.0,
        28.0,
        45.0,
    )
    assert image["width"] / image["height"] == 36.0 / 28.0
    assert image["dom_width"] == pytest.approx(45.25483399593904)
    assert image["dom_height"] == pytest.approx(45.25483399593904)
    assert area_text["note"].startswith("py-ai-text:")
    assert (area_text["area_width"], area_text["area_height"]) == (120.0, 48.0)

    request = NativeCompileRequest(document=spec, destination=str(tmp_path / "result.ai"))
    payload = json.loads(serialize_native_compile_request(request))
    assert payload["document"]["layers"][0]["items"] == layer_items


def test_area_text_overflow_fixture_uses_matching_fit_and_overset_geometry() -> None:
    document, source_base = _load_document(AREA_TEXT_OVERFLOW_FIXTURE)

    assert source_base == AREA_TEXT_OVERFLOW_FIXTURE.parent
    texts = document.layers[0].text_frames
    assert [text.id for text in texts] == [
        "fit-area-text",
        "overset-area-text",
        "point-text-control",
    ]
    fit, overset, point = texts
    assert (fit.area_width, fit.area_height, fit.font_name, fit.font_size, fit.leading) == (
        overset.area_width,
        overset.area_height,
        overset.font_name,
        overset.font_size,
        overset.leading,
    )
    assert len(overset.text) > len(fit.text)
    assert point.is_area_text is False


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
    assert "日本語" not in javascript
    assert "py-ai-native-request.json" in javascript
    assert str(tmp_path) not in javascript
    assert "item.move(parent, ElementPlacement.PLACEATBEGINNING)" in javascript
    assert "ensureParent(frame, parent, textSpec.id)" in javascript
    assert "ensureParent(image, parent, imageSpec.id)" in javascript
    assert "image.width = imageSpec.width" in javascript
    assert "image.height = imageSpec.height" in javascript
    assert "image.rotate(-imageSpec.rotation)" in javascript
    assert ".overflows" not in javascript
    assert "function areaTextOverflows(frame)" in javascript
    assert "frameStart !== storyStart" in javascript
    assert "frameEnd !== storyEnd" in javascript
    assert 'if (overflow !== false) return "area text overflow " + String(overflow)' in javascript
    assert "text_overflows: textOverflowInspections" in javascript


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


def test_compile_refuses_to_overwrite_existing_output(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    destination = tmp_path / "result.ai"
    destination.write_bytes(b"existing")
    execute_called = False

    def unexpected_execute(*args: object, **kwargs: object) -> CompletedProcess[str]:
        nonlocal execute_called
        execute_called = True
        raise AssertionError("Illustrator must not run for an existing destination")

    monkeypatch.setattr(native.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(native, "_execute_javascript", unexpected_execute)

    result = native.compile_native_ai(sample_document(), destination)

    assert result["status"] == "invalid-input"
    assert not execute_called
    assert destination.read_bytes() == b"existing"


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
                    "contract": "py-ai-illustrator.native-compile-result",
                    "version": 1,
                    "operation": "compile",
                    "ok": True,
                    "illustrator_version": "30.7.0",
                    "checks": {name: True for name in NATIVE_REQUIRED_CHECKS},
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
                    "contract": "py-ai-illustrator.native-compile-result",
                    "version": 1,
                    "operation": "compile",
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


def test_compile_rejects_success_with_a_failed_required_check(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    destination = tmp_path / "result.ai"
    checks = {name: True for name in NATIVE_REQUIRED_CHECKS}
    checks["native_editability"] = False

    monkeypatch.setattr(native.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(
        native,
        "_execute_javascript",
        lambda *args, **kwargs: CompletedProcess(
            [],
            0,
            stdout=json.dumps(
                {
                    "contract": "py-ai-illustrator.native-compile-result",
                    "version": 1,
                    "operation": "compile",
                    "ok": True,
                    "checks": checks,
                }
            ),
            stderr="",
        ),
    )

    result = native.compile_native_ai(sample_document(), destination)

    assert result["status"] == "failed"
    assert not destination.exists()


@pytest.mark.parametrize(
    ("stdout", "expected_status"),
    (
        ("not json", "failed"),
        (
            json.dumps(
                {
                    "contract": "py-ai-illustrator.native-compile-result",
                    "version": 1,
                    "operation": "compile",
                    "ok": False,
                    "error": "Illustrator exception",
                }
            ),
            "failed",
        ),
    ),
)
def test_compile_classifies_invalid_and_illustrator_error_responses(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    stdout: str,
    expected_status: str,
) -> None:
    destination = tmp_path / "result.ai"

    monkeypatch.setattr(native.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(
        native,
        "_execute_javascript",
        lambda *args, **kwargs: CompletedProcess([], 0, stdout=stdout, stderr=""),
    )

    result = native.compile_native_ai(sample_document(), destination)

    assert result["status"] == expected_status
    assert not destination.exists()


def test_compile_classifies_runtime_timeout_as_environment_unavailable(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    destination = tmp_path / "result.ai"

    monkeypatch.setattr(native.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(
        native,
        "_execute_javascript",
        lambda *args, **kwargs: (_ for _ in ()).throw(TimeoutExpired("osascript", 1)),
    )

    result = native.compile_native_ai(sample_document(), destination)

    assert result["status"] == "environment-unavailable"
    assert not destination.exists()


@pytest.mark.parametrize(
    ("fixture_name", "expected"),
    (
        (
            "quarterly-kpi-report.ai",
            {"paths": 17, "texts": 24, "groups": 4, "area_texts": 0, "images": 0},
        ),
        (
            "editorial-brochure.ai",
            {"paths": 4, "texts": 7, "groups": 0, "area_texts": 4, "images": 0},
        ),
        (
            "product-catalog.ai",
            {"paths": 3, "texts": 5, "groups": 0, "area_texts": 1, "images": 1},
        ),
    ),
)
def test_promotion_fixture_specs_cover_the_direct_backend(
    fixture_name: str,
    expected: dict[str, int],
    tmp_path: Path,
) -> None:
    document = read_ai7(ROOT / "examples" / fixture_name).document
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
