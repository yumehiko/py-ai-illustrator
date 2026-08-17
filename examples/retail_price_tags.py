"""Build editable retail shelf tags from products and reusable display rules."""

from dataclasses import dataclass
from pathlib import Path

from py_ai_illustrator import (
    Color,
    Document,
    FontSpec,
    LayerBuilder,
    RenderedComponent,
    TextBlock,
    TextStyle,
    rectangle_path,
)
from py_ai_illustrator.legacy import dump_ai7

JAPANESE_FONT = FontSpec(
    postscript_name="KozGoPr6N-Regular",
    family="小塚ゴシック Pr6N",
    style="R",
    legacy_name="_KozGoPr6N-Regular-83pv-RKSJ-H",
)
INK = Color(0.08, 0.1, 0.14)
MUTED = Color(0.38, 0.4, 0.45)
PAPER = Color(0.97, 0.96, 0.93)
VARIANT_COLORS = {
    "standard": Color(0.12, 0.32, 0.54),
    "sale": Color(0.88, 0.18, 0.14),
    "low-stock": Color(0.88, 0.5, 0.08),
}
VARIANT_LABELS = {
    "standard": "おすすめ",
    "sale": "SALE",
    "low-stock": "残りわずか",
}


@dataclass(frozen=True, slots=True)
class Product:
    sku: str
    category: str
    name: str
    price: int
    variant: str = "standard"
    original_price: int | None = None


@dataclass(frozen=True, slots=True)
class PriceTag:
    """A semantic product component that remains one movable Illustrator group."""

    id: str
    product: Product
    width: float = 174
    height: float = 150

    def _price_component(self, *, x: float, top: float) -> RenderedComponent:
        accent = VARIANT_COLORS[self.product.variant]
        builder = LayerBuilder(id=f"{self.id}.price-content", name="Price content")
        builder.add_path(
            rectangle_path(
                f"{self.id}.price-background",
                x=x,
                top=top,
                width=self.width - 20,
                height=48,
                fill=Color(0.96, 0.97, 0.98),
                name="Price background",
            )
        )
        builder.add(
            TextBlock(
                id=f"{self.id}.variant",
                name="Sales status",
                text=VARIANT_LABELS[self.product.variant],
                width=54,
                alignment="left",
                wrap=False,
                style=TextStyle(
                    font_size=8,
                    font=JAPANESE_FONT,
                    fill=accent,
                ),
            ).render(x=x + 7, top=top - 8)
        )
        if self.product.original_price is not None:
            builder.add(
                TextBlock(
                    id=f"{self.id}.original-price",
                    name="Original price",
                    text=f"通常 {self.product.original_price:,}円",
                    width=82,
                    alignment="right",
                    wrap=False,
                    style=TextStyle(
                        font_size=7,
                        font=JAPANESE_FONT,
                        fill=MUTED,
                    ),
                ).render(x=x + 65, top=top - 6)
            )
        builder.add(
            TextBlock(
                id=f"{self.id}.price",
                name="Current price",
                text=f"{self.product.price:,}円",
                width=140,
                alignment="right",
                wrap=False,
                style=TextStyle(
                    font_size=22,
                    font=JAPANESE_FONT,
                    fill=accent,
                ),
            ).render(x=x + 7, top=top - 18)
        )
        builder.add(
            TextBlock(
                id=f"{self.id}.tax",
                name="Tax note",
                text="税込",
                width=140,
                alignment="right",
                wrap=False,
                style=TextStyle(
                    font_size=6,
                    font=JAPANESE_FONT,
                    fill=MUTED,
                ),
            ).render(x=x + 7, top=top - 39)
        )
        layer = builder.build()
        return RenderedComponent(
            width=self.width - 20,
            height=48,
            paths=layer.paths,
            text_frames=layer.text_frames,
            item_order=layer.item_order,
        )

    def render(self, *, x: float, top: float) -> RenderedComponent:
        accent = VARIANT_COLORS[self.product.variant]
        builder = LayerBuilder(id=f"{self.id}.content", name=self.product.name)
        builder.add_path(
            rectangle_path(
                f"{self.id}.background",
                x=x,
                top=top,
                width=self.width,
                height=self.height,
                fill=Color(1, 1, 1),
                stroke=Color(0.72, 0.72, 0.7),
                stroke_width=0.7,
                name=f"Shelf tag: {self.product.name}",
            )
        )
        builder.add_path(
            rectangle_path(
                f"{self.id}.accent",
                x=x,
                top=top,
                width=self.width,
                height=7,
                fill=accent,
                name="Variant accent",
            )
        )
        builder.add(
            TextBlock(
                id=f"{self.id}.category",
                name="Product category",
                text=self.product.category,
                width=self.width - 24,
                alignment="left",
                wrap=False,
                style=TextStyle(
                    font_size=8,
                    font=JAPANESE_FONT,
                    fill=MUTED,
                ),
            ).render(x=x + 12, top=top - 16)
        )
        builder.add(
            TextBlock(
                id=f"{self.id}.name",
                name="Product name",
                text=self.product.name,
                width=self.width - 24,
                alignment="left",
                wrap=True,
                style=TextStyle(
                    font_size=12,
                    font=JAPANESE_FONT,
                    fill=INK,
                    line_height_ratio=1.25,
                ),
            ).render(x=x + 12, top=top - 35)
        )
        builder.add(
            TextBlock(
                id=f"{self.id}.sku",
                name="SKU",
                text=f"SKU {self.product.sku}",
                width=self.width - 24,
                alignment="right",
                wrap=False,
                style=TextStyle(font_size=7, fill=MUTED),
            ).render(x=x + 12, top=top - 83)
        )
        builder.add_grouped(
            self._price_component(x=x + 10, top=top - 94),
            group_id=f"{self.id}.price-group",
            group_name=f"Price: {self.product.name}",
        )
        layer = builder.build()
        return RenderedComponent(
            width=self.width,
            height=self.height,
            paths=layer.paths,
            text_frames=layer.text_frames,
            groups=layer.groups,
            item_order=layer.item_order,
        )


def build_document() -> Document:
    products = [
        Product("DRK-1042", "飲料", "水出しアイスコーヒー 無糖", 298),
        Product("SNK-2088", "菓子", "瀬戸内レモンのバターサブレ", 348, "sale", 398),
        Product("KIT-0315", "台所用品", "竹繊維のキッチンクロス 3枚組", 680),
        Product("TEA-1120", "飲料", "国産ほうじ茶 ティーバッグ", 458, "low-stock"),
        Product("BTH-4401", "生活雑貨", "植物由来のハンドソープ", 798, "sale", 980),
        Product("STA-0714", "文具", "方眼ノート A5 再生紙", 320),
    ]
    builder = LayerBuilder(id="price-tag-sheet", name="Retail price tags")
    builder.add_path(
        rectangle_path(
            "sheet.background",
            x=0,
            top=410,
            width=612,
            height=410,
            fill=PAPER,
            name="Sheet background",
        )
    )
    builder.add(
        TextBlock(
            id="sheet.title",
            name="Sheet title",
            text="店頭棚札 / 2026.08",
            width=552,
            alignment="right",
            wrap=False,
            style=TextStyle(font_size=10, font=JAPANESE_FONT, fill=MUTED),
        ).render(x=30, top=392)
    )
    positions = [
        (30, 360),
        (219, 360),
        (408, 360),
        (30, 190),
        (219, 190),
        (408, 190),
    ]
    for index, (product, (x, top)) in enumerate(
        zip(products, positions, strict=True),
        start=1,
    ):
        tag = PriceTag(id=f"tag-{index}", product=product)
        builder.add_grouped(
            tag.render(x=x, top=top),
            group_id=f"tag-{index}.group",
            group_name=f"Shelf tag: {product.name}",
        )
    return Document(
        width=612,
        height=410,
        title="Semantic retail shelf tags",
        metadata={
            "source": "examples/retail_price_tags.py",
            "component": "PriceTag",
            "encoding": "cp932",
            "business_case": "retail-shelf-labels",
        },
        layers=[builder.build()],
    )


if __name__ == "__main__":
    dump_ai7(build_document(), Path(__file__).with_name("retail-price-tags.ai"))
