"""Build a wrapped Japanese schedule as editable Illustrator artwork."""

from pathlib import Path

from py_ai_illustrator import Color, Document, Table, TableColumn, TableStyle
from py_ai_illustrator.legacy import dump_ai7

JAPANESE_FONT = "_KozGoPr6N-Regular-83pv-RKSJ-H"


def build_document() -> Document:
    table = Table(
        id="event-schedule",
        columns=[
            TableColumn("time", "時刻", 74, alignment="right"),
            TableColumn("category", "区分", 112, alignment="center", wrap=True),
            TableColumn("description", "内容", 294, wrap=True),
        ],
        rows=[
            {
                "time": "09:00",
                "category": "開場",
                "description": "受付を開始します。資料を受け取って会場へお進みください。",
            },
            {
                "time": "10:00",
                "category": "基調講演",
                "description": "PythonとIllustratorをつなぐ、文脈を持ったデザイン制作の考え方",
                "kind": "featured",
            },
            {
                "time": "13:30",
                "category": "ワークショップ",
                "description": "共有スタイルから複数種類の表を生成し、結果を比較します。",
            },
            {
                "time": "17:00",
                "category": "終了",
                "description": "生成したAIデータは自由に編集して持ち帰れます。",
                "kind": "notice",
            },
        ],
        variant_key="kind",
        style=TableStyle(
            header_height=36,
            row_height=38,
            padding_x=11,
            padding_y=8,
            line_height_ratio=1.35,
            header_fill=Color(0.12, 0.15, 0.25),
            body_fill=Color(1.0, 1.0, 1.0),
            alternate_fill=Color(0.96, 0.97, 0.99),
            variant_fills={
                "featured": Color(0.89, 0.95, 1.0),
                "notice": Color(1.0, 0.94, 0.75),
            },
            header_text_color=Color(1.0, 1.0, 1.0),
            body_text_color=Color(0.12, 0.14, 0.2),
            border_color=Color(0.65, 0.7, 0.78),
            border_width=0.8,
            header_font_name=JAPANESE_FONT,
            body_font_name=JAPANESE_FONT,
            header_font_size=12,
            body_font_size=11,
        ),
    )
    return Document(
        width=560,
        height=380,
        title="Python-authored Japanese schedule",
        metadata={
            "source": "examples/japanese_table.py",
            "component": "Table",
            "encoding": "cp932",
        },
        layers=[table.render_layer(x=40, top=330, layer_name="Japanese schedule")],
    )


if __name__ == "__main__":
    dump_ai7(build_document(), Path(__file__).with_name("japanese-table.ai"))
