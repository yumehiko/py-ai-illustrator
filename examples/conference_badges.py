"""Build a sheet of reusable conference badges without a table model."""

from dataclasses import dataclass
from pathlib import Path

from py_ai_illustrator import (
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
class Attendee:
    name: str
    organization: str
    role: str
    badge_number: int


ROLE_COLORS = {
    "SPEAKER": Color(0.94, 0.32, 0.25),
    "STAFF": Color(0.15, 0.55, 0.43),
    "GUEST": Color(0.2, 0.42, 0.78),
}


@dataclass(frozen=True, slots=True)
class ConferenceBadge:
    id: str
    attendee: Attendee
    width: float = 252
    height: float = 142

    def render(self, *, x: float, top: float) -> RenderedComponent:
        accent = ROLE_COLORS[self.attendee.role]
        builder = LayerBuilder(id=f"{self.id}.content", name=self.attendee.name)
        builder.add_path(
            rectangle_path(
                f"{self.id}.background",
                x=x,
                top=top,
                width=self.width,
                height=self.height,
                fill=Color(1, 1, 1),
                stroke=Color(0.72, 0.75, 0.8),
                stroke_width=0.8,
                name=f"Badge: {self.attendee.name}",
            )
        )
        builder.add_path(
            rectangle_path(
                f"{self.id}.accent",
                x=x,
                top=top,
                width=self.width,
                height=10,
                fill=accent,
            )
        )
        builder.add_path(
            ellipse_path(
                f"{self.id}.avatar",
                center_x=x + 42,
                center_y=top - 61,
                radius_x=25,
                radius_y=25,
                fill=Color(0.93, 0.95, 0.98),
                stroke=accent,
                stroke_width=2,
                name=f"Avatar: {self.attendee.name}",
            )
        )
        initial = "".join(part[0] for part in self.attendee.name.split()[:2]).upper()
        builder.add(
            TextBlock(
                id=f"{self.id}.initial",
                text=initial,
                width=50,
                alignment="center",
                wrap=False,
                style=TextStyle(font_size=14, fill=accent),
            ).render(x=x + 17, top=top - 52)
        )
        builder.add(
            TextBlock(
                id=f"{self.id}.name",
                name="Attendee name",
                text=self.attendee.name,
                width=164,
                wrap=False,
                style=TextStyle(font_size=17, font_name="Helvetica-Bold"),
            ).render(x=x + 76, top=top - 38)
        )
        builder.add(
            TextBlock(
                id=f"{self.id}.organization",
                name="Organization",
                text=self.attendee.organization,
                width=164,
                wrap=False,
                style=TextStyle(font_size=10, fill=Color(0.28, 0.32, 0.4)),
            ).render(x=x + 76, top=top - 66)
        )
        builder.add_path(
            rectangle_path(
                f"{self.id}.role-background",
                x=x + 76,
                top=top - 91,
                width=78,
                height=24,
                fill=accent,
            )
        )
        builder.add(
            TextBlock(
                id=f"{self.id}.role",
                name="Role",
                text=self.attendee.role,
                width=78,
                alignment="center",
                wrap=False,
                style=TextStyle(font_size=9, font_name="Helvetica-Bold", fill=Color(1, 1, 1)),
            ).render(x=x + 76, top=top - 97)
        )
        builder.add(
            TextBlock(
                id=f"{self.id}.number",
                name="Badge number",
                text=f"NO. {self.attendee.badge_number:03d}",
                width=75,
                alignment="right",
                wrap=False,
                style=TextStyle(font_size=9, fill=Color(0.38, 0.42, 0.5)),
            ).render(x=x + 165, top=top - 117)
        )
        layer = builder.build()
        return RenderedComponent(
            width=self.width,
            height=self.height,
            paths=layer.paths,
            text_frames=layer.text_frames,
            item_order=layer.item_order,
        )


def build_document() -> Document:
    attendees = [
        Attendee("Avery Chen", "Open Tools Lab", "SPEAKER", 1),
        Attendee("Mina Patel", "Design Systems Co.", "GUEST", 2),
        Attendee("Leo Martin", "Community Studio", "STAFF", 3),
        Attendee("Sofia Rossi", "Type & Form", "SPEAKER", 4),
    ]
    builder = LayerBuilder(id="badge-sheet", name="Conference badges")
    positions = [(44, 358), (316, 358), (44, 196), (316, 196)]
    for index, (attendee, position) in enumerate(zip(attendees, positions, strict=True)):
        badge = ConferenceBadge(id=f"badge-{index + 1}", attendee=attendee)
        builder.add(badge.render(x=position[0], top=position[1]))
    return Document(
        width=612,
        height=400,
        title="Semantic conference badge sheet",
        metadata={
            "source": "examples/conference_badges.py",
            "component": "ConferenceBadge",
        },
        layers=[builder.build()],
    )


if __name__ == "__main__":
    dump_ai7(build_document(), Path(__file__).with_name("conference-badges.ai"))
