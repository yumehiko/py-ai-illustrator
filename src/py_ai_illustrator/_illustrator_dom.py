"""Illustrator DOM projection and pure comparison helpers.

The functions here operate on Python IR or JSON snapshots only. They never
start subprocesses; the process boundary lives in ``_illustrator_bridge``.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from .format import FileFormat, inspect_file
from .legacy import load_ai7
from .model import (
    ClippingGroup,
    CmykColor,
    Color,
    CompoundPath,
    Document,
    Group,
    LinkedImage,
    ProcessColor,
    TextFrame,
)
from .model import Path as AIPath


def group_paths(group: Group) -> list[AIPath]:
    paths: list[AIPath] = []
    paths.extend(group.paths)
    for compound in group.compound_paths:
        paths.extend(compound.paths)
    for clipping_group in group.clipping_groups:
        paths.append(clipping_group.clipping_path)
        paths.extend(clipping_group.paths)
    for child in group.groups:
        paths.extend(group_paths(child))
    return paths


def document_paths(document: Document) -> list[AIPath]:
    paths: list[AIPath] = []
    for layer in document.layers:
        paths.extend(layer.paths)
        for compound in layer.compound_paths:
            paths.extend(compound.paths)
        for group in layer.clipping_groups:
            paths.append(group.clipping_path)
            paths.extend(group.paths)
        for group in layer.groups:
            paths.extend(group_paths(group))
    return paths


def document_text_frames(document: Document) -> list[TextFrame]:
    def group_text(group: Group) -> list[TextFrame]:
        return [
            *group.text_frames,
            *(text for child in group.groups for text in group_text(child)),
        ]

    return [
        text
        for layer in document.layers
        for text in [
            *layer.text_frames,
            *(text for group in layer.groups for text in group_text(group)),
        ]
    ]


def document_linked_images(document: Document) -> list[LinkedImage]:
    def group_images(group: Group) -> list[LinkedImage]:
        return [
            *group.linked_images,
            *(image for child in group.groups for image in group_images(child)),
        ]

    return [
        image
        for layer in document.layers
        for image in [
            *layer.linked_images,
            *(image for group in layer.groups for image in group_images(group)),
        ]
    ]


def document_text_frames_dom_order(document: Document) -> list[TextFrame]:
    def group_text(group: Group) -> list[TextFrame]:
        texts: list[TextFrame] = []
        for item in reversed(group.ordered_items()):
            if isinstance(item, TextFrame):
                texts.append(item)
            elif isinstance(item, Group):
                texts.extend(group_text(item))
        return texts

    texts: list[TextFrame] = []
    for layer in document.layers:
        for item in reversed(layer.ordered_items()):
            if isinstance(item, TextFrame):
                texts.append(item)
            elif isinstance(item, Group):
                texts.extend(group_text(item))
    return texts


def group_descendants(group: Group) -> list[Group]:
    return [
        group,
        *(nested for child in group.groups for nested in group_descendants(child)),
    ]


def document_groups(document: Document) -> list[Group]:
    return [
        group
        for layer in document.layers
        for root in layer.groups
        for group in group_descendants(root)
    ]


def document_compound_paths(document: Document) -> list[CompoundPath]:
    return [
        compound
        for layer in document.layers
        for compound in [
            *layer.compound_paths,
            *(
                compound
                for root in layer.groups
                for group in group_descendants(root)
                for compound in group.compound_paths
            ),
        ]
    ]


def document_clipping_groups(document: Document) -> list[ClippingGroup]:
    return [
        clipping_group
        for layer in document.layers
        for clipping_group in [
            *layer.clipping_groups,
            *(
                clipping_group
                for root in layer.groups
                for group in group_descendants(root)
                for clipping_group in group.clipping_groups
            ),
        ]
    ]


def group_signature(group: Group) -> tuple[Any, ...]:
    child_groups = {child.id: child for child in group.groups}
    return tuple(
        (reference.kind, group_signature(child_groups[reference.id]))
        if reference.kind == "group"
        else (reference.kind,)
        for reference in group.item_order
    )


def expected_structure(source: Path) -> dict[str, Any] | None:
    report = inspect_file(source)
    if report.format is not FileFormat.LEGACY_AI:
        return None
    try:
        document = load_ai7(source)
    except ValueError:
        return None
    paths = document_paths(document)
    text_frames = document_text_frames(document)
    linked_images = document_linked_images(document)
    return {
        "layer_count": len(document.layers),
        "layer_names": [layer.name for layer in document.layers],
        "layer_page_item_types": [
            [
                {
                    "path": "PathItem",
                    "text": "TextFrame",
                    "image": "PathItem",
                    "compound_path": "CompoundPathItem",
                    "clipping_group": "GroupItem",
                    "group": "GroupItem",
                }[reference.kind]
                for reference in reversed(layer.item_order)
            ]
            for layer in document.layers
        ],
        "path_item_count": len(paths) + len(linked_images),
        "text_frame_count": len(text_frames),
        "point_counts": sorted(
            [*(len(path.points) for path in paths), *(4 for _ in linked_images)]
        ),
        "closed_count": sum(path.closed for path in paths) + len(linked_images),
        "filled_count": sum(path.fill is not None for path in paths),
        "stroked_count": sum(path.stroke is not None for path in paths),
        "compound_path_item_count": len(document_compound_paths(document)),
        "clipping_group_count": len(document_clipping_groups(document)),
        "group_item_count": len(document_groups(document)),
    }


def compare_structure(expected: dict[str, Any], actual: dict[str, Any]) -> dict[str, bool]:
    keys = (
        "layer_count",
        "layer_names",
        "layer_page_item_types",
        "path_item_count",
        "text_frame_count",
        "point_counts",
        "closed_count",
        "filled_count",
        "stroked_count",
        "compound_path_item_count",
        "clipping_group_count",
        "group_item_count",
    )
    return {key: actual.get(key) == expected[key] for key in keys}


def color_close(
    expected: ProcessColor | None,
    actual: ProcessColor | None,
    *,
    tolerance: float,
) -> bool:
    if expected is None or actual is None:
        return expected is actual
    if type(expected) is not type(actual):
        return False
    if isinstance(expected, Color) and isinstance(actual, Color):
        expected_values = (expected.red, expected.green, expected.blue)
        actual_values = (actual.red, actual.green, actual.blue)
    elif isinstance(expected, CmykColor) and isinstance(actual, CmykColor):
        expected_values = (expected.cyan, expected.magenta, expected.yellow, expected.black)
        actual_values = (actual.cyan, actual.magenta, actual.yellow, actual.black)
    else:
        return False
    return all(
        math.isclose(left, right, rel_tol=0.0, abs_tol=tolerance)
        for left, right in zip(expected_values, actual_values, strict=True)
    )


def path_geometry_close(expected: AIPath, actual: AIPath, *, tolerance: float) -> bool:
    if len(expected.points) != len(actual.points):
        return False
    expected_origin = expected.points[0]
    actual_origin = actual.points[0]
    for expected_point, actual_point in zip(expected.points, actual.points, strict=True):
        coordinates = (
            (expected_point.x - expected_origin.x, actual_point.x - actual_origin.x),
            (expected_point.y - expected_origin.y, actual_point.y - actual_origin.y),
        )
        if not all(
            math.isclose(left, right, rel_tol=0.0, abs_tol=tolerance) for left, right in coordinates
        ):
            return False
        for expected_handle, actual_handle in (
            (expected_point.in_handle, actual_point.in_handle),
            (expected_point.out_handle, actual_point.out_handle),
        ):
            if expected_handle is None or actual_handle is None:
                if expected_handle is not actual_handle:
                    return False
                continue
            handle_coordinates = (
                (expected_handle.x - expected_point.x, actual_handle.x - actual_point.x),
                (expected_handle.y - expected_point.y, actual_handle.y - actual_point.y),
            )
            if not all(
                math.isclose(left, right, rel_tol=0.0, abs_tol=tolerance)
                for left, right in handle_coordinates
            ):
                return False
        if expected_point.smooth != actual_point.smooth:
            return False
    return True


def angle_close(expected: float, actual: float, *, tolerance: float) -> bool:
    difference = (expected - actual + 180.0) % 360.0 - 180.0
    return abs(difference) <= tolerance


def compare_roundtrip_semantics(
    expected: Document,
    actual: Document,
    *,
    tolerance: float = 1 / 255 + 1e-6,
) -> dict[str, bool]:
    expected_paths = document_paths(expected)
    actual_paths = document_paths(actual)
    expected_compounds = document_compound_paths(expected)
    actual_compounds = document_compound_paths(actual)
    expected_clipping_groups = document_clipping_groups(expected)
    actual_clipping_groups = document_clipping_groups(actual)
    expected_groups = document_groups(expected)
    actual_groups = document_groups(actual)
    expected_text = document_text_frames(expected)
    actual_text = document_text_frames(actual)
    paired_text = list(zip(expected_text, actual_text, strict=False))
    same_text_count = len(expected_text) == len(actual_text)
    paired_paths = list(zip(expected_paths, actual_paths, strict=False))
    same_path_count = len(expected_paths) == len(actual_paths)
    return {
        "layer_count": len(expected.layers) == len(actual.layers),
        "layer_names": [layer.name for layer in expected.layers]
        == [layer.name for layer in actual.layers],
        "layer_visibility": [layer.visible for layer in expected.layers]
        == [layer.visible for layer in actual.layers],
        "layer_item_types": [
            [reference.kind for reference in layer.item_order] for layer in expected.layers
        ]
        == [[reference.kind for reference in layer.item_order] for layer in actual.layers],
        "path_item_count": same_path_count,
        "text_frame_count": same_text_count,
        "text_contents": same_text_count
        and all(left.text == right.text for left, right in paired_text),
        "text_font_sizes": same_text_count
        and all(
            math.isclose(left.font_size, right.font_size, rel_tol=0.0, abs_tol=tolerance)
            for left, right in paired_text
        ),
        "text_font_names": same_text_count
        and all(left.font_name == right.font_name for left, right in paired_text),
        "text_alignments": same_text_count
        and all(left.alignment == right.alignment for left, right in paired_text),
        "text_trackings": same_text_count
        and all(
            math.isclose(left.tracking, right.tracking, rel_tol=0.0, abs_tol=tolerance)
            for left, right in paired_text
        ),
        "text_rotations": same_text_count
        and all(
            angle_close(left.rotation, right.rotation, tolerance=tolerance)
            for left, right in paired_text
        ),
        "text_fill_colors": same_text_count
        and all(
            color_close(left.fill, right.fill, tolerance=tolerance) for left, right in paired_text
        ),
        "text_positions": same_text_count
        and (
            not expected_text
            or all(
                math.isclose(
                    left.x - expected_text[0].x,
                    right.x - actual_text[0].x,
                    rel_tol=0.0,
                    abs_tol=tolerance,
                )
                and math.isclose(
                    left.y - expected_text[0].y,
                    right.y - actual_text[0].y,
                    rel_tol=0.0,
                    abs_tol=tolerance,
                )
                for left, right in paired_text
            )
        ),
        "path_ids": same_path_count and all(left.id == right.id for left, right in paired_paths),
        "path_names": same_path_count
        and all(left.name == right.name for left, right in paired_paths),
        "point_counts": same_path_count
        and all(len(left.points) == len(right.points) for left, right in paired_paths),
        "path_flags": same_path_count
        and all(
            (left.closed, left.fill is not None, left.stroke is not None)
            == (right.closed, right.fill is not None, right.stroke is not None)
            for left, right in paired_paths
        ),
        "path_polarities": same_path_count
        and all(left.polarity == right.polarity for left, right in paired_paths),
        "compound_path_count": len(expected_compounds) == len(actual_compounds),
        "compound_component_counts": len(expected_compounds) == len(actual_compounds)
        and all(
            len(left.paths) == len(right.paths)
            for left, right in zip(expected_compounds, actual_compounds, strict=True)
        ),
        "clipping_group_count": len(expected_clipping_groups) == len(actual_clipping_groups),
        "clipping_content_counts": len(expected_clipping_groups) == len(actual_clipping_groups)
        and all(
            len(left.paths) == len(right.paths)
            for left, right in zip(expected_clipping_groups, actual_clipping_groups, strict=True)
        ),
        "group_item_count": len(expected_groups) == len(actual_groups),
        "group_structure": len(expected_groups) == len(actual_groups)
        and [group_signature(group) for group in expected_groups]
        == [group_signature(group) for group in actual_groups],
        "stroke_widths": same_path_count
        and all(
            math.isclose(left.stroke_width, right.stroke_width, rel_tol=0.0, abs_tol=tolerance)
            for left, right in paired_paths
        ),
        "dash_patterns": same_path_count
        and all(left.dash_pattern == right.dash_pattern for left, right in paired_paths),
        "dash_offsets": same_path_count
        and all(
            math.isclose(left.dash_offset, right.dash_offset, rel_tol=0.0, abs_tol=tolerance)
            for left, right in paired_paths
        ),
        "line_caps": same_path_count
        and all(left.line_cap == right.line_cap for left, right in paired_paths),
        "line_joins": same_path_count
        and all(left.line_join == right.line_join for left, right in paired_paths),
        "miter_limits": same_path_count
        and all(
            math.isclose(left.miter_limit, right.miter_limit, rel_tol=0.0, abs_tol=tolerance)
            for left, right in paired_paths
        ),
        "fill_colors": same_path_count
        and all(
            color_close(left.fill, right.fill, tolerance=tolerance) for left, right in paired_paths
        ),
        "stroke_colors": same_path_count
        and all(
            color_close(left.stroke, right.stroke, tolerance=tolerance)
            for left, right in paired_paths
        ),
        "path_geometry": same_path_count
        and all(
            path_geometry_close(left, right, tolerance=tolerance) for left, right in paired_paths
        ),
    }
