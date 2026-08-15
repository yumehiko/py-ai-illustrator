"""Build a styled Illustrator table from meaningful Python data."""

from dataclasses import replace
from pathlib import Path

from py_ai_illustrator import Color, Document, Table, TableColumn, TableStyle
from py_ai_illustrator.legacy import dump_ai7


def money(value: object) -> str:
    return f"${float(value):,.0f}"


def build_document() -> Document:
    base_style = TableStyle(
        header_height=38,
        row_height=44,
        padding_x=12,
        header_fill=Color(0.06, 0.12, 0.23),
        body_fill=Color(1.0, 1.0, 1.0),
        alternate_fill=Color(0.96, 0.97, 0.99),
        variant_fills={
            "featured": Color(0.9, 0.95, 1.0),
            "summary": Color(1.0, 0.91, 0.68),
        },
        variant_text_colors={"summary": Color(0.18, 0.12, 0.03)},
        border_color=Color(0.68, 0.72, 0.79),
        border_width=0.75,
        header_font_size=12,
        body_font_size=11,
    )
    # Styles are regular Python values, so a family of tables can share and vary them.
    print_style = replace(base_style, border_width=0.9)
    table = Table(
        id="subscription-comparison",
        columns=[
            TableColumn("plan", "Plan", 190),
            TableColumn("category", "Type", 110, alignment="center"),
            TableColumn("seats", "Seats", 90, alignment="right"),
            TableColumn(
                "monthly",
                "Monthly",
                126,
                alignment="right",
                formatter=money,
            ),
        ],
        rows=[
            {"plan": "Starter", "category": "Basic", "seats": 3, "monthly": 29},
            {
                "plan": "Studio",
                "category": "Popular",
                "seats": 12,
                "monthly": 89,
                "kind": "featured",
            },
            {"plan": "Agency", "category": "Pro", "seats": 30, "monthly": 199},
            {
                "plan": "Annual total",
                "category": "Studio",
                "seats": 12,
                "monthly": 1068,
                "kind": "summary",
            },
        ],
        style=print_style,
        variant_key="kind",
    )
    return Document(
        width=612,
        height=360,
        title="Python-authored styled table",
        metadata={"source": "examples/styled_table.py", "component": "Table"},
        layers=[table.render_layer(x=48, top=300, layer_name="Subscription table")],
    )


if __name__ == "__main__":
    dump_ai7(build_document(), Path(__file__).with_name("styled-table.ai"))
