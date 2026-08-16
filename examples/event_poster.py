"""Build a Japanese event poster from semantic text blocks and vector shapes."""

from pathlib import Path

from py_ai_illustrator import (
    Color,
    Document,
    FontSpec,
    LayerBuilder,
    TextBlock,
    TextStyle,
    ellipse_path,
    rectangle_path,
)
from py_ai_illustrator.legacy import dump_ai7

JAPANESE_FONT = FontSpec(
    postscript_name="KozGoPr6N-Regular",
    family="小塚ゴシック Pr6N",
    style="R",
    legacy_name="_KozGoPr6N-Regular-83pv-RKSJ-H",
)


def build_document() -> Document:
    navy = Color(0.05, 0.1, 0.2)
    blue = Color(0.12, 0.42, 0.82)
    coral = Color(0.96, 0.35, 0.28)
    paper = Color(0.97, 0.95, 0.9)
    builder = LayerBuilder(id="poster", name="Event poster")
    builder.add_path(
        rectangle_path(
            "poster.background",
            x=0,
            top=720,
            width=560,
            height=720,
            fill=paper,
            name="Paper background",
        )
    )
    builder.add_path(
        ellipse_path(
            "poster.blue-orbit",
            center_x=458,
            center_y=616,
            radius_x=118,
            radius_y=118,
            fill=blue,
            name="Blue orbit",
        )
    )
    builder.add_path(
        ellipse_path(
            "poster.coral-orbit",
            center_x=92,
            center_y=142,
            radius_x=58,
            radius_y=58,
            fill=coral,
            name="Coral orbit",
        )
    )
    builder.add(
        TextBlock(
            id="poster.series",
            name="Series label",
            text="DESIGN + CODE 2026",
            width=460,
            alignment="right",
            wrap=False,
            style=TextStyle(font_size=11, font_name="Helvetica-Bold", fill=navy),
        ).render(x=50, top=674)
    )
    builder.add(
        TextBlock(
            id="poster.title",
            name="Event title",
            text="創造とコード",
            width=460,
            alignment="center",
            wrap=False,
            style=TextStyle(font_size=38, font=JAPANESE_FONT, fill=navy),
        ).render(x=50, top=548)
    )
    builder.add(
        TextBlock(
            id="poster.subtitle",
            name="Subtitle",
            text="意味からデザインを組み立てる一日",
            width=420,
            alignment="center",
            wrap=False,
            style=TextStyle(font_size=16, font=JAPANESE_FONT, fill=blue),
        ).render(x=70, top=486)
    )
    builder.add_path(
        rectangle_path(
            "poster.statement-background",
            x=72,
            top=422,
            width=416,
            height=142,
            fill=Color(1, 1, 1),
            stroke=Color(0.82, 0.8, 0.75),
            stroke_width=0.8,
            name="Statement panel",
        )
    )
    builder.add(
        TextBlock(
            id="poster.statement",
            name="Event statement",
            text=(
                "データを座標へ置き換えるだけではなく、文脈と規則を再利用できる形にします。"
                "PythonとIllustratorを往復しながら、編集できる紙面を一緒につくります。"
            ),
            width=348,
            alignment="left",
            wrap=True,
            style=TextStyle(
                font_size=13,
                font=JAPANESE_FONT,
                fill=navy,
                line_height_ratio=1.65,
            ),
        ).render(x=106, top=390)
    )
    builder.add(
        TextBlock(
            id="poster.date",
            name="Date",
            text="9月18日（金） 10:00-17:00",
            width=350,
            alignment="center",
            wrap=False,
            style=TextStyle(font_size=18, font=JAPANESE_FONT, fill=navy),
        ).render(x=105, top=232)
    )
    builder.add(
        TextBlock(
            id="poster.venue",
            name="Venue",
            text="東京・海岸スタジオ / 入場無料・要予約",
            width=350,
            alignment="center",
            wrap=False,
            style=TextStyle(font_size=11, font=JAPANESE_FONT, fill=navy),
        ).render(x=105, top=196)
    )
    builder.add(
        TextBlock(
            id="poster.footer",
            name="Registration URL",
            text="example.org/design-code",
            width=270,
            alignment="right",
            wrap=False,
            style=TextStyle(font_size=10, font_name="Helvetica", fill=navy),
        ).render(x=240, top=74)
    )
    return Document(
        width=560,
        height=720,
        title="Semantic Japanese event poster",
        metadata={
            "source": "examples/event_poster.py",
            "component": "EventPoster",
            "encoding": "cp932",
        },
        layers=[builder.build()],
    )


if __name__ == "__main__":
    dump_ai7(build_document(), Path(__file__).with_name("event-poster.ai"))
