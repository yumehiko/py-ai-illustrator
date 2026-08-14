"""Bounded, dependency-free Illustrator container detection."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import StrEnum
from pathlib import Path

_SCAN_CHUNK = 4 * 1024 * 1024


class FileFormat(StrEnum):
    """Container types relevant to Illustrator artwork."""

    LEGACY_AI = "legacy-ai"
    PDF_COMPATIBLE_AI = "pdf-compatible-ai"
    PDF = "pdf"
    EPS = "eps"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class FormatReport:
    """Result of inspecting a file without fully parsing it."""

    path: str
    format: FileFormat
    size_bytes: int
    header: str | None
    pdf_version: str | None
    illustrator_markers: tuple[str, ...]
    confidence: str
    notes: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        data = asdict(self)
        data["format"] = self.format.value
        return data


def _read_probe(path: Path, size: int) -> bytes:
    """Read a bounded prefix and suffix, where PDF metadata normally lives."""

    with path.open("rb") as source:
        prefix = source.read(_SCAN_CHUNK)
        if size <= _SCAN_CHUNK:
            return prefix
        source.seek(max(0, size - _SCAN_CHUNK))
        suffix = source.read(_SCAN_CHUNK)
    return prefix + b"\n" + suffix


def inspect_file(path: str | Path) -> FormatReport:
    """Identify a probable AI/PDF/EPS container using content, not extension."""

    file_path = Path(path)
    size = file_path.stat().st_size
    probe = _read_probe(file_path, size)
    first_line = probe.splitlines()[0] if probe else b""
    header = first_line.decode("latin-1", errors="replace") or None

    marker_needles = {
        "AIPrivateData": b"AIPrivateData",
        "Illustrator PieceInfo": b"/PieceInfo",
        "Illustrator dictionary": b"/Illustrator",
        "Adobe Illustrator creator": b"Adobe Illustrator",
        "AI DSC comments": b"%AI",
    }
    markers = tuple(label for label, needle in marker_needles.items() if needle in probe)

    if first_line.startswith(b"%PDF-"):
        version = first_line[5:].split(maxsplit=1)[0].decode("ascii", errors="replace")
        ai_evidence = {
            "AIPrivateData",
            "Illustrator dictionary",
            "Adobe Illustrator creator",
        }.intersection(markers)
        if ai_evidence:
            return FormatReport(
                str(file_path),
                FileFormat.PDF_COMPATIBLE_AI,
                size,
                header,
                version,
                markers,
                "high" if "AIPrivateData" in markers else "medium",
            )
        return FormatReport(
            str(file_path),
            FileFormat.PDF,
            size,
            header,
            version,
            markers,
            "high",
            ("No Illustrator-specific marker was found in the bounded scan.",),
        )

    if first_line.startswith(b"%!PS-Adobe"):
        if "Adobe Illustrator creator" in markers or "AI DSC comments" in markers:
            return FormatReport(
                str(file_path),
                FileFormat.LEGACY_AI,
                size,
                header,
                None,
                markers,
                "high",
            )
        return FormatReport(str(file_path), FileFormat.EPS, size, header, None, markers, "medium")

    return FormatReport(
        str(file_path),
        FileFormat.UNKNOWN,
        size,
        header,
        None,
        markers,
        "low",
        ("The file does not begin with a PDF or PostScript signature.",),
    )
