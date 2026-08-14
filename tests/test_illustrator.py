from pathlib import Path
from subprocess import CompletedProcess, TimeoutExpired

from py_ai_illustrator import illustrator
from py_ai_illustrator.illustrator import (
    _build_export_javascript,
    _build_javascript,
    _compare_structure,
    _expected_structure,
)


def test_javascript_closes_only_its_document_without_saving(tmp_path: Path) -> None:
    source = tmp_path / 'fixture "quoted".ai'
    javascript = _build_javascript(source)
    assert "documentRef = app.open(source)" in javascript
    assert "documentRef.close(SaveOptions.DONOTSAVECHANGES)" in javascript
    assert "current document" not in javascript
    assert "String.fromCharCode(92)" in javascript
    assert "String.fromCharCode(34)" in javascript
    assert "\\" not in javascript


def test_export_javascript_creates_and_closes_only_its_cmyk_fixture(tmp_path: Path) -> None:
    javascript = _build_export_javascript(tmp_path / 'native "curve".ai', "cmyk-curve")
    assert "app.documents.add(DocumentColorSpace.CMYK, 200, 200)" in javascript
    assert "Compatibility.ILLUSTRATOR8" in javascript
    assert "documentRef.saveAs(destination, options)" in javascript
    assert "documentRef.close(SaveOptions.DONOTSAVECHANGES)" in javascript
    assert "current document" not in javascript
    assert "\\" not in javascript


def test_expected_structure_comes_from_legacy_reader() -> None:
    source = Path(__file__).parents[1] / "examples" / "rectangle.ai"
    expected = _expected_structure(source)
    assert expected == {
        "layer_count": 1,
        "layer_names": ["Artwork"],
        "path_item_count": 1,
        "point_counts": [4],
        "closed_count": 1,
        "filled_count": 1,
        "stroked_count": 1,
    }


def test_structure_comparison_reports_individual_mismatches() -> None:
    expected = {
        "layer_count": 1,
        "layer_names": ["Artwork"],
        "path_item_count": 1,
        "point_counts": [4],
        "closed_count": 1,
        "filled_count": 1,
        "stroked_count": 1,
    }
    actual = dict(expected, point_counts=[3])
    checks = _compare_structure(expected, actual)
    assert checks["layer_count"] is True
    assert checks["point_counts"] is False


def test_runner_reports_a_successful_illustrator_import(monkeypatch) -> None:
    source = Path(__file__).parents[1] / "examples" / "rectangle.ai"
    actual = {
        "ok": True,
        "illustrator_version": "30.7.0",
        "layer_count": 1,
        "layer_names": ["Artwork"],
        "path_item_count": 1,
        "point_counts": [4],
        "closed_count": 1,
        "filled_count": 1,
        "stroked_count": 1,
    }

    def fake_run(command, **kwargs):
        assert command[0] == "osascript"
        assert "do javascript scriptFile" in command[2]
        assert kwargs["timeout"] == 95
        return CompletedProcess(command, 0, stdout=illustrator.json.dumps(actual), stderr="")

    monkeypatch.setattr(illustrator.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(illustrator.subprocess, "run", fake_run)
    result = illustrator.run_illustrator_test(source)
    assert result["status"] == "passed"
    assert all(result["checks"].values())


def test_runner_distinguishes_an_unready_environment(monkeypatch) -> None:
    source = Path(__file__).parents[1] / "examples" / "rectangle.ai"

    def fake_timeout(command, **kwargs):
        raise TimeoutExpired(command, kwargs["timeout"])

    monkeypatch.setattr(illustrator.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(illustrator.subprocess, "run", fake_timeout)
    result = illustrator.run_illustrator_test(source, timeout=5)
    assert result["status"] == "environment-unavailable"
    assert "sign in" in result["next_action"]


def test_export_runner_refuses_to_overwrite_existing_output(
    monkeypatch, tmp_path: Path
) -> None:
    output = tmp_path / "existing.ai"
    output.write_bytes(b"user data")
    monkeypatch.setattr(illustrator.platform, "system", lambda: "Darwin")
    result = illustrator.run_illustrator_export_test(output=output)
    assert result["status"] == "invalid-input"
    assert output.read_bytes() == b"user data"


def test_export_runner_rejects_unknown_fixture(monkeypatch) -> None:
    monkeypatch.setattr(illustrator.platform, "system", lambda: "Darwin")
    result = illustrator.run_illustrator_export_test(fixture="unknown")
    assert result == {"status": "invalid-input", "error": "Unknown fixture: unknown"}
