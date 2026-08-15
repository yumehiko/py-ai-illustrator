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
