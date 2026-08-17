"""Generate the redistributable bounded-reader modern AI fixture.

The PDF and Illustrator-shaped PrivateData are authored entirely in this
repository.  No Adobe or third-party sample bytes are embedded.
"""

from __future__ import annotations

import argparse
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "tests/fixtures/generated/modern-private-data.ai"

SEGMENT_1 = (
    b"%!PS-Adobe-3.0\n"
    b"%%Creator: py-ai-illustrator fixture generator\n"
    b"%AI5_BeginLayer\n"
    b"1 0 0 0 Lb\n"
    b"(Fixture Layer) Ln\n"
    b"opaque-operator-kept 99 ZZ\n"
    b"%AI5_EndLayer--\n"
    b"%%EOF\n"
)
SEGMENT_2 = (
    b"%AI5_Begin_NonPrinting\r\n"
    b"opaque\x00binary\xffpayload\r\n"
    b"%AI5_End_NonPrinting--\r\n"
)


def _stream(dictionary: bytes, payload: bytes) -> bytes:
    return dictionary + b"\nstream\n" + payload + b"\nendstream"


def build_fixture() -> bytes:
    """Return a deterministic, classic-xref PDF-compatible AI fixture."""

    compressed_hex = zlib.compress(SEGMENT_2, level=9).hex().upper().encode("ascii") + b">"
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 100 100] "
            b"/PieceInfo << /Illustrator 4 0 R >> >>"
        ),
        b"<< /LastModified (D:20260817000000Z) /Private 5 0 R >>",
        (
            b"<< /AIPrivateData2 7 0 R /ContainerVersion 12 "
            b"/AIPrivateData1 6 0 R /CreatorVersion 30 >>"
        ),
        _stream(f"<< /Length {len(SEGMENT_1)} >>".encode("ascii"), SEGMENT_1),
        _stream(
            (
                f"<< /Length {len(compressed_hex)} "
                "/Filter [/ASCIIHexDecode /FlateDecode] >>"
            ).encode("ascii"),
            compressed_hex,
        ),
    ]

    output = bytearray(b"%PDF-1.7\n% py-ai-illustrator generated fixture\n")
    offsets = [0]
    for number, value in enumerate(objects, start=1):
        offsets.append(len(output))
        output.extend(f"{number} 0 obj\n".encode("ascii"))
        output.extend(value)
        output.extend(b"\nendobj\n")
    xref = len(output)
    output.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    output.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        output.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    output.extend(
        (
            f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref}\n%%EOF\n"
        ).encode("ascii")
    )
    return bytes(output)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("-o", "--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    expected = build_fixture()
    if args.check:
        return 0 if args.output.read_bytes() == expected else 1
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(expected)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
