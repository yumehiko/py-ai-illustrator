from pathlib import Path

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
