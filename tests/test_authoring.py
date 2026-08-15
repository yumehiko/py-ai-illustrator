from py_ai_illustrator.authoring import Table, TableColumn, TableStyle
from py_ai_illustrator.model import Color


def test_table_compiles_semantics_to_editable_paths_and_text() -> None:
    table = Table(
        id="pricing",
        columns=[
            TableColumn("plan", "Plan", 120),
            TableColumn(
                "monthly",
                "Monthly",
                80,
                alignment="right",
                formatter=lambda value: f"${value:,.0f}",
            ),
        ],
        rows=[
            {"plan": "Starter", "monthly": 19, "kind": "standard"},
            {"plan": "Studio", "monthly": 49, "kind": "highlight"},
        ],
        variant_key="kind",
        style=TableStyle(
            header_height=32,
            row_height=28,
            variant_fills={"highlight": Color(1.0, 0.95, 0.8)},
        ),
    )

    layer = table.render_layer(x=40, top=200, layer_name="Pricing table")

    assert (table.width, table.height) == (200, 88)
    assert len(layer.paths) == 10  # 3 backgrounds + 4 horizontal + 3 vertical
    assert len(layer.text_frames) == 6
    assert len(layer.item_order) == 16
    assert layer.item_order[-1].kind == "text"
    assert layer.text_frames[3].text == "$19"
    assert layer.text_frames[3].alignment == "right"
    assert layer.paths[2].fill == Color(1.0, 0.95, 0.8)


def test_table_column_accessor_can_use_whole_row_context() -> None:
    column = TableColumn(
        key="display_name",
        title="Name",
        width=100,
        accessor=lambda row: f"{row['family']}, {row['given']}",
    )

    assert column.text_for({"family": "Doe", "given": "Jane"}) == "Doe, Jane"


def test_table_wraps_japanese_text_and_expands_row_height() -> None:
    table = Table(
        id="schedule",
        columns=[TableColumn("description", "内容", 80, wrap=True)],
        rows=[{"description": "日本語の長い説明文です"}],
        style=TableStyle(
            header_height=20,
            row_height=20,
            padding_x=10,
            padding_y=5,
            line_height_ratio=1.2,
            header_font_size=10,
            body_font_size=10,
        ),
    )

    layer = table.render_layer(x=20, top=100)
    row_lines = layer.text_frames[1:]

    assert [text.text for text in row_lines] == ["日本語の長い", "説明文です"]
    assert table.height == 52
    assert row_lines[0].id == "schedule.row-0.description.line-0"
    assert row_lines[1].y < row_lines[0].y
    assert layer.paths[-1].points[-1].y == 48


def test_explicit_cell_lines_expand_without_enabling_wrap() -> None:
    table = Table(
        id="notes",
        columns=[TableColumn("note", "Note", 120)],
        rows=[{"note": "First line\nSecond line"}],
        style=TableStyle(row_height=20, padding_y=4, body_font_size=10),
    )

    layer = table.render_layer(x=0, top=100)

    assert [text.text for text in layer.text_frames[-2:]] == [
        "First line",
        "Second line",
    ]
    assert table.height > table.style.header_height + table.style.row_height
