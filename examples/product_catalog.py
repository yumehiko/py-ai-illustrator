"""Generate a product card using linked raster, point/area text, and vector shapes."""

from __future__ import annotations

import struct
import zlib
from pathlib import Path

from py_ai_illustrator.authoring import (
    AreaTextBlock,
    LayerBuilder,
    TextBlock,
    TextStyle,
    ellipse_path,
    rectangle_path,
)
from py_ai_illustrator.legacy import dump_ai7
from py_ai_illustrator.model import Color, Document, LinkedImage

ROOT = Path(__file__).parent
LINK = ROOT / "Links" / "product-swatch.png"


def ensure_sample_png(path: Path, *, width: int = 320, height: int = 220) -> None:
    """Create a deterministic original raster fixture without external dependencies."""

    if path.is_file():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = bytearray()
    for y in range(height):
        rows.append(0)
        for x in range(width):
            distance = ((x - width * 0.52) ** 2 + (y - height * 0.48) ** 2) ** 0.5
            glow = max(0.0, 1.0 - distance / (width * 0.62))
            rows.extend(
                (
                    int(32 + 150 * glow),
                    int(55 + 105 * glow + 28 * x / width),
                    int(82 + 130 * (1 - y / height)),
                )
            )

    def chunk(kind: bytes, payload: bytes) -> bytes:
        body = kind + payload
        return struct.pack(">I", len(payload)) + body + struct.pack(">I", zlib.crc32(body))

    png = b"\x89PNG\r\n\x1a\n"
    png += chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(bytes(rows), level=9))
    png += chunk(b"IEND", b"")
    path.write_bytes(png)


def build_document() -> Document:
    ensure_sample_png(LINK)
    builder = LayerBuilder(id="catalog", name="Product card")
    builder.add_path(
        rectangle_path(
            "card.background",
            name="Card background",
            x=24,
            top=396,
            width=672,
            height=372,
            fill=Color(0.96, 0.97, 0.99),
        )
    )
    builder.add_image(
        LinkedImage(
            id="product.photo",
            name="Linked product image",
            source=str(LINK),
            x=48,
            y=348,
            width=300,
            height=252,
        )
    )
    builder.add_path(
        ellipse_path(
            "product.badge",
            name="New badge",
            center_x=322,
            center_y=320,
            radius_x=34,
            radius_y=34,
            fill=Color(0.98, 0.37, 0.22),
        )
    )
    builder.add(
        TextBlock(
            id="product.badge-label",
            name="Badge label",
            text="NEW",
            width=56,
            alignment="center",
            wrap=False,
            style=TextStyle(font_size=12, font_name="Helvetica-Bold", fill=Color(1, 1, 1)),
        ).render(x=294, top=326)
    )
    builder.add(
        TextBlock(
            id="product.eyebrow",
            name="Category",
            text="STUDIO ESSENTIALS",
            width=280,
            wrap=False,
            style=TextStyle(
                font_size=10,
                font_name="Helvetica-Bold",
                tracking=140,
                fill=Color(0.23, 0.39, 0.75),
            ),
        ).render(x=384, top=348)
    )
    builder.add(
        TextBlock(
            id="product.title",
            name="Product title",
            text="Focus Lamp 02",
            width=280,
            wrap=False,
            style=TextStyle(font_size=30, font_name="Helvetica-Bold", fill=Color(0.08, 0.1, 0.16)),
        ).render(x=384, top=310)
    )
    builder.add(
        AreaTextBlock(
            id="product.description",
            name="Product description",
            text=(
                "A compact task light designed for focused work. The linked image can be "
                "replaced independently, while this paragraph remains a reflowable area text frame."
            ),
            width=264,
            height=104,
            style=TextStyle(font_size=11, font_name="Helvetica", line_height_ratio=1.45),
        ).render(x=384, top=260)
    )
    builder.add_path(
        rectangle_path(
            "product.cta",
            name="CTA background",
            x=384,
            top=120,
            width=142,
            height=38,
            fill=Color(0.1, 0.18, 0.34),
        )
    )
    builder.add(
        TextBlock(
            id="product.cta-label",
            name="CTA label",
            text="VIEW DETAILS",
            width=118,
            alignment="center",
            wrap=False,
            style=TextStyle(font_size=10, font_name="Helvetica-Bold", fill=Color(1, 1, 1)),
        ).render(x=396, top=104)
    )
    return Document(
        width=720,
        height=420,
        title="Linked Image Product Catalog",
        layers=[builder.build()],
        metadata={
            "source": "examples/product_catalog.py",
            "business_case": "linked-image-product-card",
            "asset_policy": "portable-links-directory",
        },
    )


if __name__ == "__main__":
    dump_ai7(build_document(), ROOT / "product-catalog.ai")
