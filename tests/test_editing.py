import hashlib
import json
from pathlib import Path

import pytest

from py_ai_illustrator import (
    Color,
    Document,
    Group,
    Layer,
    LinkedImage,
    Point,
    TextFrame,
    apply_edit_plan,
    plan_edit,
    read_ai7,
    semantic_diff,
)
from py_ai_illustrator.cli import main
from py_ai_illustrator.legacy import dumps_ai7
from py_ai_illustrator.model import Path as AIPath


def editable_document(*, duplicate_path: bool = False) -> Document:
    paths = [
        AIPath(
            id="logo",
            name="Logo",
            points=[Point(10, 10), Point(30, 10), Point(30, 30), Point(10, 30)],
            fill=Color(1, 0, 0),
            stroke=Color(0, 0, 0),
            stroke_width=2,
        )
    ]
    if duplicate_path:
        paths.append(
            AIPath(
                id="logo",
                points=[Point(40, 10), Point(50, 10)],
                closed=False,
                stroke=Color(0, 0, 0),
            )
        )
    card = Group(
        id="card",
        name="Card",
        paths=[
            AIPath(
                id="card-shape",
                points=[Point(10, 40), Point(40, 40), Point(40, 70), Point(10, 70)],
                fill=Color(0.8, 0.8, 0.8),
            )
        ],
        text_frames=[TextFrame(id="card-label", text="Card", x=15, y=55)],
        linked_images=[
            LinkedImage(
                id="card-image",
                source="Links/card.png",
                x=45,
                y=70,
                width=30,
                height=20,
            )
        ],
    )
    return Document(
        width=120,
        height=100,
        layers=[
            Layer(
                id="artwork",
                name="Artwork",
                paths=paths,
                text_frames=[TextFrame(id="headline", text="Old heading", x=10, y=90)],
                linked_images=[
                    LinkedImage(
                        id="hero",
                        source="Links/hero.png",
                        x=80,
                        y=80,
                        width=30,
                        height=30,
                    )
                ],
                groups=[card],
            )
        ],
    )


def request(*operations: dict[str, object], source_sha256: str | None = None) -> dict[str, object]:
    result: dict[str, object] = {"schema_version": 1, "operations": list(operations)}
    if source_sha256 is not None:
        result["source_sha256"] = source_sha256
    return result


def selector(node_type: str, node_id: str) -> dict[str, str]:
    return {"type": node_type, "id": node_id}


def write_fixture(path: Path, *, duplicate_path: bool = False) -> bytes:
    data = dumps_ai7(editable_document(duplicate_path=duplicate_path))
    path.write_bytes(data)
    return data


def assert_unchanged_outside_replacements(before: bytes, after: bytes, plan: object) -> None:
    source_cursor = 0
    output_cursor = 0
    for replacement in plan.patch_plan.replacements:
        unchanged_size = replacement.start - source_cursor
        assert (
            after[output_cursor : output_cursor + unchanged_size]
            == before[source_cursor : replacement.start]
        )
        source_cursor = replacement.end
        output_cursor += unchanged_size + len(replacement.data)
    assert after[output_cursor:] == before[source_cursor:]


def all_operations() -> dict[str, object]:
    return request(
        {
            "op": "replace_text",
            "selector": selector("text", "headline"),
            "text": "New heading",
        },
        {
            "op": "set_fill",
            "selector": selector("path", "logo"),
            "color": {"red": 0.1, "green": 0.2, "blue": 0.3},
        },
        {
            "op": "set_stroke",
            "selector": selector("path", "logo"),
            "color": {"cyan": 0.1, "magenta": 0.2, "yellow": 0.3, "black": 0.4},
        },
        {
            "op": "translate",
            "selector": selector("path", "logo"),
            "dx": 3,
            "dy": -2,
        },
        {
            "op": "translate",
            "selector": selector("group", "card"),
            "dx": 5,
            "dy": 7,
        },
        {
            "op": "replace_linked_image_source",
            "selector": selector("linked_image", "hero"),
            "source": "Links/new-hero.png",
        },
    )


def test_cli_plan_apply_validate_and_semantic_diff_complete_the_vertical_slice(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source = tmp_path / "input.ai"
    operations = tmp_path / "operations.json"
    output = tmp_path / "output.ai"
    before_bytes = write_fixture(source)
    operations.write_text(json.dumps(all_operations()), encoding="utf-8")

    assert main(["plan", str(source), str(operations)]) == 0
    plan_json = json.loads(capsys.readouterr().out)
    assert plan_json["applicable"] is True
    assert plan_json["input"]["format"] == "legacy-ai"
    assert plan_json["feature_profile"]["id"] == "legacy-ai7-trusted-v1"
    assert plan_json["replacement_count"] > len(plan_json["operations"])
    assert plan_json["expected_semantic_diff"]["difference_count"] > 0
    assert not output.exists()

    assert main(["apply", str(source), str(operations), "-o", str(output)]) == 0
    apply_json = json.loads(capsys.readouterr().out)
    assert apply_json["status"] == "applied"
    assert apply_json["validation"] == {
        "bytes_outside_replacement_spans_identical": True,
        "output_reparsed": True,
        "semantic_diff_matches_plan": True,
        "semantic_impact_allowed": True,
    }
    assert apply_json["compatibility"]["after"]["profile"]["id"] == (
        "legacy-ai7-trusted-v1"
    )
    assert source.read_bytes() == before_bytes

    restored = read_ai7(output).document
    layer = restored.layers[0]
    logo = layer.paths[0]
    assert layer.text_frames[0].text == "New heading"
    assert logo.fill == Color(0.1, 0.2, 0.3)
    assert logo.points[0] == Point(13, 8)
    assert layer.linked_images[0].source == "Links/new-hero.png"
    card = layer.groups[0]
    assert card.paths[0].points[0] == Point(15, 47)
    assert (card.text_frames[0].x, card.text_frames[0].y) == (20, 62)
    assert (card.linked_images[0].x, card.linked_images[0].y) == (50, 77)

    assert main(["validate", str(output)]) == 0
    validate_json = json.loads(capsys.readouterr().out)
    assert validate_json["valid"] is True

    assert main(["diff", str(source), str(output), "--semantic"]) == 0
    diff_json = json.loads(capsys.readouterr().out)
    assert diff_json["semantic_diff"] == apply_json["semantic_diff"]


def test_apply_preserves_every_byte_outside_planned_replacement_spans(tmp_path: Path) -> None:
    source = tmp_path / "input.ai"
    output = tmp_path / "output.ai"
    before = write_fixture(source)
    plan = plan_edit(source, all_operations())

    result = apply_edit_plan(plan, output)

    assert result["applied"] is True
    assert_unchanged_outside_replacements(before, output.read_bytes(), plan)
    assert semantic_diff(read_ai7(source).document, read_ai7(output).document) == plan.expected_diff


def test_plan_is_deterministic_and_never_creates_an_output(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source = tmp_path / "input.ai"
    operations = tmp_path / "operations.json"
    write_fixture(source)
    operations.write_text(json.dumps(all_operations()), encoding="utf-8")

    assert main(["plan", str(source), str(operations)]) == 0
    first = capsys.readouterr().out
    assert main(["plan", str(source), str(operations)]) == 0
    second = capsys.readouterr().out

    assert first == second
    assert sorted(tmp_path.iterdir()) == [source, operations]


def test_inspect_json_lists_exact_public_selectors(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source = tmp_path / "input.ai"
    write_fixture(source)

    assert main(["inspect", str(source), "--json"]) == 0
    report = json.loads(capsys.readouterr().out)

    assert {
        (item["selector"]["type"], item["selector"]["id"]) for item in report["selectors"]
    } >= {
        ("path", "logo"),
        ("text", "headline"),
        ("group", "card"),
        ("linked_image", "hero"),
    }
    assert report["compatibility"]["classification"] == "convertible"


def test_selector_zero_matches_stops_before_output(tmp_path: Path) -> None:
    source = tmp_path / "input.ai"
    output = tmp_path / "output.ai"
    write_fixture(source)

    plan = plan_edit(
        source,
        request(
            {
                "op": "replace_text",
                "selector": selector("text", "missing"),
                "text": "No",
            }
        ),
    )

    assert plan.applicable is False
    assert "matched 0 nodes" in plan.report["stop_reasons"][0]["message"]
    assert apply_edit_plan(plan, output)["applied"] is False
    assert not output.exists()


def test_selector_multiple_matches_stops_before_output(tmp_path: Path) -> None:
    source = tmp_path / "input.ai"
    write_fixture(source, duplicate_path=True)

    plan = plan_edit(
        source,
        request(
            {
                "op": "set_fill",
                "selector": selector("path", "logo"),
                "color": {"red": 0, "green": 1, "blue": 0},
            }
        ),
    )

    assert plan.applicable is False
    assert "matched 2 nodes" in plan.report["stop_reasons"][0]["message"]


def test_operation_target_type_mismatch_stops_without_fallback(tmp_path: Path) -> None:
    source = tmp_path / "input.ai"
    write_fixture(source)

    plan = plan_edit(
        source,
        request(
            {
                "op": "set_fill",
                "selector": selector("text", "headline"),
                "color": {"red": 0, "green": 1, "blue": 0},
            }
        ),
    )

    assert plan.applicable is False
    assert "does not support target type" in plan.report["stop_reasons"][0]["message"]


def test_source_sha_precondition_and_prepared_plan_both_detect_stale_source(
    tmp_path: Path,
) -> None:
    source = tmp_path / "input.ai"
    output = tmp_path / "output.ai"
    original = write_fixture(source)
    operation = {
        "op": "replace_text",
        "selector": selector("text", "headline"),
        "text": "Updated",
    }

    stale_request = request(operation, source_sha256="0" * 64)
    assert plan_edit(source, stale_request).report["stop_reasons"][0]["code"] == "stale-source"

    digest = hashlib.sha256(original).hexdigest()
    prepared = plan_edit(source, request(operation, source_sha256=digest))
    assert prepared.applicable is True
    source.write_bytes(original + b"% changed after planning\n")
    result = apply_edit_plan(prepared, output)

    assert result["applied"] is False
    assert "complete source changed" in result["stop_reasons"][0]["message"]
    assert not output.exists()


def test_overlapping_operations_are_rejected_as_one_atomic_batch(tmp_path: Path) -> None:
    source = tmp_path / "input.ai"
    write_fixture(source)
    first = {
        "op": "set_fill",
        "selector": selector("path", "logo"),
        "color": {"red": 0, "green": 1, "blue": 0},
    }
    second = {
        "op": "set_fill",
        "selector": selector("path", "logo"),
        "color": {"red": 0, "green": 0, "blue": 1},
    }

    plan = plan_edit(source, request(first, second))

    assert plan.applicable is False
    assert "operations conflict" in plan.report["stop_reasons"][0]["message"]


def test_unsupported_syntax_intersecting_target_stops_but_outside_is_a_warning(
    tmp_path: Path,
) -> None:
    source = tmp_path / "input.ai"
    operation = request(
        {
            "op": "translate",
            "selector": selector("path", "logo"),
            "dx": 1,
            "dy": 1,
        }
    )
    data = dumps_ai7(editable_document())
    source.write_bytes(data.replace(b"10 10 m\n", b"10 10 m\n12 FutureOperator\n", 1))

    intersecting = plan_edit(source, operation)

    assert intersecting.applicable is False
    assert "intersects unsupported source syntax" in (
        intersecting.report["stop_reasons"][0]["message"]
    )

    source.write_bytes(data.replace(b"%%EndSetup\n", b"%%EndSetup\n12 FutureOperator\n"))
    outside = plan_edit(source, operation)
    assert outside.applicable is True
    assert outside.report["compatibility"]["classification"] == "partially_parsed"
    assert outside.report["warnings"]


def test_apply_refuses_input_overwrite_and_existing_output(tmp_path: Path) -> None:
    source = tmp_path / "input.ai"
    existing = tmp_path / "existing.ai"
    original = write_fixture(source)
    existing.write_bytes(b"keep me")
    plan = plan_edit(
        source,
        request(
            {
                "op": "replace_text",
                "selector": selector("text", "headline"),
                "text": "Updated",
            }
        ),
    )

    overwrite_input = apply_edit_plan(plan, source)
    overwrite_existing = apply_edit_plan(plan, existing)

    assert overwrite_input["stop_reasons"][0]["code"] == "input-overwrite-refused"
    assert overwrite_existing["stop_reasons"][0]["code"] == "output-exists"
    assert source.read_bytes() == original
    assert existing.read_bytes() == b"keep me"


def test_request_schema_rejects_arbitrary_replacement_fields(tmp_path: Path) -> None:
    source = tmp_path / "input.ai"
    write_fixture(source)

    plan = plan_edit(
        source,
        {
            "schema_version": 1,
            "operations": [
                {
                    "op": "replace_text",
                    "selector": selector("text", "headline"),
                    "text": "Updated",
                    "replacement_bytes": "00ff",
                }
            ],
        },
    )

    assert plan.applicable is False
    assert plan.report["stop_reasons"][0]["code"] == "invalid-operation-request"
    assert "replacement_bytes" in plan.report["stop_reasons"][0]["message"]
