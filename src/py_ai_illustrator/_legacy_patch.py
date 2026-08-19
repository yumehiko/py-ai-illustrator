"""Typed patch operations and lossless replacement planning for legacy data."""

from __future__ import annotations

import base64
import hashlib
import json
import math
import re
from dataclasses import dataclass
from typing import Literal

from ._legacy_codec import (
    UnsupportedLegacyFeature,
    _color_operator,
    _escape_postscript_text,
    _number,
)
from .compatibility import LegacyFieldOrigin, LegacyNodeOrigin, LegacyReadResult
from .lossless import LegacySource, SourceReplacement
from .model import (
    ClippingGroup,
    CompoundPath,
    Group,
    Layer,
    LinkedImage,
    Path,
    Point,
    ProcessColor,
    TextFrame,
)

_NUMBER = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?"
_POINT_RE = re.compile(rf"^({_NUMBER})\s+({_NUMBER})\s+([mLl])$")
_CUBIC_RE = re.compile(
    rf"^({_NUMBER})\s+({_NUMBER})\s+({_NUMBER})\s+({_NUMBER})\s+"
    rf"({_NUMBER})\s+({_NUMBER})\s+([cC])$"
)
_SHORT_CUBIC_RE = re.compile(
    rf"^({_NUMBER})\s+({_NUMBER})\s+({_NUMBER})\s+({_NUMBER})\s+([vVyY])$"
)
@dataclass(frozen=True, slots=True)
class SetPathFill:
    """Typed local edit with an explicit semantic precondition."""

    path_id: str
    fill: ProcessColor
    expected_fill: ProcessColor
    origin_start: int | None = None

    def __post_init__(self) -> None:
        if not self.path_id:
            raise ValueError("path_id must not be empty")


@dataclass(frozen=True, slots=True)
class SetPathStroke:
    """Typed local stroke-color edit with an explicit semantic precondition."""

    path_id: str
    stroke: ProcessColor
    expected_stroke: ProcessColor
    origin_start: int | None = None

    def __post_init__(self) -> None:
        if not self.path_id:
            raise ValueError("path_id must not be empty")


@dataclass(frozen=True, slots=True)
class TranslatePath:
    """Typed local path translation with an explicit geometry precondition."""

    path_id: str
    dx: float
    dy: float
    expected_points: tuple[Point, ...]
    origin_start: int | None = None

    def __post_init__(self) -> None:
        if not self.path_id:
            raise ValueError("path_id must not be empty")
        if not math.isfinite(self.dx) or not math.isfinite(self.dy):
            raise ValueError("translation offsets must be finite")
        if not self.expected_points:
            raise ValueError("expected_points must not be empty")


ContainerType = Literal["layer", "group", "compound_path", "clipping_group"]
TranslationMember = tuple[Literal["path", "text", "linked_image"], str]


@dataclass(frozen=True, slots=True)
class TranslateContainer:
    """Translate all leaf artwork in a source-backed container."""

    container_type: ContainerType
    container_id: str
    dx: float
    dy: float
    expected_members: frozenset[TranslationMember]

    def __post_init__(self) -> None:
        if self.container_type not in {"layer", "group", "compound_path", "clipping_group"}:
            raise ValueError("container_type is not translatable")
        if not self.container_id:
            raise ValueError("container_id must not be empty")
        if not math.isfinite(self.dx) or not math.isfinite(self.dy):
            raise ValueError("translation offsets must be finite")
        if not self.expected_members:
            raise ValueError("expected_members must not be empty")
        if any(
            kind not in {"path", "text", "linked_image"} or not node_id
            for kind, node_id in self.expected_members
        ):
            raise ValueError("expected_members must contain supported node types and non-empty ids")


@dataclass(frozen=True, slots=True)
class ReplaceLinkedImageSource:
    """Typed local linked-image source edit with an explicit precondition."""

    image_id: str
    source: str
    expected_source: str
    origin_start: int | None = None

    def __post_init__(self) -> None:
        if not self.image_id:
            raise ValueError("image_id must not be empty")
        if not self.source or "\x00" in self.source:
            raise ValueError("source must be a non-empty path without NUL bytes")
        if not self.expected_source or "\x00" in self.expected_source:
            raise ValueError("expected_source must be a non-empty path without NUL bytes")


@dataclass(frozen=True, slots=True)
class ReplaceText:
    """Typed local text edit with an explicit semantic precondition."""

    text_id: str
    text: str
    expected_text: str
    origin_start: int | None = None

    def __post_init__(self) -> None:
        if not self.text_id:
            raise ValueError("text_id must not be empty")


LegacyPatchOperation = (
    SetPathFill
    | SetPathStroke
    | TranslatePath
    | TranslateContainer
    | ReplaceLinkedImageSource
    | ReplaceText
)


@dataclass(frozen=True, slots=True)
class LegacyPatchPlan:
    """Validated, non-conflicting replacements against one exact source."""

    source_sha256: str
    source_size: int
    operation_count: int
    replacements: tuple[SourceReplacement, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "source_sha256": self.source_sha256,
            "source_size": self.source_size,
            "operation_count": self.operation_count,
            "replacement_count": len(self.replacements),
            "replacements": [
                {
                    "span": {"start": replacement.start, "end": replacement.end},
                    "replacement_size": len(replacement.data),
                }
                for replacement in self.replacements
            ],
        }


def _container_paths(container: Layer | Group) -> list[Path]:
    paths = [
        *container.paths,
        *(path for compound in container.compound_paths for path in compound.paths),
        *(
            path
            for clipping in container.clipping_groups
            for path in [clipping.clipping_path, *clipping.paths]
        ),
    ]
    for group in container.groups:
        paths.extend(_container_paths(group))
    return paths


def _container_text_frames(container: Layer | Group) -> list[TextFrame]:
    text_frames = list(container.text_frames)
    for group in container.groups:
        text_frames.extend(_container_text_frames(group))
    return text_frames


def _container_linked_images(container: Layer | Group) -> list[LinkedImage]:
    linked_images = list(container.linked_images)
    for group in container.groups:
        linked_images.extend(_container_linked_images(group))
    return linked_images


def _matching_paths(result: LegacyReadResult, path_id: str) -> list[Path]:
    return [
        path
        for layer in result.document.layers
        for path in _container_paths(layer)
        if path.id == path_id
    ]


LegacyContainer = Layer | Group | CompoundPath | ClippingGroup


def _nested_groups(container: Layer | Group) -> list[Group]:
    groups = list(container.groups)
    for group in container.groups:
        groups.extend(_nested_groups(group))
    return groups


def _container_candidates(
    result: LegacyReadResult, container_type: ContainerType
) -> list[LegacyContainer]:
    if container_type == "layer":
        return list(result.document.layers)
    groups = [group for layer in result.document.layers for group in _nested_groups(layer)]
    if container_type == "group":
        return groups
    containers: list[Layer | Group] = [*result.document.layers, *groups]
    if container_type == "compound_path":
        return [compound for container in containers for compound in container.compound_paths]
    return [clipping for container in containers for clipping in container.clipping_groups]


def _translation_leaves(
    container: LegacyContainer,
) -> tuple[list[Path], list[TextFrame], list[LinkedImage]]:
    if isinstance(container, (Layer, Group)):
        return (
            _container_paths(container),
            _container_text_frames(container),
            _container_linked_images(container),
        )
    if isinstance(container, CompoundPath):
        return list(container.paths), [], []
    return [container.clipping_path, *container.paths], [], []


def _translation_members(container: LegacyContainer) -> list[TranslationMember]:
    paths, text_frames, images = _translation_leaves(container)
    return [
        *(("path", path.id) for path in paths),
        *(("text", text.id) for text in text_frames),
        *(("linked_image", image.id) for image in images),
    ]


def _unique_origin(
    result: LegacyReadResult,
    *,
    node_type: str,
    node_id: str,
    origin_start: int | None = None,
) -> LegacyNodeOrigin:
    matching_origins = [
        origin
        for origin in result.origins
        if origin.node_type == node_type and origin.node_id == node_id
        and (origin_start is None or origin.start == origin_start)
    ]
    if len(matching_origins) != 1:
        raise UnsupportedLegacyFeature(
            f"{node_type.capitalize()} {node_id!r} has {len(matching_origins)} source origins; "
            "exactly one is required."
        )
    return matching_origins[0]


def _validate_patch_field(
    result: LegacyReadResult,
    *,
    origin: LegacyNodeOrigin,
    field_name: str,
    node_label: str,
) -> LegacyFieldOrigin:
    field_origin = origin.field(field_name)
    if field_origin is None:
        raise UnsupportedLegacyFeature(
            f"{node_label} does not have an exclusive source {field_name} span."
        )
    actual = result.source.data[field_origin.start : field_origin.end]
    if actual != field_origin.expected:
        raise UnsupportedLegacyFeature(
            f"{node_label} source precondition failed; the {field_name} span changed."
        )
    intersecting = [
        diagnostic
        for diagnostic in result.diagnostics
        if diagnostic.start < field_origin.end and diagnostic.end > field_origin.start
    ]
    if intersecting:
        raise UnsupportedLegacyFeature(
            f"{node_label} {field_name} span intersects unsupported source syntax."
        )
    return field_origin


def _validate_patch_origin(
    result: LegacyReadResult, *, origin: LegacyNodeOrigin, node_label: str
) -> None:
    intersecting = [
        diagnostic
        for diagnostic in result.diagnostics
        if diagnostic.start < origin.end and diagnostic.end > origin.start
    ]
    if intersecting:
        raise UnsupportedLegacyFeature(
            f"{node_label} source span intersects unsupported source syntax."
        )


def _path_fill_replacements(
    result: LegacyReadResult, operation: SetPathFill
) -> list[SourceReplacement]:
    matching_paths = _matching_paths(result, operation.path_id)
    if operation.origin_start is None and len(matching_paths) != 1:
        raise UnsupportedLegacyFeature(
            f"Path selector id={operation.path_id!r} matched {len(matching_paths)} nodes; "
            "exactly one is required."
        )
    if not any(path.fill == operation.expected_fill for path in matching_paths):
        raise UnsupportedLegacyFeature(
            f"Path {operation.path_id!r} fill precondition failed: "
            f"expected {operation.expected_fill!r}."
        )

    origin = _unique_origin(
        result,
        node_type="path",
        node_id=operation.path_id,
        origin_start=operation.origin_start,
    )
    fill_origin = _validate_patch_field(
        result,
        origin=origin,
        field_name="fill",
        node_label=f"Path {operation.path_id!r}",
    )

    replacement = _color_operator(operation.fill, stroke=False).encode("ascii")
    return [SourceReplacement(fill_origin.start, fill_origin.end, replacement)]


def _path_stroke_replacements(
    result: LegacyReadResult, operation: SetPathStroke
) -> list[SourceReplacement]:
    matching_paths = _matching_paths(result, operation.path_id)
    if operation.origin_start is None and len(matching_paths) != 1:
        raise UnsupportedLegacyFeature(
            f"Path selector id={operation.path_id!r} matched {len(matching_paths)} nodes; "
            "exactly one is required."
        )
    if not any(path.stroke == operation.expected_stroke for path in matching_paths):
        raise UnsupportedLegacyFeature(
            f"Path {operation.path_id!r} stroke precondition failed: "
            f"expected {operation.expected_stroke!r}."
        )

    origin = _unique_origin(
        result,
        node_type="path",
        node_id=operation.path_id,
        origin_start=operation.origin_start,
    )
    stroke_origin = _validate_patch_field(
        result,
        origin=origin,
        field_name="stroke",
        node_label=f"Path {operation.path_id!r}",
    )

    replacement = _color_operator(operation.stroke, stroke=True).encode("ascii")
    return [SourceReplacement(stroke_origin.start, stroke_origin.end, replacement)]


def _translated_geometry_statement(statement: bytes, *, dx: float, dy: float) -> bytes:
    line = statement.decode("latin-1")
    point_match = _POINT_RE.fullmatch(line)
    if point_match:
        return (
            f"{_number(float(point_match.group(1)) + dx)} "
            f"{_number(float(point_match.group(2)) + dy)} {point_match.group(3)}"
        ).encode("ascii")

    cubic_match = _CUBIC_RE.fullmatch(line)
    if cubic_match:
        values = [float(cubic_match.group(index)) for index in range(1, 7)]
        translated = [
            value + (dx if index % 2 == 0 else dy) for index, value in enumerate(values)
        ]
        return " ".join(
            [*(_number(value) for value in translated), cubic_match.group(7)]
        ).encode("ascii")

    short_cubic_match = _SHORT_CUBIC_RE.fullmatch(line)
    if short_cubic_match:
        values = [float(short_cubic_match.group(index)) for index in range(1, 5)]
        translated = [
            value + (dx if index % 2 == 0 else dy) for index, value in enumerate(values)
        ]
        return " ".join(
            [*(_number(value) for value in translated), short_cubic_match.group(5)]
        ).encode("ascii")

    raise UnsupportedLegacyFeature(
        "Path geometry source precondition failed; a geometry statement is no longer recognized."
    )


def _path_translation_replacements(
    result: LegacyReadResult, operation: TranslatePath
) -> list[SourceReplacement]:
    matching_paths = _matching_paths(result, operation.path_id)
    if operation.origin_start is None and len(matching_paths) != 1:
        raise UnsupportedLegacyFeature(
            f"Path selector id={operation.path_id!r} matched {len(matching_paths)} nodes; "
            "exactly one is required."
        )
    if not any(tuple(path.points) == operation.expected_points for path in matching_paths):
        raise UnsupportedLegacyFeature(
            f"Path {operation.path_id!r} geometry precondition failed: expected points do not "
            "match the parsed path."
        )

    origin = _unique_origin(
        result,
        node_type="path",
        node_id=operation.path_id,
        origin_start=operation.origin_start,
    )
    _validate_patch_origin(result, origin=origin, node_label=f"Path {operation.path_id!r}")
    return _geometry_translation_replacements(
        result,
        origin=origin,
        node_label=f"Path {operation.path_id!r}",
        dx=operation.dx,
        dy=operation.dy,
    )


def _geometry_translation_replacements(
    result: LegacyReadResult,
    *,
    origin: LegacyNodeOrigin,
    node_label: str,
    dx: float,
    dy: float,
) -> list[SourceReplacement]:
    geometry_origins = origin.fields_with_prefix("geometry.")
    if not geometry_origins:
        raise UnsupportedLegacyFeature(f"{node_label} does not have local source geometry spans.")
    replacements: list[SourceReplacement] = []
    for index, geometry_origin in enumerate(geometry_origins):
        if geometry_origin.field != f"geometry.{index}":
            raise UnsupportedLegacyFeature(f"{node_label} has incomplete source geometry spans.")
        validated = _validate_patch_field(
            result,
            origin=origin,
            field_name=geometry_origin.field,
            node_label=node_label,
        )
        replacements.append(
            SourceReplacement(
                validated.start,
                validated.end,
                (
                    validated.expected
                    if dx == 0 and dy == 0
                    else _translated_geometry_statement(validated.expected, dx=dx, dy=dy)
                ),
            )
        )
    return replacements


def _translated_text_position(statement: bytes, *, dx: float, dy: float) -> bytes:
    parts = statement.decode("latin-1").split()
    recognized = (parts[-1:] == ["Tp"] and len(parts) == 8) or (
        parts[-1:] == ["Tm"] and len(parts) == 7
    )
    if not recognized:
        raise UnsupportedLegacyFeature(
            "Text position source precondition failed; the statement is no longer recognized."
        )
    x_index, y_index = 4, 5
    try:
        parts[x_index] = _number(float(parts[x_index]) + dx)
        parts[y_index] = _number(float(parts[y_index]) + dy)
    except ValueError as error:
        raise UnsupportedLegacyFeature(
            "Text position source precondition failed; coordinates are invalid."
        ) from error
    return " ".join(parts).encode("ascii")


def _text_translation_replacements(
    result: LegacyReadResult,
    *,
    origin: LegacyNodeOrigin,
    text_id: str,
    dx: float,
    dy: float,
) -> list[SourceReplacement]:
    node_label = f"Text {text_id!r}"
    replacements: list[SourceReplacement] = []
    for field_name in ("position", "matrix"):
        field = _validate_patch_field(
            result,
            origin=origin,
            field_name=field_name,
            node_label=node_label,
        )
        replacements.append(
            SourceReplacement(
                field.start,
                field.end,
                (
                    field.expected
                    if dx == 0 and dy == 0
                    else _translated_text_position(field.expected, dx=dx, dy=dy)
                ),
            )
        )
    return replacements


def _image_translation_replacements(
    result: LegacyReadResult,
    *,
    origin: LegacyNodeOrigin,
    image: LinkedImage,
    dx: float,
    dy: float,
) -> list[SourceReplacement]:
    node_label = f"Linked image {image.id!r}"
    metadata = _validate_patch_field(
        result,
        origin=origin,
        field_name="metadata",
        node_label=node_label,
    )
    try:
        payload = json.loads(base64.b64decode(metadata.expected, validate=True).decode("utf-8"))
    except (ValueError, UnicodeError, json.JSONDecodeError) as error:
        raise UnsupportedLegacyFeature(f"{node_label} metadata precondition failed.") from error
    if (
        not isinstance(payload, dict)
        or payload.get("id") != image.id
        or payload.get("x") != image.x
        or payload.get("y") != image.y
    ):
        raise UnsupportedLegacyFeature(f"{node_label} metadata precondition failed.")
    payload["x"] = image.x + dx
    payload["y"] = image.y + dy
    replacement = base64.b64encode(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    )
    return [
        SourceReplacement(
            metadata.start,
            metadata.end,
            metadata.expected if dx == 0 and dy == 0 else replacement,
        ),
        *_geometry_translation_replacements(
            result,
            origin=origin,
            node_label=node_label,
            dx=dx,
            dy=dy,
        ),
    ]


def _container_translation_replacements(
    result: LegacyReadResult, operation: TranslateContainer
) -> list[SourceReplacement]:
    matching = [
        container
        for container in _container_candidates(result, operation.container_type)
        if container.id == operation.container_id
    ]
    if len(matching) != 1:
        raise UnsupportedLegacyFeature(
            f"{operation.container_type} selector id={operation.container_id!r} matched "
            f"{len(matching)} nodes; exactly one is required."
        )
    container = matching[0]
    members = _translation_members(container)
    member_set = frozenset(members)
    if len(member_set) != len(members):
        raise UnsupportedLegacyFeature(
            f"Container {operation.container_id!r} has duplicate leaf ids; "
            "translation is ambiguous."
        )
    if member_set != operation.expected_members:
        raise UnsupportedLegacyFeature(
            f"Container {operation.container_id!r} members precondition failed: expected "
            f"{sorted(operation.expected_members)!r}, found {sorted(member_set)!r}."
        )

    container_origin = _unique_origin(
        result,
        node_type=operation.container_type,
        node_id=operation.container_id,
    )
    _validate_patch_origin(
        result,
        origin=container_origin,
        node_label=f"Container {operation.container_id!r}",
    )
    paths, text_frames, images = _translation_leaves(container)
    replacements: list[SourceReplacement] = []
    for node_type, nodes in (
        ("path", paths),
        ("text", text_frames),
        ("linked_image", images),
    ):
        for node in nodes:
            origin = _unique_origin(result, node_type=node_type, node_id=node.id)
            if origin.start < container_origin.start or origin.end > container_origin.end:
                raise UnsupportedLegacyFeature(
                    f"Container {operation.container_id!r} member {node.id!r} has an "
                    "out-of-range source span."
                )
            _validate_patch_origin(
                result,
                origin=origin,
                node_label=f"{node_type} {node.id!r}",
            )
            if isinstance(node, Path):
                replacements.extend(
                    _geometry_translation_replacements(
                        result,
                        origin=origin,
                        node_label=f"Path {node.id!r}",
                        dx=operation.dx,
                        dy=operation.dy,
                    )
                )
            elif isinstance(node, TextFrame):
                replacements.extend(
                    _text_translation_replacements(
                        result,
                        origin=origin,
                        text_id=node.id,
                        dx=operation.dx,
                        dy=operation.dy,
                    )
                )
            else:
                replacements.extend(
                    _image_translation_replacements(
                        result,
                        origin=origin,
                        image=node,
                        dx=operation.dx,
                        dy=operation.dy,
                    )
                )
    return replacements


def _linked_image_source_replacements(
    result: LegacyReadResult, operation: ReplaceLinkedImageSource
) -> list[SourceReplacement]:
    matching_images = [
        image
        for layer in result.document.layers
        for image in _container_linked_images(layer)
        if image.id == operation.image_id
    ]
    if len(matching_images) != 1:
        raise UnsupportedLegacyFeature(
            f"Linked image selector id={operation.image_id!r} matched {len(matching_images)} "
            "nodes; exactly one is required."
        )
    image = matching_images[0]
    if image.source != operation.expected_source:
        raise UnsupportedLegacyFeature(
            f"Linked image {operation.image_id!r} source precondition failed: "
            f"expected {operation.expected_source!r}, found {image.source!r}."
        )

    origin = _unique_origin(result, node_type="linked_image", node_id=operation.image_id)
    metadata_origin = _validate_patch_field(
        result,
        origin=origin,
        field_name="metadata",
        node_label=f"Linked image {operation.image_id!r}",
    )
    try:
        decoded = base64.b64decode(metadata_origin.expected, validate=True).decode("utf-8")
        payload = json.loads(decoded)
    except (ValueError, UnicodeError, json.JSONDecodeError) as error:
        raise UnsupportedLegacyFeature(
            f"Linked image {operation.image_id!r} metadata precondition failed."
        ) from error
    if (
        not isinstance(payload, dict)
        or payload.get("id") != operation.image_id
        or payload.get("source") != operation.expected_source
    ):
        raise UnsupportedLegacyFeature(
            f"Linked image {operation.image_id!r} metadata precondition failed."
        )

    payload["source"] = operation.source
    replacement = base64.b64encode(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    )
    return [SourceReplacement(metadata_origin.start, metadata_origin.end, replacement)]


def _text_replacements(
    result: LegacyReadResult, operation: ReplaceText
) -> list[SourceReplacement]:
    matching_text_frames = [
        text_frame
        for layer in result.document.layers
        for text_frame in _container_text_frames(layer)
        if text_frame.id == operation.text_id
    ]
    if len(matching_text_frames) != 1:
        raise UnsupportedLegacyFeature(
            f"Text selector id={operation.text_id!r} matched {len(matching_text_frames)} nodes; "
            "exactly one is required."
        )
    text_frame = matching_text_frames[0]
    if text_frame.text != operation.expected_text:
        raise UnsupportedLegacyFeature(
            f"Text {operation.text_id!r} content precondition failed: "
            f"expected {operation.expected_text!r}, found {text_frame.text!r}."
        )

    origin = _unique_origin(result, node_type="text", node_id=operation.text_id)
    text_origin = _validate_patch_field(
        result,
        origin=origin,
        field_name="text",
        node_label=f"Text {operation.text_id!r}",
    )
    replacement = _escape_postscript_text(
        operation.text, font_name=text_frame.font_name
    ).encode("ascii")
    return [SourceReplacement(text_origin.start, text_origin.end, replacement)]


def _operation_replacements(
    result: LegacyReadResult, operation: LegacyPatchOperation
) -> list[SourceReplacement]:
    if isinstance(operation, SetPathFill):
        return _path_fill_replacements(result, operation)
    if isinstance(operation, SetPathStroke):
        return _path_stroke_replacements(result, operation)
    if isinstance(operation, TranslatePath):
        return _path_translation_replacements(result, operation)
    if isinstance(operation, TranslateContainer):
        return _container_translation_replacements(result, operation)
    if isinstance(operation, ReplaceLinkedImageSource):
        return _linked_image_source_replacements(result, operation)
    if isinstance(operation, ReplaceText):
        return _text_replacements(result, operation)
    raise TypeError(f"Unsupported legacy patch operation: {type(operation).__name__}")


def _validated_replacements(
    replacements: list[SourceReplacement] | tuple[SourceReplacement, ...],
    *,
    source_size: int,
) -> tuple[SourceReplacement, ...]:
    ordered = tuple(
        sorted(replacements, key=lambda replacement: (replacement.start, replacement.end))
    )
    previous: SourceReplacement | None = None
    for replacement in ordered:
        if replacement.end > source_size:
            raise UnsupportedLegacyFeature(
                f"Patch span [{replacement.start}, {replacement.end}) exceeds source size "
                f"{source_size}."
            )
        if previous is not None:
            overlaps = replacement.start < previous.end
            same_insertion_point = (
                replacement.start == previous.start
                and (replacement.start == replacement.end or previous.start == previous.end)
            )
            if overlaps or same_insertion_point:
                raise UnsupportedLegacyFeature(
                    "Patch operations conflict at source spans "
                    f"[{previous.start}, {previous.end}) and "
                    f"[{replacement.start}, {replacement.end})."
                )
        previous = replacement
    return ordered


def plan_legacy_patch(
    result: LegacyReadResult, operations: tuple[LegacyPatchOperation, ...]
) -> LegacyPatchPlan:
    """Validate typed operations and produce one conflict-free patch plan."""

    if not operations:
        raise ValueError("operations must not be empty")
    replacements = [
        replacement
        for operation in operations
        for replacement in _operation_replacements(result, operation)
    ]
    ordered = _validated_replacements(replacements, source_size=len(result.source.data))
    return LegacyPatchPlan(
        source_sha256=hashlib.sha256(result.source.data).hexdigest(),
        source_size=len(result.source.data),
        operation_count=len(operations),
        replacements=ordered,
    )


def apply_legacy_patch(result: LegacyReadResult, plan: LegacyPatchPlan) -> LegacySource:
    """Apply a plan only when the complete source precondition still matches."""

    actual_digest = hashlib.sha256(result.source.data).hexdigest()
    if len(result.source.data) != plan.source_size or actual_digest != plan.source_sha256:
        raise UnsupportedLegacyFeature(
            "Patch source precondition failed; the complete source changed after planning."
        )
    replacements = _validated_replacements(
        plan.replacements,
        source_size=len(result.source.data),
    )
    try:
        return result.source.patched(list(replacements))
    except ValueError as error:
        raise UnsupportedLegacyFeature(
            "Patch plan contains conflicting or out-of-range replacements."
        ) from error


def patch_legacy(
    result: LegacyReadResult, operations: tuple[LegacyPatchOperation, ...]
) -> LegacySource:
    """Plan and atomically apply one or more typed legacy operations."""

    return apply_legacy_patch(result, plan_legacy_patch(result, operations))


def patch_path_fill(result: LegacyReadResult, operation: SetPathFill) -> LegacySource:
    """Patch one uniquely selected path fill while preserving all other source bytes."""

    return patch_legacy(result, (operation,))
def patch_path_stroke(result: LegacyReadResult, operation: SetPathStroke) -> LegacySource:
    """Patch one uniquely selected path stroke while preserving all other source bytes."""

    return patch_legacy(result, (operation,))

def patch_path_translate(result: LegacyReadResult, operation: TranslatePath) -> LegacySource:
    """Translate one path through statement-local replacements."""

    return patch_legacy(result, (operation,))


def patch_container_translate(
    result: LegacyReadResult, operation: TranslateContainer
) -> LegacySource:
    """Translate one uniquely selected container through leaf field replacements."""

    return patch_legacy(result, (operation,))


def patch_linked_image_source(
    result: LegacyReadResult, operation: ReplaceLinkedImageSource
) -> LegacySource:
    """Patch one linked-image source in its private legacy metadata."""

    return patch_legacy(result, (operation,))


def patch_text(result: LegacyReadResult, operation: ReplaceText) -> LegacySource:
    """Patch one uniquely selected text frame while preserving all other source bytes."""

    return patch_legacy(result, (operation,))
