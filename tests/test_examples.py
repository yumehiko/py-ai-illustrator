from pathlib import Path

from py_ai_illustrator.format import FileFormat, inspect_file
from py_ai_illustrator.legacy import load_ai7


def test_generated_styled_table_contains_editable_cells_and_visual_rules() -> None:
    example = Path(__file__).parents[1] / "examples" / "styled-table.ai"
    document = load_ai7(example)
    layer = document.layers[0]

    assert document.metadata["source"] == "examples/styled_table.py"
    assert layer.name == "Subscription table"
    assert len(layer.paths) == 16
    assert len(layer.text_frames) == 20
    assert layer.text_frames[0].text == "Plan"
    assert layer.text_frames[-1].text == "$1,068"
    assert layer.text_frames[-1].alignment == "right"


def test_generated_japanese_table_preserves_wrapped_unicode_text() -> None:
    example = Path(__file__).parents[1] / "examples" / "japanese-table.ai"
    document = load_ai7(example)
    layer = document.layers[0]

    assert document.metadata["encoding"] == "cp932"
    assert len(layer.paths) == 15
    assert len(layer.text_frames) == 18
    assert [text.text for text in layer.text_frames[:3]] == ["時刻", "区分", "内容"]
    assert "".join(text.text for text in layer.text_frames[5:7]) == (
        "受付を開始します。資料を受け取って会場へお進みください。"
    )


def test_conference_badges_are_independent_semantic_components() -> None:
    example = Path(__file__).parents[1] / "examples" / "conference-badges.ai"
    document = load_ai7(example)
    layer = document.layers[0]

    assert document.metadata["component"] == "ConferenceBadge"
    assert len(layer.paths) == 16
    assert len(layer.text_frames) == 20
    assert {text.text for text in layer.text_frames if text.id.endswith("role.line-0")} == {
        "SPEAKER",
        "GUEST",
        "STAFF",
    }
    assert all(
        text.alignment == "right"
        for text in layer.text_frames
        if text.id.endswith("number.line-0")
    )


def test_event_poster_preserves_hierarchy_and_wrapped_japanese() -> None:
    example = Path(__file__).parents[1] / "examples" / "event-poster.ai"
    document = load_ai7(example)
    layer = document.layers[0]

    assert document.metadata["component"] == "EventPoster"
    assert len(layer.paths) == 4
    assert any(text.text == "創造とコード" for text in layer.text_frames)
    statement = [
        text for text in layer.text_frames if text.id.startswith("poster.statement.line-")
    ]
    assert len(statement) > 1
    assert "".join(text.text for text in statement) == (
        "データを座標へ置き換えるだけではなく、文脈と規則を再利用できる形にします。"
        "PythonとIllustratorを往復しながら、編集できる紙面を一緒につくります。"
    )


def test_retail_price_tags_preserve_editable_nested_business_components() -> None:
    example = Path(__file__).parents[1] / "examples" / "retail-price-tags.ai"
    document = load_ai7(example)
    layer = document.layers[0]

    assert document.metadata["business_case"] == "retail-shelf-labels"
    assert len(layer.groups) == 6
    assert all(len(tag.groups) == 1 for tag in layer.groups)
    assert all(tag.groups[0].id.endswith("price-group") for tag in layer.groups)
    price_frames = [
        text
        for tag in layer.groups
        for price_group in tag.groups
        for text in price_group.text_frames
        if ".price.line-" in text.id
    ]
    assert [text.text for text in price_frames] == [
        "298円",
        "348円",
        "680円",
        "458円",
        "798円",
        "320円",
    ]
    assert all(text.alignment == "right" for text in price_frames)
    statuses = {
        text.text
        for tag in layer.groups
        for price_group in tag.groups
        for text in price_group.text_frames
        if ".variant.line-" in text.id
    }
    assert statuses == {"おすすめ", "SALE", "残りわずか"}


def test_quarterly_report_preserves_chart_semantics_and_stroke_styles() -> None:
    example = Path(__file__).parents[1] / "examples" / "quarterly-kpi-report.ai"
    document = load_ai7(example)
    layer = document.layers[0]

    assert document.metadata["business_case"] == "quarterly-kpi-report"
    assert len(layer.groups) == 4
    chart = next(group for group in layer.groups if group.id == "operating-index.group")
    target = next(path for path in chart.paths if path.id == "operating-index.target")
    actual = next(path for path in chart.paths if path.id == "operating-index.actual")
    assert target.dash_pattern == [10, 6]
    assert target.dash_offset == 2
    assert target.line_cap == "round"
    assert actual.dash_pattern == []
    assert actual.line_cap == "round"
    assert actual.line_join == "round"
    metric_values = [
        text
        for metric in layer.groups[:3]
        for text in metric.text_frames
        if ".value.line-" in text.id
    ]
    assert [text.text for text in metric_values] == ["$1.42M", "42.8%", "76.2%"]
    assert all(text.alignment == "right" for text in metric_values)


def test_materialized_examples_are_pdf_compatible_native_ai() -> None:
    examples = Path(__file__).parents[1] / "examples"
    native_examples = (
        "styled-table.native.ai",
        "japanese-table.native.ai",
        "conference-badges.native.ai",
        "event-poster.native.ai",
        "retail-price-tags.native.ai",
        "quarterly-kpi-report.native.ai",
    )

    for name in native_examples:
        report = inspect_file(examples / name)
        assert report.format is FileFormat.PDF_COMPATIBLE_AI
        assert "AIPrivateData" in report.illustrator_markers
