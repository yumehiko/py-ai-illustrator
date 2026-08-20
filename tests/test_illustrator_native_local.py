import hashlib
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

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


def _replace_text_manifest(source_sha256: str) -> dict[str, object]:
    return {
        "schema_version": 1,
        "source_sha256": source_sha256,
        "operations": [
            {
                "op": "replace_text",
                "selector": {"type": "text", "id": "illustrator-dom-text-2"},
                "text": "空気清浄機が",
            }
        ],
    }


def _applicable_plan(
    source_sha256: str, *, replacement: Path | None = None
) -> dict[str, object]:
    operations: list[dict[str, object]] = []
    if replacement is not None:
        operations.append(
            {
                "index": 0,
                "request": {
                    "op": "replace_linked_image_source",
                    "selector": {
                        "type": "linked_image",
                        "id": "illustrator-dom-linked-image-0",
                    },
                    "source": str(replacement),
                },
                "resolved_target": {
                    "type": "linked_image",
                    "id": "illustrator-dom-linked-image-0",
                },
                "runtime_evidence": {"dom_index": 0},
                "requested_after": str(replacement),
                "replacement_asset": {
                    "path": str(replacement),
                    "sha256": hashlib.sha256(b"planned asset").hexdigest(),
                },
            }
        )
    return {
        "applicable": True,
        "source_sha256": source_sha256,
        "operations": operations,
        "stop_reasons": [],
    }


def _replace_image_manifest(source_sha256: str, replacement: Path) -> dict[str, object]:
    return {
        "schema_version": 1,
        "source_sha256": source_sha256,
        "operations": [
            {
                "op": "replace_linked_image_source",
                "selector": {
                    "type": "linked_image",
                    "id": "illustrator-dom-linked-image-0",
                },
                "source": str(replacement),
            }
        ],
    }


def test_native_local_apply_stops_when_source_changes_after_plan(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    planned_source = b"planned source bytes"
    source = tmp_path / "source.ai"
    source.write_bytes(planned_source)
    digest = hashlib.sha256(planned_source).hexdigest()
    executor_called = False

    def fake_plan(*args: object, **kwargs: object) -> dict[str, object]:
        source.write_bytes(b"changed after plan")
        return _applicable_plan(digest)

    def fake_executor(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        nonlocal executor_called
        executor_called = True
        raise AssertionError("stale source must stop before the apply executor")

    monkeypatch.setattr(native_local, "plan_illustrator_native_local", fake_plan)

    result = native_local.apply_illustrator_native_local(
        source,
        _replace_text_manifest(digest),
        tmp_path / "output.ai",
        executor=fake_executor,
    )

    assert result["applied"] is False
    assert result["stop_reasons"][0]["code"] == "stale-source"
    assert executor_called is False


@pytest.mark.parametrize("asset_state", ["missing", "mismatched"])
def test_native_local_apply_stops_on_stale_asset_before_executor(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, asset_state: str
) -> None:
    source_data = b"source"
    source = tmp_path / "source.ai"
    source.write_bytes(source_data)
    digest = hashlib.sha256(source_data).hexdigest()
    replacement = tmp_path / "replacement.png"
    if asset_state == "mismatched":
        replacement.write_bytes(b"changed asset")
    plan = _applicable_plan(digest, replacement=replacement)
    executor_called = False

    def fake_executor(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        nonlocal executor_called
        executor_called = True
        raise AssertionError("stale asset must stop before the apply executor")

    monkeypatch.setattr(
        native_local, "plan_illustrator_native_local", lambda *args, **kwargs: plan
    )

    result = native_local.apply_illustrator_native_local(
        source,
        _replace_image_manifest(digest, replacement),
        tmp_path / "output.ai",
        executor=fake_executor,
    )

    assert result["applied"] is False
    assert result["stop_reasons"][0]["code"] == "stale-replacement-asset"
    assert executor_called is False


def test_native_local_apply_does_not_publish_asset_changed_during_executor(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    source_data = b"verified source bytes"
    source = tmp_path / "source.ai"
    source.write_bytes(source_data)
    digest = hashlib.sha256(source_data).hexdigest()
    replacement = tmp_path / "replacement.png"
    replacement.write_bytes(b"planned asset")
    plan = _applicable_plan(digest, replacement=replacement)
    output = tmp_path / "output.ai"

    def fake_executor(
        script: str, directory: Path, **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        assert (directory / "input.ai").read_bytes() == source_data
        (directory / "candidate.ai").write_bytes(b"candidate")
        replacement.write_bytes(b"changed during executor")
        return subprocess.CompletedProcess([], 0, '{"ok": true}', "")

    monkeypatch.setattr(
        native_local, "plan_illustrator_native_local", lambda *args, **kwargs: plan
    )

    result = native_local.apply_illustrator_native_local(
        source,
        _replace_image_manifest(digest, replacement),
        output,
        executor=fake_executor,
    )

    assert result["applied"] is False
    assert result["stop_reasons"][0]["code"] == (
        "replacement-asset-changed-during-apply"
    )
    assert output.exists() is False
    assert output.with_name("output-visual-diff.png").exists() is False


def test_native_local_apply_rechecks_asset_immediately_before_publish(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    source_data = b"verified source bytes"
    source = tmp_path / "source.ai"
    source.write_bytes(source_data)
    digest = hashlib.sha256(source_data).hexdigest()
    replacement = tmp_path / "replacement.png"
    replacement.write_bytes(b"planned asset")
    plan = _applicable_plan(digest, replacement=replacement)
    output = tmp_path / "output.ai"

    def fake_executor(
        script: str, directory: Path, **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        (directory / "candidate.ai").write_bytes(b"candidate")
        return subprocess.CompletedProcess([], 0, '{"ok": true, "checks": {"ok": true}}', "")

    def mutate_during_visual_validation(
        *args: object, **kwargs: object
    ) -> tuple[bool, int, list[list[float]]]:
        replacement.write_bytes(b"changed before publish")
        return True, 0, [[0.0, 0.0, 1.0, 1.0]]

    monkeypatch.setattr(
        native_local, "plan_illustrator_native_local", lambda *args, **kwargs: plan
    )
    monkeypatch.setattr(
        native_local,
        "read_modern_ai",
        lambda *args, **kwargs: SimpleNamespace(
            private_data_status="extracted", container_status="parsed"
        ),
    )
    monkeypatch.setattr(
        native_local,
        "extract_pdf_display",
        lambda *args, **kwargs: SimpleNamespace(
            valid=True, private_data_freshness="timestamps_match"
        ),
    )
    monkeypatch.setattr(
        native_local,
        "visual_diff",
        lambda *args, **kwargs: SimpleNamespace(to_dict=lambda: {}),
    )
    monkeypatch.setattr(
        native_local,
        "inspect_file",
        lambda *args, **kwargs: SimpleNamespace(
            format=native_local.FileFormat.PDF_COMPATIBLE_AI
        ),
    )
    monkeypatch.setattr(
        native_local,
        "_visual_impacts_within_targets",
        mutate_during_visual_validation,
    )

    result = native_local.apply_illustrator_native_local(
        source,
        _replace_image_manifest(digest, replacement),
        output,
        executor=fake_executor,
    )

    assert result["applied"] is False
    assert result["stop_reasons"][0]["code"] == (
        "replacement-asset-changed-during-apply"
    )
    before_publish = result["replacement_asset_verification"]["before_publish"]
    assert before_publish[0]["matches"] is False
    assert output.exists() is False
    assert output.with_name("output-visual-diff.png").exists() is False


def test_native_local_publish_collision_preserves_competing_diff(tmp_path: Path) -> None:
    destination = tmp_path / "output.ai"
    difference = tmp_path / "output-visual-diff.png"
    difference.write_bytes(b"competing process")

    with pytest.raises(FileExistsError):
        native_local._publish_exclusive(destination, b"ours", difference, b"our diff")

    assert destination.exists() is False
    assert difference.read_bytes() == b"competing process"


def test_native_local_publish_collision_preserves_competing_destination(
    tmp_path: Path,
) -> None:
    destination = tmp_path / "output.ai"
    difference = tmp_path / "output-visual-diff.png"
    destination.write_bytes(b"competing process")

    with pytest.raises(FileExistsError):
        native_local._publish_exclusive(destination, b"ours", difference, b"our diff")

    assert destination.read_bytes() == b"competing process"
    assert difference.exists() is False
