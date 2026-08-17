"""Build a Japanese editorial brochure with native reflowable area text."""

from pathlib import Path

from py_ai_illustrator import (
    AreaTextBlock,
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
    paper = Color(0.96, 0.94, 0.88)
    ink = Color(0.07, 0.1, 0.14)
    blue = Color(0.08, 0.36, 0.68)
    coral = Color(0.91, 0.28, 0.2)
    white = Color(1, 1, 1)
    builder = LayerBuilder(id="brochure", name="Editorial brochure")

    builder.add_path(
        rectangle_path(
            "brochure.background",
            x=0,
            top=842,
            width=595,
            height=842,
            fill=paper,
            name="Paper",
        )
    )
    builder.add_path(
        rectangle_path(
            "brochure.header-band",
            x=0,
            top=842,
            width=595,
            height=88,
            fill=ink,
            name="Header band",
        )
    )
    builder.add_path(
        ellipse_path(
            "brochure.accent",
            center_x=520,
            center_y=672,
            radius_x=76,
            radius_y=76,
            fill=coral,
            name="Coral accent",
        )
    )
    builder.add_path(
        rectangle_path(
            "brochure.article-panel",
            x=42,
            top=596,
            width=511,
            height=442,
            fill=white,
            name="Article panel",
        )
    )

    builder.add(
        TextBlock(
            id="brochure.series",
            name="Series",
            text="DESIGN SYSTEMS / FIELD NOTE 04",
            width=511,
            wrap=False,
            style=TextStyle(
                font_size=10,
                font_name="Helvetica-Bold",
                tracking=180,
                fill=white,
            ),
        ).render(x=42, top=808)
    )
    builder.add(
        TextBlock(
            id="brochure.title",
            name="Title",
            text="意味から組み立てる紙面",
            width=470,
            wrap=False,
            style=TextStyle(font_size=30, font=JAPANESE_FONT, fill=ink),
        ).render(x=42, top=714)
    )
    builder.add(
        AreaTextBlock(
            id="brochure.deck",
            name="Introduction",
            text=(
                "データを座標へ置き換えるだけでは、制作物が持つ文脈は残りません。"
                "再利用したい規則をPythonの部品として表現し、Illustratorでは編集可能な形を保ちます。"
            ),
            width=390,
            height=66,
            style=TextStyle(
                font_size=13,
                font=JAPANESE_FONT,
                fill=blue,
                line_height_ratio=1.55,
            ),
        ).render(x=42, top=650)
    )

    left_body = (
        "同じ体裁を共有する制作物でも、要素の意味は案件ごとに異なります。商品一覧なら価格や在庫、"
        "イベント案内なら日時や登壇者、報告書なら実績と目標が中心です。\n\n"
        "この作例では、文章を座標ごとの短い文字列へ分解せず、一つの文章枠として保持します。"
        "Illustratorで枠幅を変更すれば、文章はその場で再流し込みされます。"
    )
    right_body = (
        "Python側では、原稿、書体、段落の揃え、行送り、文章枠の寸法を意味のある属性として指定します。"
        "レンダリング後も安定IDが残るため、別の自動処理から同じ文章枠を見つけられます。\n\n"
        "JSONは交換用の中間表現です。制作物固有の判断や条件分岐はPython componentに置き、"
        "Illustratorの標準機能で再編集できる文書へ変換します。"
    )
    body_style = TextStyle(
        font_size=10.5,
        font=JAPANESE_FONT,
        fill=ink,
        line_height_ratio=1.7,
    )
    builder.add(
        AreaTextBlock(
            id="brochure.body-left",
            name="Article body left",
            text=left_body,
            width=222,
            height=310,
            style=body_style,
        ).render(x=66, top=560)
    )
    builder.add(
        AreaTextBlock(
            id="brochure.body-right",
            name="Article body right",
            text=right_body,
            width=222,
            height=310,
            style=body_style,
        ).render(x=307, top=560)
    )
    builder.add(
        AreaTextBlock(
            id="brochure.pull-quote",
            name="Pull quote",
            text="文章枠は、見た目だけでなく編集の単位でもある。",
            width=180,
            height=80,
            alignment="center",
            style=TextStyle(
                font_size=15,
                font=JAPANESE_FONT,
                fill=white,
                line_height_ratio=1.45,
            ),
        ).render(x=378, top=710)
    )
    builder.add(
        TextBlock(
            id="brochure.footer",
            name="Footer",
            text="PY-AI / EDITABLE BY DESIGN",
            width=511,
            alignment="right",
            wrap=False,
            style=TextStyle(
                font_size=9,
                font_name="Helvetica-Bold",
                tracking=120,
                fill=ink,
            ),
        ).render(x=42, top=102)
    )

    return Document(
        width=595,
        height=842,
        title="Editorial brochure with native area text",
        metadata={
            "source": "examples/editorial_brochure.py",
            "component": "EditorialBrochure",
            "text_model": "native-area-text",
        },
        layers=[builder.build()],
    )


if __name__ == "__main__":
    dump_ai7(build_document(), Path(__file__).with_name("editorial-brochure.ai"))
