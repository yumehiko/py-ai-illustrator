from copy import deepcopy

from py_ai_illustrator import semantic_diff
from py_ai_illustrator.legacy import SetPathFill, dumps_ai7, patch_path_fill, reads_ai7
from py_ai_illustrator.model import Color, Document, Layer, LayerItemRef, Path, Point


def semantic_document() -> Document:
    first = Path(
        id="first",
        points=[Point(10, 10), Point(20, 10), Point(20, 20)],
        fill=Color(1, 0, 0),
    )
    second = Path(
        id="second",
        points=[Point(30, 30), Point(40, 30), Point(40, 40)],
        fill=Color(0, 0, 1),
    )
    return Document(
        width=100,
        height=80,
        layers=[
            Layer(
                id="artwork",
                name="Artwork",
                paths=[first, second],
                item_order=[
                    LayerItemRef("path", "first"),
                    LayerItemRef("path", "second"),
                ],
            )
        ],
    )


def test_semantic_diff_is_empty_for_supported_round_trip() -> None:
    document = semantic_document()
    restored = reads_ai7(dumps_ai7(document)).document

    difference = semantic_diff(document, restored)

    assert difference.equal is True
    assert difference.to_dict() == {
        "equal": True,
        "difference_count": 0,
        "differences": [],
    }


def test_semantic_diff_matches_nodes_by_stable_id() -> None:
    before = semantic_document()
    after = deepcopy(before)
    after.layers[0].paths[1].fill = Color(0, 1, 0)

    difference = semantic_diff(before, after)

    assert difference.equal is False
    assert [(item.kind, item.path, item.before, item.after) for item in difference.differences] == [
        (
            "changed",
            "layers[id='artwork'].paths[id='second'].fill.blue",
            1,
            0,
        ),
        (
            "changed",
            "layers[id='artwork'].paths[id='second'].fill.green",
            0,
            1,
        ),
    ]


def test_semantic_diff_reports_stacking_reorder_separately() -> None:
    before = semantic_document()
    after = deepcopy(before)
    after.layers[0].item_order.reverse()

    difference = semantic_diff(before, after)

    assert [(item.kind, item.path) for item in difference.differences] == [
        ("reordered", "layers[id='artwork'].item_order.@order")
    ]


def test_semantic_diff_reports_added_and_removed_nodes_by_id() -> None:
    before = semantic_document()
    after = deepcopy(before)
    after.layers[0].paths.pop(0)
    after.layers[0].paths.append(
        Path(id="third", points=[Point(50, 50), Point(60, 50)], fill=Color(0, 0, 0))
    )

    difference = semantic_diff(before, after)

    assert [(item.kind, item.path) for item in difference.differences] == [
        ("removed", "layers[id='artwork'].paths[id='first']"),
        ("added", "layers[id='artwork'].paths[id='third']"),
    ]


def test_semantic_diff_proves_a_typed_patch_has_only_the_requested_effect() -> None:
    result = reads_ai7(dumps_ai7(semantic_document()))
    patched = patch_path_fill(
        result,
        SetPathFill(
            path_id="first",
            expected_fill=Color(1, 0, 0),
            fill=Color(0.25, 0.5, 0.75),
        ),
    )

    difference = semantic_diff(result.document, reads_ai7(patched.data).document)

    assert [(item.kind, item.path) for item in difference.differences] == [
        ("changed", "layers[id='artwork'].paths[id='first'].fill.blue"),
        ("changed", "layers[id='artwork'].paths[id='first'].fill.green"),
        ("changed", "layers[id='artwork'].paths[id='first'].fill.red"),
    ]
