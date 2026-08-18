from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from py_ai_illustrator import (
    extract_pdf_display,
    render_legacy_preview,
    render_pdf_preview,
    render_preview,
    visual_diff,
)
from py_ai_illustrator.cli import main
from py_ai_illustrator.verification import _encode_png_rgba, _read_png_rgba

ROOT = Path(__file__).resolve().parents[1]
MODERN_FIXTURE = ROOT / "examples/styled-table.native.ai"
SECOND_MODERN_FIXTURE = ROOT / "examples/cmyk-curve.native.ai"
LEGACY_FIXTURE = ROOT / "examples/rectangle.ai"
SECOND_LEGACY_FIXTURE = ROOT / "examples/cmyk-curve.ai"
HAS_PDFTOCAIRO = shutil.which("pdftocairo") is not None


def _pdf(*objects: bytes) -> bytes:
    output = bytearray(b"%PDF-1.7\n")
    for number, value in enumerate(objects, start=1):
        output.extend(f"{number} 0 obj\n".encode())
        output.extend(value)
        output.extend(b"\nendobj\n")
    output.extend(b"%%EOF\n")
    return bytes(output)


def _display_pdf(content: bytes = b"0 0 m 10 10 l S") -> bytes:
    return _pdf(
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Count 1 /Kids [3 0 R] /MediaBox [0 0 100 80] "
        b"/Resources << >> >>",
        b"<< /Type /Page /Parent 2 0 R /Contents 4 0 R "
        b"/LastModified (same) /PieceInfo << /Illustrator 5 0 R >> >>",
        b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"\nendstream",
        b"<< /LastModified (same) /Private 6 0 R >>",
        b"<< >>",
    )


def test_pdf_display_extracts_page_tree_content_resources_and_consistency() -> None:
    result = extract_pdf_display(_display_pdf())

    assert result.valid is True
    assert result.status == "extracted"
    assert result.private_data_freshness == "timestamps_match"
    assert len(result.pages) == 1
    page = result.pages[0]
    assert page.object_ref == "3 0 R"
    assert page.media_box == (0.0, 0.0, 100.0, 80.0)
    assert page.crop_box == page.media_box
    assert page.content_refs == ("4 0 R",)
    assert page.page_last_modified == "same"
    assert page.illustrator_last_modified == "same"
    assert page.content_sha256
    assert page.resources_sha256
    assert page.display_sha256


def test_pdf_display_fingerprint_is_deterministic_and_changes_with_visible_content() -> None:
    first = extract_pdf_display(_display_pdf())
    second = extract_pdf_display(_display_pdf())
    changed = extract_pdf_display(_display_pdf(b"0 0 m 20 20 l S"))

    assert first.to_dict() == second.to_dict()
    assert first.display_sha256 != changed.display_sha256
    assert first.pages[0].content_sha256 != changed.pages[0].content_sha256


def test_pdf_and_private_data_timestamp_mismatch_is_explicit() -> None:
    source = _display_pdf().replace(b"/LastModified (same)", b"/LastModified (page)", 1)

    result = extract_pdf_display(source)

    assert result.valid is True
    assert result.private_data_freshness == "timestamp_mismatch"
    assert result.pages[0].private_data_freshness == "timestamp_mismatch"
    assert "pdf_private_data_last_modified_mismatch" in {
        item.code for item in result.diagnostics
    }


def test_normalized_png_round_trip_is_byte_deterministic() -> None:
    pixels = bytes(
        (
            255,
            0,
            0,
            255,
            0,
            255,
            0,
            128,
            0,
            0,
            255,
            255,
            255,
            255,
            255,
            255,
        )
    )
    encoded = _encode_png_rgba(2, 2, pixels)

    assert _read_png_rgba(encoded) == (2, 2, pixels)
    assert _encode_png_rgba(*_read_png_rgba(encoded)) == encoded


def test_legacy_reference_preview_is_deterministic_without_external_renderer(
    tmp_path: Path,
) -> None:
    first_path = tmp_path / "legacy-first.png"
    second_path = tmp_path / "legacy-second.png"

    first = render_legacy_preview(LEGACY_FIXTURE, first_path, dpi=72)
    second = render_preview(LEGACY_FIXTURE, second_path, dpi=72)

    assert first.renderer == "py-ai-legacy-ir"
    assert first.pages[0].pixel_sha256 == second.pages[0].pixel_sha256
    assert first_path.read_bytes() == second_path.read_bytes()


def test_legacy_reference_visual_diff_detects_changes(tmp_path: Path) -> None:
    result = visual_diff(
        LEGACY_FIXTURE,
        SECOND_LEGACY_FIXTURE,
        tmp_path / "legacy-difference.png",
        dpi=72,
    )

    assert result.equal is False
    assert result.pages[0].changed_pixels > 0
    assert result.pages[0].changed_bounds is not None


def test_cli_preview_dispatches_to_legacy_reference_renderer(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    preview = tmp_path / "legacy-preview.png"

    assert main(["preview", str(LEGACY_FIXTURE), "-o", str(preview), "--dpi", "72"]) == 0
    report = json.loads(capsys.readouterr().out)

    assert report["renderer"] == "py-ai-legacy-ir"
    assert report["page_count"] == 1
    assert preview.exists()


@pytest.mark.skipif(not HAS_PDFTOCAIRO, reason="Poppler pdftocairo is not installed")
def test_preview_is_deterministic_and_refuses_implicit_overwrite(tmp_path: Path) -> None:
    first_path = tmp_path / "first.png"
    second_path = tmp_path / "second.png"

    first = render_pdf_preview(MODERN_FIXTURE, first_path, dpi=72)
    second = render_pdf_preview(MODERN_FIXTURE, second_path, dpi=72)

    assert first.pages[0].pixel_sha256 == second.pages[0].pixel_sha256
    assert first.pages[0].png_sha256 == second.pages[0].png_sha256
    assert first_path.read_bytes() == second_path.read_bytes()
    with pytest.raises(FileExistsError, match="refusing to overwrite"):
        render_pdf_preview(MODERN_FIXTURE, first_path, dpi=72)


@pytest.mark.skipif(not HAS_PDFTOCAIRO, reason="Poppler pdftocairo is not installed")
def test_visual_diff_reports_equal_and_changed_pages(tmp_path: Path) -> None:
    equal = visual_diff(
        MODERN_FIXTURE,
        MODERN_FIXTURE,
        tmp_path / "equal.png",
        dpi=72,
    )
    changed = visual_diff(
        MODERN_FIXTURE,
        SECOND_MODERN_FIXTURE,
        tmp_path / "changed.png",
        dpi=72,
    )

    assert equal.equal is True
    assert equal.pages[0].changed_pixels == 0
    assert changed.equal is False
    assert changed.pages[0].changed_pixels > 0
    assert changed.pages[0].changed_bounds is not None


@pytest.mark.skipif(not HAS_PDFTOCAIRO, reason="Poppler pdftocairo is not installed")
def test_cli_preview_and_visual_diff_complete_verification_slice(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    preview = tmp_path / "preview.png"
    difference = tmp_path / "difference.png"

    assert main(["preview", str(MODERN_FIXTURE), "-o", str(preview), "--dpi", "72"]) == 0
    preview_report = json.loads(capsys.readouterr().out)
    assert preview_report["profile"] == "deterministic-raster-preview-v1"
    assert preview_report["page_count"] == 1
    assert preview.exists()

    assert (
        main(
            [
                "diff",
                str(MODERN_FIXTURE),
                str(SECOND_MODERN_FIXTURE),
                "--visual",
                "-o",
                str(difference),
                "--dpi",
                "72",
            ]
        )
        == 0
    )
    diff_report = json.loads(capsys.readouterr().out)
    assert diff_report["profile"] == "pixel-visual-diff-v1"
    assert diff_report["equal"] is False
    assert difference.exists()
