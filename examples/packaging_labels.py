"""Build editable packaging labels with rotated text and transformed badges."""

from dataclasses import dataclass
from pathlib import Path

from py_ai_illustrator import (
    AffineTransform,
    Color,
    Document,
    LayerBuilder,
    RenderedComponent,
    TextBlock,
    TextStyle,
    ellipse_path,
    rectangle_path,
)
from py_ai_illustrator.legacy import dump_ai7

INK = Color(0.06, 0.08, 0.09)
PAPER = Color(0.97, 0.95, 0.89)
WHITE = Color(1.0, 1.0, 1.0)


@dataclass(frozen=True, slots=True)
class Product:
    sku: str
    category: str
    name: str
    note: str
    weight: str
    accent: Color
    badge: str | None = None


@dataclass(frozen=True, slots=True)
class PackagingLabel:
    id: str
    product: Product
    width: float = 180
    height: float = 300

    def render(self, *, x: float, top: float) -> RenderedComponent:
        builder = LayerBuilder(id=self.id, name=self.product.name)
        builder.add_path(
            rectangle_path(
                f"{self.id}.background",
                x=x,
                top=top,
                width=self.width,
                height=self.height,
                fill=PAPER,
                stroke=INK,
                stroke_width=0.8,
                name="Label substrate",
            )
        )
        builder.add_path(
            rectangle_path(
                f"{self.id}.accent",
                x=x,
                top=top,
                width=self.width,
                height=18,
                fill=self.product.accent,
                name="Flavor accent",
            )
        )
        builder.add_path(
            ellipse_path(
                f"{self.id}.mark",
                center_x=x + self.width / 2,
                center_y=top - 98,
                radius_x=48,
                radius_y=48,
                fill=self.product.accent,
                name="Product mark",
            )
        )
        builder.add_path(
            rectangle_path(
                f"{self.id}.divider",
                x=x + 28,
                top=top - 196,
                width=self.width - 56,
                height=1,
                fill=INK,
                name="Information divider",
            )
        )
        builder.add(
            TextBlock(
                id=f"{self.id}.category",
                text=self.product.category.upper(),
                width=self.width - 36,
                alignment="center",
                wrap=False,
                style=TextStyle(font_size=8, tracking=180, fill=INK),
            ).render(x=x + 18, top=top - 35)
        )
        builder.add(
            TextBlock(
                id=f"{self.id}.name",
                text=self.product.name,
                width=self.width - 24,
                alignment="center",
                wrap=False,
                style=TextStyle(font_size=20, font_name="Helvetica-Bold", fill=INK),
            ).render(x=x + 12, top=top - 164)
        )
        builder.add(
            TextBlock(
                id=f"{self.id}.note",
                text=self.product.note,
                width=self.width - 48,
                alignment="center",
                wrap=True,
                style=TextStyle(font_size=9, line_height_ratio=1.4, fill=INK),
            ).render(x=x + 24, top=top - 214)
        )
        builder.add(
            TextBlock(
                id=f"{self.id}.weight",
                text=self.product.weight,
                width=self.width - 36,
                alignment="right",
                wrap=False,
                style=TextStyle(font_size=9, font_name="Helvetica-Bold", fill=INK),
            ).render(x=x + 18, top=top - 278)
        )
        builder.add(
            TextBlock(
                id=f"{self.id}.side-code",
                text=f"{self.product.sku} / SMALL BATCH",
                width=180,
                alignment="left",
                wrap=False,
                style=TextStyle(font_size=7, tracking=100, rotation=90, fill=INK),
            ).render(x=x + 10, top=top - 205)
        )

        if self.product.badge is not None:
            badge_x = x + 105
            badge_top = top - 62
            badge_builder = LayerBuilder(id=f"{self.id}.badge", name="Promotional badge")
            badge_builder.add_path(
                rectangle_path(
                    f"{self.id}.badge.background",
                    x=badge_x,
                    top=badge_top,
                    width=58,
                    height=22,
                    fill=INK,
                    name="Badge background",
                )
            )
            badge_builder.add(
                TextBlock(
                    id=f"{self.id}.badge.label",
                    text=self.product.badge,
                    width=50,
                    alignment="center",
                    wrap=False,
                    style=TextStyle(
                        font_size=8,
                        font_name="Helvetica-Bold",
                        tracking=80,
                        fill=WHITE,
                    ),
                ).render(x=badge_x + 4, top=badge_top - 5)
            )
            badge_layer = badge_builder.build()
            badge = RenderedComponent(
                width=58,
                height=22,
                paths=badge_layer.paths,
                text_frames=badge_layer.text_frames,
                item_order=badge_layer.item_order,
            ).transformed(
                AffineTransform.rotation(
                    -12,
                    origin_x=badge_x + 29,
                    origin_y=badge_top - 11,
                )
            )
            builder.add_grouped(
                badge,
                group_id=f"{self.id}.badge-group",
                group_name="Rotated promotional badge",
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
    products = (
        Product(
            sku="CM-101",
            category="Botanical soda",
            name="CITRUS MIST",
            note="Yuzu peel, green mandarin, and a dry mineral finish.",
            weight="250 mL",
            accent=Color(0.96, 0.65, 0.12),
            badge="NEW",
        ),
        Product(
            sku="FT-204",
            category="Cold brew tea",
            name="FOREST TEA",
            note="Roasted tea, cedar leaf, and a quiet smoky aroma.",
            weight="250 mL",
            accent=Color(0.18, 0.48, 0.32),
        ),
        Product(
            sku="CN-308",
            category="Cacao drink",
            name="COCOA NIGHT",
            note="Dark cacao, oat milk, and a restrained sea-salt finish.",
            weight="250 mL",
            accent=Color(0.48, 0.24, 0.18),
            badge="LIMITED",
        ),
    )
    page = LayerBuilder(id="packaging", name="Packaging labels")
    for index, product in enumerate(products):
        label = PackagingLabel(id=f"label-{index + 1}", product=product)
        page.add_grouped(
            label.render(x=30 + index * 210, top=360),
            group_id=f"label-{index + 1}.group",
            group_name=product.name,
        )
    return Document(
        width=660,
        height=400,
        title="Rotated packaging label variants",
        metadata={
            "source": "examples/packaging_labels.py",
            "business_case": "packaging-label-variants",
            "component": "PackagingLabel",
        },
        layers=[page.build()],
    )


if __name__ == "__main__":
    dump_ai7(build_document(), Path(__file__).with_name("packaging-labels.ai"))
