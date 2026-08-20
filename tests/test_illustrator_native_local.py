import json
from pathlib import Path

from py_ai_illustrator import _illustrator_native_local as native_local
from py_ai_illustrator._illustrator_scripts import (
    build_native_local_apply_javascript,
    build_native_local_inspection_javascript,
)
from py_ai_illustrator.cli import build_parser

FIXTURE = Path(__file__).parent / "fixtures" / "modern-native-local-banner.json"


def captured_fixture() -> dict[str, object]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_real_banner_dom_capture_exposes_unique_product_text_and_linked_image() -> None:
    fixture = captured_fixture()
    provenance = fixture["provenance"]
    snapshot = fixture["snapshot"]
    assert isinstance(provenance, dict) and isinstance(snapshot, dict)

    selectors = native_local._selectors_from_snapshot(
        snapshot,
        source_hints=[str(provenance["linked_source_hint"])],
    )

    product = [item for item in selectors if item.get("before") == "オーブントースターが"]
    image = [item for item in selectors if item.get("type") == "linked_image"]
    assert len(product) == 1
    assert product[0]["selector"] == {"type": "text", "id": "illustrator-dom-text-2"}
    assert product[0]["operations"] == ["replace_text"]
    assert len(image) == 1
    assert image[0]["selector"] == {
        "type": "linked_image",
        "id": "illustrator-dom-linked-image-0",
    }
    assert image[0]["before"] == "/Users/yumehiko/Desktop/oven.png"
    assert image[0]["operations"] == ["replace_linked_image_source"]
    assert provenance["source_sha256"] == (
        "57f4c266077eeb74960475e7f4f607599a4650e2e18fdee894887381ee2bf5c4"
    )


def test_native_local_plan_resolves_both_real_targets_atomically(
    monkeypatch, tmp_path: Path
) -> None:
    fixture = captured_fixture()
    provenance = fixture["provenance"]
    snapshot = fixture["snapshot"]
    assert isinstance(provenance, dict) and isinstance(snapshot, dict)
    selectors = native_local._selectors_from_snapshot(
        snapshot,
        source_hints=[str(provenance["linked_source_hint"])],
    )
    source = tmp_path / "banner-test-oven.ai"
    source.write_bytes(b"test source is replaced by captured runtime evidence")
    replacement = tmp_path / "aircl.png"
    replacement.write_bytes(
        b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\x0dIHDR" + b"\x00\x00\x03\x00\x00\x00\x03\x00"
    )
    monkeypatch.setattr(
        native_local,
        "inspect_illustrator_native_local",
        lambda *args, **kwargs: {
            "status": "passed",
            "profile": native_local.PROFILE_ID,
            "source_sha256": provenance["source_sha256"],
            "selectors": selectors,
        },
    )
    manifest = {
        "schema_version": 1,
        "source_sha256": provenance["source_sha256"],
        "operations": [
            {
                "op": "replace_linked_image_source",
                "selector": {
                    "type": "linked_image",
                    "id": "illustrator-dom-linked-image-0",
                },
                "source": str(replacement),
            },
            {
                "op": "replace_text",
                "selector": {"type": "text", "id": "illustrator-dom-text-2"},
                "text": "空気清浄機が",
            },
        ],
    }

    plan = native_local.plan_illustrator_native_local(source, manifest)

    assert plan["applicable"] is True
    assert plan["stop_reasons"] == []
    assert len(plan["operations"]) == 2
    assert plan["feature_profile"] == {
        "id": "illustrator-native-local-edit-v1",
        "licensed_runtime_required": True,
    }
    assert plan["atomic_policy"] == ("copy-open-edit-save-as-reopen-publish-after-all-checks")


def test_native_local_javascript_owns_copy_and_reopens_saved_candidate(tmp_path: Path) -> None:
    source = tmp_path / 'source "quoted".ai'
    destination = tmp_path / 'candidate "quoted".ai'
    inspection = build_native_local_inspection_javascript(source)
    apply = build_native_local_apply_javascript(
        source,
        destination,
        {"operations": []},
    )

    assert "documentRef = app.open(source)" in inspection
    assert "saveAs" not in inspection
    assert "documentRef.close(SaveOptions.DONOTSAVECHANGES)" in inspection
    assert "documentRef.saveAs(destination, options)" in apply
    assert "documentRef = app.open(destination)" in apply
    assert "options.pdfCompatible = true" in apply
    assert "options.embedLinkedFiles = false" in apply
    assert "placed.relink(replacement)" in apply
    assert "frame.contents = operation.after" in apply
    assert "nonTargetsEqual" in apply
    assert "pathSnapshot" in apply
    assert "before.paths[index]" in apply
    assert "targetsMatch(afterReopen" in apply
    assert "JSON.parse" not in apply
    assert str(tmp_path) not in inspection
    assert str(tmp_path) not in apply


def test_native_local_cli_commands_are_explicit_runtime_routes() -> None:
    parser = build_parser()

    assert parser.parse_args(["inspect-native-local", "input.ai"]).command == (
        "inspect-native-local"
    )
    assert (
        parser.parse_args(["plan-native-local", "input.ai", "operations.json"]).command
        == "plan-native-local"
    )
    assert (
        parser.parse_args(
            ["apply-native-local", "input.ai", "operations.json", "-o", "output.ai"]
        ).command
        == "apply-native-local"
    )
