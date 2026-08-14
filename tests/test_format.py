from pathlib import Path

from py_ai_illustrator.format import FileFormat, inspect_file


def test_detects_legacy_ai(tmp_path: Path) -> None:
    source = tmp_path / "legacy.ai"
    source.write_bytes(b"%!PS-Adobe-3.0\n%%Creator: Adobe Illustrator 7.0\n%AI5_FileFormat 3.0\n")
    assert inspect_file(source).format is FileFormat.LEGACY_AI


def test_detects_pdf_compatible_ai(tmp_path: Path) -> None:
    source = tmp_path / "modern.ai"
    source.write_bytes(
        b"%PDF-1.7\n1 0 obj <</PieceInfo <</Illustrator <</Private /AIPrivateData>>>>>>\n"
    )
    report = inspect_file(source)
    assert report.format is FileFormat.PDF_COMPATIBLE_AI
    assert report.pdf_version == "1.7"


def test_does_not_trust_ai_extension(tmp_path: Path) -> None:
    source = tmp_path / "not-really.ai"
    source.write_bytes(b"hello")
    assert inspect_file(source).format is FileFormat.UNKNOWN
