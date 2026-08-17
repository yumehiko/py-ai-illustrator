"""Build three campaign formats as named native Illustrator artboards."""

from dataclasses import dataclass
from pathlib import Path

from py_ai_illustrator import (
    Artboard,
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


@dataclass(frozen=True, slots=True)
class VariantSpec:
    id: str
    name: str
    left: float
    top: float
    width: float
    height: float
    layout: str


@dataclass(frozen=True, slots=True)
class CampaignVariant:
    spec: VariantSpec
    eyebrow: str
    title: str
    description: str
    action: str

    def render(self) -> RenderedComponent:
        navy = Color(0.04, 0.08, 0.15)
        white = Color(1, 1, 1)
        lime = Color(0.72, 0.92, 0.24)
        coral = Color(0.96, 0.3, 0.2)
        muted = Color(0.73, 0.78, 0.84)
        spec = self.spec
        builder = LayerBuilder(id=spec.id, name=spec.name)
        builder.add_path(
            rectangle_path(
                f"{spec.id}.background",
                x=spec.left,
                top=spec.top,
                width=spec.width,
                height=spec.height,
                fill=navy,
                name="Background",
            )
        )

        if spec.layout == "banner":
            accent_x = spec.left + spec.width - 72
            accent_y = spec.top - spec.height / 2
            title_width = 270
            title_size = 27
            eyebrow_top = spec.top - 24
            title_top = spec.top - 59
            description_top = spec.top - 127
            action_x = spec.left + 320
            action_top = spec.top - 67
            action_width = 138
            footer_top = spec.top - 158
        else:
            accent_x = spec.left + spec.width - 58
            accent_y = spec.top - 60
            title_width = spec.width - 54
            title_size = 32 if spec.layout == "square" else 27
            eyebrow_top = spec.top - 27
            title_top = spec.top - 86
            description_top = spec.top - 190
            action_x = spec.left + 27
            action_top = spec.top - 270
            action_width = min(164, spec.width - 54)
            footer_top = spec.top - spec.height + 24

        builder.add_path(
            ellipse_path(
                f"{spec.id}.accent",
                center_x=accent_x,
                center_y=accent_y,
                radius_x=62 if spec.layout == "banner" else 54,
                radius_y=62 if spec.layout == "banner" else 54,
                fill=coral,
                name="Campaign accent",
            )
        )
        builder.add(
            TextBlock(
                id=f"{spec.id}.eyebrow",
                name="Campaign series",
                text=self.eyebrow,
                width=spec.width - 54,
                wrap=False,
                style=TextStyle(
                    font_size=9,
                    font_name="Helvetica-Bold",
                    tracking=160,
                    fill=lime,
                ),
            ).render(x=spec.left + 27, top=eyebrow_top)
        )
        builder.add(
            TextBlock(
                id=f"{spec.id}.title",
                name="Campaign title",
                text=self.title,
                width=title_width,
                wrap=False,
                style=TextStyle(
                    font_size=title_size,
                    font_name="Helvetica-Bold",
                    line_height_ratio=0.98,
                    fill=white,
                ),
            ).render(x=spec.left + 27, top=title_top)
        )
        builder.add(
            TextBlock(
                id=f"{spec.id}.description",
                name="Campaign description",
                text=self.description,
                width=title_width,
                wrap=spec.layout != "banner",
                style=TextStyle(
                    font_size=11,
                    font_name="Helvetica",
                    line_height_ratio=1.35,
                    fill=muted,
                ),
            ).render(x=spec.left + 27, top=description_top)
        )
        builder.add_path(
            rectangle_path(
                f"{spec.id}.action-background",
                x=action_x,
                top=action_top,
                width=action_width,
                height=38,
                fill=lime,
                name="Action background",
            )
        )
        builder.add(
            TextBlock(
                id=f"{spec.id}.action",
                name="Action label",
                text=self.action,
                width=action_width,
                alignment="center",
                wrap=False,
                style=TextStyle(
                    font_size=11,
                    font_name="Helvetica-Bold",
                    tracking=70,
                    fill=navy,
                ),
            ).render(x=action_x, top=action_top - 11)
        )
        builder.add(
            TextBlock(
                id=f"{spec.id}.footer",
                name="Format label",
                text=spec.name.upper(),
                width=spec.width - 54,
                alignment="right",
                wrap=False,
                style=TextStyle(
                    font_size=8,
                    font_name="Helvetica-Bold",
                    tracking=120,
                    fill=muted,
                ),
            ).render(x=spec.left + 27, top=footer_top)
        )

        layer = builder.build()
        return RenderedComponent(
            width=spec.width,
            height=spec.height,
            paths=layer.paths,
            text_frames=layer.text_frames,
            item_order=layer.item_order,
        )


VARIANTS = (
    VariantSpec("campaign.square", "Square 1x1", 20, 380, 360, 360, "square"),
    VariantSpec("campaign.portrait", "Portrait 3x4", 400, 380, 270, 360, "portrait"),
    VariantSpec("campaign.banner", "Banner 3x1", 690, 380, 540, 180, "banner"),
)


def build_document() -> Document:
    page = LayerBuilder(id="campaign", name="Campaign variants")
    for spec in VARIANTS:
        component = CampaignVariant(
            spec=spec,
            eyebrow="DESIGN SYSTEMS / WORKSHOP",
            title="BUILD\nWITH MEANING",
            description="Turn shared rules into editable, reusable design.",
            action="RESERVE A SEAT",
        ).render()
        page.add_grouped(component, group_id=f"{spec.id}.group", group_name=spec.name)

    return Document(
        width=1250,
        height=400,
        title="Campaign variants with multiple artboards",
        metadata={
            "source": "examples/campaign_variants.py",
            "business_case": "multi-format-campaign",
            "variant_count": len(VARIANTS),
        },
        artboards=[
            Artboard(
                id=spec.id,
                name=spec.name,
                left=spec.left,
                top=spec.top,
                width=spec.width,
                height=spec.height,
            )
            for spec in VARIANTS
        ],
        layers=[page.build()],
    )


if __name__ == "__main__":
    dump_ai7(build_document(), Path(__file__).with_name("campaign-variants.ai"))
