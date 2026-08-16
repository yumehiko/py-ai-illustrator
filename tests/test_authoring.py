import pytest

from py_ai_illustrator.authoring import (
    FontSpec,
    LayerBuilder,
    Table,
    TableColumn,
    TableStyle,
    TextBlock,
    TextStyle,
    ellipse_path,
    polyline_path,
    rectangle_path,
)
from py_ai_illustrator.model import Color, LayerItemRef


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
    assert layer.text_frames[3].x == 230
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


def test_text_block_wraps_and_uses_alignment_anchor() -> None:
    block = TextBlock(
        id="summary",
        text="Reusable semantic text wraps deterministically",
        width=110,
        alignment="right",
        style=TextStyle(font_size=10, line_height_ratio=1.4),
    )

    rendered = block.render(x=20, top=100)

    assert len(rendered.text_frames) > 1
    assert all(frame.x == 130 for frame in rendered.text_frames)
    assert all(frame.alignment == "right" for frame in rendered.text_frames)
    assert rendered.text_frames[1].y == pytest.approx(rendered.text_frames[0].y - 14)


def test_font_spec_keeps_legacy_encoding_and_native_font_separate() -> None:
    font = FontSpec(
        postscript_name="KozGoPr6N-Regular",
        family="小塚ゴシック Pr6N",
        style="R",
        legacy_name="_KozGoPr6N-Regular-83pv-RKSJ-H",
    )
    rendered = TextBlock(
        id="heading",
        text="見出し",
        width=100,
        wrap=False,
        style=TextStyle(font=font),
    ).render(x=10, top=80)

    assert rendered.text_frames[0].font_name == font.legacy_name
    assert rendered.text_frames[0].native_font_name == font.postscript_name


def test_font_spec_rejects_a_family_name_as_postscript_name() -> None:
    with pytest.raises(ValueError, match="PostScript"):
        FontSpec(postscript_name="Noto Sans JP")


def test_layer_builder_composes_components_and_rejects_duplicate_ids() -> None:
    builder = LayerBuilder(id="page", name="Page")
    background = rectangle_path(
        "background", x=0, top=100, width=120, height=80, fill=Color(1, 1, 1)
    )
    marker = ellipse_path(
        "marker",
        center_x=20,
        center_y=70,
        radius_x=8,
        radius_y=8,
        fill=Color(0.2, 0.4, 0.8),
    )
    text = TextBlock(id="label", text="Label", width=80).render(x=32, top=78)

    builder.add_path(background)
    builder.add_path(marker)
    builder.add(text)
    layer = builder.build()

    assert [reference.id for reference in layer.item_order] == [
        "background",
        "marker",
        "label.line-0",
    ]
    assert layer.paths[1].points[0].out_handle is not None
    with pytest.raises(ValueError, match="Duplicate item id"):
        builder.add_path(background)


def test_table_can_render_as_a_composable_component() -> None:
    table = Table(
        id="small",
        columns=[TableColumn("name", "Name", 100)],
        rows=[{"name": "One"}],
    )

    rendered = table.render(x=10, top=100)
    builder = LayerBuilder(id="page", name="Page")
    builder.add(rendered)

    assert rendered.width == 100
    assert rendered.height == table.height
    assert len(builder.build().item_order) == len(rendered.item_order)


def test_layer_builder_can_keep_a_component_as_an_editable_group() -> None:
    rendered = TextBlock(id="label", text="Grouped", width=80).render(x=20, top=80)
    builder = LayerBuilder(id="page", name="Page")

    group = builder.add_grouped(
        rendered,
        group_id="product-card",
        group_name="Product Card",
    )
    layer = builder.build()

    assert group.name == "Product Card"
    assert group.text_frames[0].text == "Grouped"
    assert layer.item_order == [LayerItemRef("group", "product-card")]
    assert layer.groups == [group]


def test_polyline_path_keeps_native_stroke_style() -> None:
    route = polyline_path(
        "route",
        points=[(10, 20), (30, 50), (80, 40)],
        stroke=Color(0.1, 0.4, 0.8),
        stroke_width=4,
        dash_pattern=(12, 6),
        dash_offset=2,
        line_cap="round",
        line_join="bevel",
    )

    assert not route.closed
    assert route.dash_pattern == [12, 6]
    assert route.dash_offset == 2
    assert route.line_cap == "round"
    assert route.line_join == "bevel"
