"""Operation planning and apply orchestration implementation.

The public ``editing`` module is a compatibility facade.  This backend owns
selector resolution, profile-specific planning, apply orchestration, and
post-apply checks while delegating modern target discovery and patching to
their dedicated boundaries.
"""

from __future__ import annotations

import hashlib
import math
import tempfile
from dataclasses import asdict, dataclass, replace
from pathlib import Path as FilePath
from typing import Any, Literal

from ._modern_discovery import (
    inspect_modern_container_translate_targets,
    inspect_modern_fill_targets,
    inspect_modern_stroke_targets,
    inspect_modern_text_targets,
    inspect_modern_translate_targets,
)
from ._modern_patch import (
    ModernWriteError,
    patch_modern_path_fill,
    patch_modern_path_stroke,
    patch_modern_path_translate,
    patch_modern_text,
)
from .compatibility import LEGACY_FEATURE_PROFILE_ID, LegacyReadResult
from .format import FileFormat, inspect_file
from .legacy import (
    LegacyPatchOperation,
    LegacyPatchPlan,
    ReplaceLinkedImageSource,
    ReplaceText,
    SetPathFill,
    SetPathStroke,
    TranslateContainer,
    TranslatePath,
    UnsupportedLegacyFeature,
    apply_legacy_patch,
    plan_legacy_patch,
    read_ai7,
    reads_ai7,
)
from .model import (
    ClippingGroup,
    CmykColor,
    Color,
    CompoundPath,
    ControlPoint,
    Group,
    Layer,
    LinkedImage,
    Path,
    Point,
    ProcessColor,
    TextFrame,
)
from .semantic import SemanticDiff, SemanticDifference, semantic_diff
from .verification import extract_pdf_display, visual_diff

SelectorType = Literal[
    "path",
    "text",
    "linked_image",
    "layer",
    "group",
    "compound_path",
    "clipping_group",
]
OperationName = Literal[
    "set_fill",
    "set_stroke",
    "replace_text",
    "translate",
    "replace_linked_image_source",
]
ContainerSelectorType = Literal["layer", "group", "compound_path", "clipping_group"]

_SELECTOR_TYPES = frozenset(
    {
        "path",
        "text",
        "linked_image",
        "layer",
        "group",
        "compound_path",
        "clipping_group",
    }
)
_CONTAINER_TYPES = frozenset({"layer", "group", "compound_path", "clipping_group"})
_SHA256_LENGTH = 64


class OperationRequestError(ValueError):
    """Raised when a public operation manifest is not valid schema version 1."""


@dataclass(frozen=True, slots=True)
class AncestorSelector:
    type: SelectorType
    id: str

    @classmethod
    def from_dict(cls, data: object, *, location: str) -> AncestorSelector:
        mapping = _mapping(data, location=location, required={"type", "id"})
        node_type = mapping["type"]
        node_id = mapping["id"]
        if not isinstance(node_type, str) or node_type not in _CONTAINER_TYPES:
            raise OperationRequestError(f"{location}.type must be a container selector type")
        if not isinstance(node_id, str) or not node_id:
            raise OperationRequestError(f"{location}.id must be a non-empty string")
        return cls(type=node_type, id=node_id)  # type: ignore[arg-type]

    def to_dict(self) -> dict[str, str]:
        return {"type": self.type, "id": self.id}


@dataclass(frozen=True, slots=True)
class Selector:
    """A conjunctive safe selector; every supplied field must match exactly."""

    type: SelectorType
    id: str | None = None
    name: str | None = None
    bounds: tuple[float, float, float, float] | None = None
    tolerance: float = 0.0
    ancestors: tuple[AncestorSelector, ...] = ()

    @classmethod
    def from_dict(cls, data: object, *, location: str) -> Selector:
        if not isinstance(data, dict):
            raise OperationRequestError(f"{location} must be an object")
        allowed = {"type", "id", "name", "bounds", "tolerance", "ancestors"}
        unexpected = sorted(set(data) - allowed)
        if unexpected:
            raise OperationRequestError(_key_error(location, [], unexpected))
        if "type" not in data:
            raise OperationRequestError(_key_error(location, ["type"], []))
        mapping = data
        node_type = mapping["type"]
        if not isinstance(node_type, str) or node_type not in _SELECTOR_TYPES:
            raise OperationRequestError(f"{location}.type is not a supported selector type")
        node_id = mapping.get("id")
        if node_id is not None and (not isinstance(node_id, str) or not node_id):
            raise OperationRequestError(f"{location}.id must be a non-empty string")
        name = mapping.get("name")
        if name is not None and (not isinstance(name, str) or not name):
            raise OperationRequestError(f"{location}.name must be a non-empty string")
        bounds_value = mapping.get("bounds")
        bounds: tuple[float, float, float, float] | None = None
        if bounds_value is not None:
            if not isinstance(bounds_value, list) or len(bounds_value) != 4:
                raise OperationRequestError(f"{location}.bounds must be a four-number array")
            bounds = tuple(
                _finite_number(value, location=f"{location}.bounds[{index}]")
                for index, value in enumerate(bounds_value)
            )  # type: ignore[assignment]
            if bounds[0] > bounds[2] or bounds[1] > bounds[3]:
                raise OperationRequestError(
                    f"{location}.bounds must be ordered [left, bottom, right, top]"
                )
        tolerance = _finite_number(
            mapping.get("tolerance", 0.0), location=f"{location}.tolerance"
        )
        if tolerance < 0:
            raise OperationRequestError(f"{location}.tolerance must be non-negative")
        ancestors_value = mapping.get("ancestors", [])
        if not isinstance(ancestors_value, list):
            raise OperationRequestError(f"{location}.ancestors must be an array")
        ancestors = tuple(
            AncestorSelector.from_dict(item, location=f"{location}.ancestors[{index}]")
            for index, item in enumerate(ancestors_value)
        )
        if node_id is None and name is None and bounds is None and not ancestors:
            raise OperationRequestError(
                f"{location} must include id, name, bounds, or ancestors in addition to type"
            )
        return cls(  # type: ignore[arg-type]
            type=node_type,
            id=node_id,
            name=name,
            bounds=bounds,
            tolerance=tolerance,
            ancestors=ancestors,
        )

    def to_dict(self) -> dict[str, object]:
        result: dict[str, object] = {"type": self.type}
        if self.id is not None:
            result["id"] = self.id
        if self.name is not None:
            result["name"] = self.name
        if self.bounds is not None:
            result["bounds"] = list(self.bounds)
            if self.tolerance:
                result["tolerance"] = self.tolerance
        if self.ancestors:
            result["ancestors"] = [ancestor.to_dict() for ancestor in self.ancestors]
        return result


@dataclass(frozen=True, slots=True)
class OperationRequest:
    """One validated, high-level operation request without internal preconditions."""

    op: OperationName
    selector: Selector
    color: ProcessColor | None = None
    text: str | None = None
    source: str | None = None
    dx: float | None = None
    dy: float | None = None

    @classmethod
    def from_dict(cls, data: object, *, index: int) -> OperationRequest:
        location = f"operations[{index}]"
        if not isinstance(data, dict):
            raise OperationRequestError(f"{location} must be an object")
        op = data.get("op")
        if not isinstance(op, str):
            raise OperationRequestError(f"{location}.op must be a string")
        selector = Selector.from_dict(data.get("selector"), location=f"{location}.selector")
        if op in {"set_fill", "set_stroke"}:
            mapping = _mapping(data, location=location, required={"op", "selector", "color"})
            return cls(
                op=op,  # type: ignore[arg-type]
                selector=selector,
                color=_parse_color(mapping["color"], location=f"{location}.color"),
            )
        if op == "replace_text":
            mapping = _mapping(data, location=location, required={"op", "selector", "text"})
            text = mapping["text"]
            if not isinstance(text, str):
                raise OperationRequestError(f"{location}.text must be a string")
            return cls(op="replace_text", selector=selector, text=text)
        if op == "translate":
            mapping = _mapping(
                data,
                location=location,
                required={"op", "selector", "dx", "dy"},
            )
            return cls(
                op="translate",
                selector=selector,
                dx=_finite_number(mapping["dx"], location=f"{location}.dx"),
                dy=_finite_number(mapping["dy"], location=f"{location}.dy"),
            )
        if op == "replace_linked_image_source":
            mapping = _mapping(data, location=location, required={"op", "selector", "source"})
            source = mapping["source"]
            if not isinstance(source, str) or not source or "\x00" in source:
                raise OperationRequestError(
                    f"{location}.source must be a non-empty string without NUL bytes"
                )
            return cls(op="replace_linked_image_source", selector=selector, source=source)
        raise OperationRequestError(f"{location}.op {op!r} is not supported")

    def to_dict(self) -> dict[str, object]:
        result: dict[str, object] = {"op": self.op, "selector": self.selector.to_dict()}
        if self.color is not None:
            result["color"] = asdict(self.color)
        if self.text is not None:
            result["text"] = self.text
        if self.source is not None:
            result["source"] = self.source
        if self.dx is not None:
            result["dx"] = self.dx
        if self.dy is not None:
            result["dy"] = self.dy
        return result


@dataclass(frozen=True, slots=True)
class OperationManifest:
    """Versioned public request document for an atomic operation batch."""

    operations: tuple[OperationRequest, ...]
    source_sha256: str | None = None
    schema_version: int = 1

    @classmethod
    def from_dict(cls, data: object) -> OperationManifest:
        if not isinstance(data, dict):
            raise OperationRequestError("operation manifest must be an object")
        allowed = {"schema_version", "source_sha256", "operations"}
        unexpected = sorted(set(data) - allowed)
        missing = sorted({"schema_version", "operations"} - set(data))
        if missing or unexpected:
            raise OperationRequestError(_key_error("operation manifest", missing, unexpected))
        if data["schema_version"] != 1:
            raise OperationRequestError("schema_version must be 1")
        raw_operations = data["operations"]
        if not isinstance(raw_operations, list) or not raw_operations:
            raise OperationRequestError("operations must be a non-empty array")
        source_sha256 = data.get("source_sha256")
        if source_sha256 is not None and (
            not isinstance(source_sha256, str)
            or len(source_sha256) != _SHA256_LENGTH
            or any(character not in "0123456789abcdef" for character in source_sha256)
        ):
            raise OperationRequestError("source_sha256 must be a lowercase SHA-256 hex digest")
        return cls(
            operations=tuple(
                OperationRequest.from_dict(operation, index=index)
                for index, operation in enumerate(raw_operations)
            ),
            source_sha256=source_sha256,
        )

    def to_dict(self) -> dict[str, object]:
        result: dict[str, object] = {
            "schema_version": self.schema_version,
            "operations": [operation.to_dict() for operation in self.operations],
        }
        if self.source_sha256 is not None:
            result["source_sha256"] = self.source_sha256
        return result


def _mapping(
    data: object,
    *,
    location: str,
    required: set[str],
) -> dict[str, object]:
    if not isinstance(data, dict):
        raise OperationRequestError(f"{location} must be an object")
    missing = sorted(required - set(data))
    unexpected = sorted(set(data) - required)
    if missing or unexpected:
        raise OperationRequestError(_key_error(location, missing, unexpected))
    return data


def _key_error(location: str, missing: list[str], unexpected: list[str]) -> str:
    parts = []
    if missing:
        parts.append("missing " + ", ".join(repr(key) for key in missing))
    if unexpected:
        parts.append("unexpected " + ", ".join(repr(key) for key in unexpected))
    return f"{location}: " + "; ".join(parts)


def _finite_number(value: object, *, location: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise OperationRequestError(f"{location} must be a finite number")
    result = float(value)
    if not math.isfinite(result):
        raise OperationRequestError(f"{location} must be a finite number")
    return result


def _parse_color(data: object, *, location: str) -> ProcessColor:
    if not isinstance(data, dict):
        raise OperationRequestError(f"{location} must be an RGB or CMYK object")
    keys = set(data)
    if keys == {"red", "green", "blue"}:
        values = [
            _finite_number(data[key], location=f"{location}.{key}")
            for key in ("red", "green", "blue")
        ]
        try:
            return Color(*values)
        except ValueError as error:
            raise OperationRequestError(f"{location}: {error}") from error
    if keys == {"cyan", "magenta", "yellow", "black"}:
        values = [
            _finite_number(data[key], location=f"{location}.{key}")
            for key in ("cyan", "magenta", "yellow", "black")
        ]
        try:
            return CmykColor(*values)
        except ValueError as error:
            raise OperationRequestError(f"{location}: {error}") from error
    raise OperationRequestError(
        f"{location} must contain exactly red/green/blue or cyan/magenta/yellow/black"
    )


@dataclass(frozen=True, slots=True)
class ResolvedNode:
    """One selector candidate with its stable semantic-diff path."""

    type: SelectorType
    id: str
    node: Path | TextFrame | LinkedImage | Layer | Group | CompoundPath | ClippingGroup
    semantic_path: str
    ancestors: tuple[AncestorSelector, ...] = ()
    origin_start: int | None = None

    @property
    def name(self) -> str | None:
        return getattr(self.node, "name", None)

    @property
    def bounds(self) -> tuple[float, float, float, float] | None:
        return _node_bounds(self.node)


def _path_bounds(path: Path) -> tuple[float, float, float, float] | None:
    coordinates = [(point.x, point.y) for point in path.points]
    for point in path.points:
        if point.in_handle is not None:
            coordinates.append((point.in_handle.x, point.in_handle.y))
        if point.out_handle is not None:
            coordinates.append((point.out_handle.x, point.out_handle.y))
    if not coordinates:
        return None
    xs = [coordinate[0] for coordinate in coordinates]
    ys = [coordinate[1] for coordinate in coordinates]
    return min(xs), min(ys), max(xs), max(ys)


def _combine_bounds(
    values: list[tuple[float, float, float, float] | None],
) -> tuple[float, float, float, float] | None:
    present = [value for value in values if value is not None]
    if not present:
        return None
    return (
        min(value[0] for value in present),
        min(value[1] for value in present),
        max(value[2] for value in present),
        max(value[3] for value in present),
    )


def _node_bounds(
    node: Path | TextFrame | LinkedImage | Layer | Group | CompoundPath | ClippingGroup,
) -> tuple[float, float, float, float] | None:
    if isinstance(node, Path):
        return _path_bounds(node)
    if isinstance(node, TextFrame):
        character_advance = node.font_size * 0.6
        text_width = max(node.font_size * 0.7, len(node.text) * character_advance)
        return (node.x, node.y - node.font_size, node.x + text_width, node.y)
    if isinstance(node, LinkedImage):
        return (
            min(node.x, node.x + node.width),
            min(node.y, node.y + node.height),
            max(node.x, node.x + node.width),
            max(node.y, node.y + node.height),
        )
    if isinstance(node, CompoundPath):
        return _combine_bounds([_path_bounds(path) for path in node.paths])
    if isinstance(node, ClippingGroup):
        return _combine_bounds(
            [_path_bounds(node.clipping_path), *(_path_bounds(path) for path in node.paths)]
        )
    if isinstance(node, (Layer, Group)):
        values: list[tuple[float, float, float, float] | None] = []
        values.extend(_node_bounds(path) for path in node.paths)
        values.extend(_node_bounds(text) for text in node.text_frames)
        values.extend(_node_bounds(image) for image in node.linked_images)
        values.extend(_node_bounds(compound) for compound in node.compound_paths)
        values.extend(_node_bounds(clipping) for clipping in node.clipping_groups)
        values.extend(_node_bounds(group) for group in node.groups)
        return _combine_bounds(values)
    return None


def _operation_visual_bounds(
    operation: ResolvedOperation,
) -> tuple[float, float, float, float] | None:
    """Return the union of reference-raster areas an operation may affect."""

    bounds = operation.target.bounds
    if bounds is None:
        return None
    if operation.request.op == "replace_text" and isinstance(operation.target.node, TextFrame):
        replacement = replace(operation.target.node, text=str(operation.request.text))
        return _combine_bounds([bounds, _node_bounds(replacement)])
    if operation.request.op == "translate":
        assert operation.request.dx is not None and operation.request.dy is not None
        left, bottom, right, top = bounds
        translated = (
            left + operation.request.dx,
            bottom + operation.request.dy,
            right + operation.request.dx,
            top + operation.request.dy,
        )
        return _combine_bounds([bounds, translated])
    return bounds


def _legacy_visual_impact_allowed(
    operations: tuple[ResolvedOperation, ...],
    changed_bounds: tuple[int, int, int, int] | None,
    *,
    document_height: float,
    dpi: int,
) -> bool:
    allowed = _combine_bounds([_operation_visual_bounds(operation) for operation in operations])
    if allowed is None:
        return changed_bounds is None
    if changed_bounds is None:
        return True
    scale = dpi / 72
    left, bottom, right, top = allowed
    expected = (
        left * scale,
        (document_height - top) * scale,
        right * scale,
        (document_height - bottom) * scale,
    )
    # The reference raster's stroke caps and integer sampling may extend a few pixels.
    margin = 8
    return (
        changed_bounds[0] >= expected[0] - margin
        and changed_bounds[1] >= expected[1] - margin
        and changed_bounds[2] <= expected[2] + margin
        and changed_bounds[3] <= expected[3] + margin
    )


class SelectorResolver:
    """Resolve public selectors without guesses or fallback matching."""

    def __init__(self, result: LegacyReadResult) -> None:
        self.result = result
        origins: dict[tuple[str, str], list[int]] = {}
        for origin in result.origins:
            origins.setdefault((origin.node_type, origin.node_id), []).append(origin.start)
        occurrences: dict[tuple[str, str], int] = {}
        nodes: list[ResolvedNode] = []
        for node in _document_nodes(result):
            key = (node.type, node.id)
            index = occurrences.get(key, 0)
            starts = origins.get(key, [])
            origin_start = starts[index] if index < len(starts) else None
            occurrences[key] = index + 1
            nodes.append(replace(node, origin_start=origin_start))
        self.nodes = tuple(nodes)

    def resolve(self, selector: Selector) -> ResolvedNode:
        matches = [
            node
            for node in self.nodes
            if node.type == selector.type
            and (selector.id is None or node.id == selector.id)
            and (selector.name is None or node.name == selector.name)
            and (
                selector.bounds is None
                or (
                    node.bounds is not None
                    and all(
                        abs(actual - expected) <= selector.tolerance
                        for actual, expected in zip(
                            node.bounds, selector.bounds, strict=True
                        )
                    )
                )
            )
            and (not selector.ancestors or node.ancestors == selector.ancestors)
        ]
        if len(matches) != 1:
            raise UnsupportedLegacyFeature(
                f"Selector {selector.to_dict()!r} matched "
                f"{len(matches)} nodes; exactly one is required."
            )
        return matches[0]

    def leaf_members(self, container: ResolvedNode) -> tuple[ResolvedNode, ...]:
        prefix = container.semantic_path + "."
        return tuple(
            node
            for node in self.nodes
            if node.type in {"path", "text", "linked_image"}
            and node.semantic_path.startswith(prefix)
        )

    def inventory(self) -> list[dict[str, object]]:
        return [
            {
                "type": node.type,
                "id": node.id,
                "name": node.name,
                "bounds": list(node.bounds) if node.bounds is not None else None,
                "ancestors": [ancestor.to_dict() for ancestor in node.ancestors],
                "selector": {"type": node.type, "id": node.id},
            }
            for node in self.nodes
        ]


def _identity_path(parent: str, collection: str, node_id: str) -> str:
    return f"{parent + '.' if parent else ''}{collection}[id={node_id!r}]"


def _document_nodes(result: LegacyReadResult) -> list[ResolvedNode]:
    output: list[ResolvedNode] = []
    for layer in result.document.layers:
        prefix = _identity_path("", "layers", layer.id)
        output.append(ResolvedNode("layer", layer.id, layer, prefix))
        _container_nodes(
            layer,
            prefix,
            output,
            (AncestorSelector("layer", layer.id),),
        )
    return output


def _container_nodes(
    container: Layer | Group,
    prefix: str,
    output: list[ResolvedNode],
    ancestors: tuple[AncestorSelector, ...],
) -> None:
    for path in container.paths:
        child = _identity_path(prefix, "paths", path.id)
        output.append(ResolvedNode("path", path.id, path, child, ancestors))
    for text in container.text_frames:
        child = _identity_path(prefix, "text_frames", text.id)
        output.append(ResolvedNode("text", text.id, text, child, ancestors))
    for image in container.linked_images:
        child = _identity_path(prefix, "linked_images", image.id)
        output.append(ResolvedNode("linked_image", image.id, image, child, ancestors))
    for compound in container.compound_paths:
        child = _identity_path(prefix, "compound_paths", compound.id)
        output.append(ResolvedNode("compound_path", compound.id, compound, child, ancestors))
        compound_ancestors = (*ancestors, AncestorSelector("compound_path", compound.id))
        for path in compound.paths:
            path_prefix = _identity_path(child, "paths", path.id)
            output.append(ResolvedNode("path", path.id, path, path_prefix, compound_ancestors))
    for clipping in container.clipping_groups:
        child = _identity_path(prefix, "clipping_groups", clipping.id)
        output.append(ResolvedNode("clipping_group", clipping.id, clipping, child, ancestors))
        clipping_ancestors = (*ancestors, AncestorSelector("clipping_group", clipping.id))
        output.append(
            ResolvedNode(
                "path",
                clipping.clipping_path.id,
                clipping.clipping_path,
                child + ".clipping_path",
                clipping_ancestors,
            )
        )
        for path in clipping.paths:
            path_prefix = _identity_path(child, "paths", path.id)
            output.append(ResolvedNode("path", path.id, path, path_prefix, clipping_ancestors))
    for group in container.groups:
        child = _identity_path(prefix, "groups", group.id)
        output.append(ResolvedNode("group", group.id, group, child, ancestors))
        _container_nodes(
            group,
            child,
            output,
            (*ancestors, AncestorSelector("group", group.id)),
        )


@dataclass(frozen=True, slots=True)
class AllowedImpact:
    node_type: Literal["path", "text", "linked_image"]
    node_id: str
    semantic_path: str
    field: Literal["fill", "stroke", "text", "points", "x", "y", "source"]

    def to_dict(self) -> dict[str, str]:
        return {
            "node_type": self.node_type,
            "node_id": self.node_id,
            "field": self.field,
        }


@dataclass(frozen=True, slots=True)
class ResolvedOperation:
    request: OperationRequest
    target: ResolvedNode
    typed_operation: LegacyPatchOperation
    before: object
    requested_after: object
    precondition: object
    impacts: tuple[AllowedImpact, ...]


@dataclass(frozen=True, slots=True)
class LegacyEditPlan:
    """A complete dry-run report plus the exact in-memory patch plan."""

    input_path: FilePath
    report: dict[str, object]
    read_result: LegacyReadResult | None = None
    manifest: OperationManifest | None = None
    resolved_operations: tuple[ResolvedOperation, ...] = ()
    patch_plan: LegacyPatchPlan | None = None
    expected_diff: SemanticDiff | None = None

    @property
    def applicable(self) -> bool:
        return bool(self.report.get("applicable"))

    def to_dict(self) -> dict[str, object]:
        return self.report


@dataclass(frozen=True, slots=True)
class ModernEditPlan:
    """Dry-run evidence for one synchronized modern AI operation."""

    input_path: FilePath
    report: dict[str, object]
    manifest: OperationManifest | None = None
    request: OperationRequest | None = None
    capability: dict[str, object] | None = None
    resolved_operations: tuple[tuple[OperationRequest, dict[str, object]], ...] = ()

    @property
    def applicable(self) -> bool:
        return bool(self.report.get("applicable"))

    def to_dict(self) -> dict[str, object]:
        return self.report


def _origin_span(result: LegacyReadResult, node: ResolvedNode) -> dict[str, int] | None:
    matches = [
        origin
        for origin in result.origins
        if origin.node_type == node.type and origin.node_id == node.id
        and (node.origin_start is None or origin.start == node.origin_start)
    ]
    if len(matches) != 1:
        return None
    return {"start": matches[0].start, "end": matches[0].end}


def _translated_control(handle: ControlPoint | None, dx: float, dy: float) -> object:
    if handle is None:
        return None
    return {"x": handle.x + dx, "y": handle.y + dy}


def _translated_points(points: list[Point], dx: float, dy: float) -> list[dict[str, object]]:
    return [
        {
            "x": point.x + dx,
            "y": point.y + dy,
            "in_handle": _translated_control(point.in_handle, dx, dy),
            "out_handle": _translated_control(point.out_handle, dx, dy),
            "smooth": point.smooth,
        }
        for point in points
    ]


def _member_state(member: ResolvedNode, dx: float = 0, dy: float = 0) -> dict[str, object]:
    if isinstance(member.node, Path):
        value: object = _translated_points(member.node.points, dx, dy)
        field = "points"
    elif isinstance(member.node, (TextFrame, LinkedImage)):
        value = {"x": member.node.x + dx, "y": member.node.y + dy}
        field = "position"
    else:
        raise TypeError("translation members must be leaf artwork")
    return {"type": member.type, "id": member.id, field: value}


def _check_operation_type(request: OperationRequest) -> None:
    expected: set[str]
    if request.op in {"set_fill", "set_stroke"}:
        expected = {"path"}
    elif request.op == "replace_text":
        expected = {"text"}
    elif request.op == "replace_linked_image_source":
        expected = {"linked_image"}
    else:
        expected = {"path", *_CONTAINER_TYPES}
    if request.selector.type not in expected:
        raise UnsupportedLegacyFeature(
            f"Operation {request.op!r} does not support target type "
            f"{request.selector.type!r}; expected one of {sorted(expected)!r}."
        )


def _resolve_operation(
    resolver: SelectorResolver, request: OperationRequest
) -> ResolvedOperation:
    _check_operation_type(request)
    target = resolver.resolve(request.selector)
    if request.op == "set_fill":
        assert isinstance(target.node, Path) and request.color is not None
        if target.node.fill is None:
            raise UnsupportedLegacyFeature("set_fill cannot add a fill to an unfilled path")
        return ResolvedOperation(
            request,
            target,
            SetPathFill(
                target.id,
                request.color,
                target.node.fill,
                origin_start=target.origin_start,
            ),
            asdict(target.node.fill),
            asdict(request.color),
            {"expected_fill": asdict(target.node.fill)},
            (AllowedImpact("path", target.id, target.semantic_path, "fill"),),
        )
    if request.op == "set_stroke":
        assert isinstance(target.node, Path) and request.color is not None
        if target.node.stroke is None:
            raise UnsupportedLegacyFeature("set_stroke cannot add a stroke to an unstroked path")
        return ResolvedOperation(
            request,
            target,
            SetPathStroke(
                target.id,
                request.color,
                target.node.stroke,
                origin_start=target.origin_start,
            ),
            asdict(target.node.stroke),
            asdict(request.color),
            {"expected_stroke": asdict(target.node.stroke)},
            (AllowedImpact("path", target.id, target.semantic_path, "stroke"),),
        )
    if request.op == "replace_text":
        assert isinstance(target.node, TextFrame) and request.text is not None
        return ResolvedOperation(
            request,
            target,
            ReplaceText(
                target.id,
                request.text,
                target.node.text,
                origin_start=target.origin_start,
            ),
            target.node.text,
            request.text,
            {"expected_text": target.node.text},
            (AllowedImpact("text", target.id, target.semantic_path, "text"),),
        )
    if request.op == "replace_linked_image_source":
        assert isinstance(target.node, LinkedImage) and request.source is not None
        return ResolvedOperation(
            request,
            target,
            ReplaceLinkedImageSource(
                target.id,
                request.source,
                target.node.source,
                origin_start=target.origin_start,
            ),
            target.node.source,
            request.source,
            {"expected_source": target.node.source},
            (AllowedImpact("linked_image", target.id, target.semantic_path, "source"),),
        )
    assert request.dx is not None and request.dy is not None
    if isinstance(target.node, Path):
        return ResolvedOperation(
            request,
            target,
            TranslatePath(
                target.id,
                request.dx,
                request.dy,
                tuple(target.node.points),
                origin_start=target.origin_start,
            ),
            {"points": [asdict(point) for point in target.node.points]},
            {"points": _translated_points(target.node.points, request.dx, request.dy)},
            {"expected_points": [asdict(point) for point in target.node.points]},
            (AllowedImpact("path", target.id, target.semantic_path, "points"),),
        )
    members = resolver.leaf_members(target)
    identities = [(member.type, member.id) for member in members]
    if len(set(identities)) != len(identities):
        raise UnsupportedLegacyFeature(
            f"Container {target.id!r} has duplicate leaf ids; translation is ambiguous."
        )
    if not members:
        raise UnsupportedLegacyFeature(f"Container {target.id!r} has no translatable members.")
    expected_members = frozenset((member.type, member.id) for member in members)
    impacts = tuple(
        impact
        for member in members
        for impact in (
            (
                AllowedImpact("path", member.id, member.semantic_path, "points"),
            )
            if member.type == "path"
            else (
                AllowedImpact(member.type, member.id, member.semantic_path, "x"),
                AllowedImpact(member.type, member.id, member.semantic_path, "y"),
            )
        )
    )
    return ResolvedOperation(
        request,
        target,
        TranslateContainer(
            target.type,  # type: ignore[arg-type]
            target.id,
            request.dx,
            request.dy,
            expected_members,  # type: ignore[arg-type]
        ),
        {"members": [_member_state(member) for member in members]},
        {
            "members": [
                _member_state(member, request.dx, request.dy) for member in members
            ]
        },
        {
            "expected_members": [
                {"type": member_type, "id": member_id}
                for member_type, member_id in sorted(expected_members)
            ]
        },
        impacts,
    )


def _difference_allowed(difference: SemanticDifference, impacts: tuple[AllowedImpact, ...]) -> bool:
    for impact in impacts:
        prefix = f"{impact.semantic_path}.{impact.field}"
        if difference.path == prefix or difference.path.startswith((prefix + ".", prefix + "[")):
            return True
    return False


def unexpected_semantic_differences(
    difference: SemanticDiff, resolved: tuple[ResolvedOperation, ...]
) -> tuple[SemanticDifference, ...]:
    """Return differences outside the explicitly permitted operation impact."""

    impacts = tuple(impact for operation in resolved for impact in operation.impacts)
    return tuple(item for item in difference.differences if not _difference_allowed(item, impacts))


def _outside_replacements_equal(
    before: bytes, after: bytes, replacements: tuple[Any, ...]
) -> bool:
    source_cursor = 0
    output_cursor = 0
    for replacement in replacements:
        unchanged_size = replacement.start - source_cursor
        if (
            after[output_cursor : output_cursor + unchanged_size]
            != before[source_cursor : replacement.start]
        ):
            return False
        source_cursor = replacement.end
        output_cursor += unchanged_size + len(replacement.data)
    return after[output_cursor:] == before[source_cursor:]


def _base_report(path: FilePath) -> tuple[dict[str, object], FileFormat]:
    inspected = inspect_file(path)
    return (
        {
            "schema_version": 1,
            "input": {"path": str(path), "format": inspected.format.value},
            "feature_profile": {"id": LEGACY_FEATURE_PROFILE_ID},
            "compatibility": None,
            "source_sha256": None,
            "operations": [],
            "replacement_count": 0,
            "expected_semantic_diff": None,
            "warnings": [],
            "stop_reasons": [],
            "applicable": False,
        },
        inspected.format,
    )


def _stop(code: str, message: str, *, operation_index: int | None = None) -> dict[str, object]:
    result: dict[str, object] = {"code": code, "message": message}
    if operation_index is not None:
        result["operation_index"] = operation_index
    return result


def _plan_modern_edit(
    path: FilePath, request_data: object, report: dict[str, object]
) -> ModernEditPlan:
    report["feature_profile"] = {"id": "modern-ai-synchronized-patch-v1"}
    data = path.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    report["source_sha256"] = digest
    try:
        manifest = OperationManifest.from_dict(request_data)
    except OperationRequestError as error:
        report["stop_reasons"] = [_stop("invalid-operation-request", str(error))]
        return ModernEditPlan(path, report)
    if manifest.source_sha256 is not None and manifest.source_sha256 != digest:
        report["stop_reasons"] = [
            _stop(
                "stale-source",
                "source_sha256 precondition does not match the current input file.",
            )
        ]
        return ModernEditPlan(path, report, manifest=manifest)
    if len(manifest.operations) > 1:
        operation_reports: list[dict[str, object]] = []
        stop_reasons: list[dict[str, object]] = []
        resolved_batch: list[tuple[OperationRequest, dict[str, object]]] = []
        for index, operation in enumerate(manifest.operations):
            subreport, _input_format = _base_report(path)
            subplan = _plan_modern_edit(
                path,
                {
                    "schema_version": 1,
                    "source_sha256": digest,
                    "operations": [operation.to_dict()],
                },
                subreport,
            )
            if subplan.report.get("operations"):
                operation_report = dict(subplan.report["operations"][0])  # type: ignore[index]
                operation_report["index"] = index
                operation_reports.append(operation_report)
            for reason in subplan.report.get("stop_reasons", []):  # type: ignore[union-attr]
                item = dict(reason)
                item["operation_index"] = index
                stop_reasons.append(item)
            resolved_batch.extend(subplan.resolved_operations)
        report["operations"] = operation_reports
        report["stop_reasons"] = stop_reasons
        report["applicable"] = not stop_reasons and len(resolved_batch) == len(
            manifest.operations
        )
        report["updated_representation_count"] = 2
        report["batch_policy"] = {
            "ordering": "manifest-order",
            "preconditions": "replanned-against-prior-temporary-revision",
            "commit": "destination-created-only-after-all-operations-validate",
        }
        report["expected_visual_impacts"] = [
            {"operation_index": index, "bounds": capability.get("pdf_impact_bounds")}
            for index, (_request, capability) in enumerate(resolved_batch)
        ]
        report["byte_preservation"] = {
            "guarantee": "original-source-preserved-as-incremental-pdf-prefix",
            "validated": bool(report["applicable"]),
        }
        return ModernEditPlan(
            path,
            report,
            manifest=manifest,
            resolved_operations=tuple(resolved_batch),
        )
    request = manifest.operations[0]
    supported_paint = (
        request.op in {"set_fill", "set_stroke"}
        and request.selector.type == "path"
        and request.color is not None
    )
    supported_text = (
        request.op == "replace_text"
        and request.selector.type == "text"
        and request.text is not None
        and bool(request.text)
        and all(32 <= ord(character) <= 126 for character in request.text)
    )
    supported_translate = (
        request.op == "translate"
        and request.selector.type in {"path", "group", "layer"}
        and request.dx is not None
        and request.dy is not None
    )
    if not supported_paint and not supported_text and not supported_translate:
        report["stop_reasons"] = [
            _stop(
                "modern-operation-unsupported",
                "The current synchronized modern profile supports set_fill/set_stroke on path "
                "printable-ASCII replace_text on text, and translate on proven rectangle paths "
                "or path-only containers.",
                operation_index=0,
            )
        ]
        return ModernEditPlan(path, report, manifest=manifest, request=request)
    if request.op == "set_fill":
        capabilities = inspect_modern_fill_targets(path)
    elif request.op == "set_stroke":
        capabilities = inspect_modern_stroke_targets(path)
    elif request.op == "translate":
        capabilities = (
            inspect_modern_translate_targets(path)
            if request.selector.type == "path"
            else inspect_modern_container_translate_targets(path)
        )
    else:
        capabilities = inspect_modern_text_targets(path)
    matches = [
        item
        for item in capabilities["selectors"]  # type: ignore[union-attr]
        if isinstance(item, dict)
        and (request.selector.id is None or item.get("id") == request.selector.id)
        and (request.selector.name is None or item.get("name") == request.selector.name)
        and (
            request.selector.bounds is None
            or (
                isinstance(item.get("bounds"), list)
                and len(item["bounds"]) == 4
                and all(
                    abs(float(actual) - expected) <= request.selector.tolerance
                    for actual, expected in zip(
                        item["bounds"], request.selector.bounds, strict=True
                    )
                )
            )
        )
        and (
            not request.selector.ancestors
            or item.get("ancestors")
            == [ancestor.to_dict() for ancestor in request.selector.ancestors]
        )
    ]
    if len(matches) != 1:
        report["stop_reasons"] = [
            _stop(
                "selector-not-unique",
                f"selector {request.selector.to_dict()!r} matched {len(matches)} targets; "
                "exactly one is required.",
                operation_index=0,
            )
        ]
        return ModernEditPlan(path, report, manifest=manifest, request=request)
    capability = matches[0]
    capability = dict(capability)
    if request.op == "replace_text":
        impact = capability.get("pdf_impact_bounds")
        old_text = capability.get("before")
        if (
            isinstance(impact, list)
            and len(impact) == 4
            and isinstance(old_text, str)
            and old_text
            and request.text is not None
        ):
            impact = list(impact)
            impact[2] *= max(1.0, len(request.text) / len(old_text))
            capability["pdf_impact_bounds"] = impact
    elif request.op == "translate":
        impact = capability.get("pdf_impact_bounds")
        if (
            isinstance(impact, list)
            and len(impact) == 4
            and request.dx is not None
            and request.dy is not None
        ):
            x, y, width, height = (float(value) for value in impact)
            left = min(x, x + request.dx)
            bottom = min(y, y + request.dy)
            right = max(x + width, x + request.dx + width)
            top = max(y + height, y + request.dy + height)
            capability["pdf_impact_bounds"] = [
                left,
                bottom,
                right - left,
                top - bottom,
            ]
    operation_report = {
        "index": 0,
        "request": request.to_dict(),
        "resolved_target": {
            "type": capability["type"],
            "id": capability["id"],
            "name": capability.get("name"),
            "bounds": capability.get("bounds"),
        },
        "before": capability.get("before"),
        "requested_after": (
            asdict(request.color)
            if request.color is not None
            else request.text
            if request.text is not None
            else {"dx": request.dx, "dy": request.dy}
        ),
        "changes": [
            {
                "node_type": capability["type"],
                "node_id": capability["id"],
                "field": (
                    "fill"
                    if request.op == "set_fill"
                    else "stroke"
                    if request.op == "set_stroke"
                    else "text"
                    if request.op == "replace_text"
                    else "points"
                ),
            }
        ],
        "representations": ["illustrator-private-data", "pdf-display"],
        "applicable": bool(capability.get("writable")),
    }
    report["operations"] = [operation_report]
    if not capability.get("writable"):
        reasons = capability.get("stop_reasons")
        message = "; ".join(str(item) for item in reasons) if isinstance(reasons, list) else ""
        report["stop_reasons"] = [
            _stop("operation-not-applicable", message, operation_index=0)
        ]
        return ModernEditPlan(
            path,
            report,
            manifest=manifest,
            request=request,
            capability=capability,
        )
    report.update(
        {
            "updated_representation_count": 2,
            "expected_visual_impact": {"bounds": capability.get("pdf_impact_bounds")},
            "byte_preservation": {
                "guarantee": "original-source-preserved-as-incremental-pdf-prefix",
                "validated": True,
            },
            "stop_reasons": [],
            "applicable": True,
        }
    )
    resolved_operations: tuple[tuple[OperationRequest, dict[str, object]], ...]
    if request.op == "translate" and request.selector.type in {"group", "layer"}:
        raw_members = capability.get("members")
        if not isinstance(raw_members, list) or not raw_members:
            report["applicable"] = False
            report["stop_reasons"] = [
                _stop(
                    "container-members-unproven",
                    "Container translation has no proven path members.",
                    operation_index=0,
                )
            ]
            return ModernEditPlan(
                path,
                report,
                manifest=manifest,
                request=request,
                capability=capability,
            )
        children: list[tuple[OperationRequest, dict[str, object]]] = []
        for member in raw_members:
            if not isinstance(member, dict):
                continue
            member_selector = Selector.from_dict(
                {
                    "type": "path",
                    "id": member.get("id"),
                    "ancestors": member.get("ancestors", []),
                },
                location="container member selector",
            )
            child_request = OperationRequest(
                op="translate",
                selector=member_selector,
                dx=request.dx,
                dy=request.dy,
            )
            children.append((child_request, member))
        resolved_operations = tuple(children)
        report["container_member_count"] = len(children)
        report["batch_policy"] = {
            "ordering": "container-projected-item-order",
            "preconditions": "replanned-against-prior-temporary-revision",
            "commit": "destination-created-only-after-all-members-validate",
        }
    else:
        resolved_operations = ((request, capability),)
    return ModernEditPlan(
        path,
        report,
        manifest=manifest,
        request=request,
        capability=capability,
        resolved_operations=resolved_operations,
    )


def plan_edit(source: str | FilePath, request_data: object) -> LegacyEditPlan | ModernEditPlan:
    """Resolve, dry-run, and validate a public edit request without writing a file."""

    path = FilePath(source)
    report, input_format = _base_report(path)
    if input_format is FileFormat.PDF_COMPATIBLE_AI:
        try:
            return _plan_modern_edit(path, request_data, report)
        except (OSError, ValueError) as error:
            report["feature_profile"] = {"id": "modern-ai-synchronized-patch-v1"}
            report["stop_reasons"] = [_stop("input-read-failed", str(error))]
            return ModernEditPlan(path, report)
    if input_format is not FileFormat.LEGACY_AI:
        report["stop_reasons"] = [
            _stop(
                "unsupported-input-format",
                f"Safe editing supports legacy_ai only; detected {input_format.value}.",
            )
        ]
        return LegacyEditPlan(path, report)
    try:
        result = read_ai7(path)
    except (OSError, ValueError, UnicodeError) as error:
        report["stop_reasons"] = [_stop("input-read-failed", str(error))]
        return LegacyEditPlan(path, report)

    digest = hashlib.sha256(result.source.data).hexdigest()
    report["source_sha256"] = digest
    report["compatibility"] = result.compatibility_report()
    report["warnings"] = [diagnostic.message for diagnostic in result.diagnostics]
    try:
        manifest = OperationManifest.from_dict(request_data)
    except OperationRequestError as error:
        report["stop_reasons"] = [_stop("invalid-operation-request", str(error))]
        return LegacyEditPlan(path, report, read_result=result)
    if manifest.source_sha256 is not None and manifest.source_sha256 != digest:
        report["stop_reasons"] = [
            _stop(
                "stale-source",
                "source_sha256 precondition does not match the current input file.",
            )
        ]
        return LegacyEditPlan(path, report, read_result=result, manifest=manifest)

    resolver = SelectorResolver(result)
    resolved: list[ResolvedOperation] = []
    operation_reports: list[dict[str, object]] = []
    stop_reasons: list[dict[str, object]] = []
    for index, request in enumerate(manifest.operations):
        operation_report: dict[str, object] = {
            "index": index,
            "request": request.to_dict(),
            "resolved_target": None,
            "before": None,
            "requested_after": None,
            "precondition": None,
            "replacement_count": 0,
            "replacement_spans": [],
            "changes": [],
            "applicable": False,
        }
        try:
            operation = _resolve_operation(resolver, request)
            local_plan = plan_legacy_patch(result, (operation.typed_operation,))
        except (TypeError, ValueError) as error:
            stop_reasons.append(
                _stop("operation-not-applicable", str(error), operation_index=index)
            )
            operation_reports.append(operation_report)
            continue
        resolved.append(operation)
        operation_report.update(
            {
                "resolved_target": {
                    "type": operation.target.type,
                    "id": operation.target.id,
                    "name": operation.target.name,
                    "bounds": (
                        list(operation.target.bounds)
                        if operation.target.bounds is not None
                        else None
                    ),
                    "source_span": _origin_span(result, operation.target),
                },
                "before": operation.before,
                "requested_after": operation.requested_after,
                "precondition": operation.precondition,
                "replacement_count": len(local_plan.replacements),
                "replacement_spans": [
                    {"start": replacement.start, "end": replacement.end}
                    for replacement in local_plan.replacements
                ],
                "changes": [impact.to_dict() for impact in operation.impacts],
                "applicable": True,
            }
        )
        operation_reports.append(operation_report)
    report["operations"] = operation_reports
    if stop_reasons:
        report["stop_reasons"] = stop_reasons
        return LegacyEditPlan(
            path,
            report,
            read_result=result,
            manifest=manifest,
            resolved_operations=tuple(resolved),
        )

    resolved_tuple = tuple(resolved)
    try:
        patch_plan = plan_legacy_patch(
            result, tuple(operation.typed_operation for operation in resolved_tuple)
        )
        candidate = apply_legacy_patch(result, patch_plan)
        after = reads_ai7(candidate.data)
        expected_diff = semantic_diff(result.document, after.document)
        unexpected = unexpected_semantic_differences(expected_diff, resolved_tuple)
        bytes_preserved = _outside_replacements_equal(
            result.source.data, candidate.data, patch_plan.replacements
        )
        if unexpected:
            raise UnsupportedLegacyFeature(
                "Dry-run produced semantic changes outside the requested impact: "
                + ", ".join(item.path for item in unexpected)
            )
        if not bytes_preserved:
            raise UnsupportedLegacyFeature("Dry-run changed bytes outside replacement spans.")
    except (TypeError, ValueError, UnicodeError) as error:
        report["stop_reasons"] = [_stop("batch-not-applicable", str(error))]
        return LegacyEditPlan(
            path,
            report,
            read_result=result,
            manifest=manifest,
            resolved_operations=resolved_tuple,
        )

    report.update(
        {
            "replacement_count": len(patch_plan.replacements),
            "replacement_spans": [
                {"start": replacement.start, "end": replacement.end}
                for replacement in patch_plan.replacements
            ],
            "expected_semantic_diff": expected_diff.to_dict(),
            "expected_visual_impacts": [
                {
                    "operation_index": index,
                    "bounds": (
                        list(bounds)
                        if (bounds := _operation_visual_bounds(operation)) is not None
                        else None
                    ),
                }
                for index, operation in enumerate(resolved_tuple)
            ],
            "byte_preservation": {
                "guarantee": "all-bytes-outside-replacement-spans-identical",
                "validated": True,
            },
            "stop_reasons": [],
            "applicable": True,
        }
    )
    return LegacyEditPlan(
        path,
        report,
        read_result=result,
        manifest=manifest,
        resolved_operations=resolved_tuple,
        patch_plan=patch_plan,
        expected_diff=expected_diff,
    )


def inspect_editable_legacy(source: str | FilePath) -> dict[str, object]:
    """Return compatibility evidence and exact public selector candidates."""

    result = read_ai7(source)
    return {
        "compatibility": result.compatibility_report(),
        "selectors": SelectorResolver(result).inventory(),
    }


def inspect_editable_modern(source: str | FilePath) -> dict[str, object]:
    """Return synchronized modern writer selector capabilities."""

    fill = inspect_modern_fill_targets(source)
    stroke = inspect_modern_stroke_targets(source)
    text_targets = inspect_modern_text_targets(source)
    translate = inspect_modern_translate_targets(source)
    container_translate = inspect_modern_container_translate_targets(source)
    by_identity: dict[tuple[str, str], dict[str, object]] = {}
    for operation, report in (
        ("set_fill", fill),
        ("set_stroke", stroke),
        ("replace_text", text_targets),
        ("translate", translate),
        ("translate", container_translate),
    ):
        for raw in report["selectors"]:  # type: ignore[union-attr]
            if not isinstance(raw, dict):
                continue
            key = (str(raw["type"]), str(raw["id"]))
            entry = by_identity.setdefault(
                key,
                {
                    "type": raw["type"],
                    "id": raw["id"],
                    "name": raw.get("name"),
                    "selector": raw["selector"],
                    "ancestors": raw.get("ancestors", []),
                    "operations": [],
                    "capabilities": {},
                },
            )
            if raw.get("writable"):
                entry["operations"].append(operation)  # type: ignore[union-attr]
            entry["capabilities"][operation] = {  # type: ignore[index]
                "writable": raw.get("writable"),
                "bounds": raw.get("bounds"),
                "pdf_impact_bounds": raw.get("pdf_impact_bounds"),
                "representations_consistent": raw.get(
                    "representations_consistent"
                ),
                "stop_reasons": raw.get("stop_reasons"),
            }
    return {
        "profile": "modern-ai-synchronized-patch-v1",
        "source_sha256": fill["source_sha256"],
        "selectors": list(by_identity.values()),
    }


def _failed_apply(plan: LegacyEditPlan, code: str, message: str) -> dict[str, object]:
    return {
        "status": "failed",
        "applied": False,
        "input": str(plan.input_path),
        "output": None,
        "source_sha256": plan.report.get("source_sha256"),
        "output_sha256": None,
        "compatibility": {"before": plan.report.get("compatibility"), "after": None},
        "validation": {
            "output_reparsed": False,
            "bytes_outside_replacement_spans_identical": False,
            "semantic_impact_allowed": False,
            "semantic_diff_matches_plan": False,
            "visual_impact_within_target_bounds": False,
        },
        "semantic_diff": None,
        "warnings": plan.report.get("warnings", []),
        "stop_reasons": [_stop(code, message)],
    }


def _apply_modern_edit_plan(plan: ModernEditPlan, output: str | FilePath) -> dict[str, object]:
    if len(plan.resolved_operations) > 1 or (
        plan.request is not None and plan.request.selector.type in {"group", "layer"}
    ):
        return _apply_modern_batch(plan, output)
    destination = FilePath(output)
    if not plan.applicable or plan.request is None or plan.capability is None:
        return {
            "status": "failed",
            "applied": False,
            "input": str(plan.input_path),
            "output": None,
            "source_sha256": plan.report.get("source_sha256"),
            "output_sha256": None,
            "validation": {},
            "stop_reasons": plan.report.get("stop_reasons")
            or [_stop("plan-not-applicable", "The edit plan is not applicable.")],
        }
    if plan.input_path.resolve() == destination.resolve():
        return {
            "status": "failed",
            "applied": False,
            "output": None,
            "stop_reasons": [
                _stop("input-overwrite-refused", "The input file cannot be overwritten.")
            ],
        }
    if destination.exists():
        return {
            "status": "failed",
            "applied": False,
            "output": None,
            "stop_reasons": [
                _stop("output-exists", f"Output already exists: {destination}")
            ],
        }
    try:
        if plan.request.op == "replace_text":
            assert plan.request.text is not None
            result = patch_modern_text(
                plan.input_path,
                destination,
                text_id=str(plan.capability["id"]),
                text=plan.request.text,
                source_sha256=str(plan.report["source_sha256"]),
            )
        elif plan.request.op == "translate":
            assert plan.request.dx is not None and plan.request.dy is not None
            result = patch_modern_path_translate(
                plan.input_path,
                destination,
                path_id=str(plan.capability["id"]),
                dx=plan.request.dx,
                dy=plan.request.dy,
                source_sha256=str(plan.report["source_sha256"]),
            )
        else:
            assert plan.request.color is not None
            patcher = (
                patch_modern_path_fill
                if plan.request.op == "set_fill"
                else patch_modern_path_stroke
            )
            result = patcher(
                plan.input_path,
                destination,
                path_id=str(plan.capability["id"]),
                color=plan.request.color,
                source_sha256=str(plan.report["source_sha256"]),
            )
        with tempfile.TemporaryDirectory(prefix="py-ai-operation-impact-") as directory:
            difference = visual_diff(
                plan.input_path,
                destination,
                FilePath(directory) / "difference.png",
                dpi=144,
            )
        bounds_value = plan.capability.get("pdf_impact_bounds") if plan.capability else None
        display = extract_pdf_display(plan.input_path)
        impact_allowed = False
        if (
            isinstance(bounds_value, list)
            and len(bounds_value) == 4
            and display.pages
            and display.pages[0].crop_box is not None
            and len(difference.pages) == 1
        ):
            x, y, width, height = (float(value) for value in bounds_value)
            crop = display.pages[0].crop_box
            scale = 144 / 72
            expected = (
                (x - crop[0]) * scale,
                (crop[3] - (y + height)) * scale,
                (x + width - crop[0]) * scale,
                (crop[3] - y) * scale,
            )
            actual = difference.pages[0].changed_bounds
            # Stroke width and antialiasing can extend several raster pixels beyond path geometry.
            margin = 8
            impact_allowed = actual is None or (
                actual[0] >= expected[0] - margin
                and actual[1] >= expected[1] - margin
                and actual[2] <= expected[2] + margin
                and actual[3] <= expected[3] + margin
            )
        if not impact_allowed:
            destination.unlink(missing_ok=True)
            raise ModernWriteError("visual diff escaped the requested path bounds")
    except (OSError, ValueError, RuntimeError) as error:
        destination.unlink(missing_ok=True)
        return {
            "status": "failed",
            "applied": False,
            "input": str(plan.input_path),
            "output": None,
            "source_sha256": plan.report.get("source_sha256"),
            "output_sha256": None,
            "validation": {},
            "stop_reasons": [_stop("apply-validation-failed", str(error))],
        }
    report = result.to_dict()
    report.update(
        {
            "status": "applied",
            "applied": True,
            "visual_diff": difference.to_dict(),
            "validation": {
                **result.validation,
                "visual_impact_within_target_bounds": impact_allowed,
            },
            "stop_reasons": [],
        }
    )
    return report


def _apply_modern_batch(plan: ModernEditPlan, output: str | FilePath) -> dict[str, object]:
    """Apply independently re-planned modern operations to temporary incremental revisions."""

    destination = FilePath(output)
    if not plan.applicable:
        return {
            "status": "failed",
            "applied": False,
            "input": str(plan.input_path),
            "output": None,
            "stop_reasons": plan.report.get("stop_reasons")
            or [_stop("plan-not-applicable", "The edit plan is not applicable.")],
        }
    if plan.input_path.resolve() == destination.resolve():
        return {
            "status": "failed",
            "applied": False,
            "output": None,
            "stop_reasons": [
                _stop("input-overwrite-refused", "The input file cannot be overwritten.")
            ],
        }
    if destination.exists():
        return {
            "status": "failed",
            "applied": False,
            "output": None,
            "stop_reasons": [
                _stop("output-exists", f"Output already exists: {destination}")
            ],
        }
    original = plan.input_path.read_bytes()
    operation_results: list[dict[str, object]] = []
    final_data: bytes | None = None
    try:
        with tempfile.TemporaryDirectory(prefix="py-ai-modern-batch-") as directory:
            current = plan.input_path
            for index, (request, _capability) in enumerate(plan.resolved_operations):
                current_digest = hashlib.sha256(current.read_bytes()).hexdigest()
                subreport, _input_format = _base_report(current)
                subplan = _plan_modern_edit(
                    current,
                    {
                        "schema_version": 1,
                        "source_sha256": current_digest,
                        "operations": [request.to_dict()],
                    },
                    subreport,
                )
                if not subplan.applicable:
                    raise ModernWriteError(
                        f"operation {index} no longer applies after prior operations: "
                        f"{subplan.report.get('stop_reasons')}"
                    )
                step_output = FilePath(directory) / f"step-{index}.ai"
                step_result = _apply_modern_edit_plan(subplan, step_output)
                if not step_result.get("applied"):
                    raise ModernWriteError(
                        f"operation {index} failed: {step_result.get('stop_reasons')}"
                    )
                visual = step_result.get("visual_diff")
                visual_summary = None
                if isinstance(visual, dict):
                    visual_summary = {
                        "profile": visual.get("profile"),
                        "equal": visual.get("equal"),
                        "changed_pixels": visual.get("changed_pixels"),
                        "pages": [
                            {
                                "index": page.get("index"),
                                "changed_pixels": page.get("changed_pixels"),
                                "changed_ratio": page.get("changed_ratio"),
                                "changed_bounds": page.get("changed_bounds"),
                            }
                            for page in visual.get("pages", [])
                            if isinstance(page, dict)
                        ],
                    }
                operation_results.append(
                    {
                        "index": index,
                        "operation": step_result.get("operation"),
                        "selector": step_result.get("selector"),
                        "validation": step_result.get("validation"),
                        "visual_diff": visual_summary,
                    }
                )
                current = step_output
            final_data = current.read_bytes()
        with destination.open("xb") as stream:
            stream.write(final_data)
    except (OSError, ValueError, RuntimeError) as error:
        destination.unlink(missing_ok=True)
        return {
            "status": "failed",
            "applied": False,
            "input": str(plan.input_path),
            "output": None,
            "source_sha256": hashlib.sha256(original).hexdigest(),
            "output_sha256": None,
            "operation_results": operation_results,
            "stop_reasons": [_stop("atomic-batch-failed", str(error))],
        }
    assert final_data is not None
    return {
        "status": "applied",
        "applied": True,
        "operation": "batch",
        "input": str(plan.input_path),
        "output": str(destination),
        "source_sha256": hashlib.sha256(original).hexdigest(),
        "output_sha256": hashlib.sha256(final_data).hexdigest(),
        "operation_count": len(operation_results),
        "operation_results": operation_results,
        "validation": {
            "all_operations_validated": all(
                all(result["validation"].values())  # type: ignore[union-attr]
                for result in operation_results
            ),
            "atomic_destination_created": destination.read_bytes() == final_data,
            "source_not_overwritten": plan.input_path.read_bytes() == original,
            "original_source_prefix_preserved": final_data.startswith(original),
        },
        "stop_reasons": [],
    }


def apply_edit_plan(
    plan: LegacyEditPlan | ModernEditPlan, output: str | FilePath
) -> dict[str, object]:
    """Atomically apply a prepared plan to a distinct, non-existing output path."""

    if isinstance(plan, ModernEditPlan):
        return _apply_modern_edit_plan(plan, output)
    destination = FilePath(output)
    if not plan.applicable or plan.read_result is None or plan.patch_plan is None:
        result = _failed_apply(plan, "plan-not-applicable", "The edit plan is not applicable.")
        result["stop_reasons"] = plan.report.get("stop_reasons") or result["stop_reasons"]
        return result
    if plan.input_path.resolve() == destination.resolve():
        return _failed_apply(
            plan, "input-overwrite-refused", "The input file cannot be overwritten."
        )
    if destination.exists():
        return _failed_apply(
            plan,
            "output-exists",
            f"Output already exists and will not be overwritten: {destination}",
        )
    try:
        current = read_ai7(plan.input_path)
        candidate = apply_legacy_patch(current, plan.patch_plan)
        candidate_after = reads_ai7(candidate.data)
        candidate_diff = semantic_diff(current.document, candidate_after.document)
        unexpected = unexpected_semantic_differences(candidate_diff, plan.resolved_operations)
        matches_plan = candidate_diff == plan.expected_diff
        bytes_preserved = _outside_replacements_equal(
            current.source.data, candidate.data, plan.patch_plan.replacements
        )
        if unexpected:
            raise UnsupportedLegacyFeature(
                "Output would contain semantic changes outside the requested impact: "
                + ", ".join(item.path for item in unexpected)
            )
        if not matches_plan:
            raise UnsupportedLegacyFeature("Output semantic diff does not match the dry-run plan.")
        if not bytes_preserved:
            raise UnsupportedLegacyFeature("Output would change bytes outside replacement spans.")
    except (OSError, ValueError, UnicodeError) as error:
        return _failed_apply(plan, "apply-validation-failed", str(error))

    created = False
    try:
        with destination.open("xb") as stream:
            stream.write(candidate.data)
        created = True
        disk_data = destination.read_bytes()
        disk_after = reads_ai7(disk_data)
        disk_diff = semantic_diff(current.document, disk_after.document)
        disk_unexpected = unexpected_semantic_differences(disk_diff, plan.resolved_operations)
        disk_matches_plan = disk_diff == plan.expected_diff
        disk_bytes_preserved = _outside_replacements_equal(
            current.source.data, disk_data, plan.patch_plan.replacements
        )
        if disk_unexpected or not disk_matches_plan or not disk_bytes_preserved:
            raise UnsupportedLegacyFeature("Written output failed post-write validation.")
        with tempfile.TemporaryDirectory(prefix="py-ai-legacy-operation-impact-") as directory:
            difference = visual_diff(
                plan.input_path,
                destination,
                FilePath(directory) / "difference.png",
                dpi=144,
            )
        impact_allowed = len(difference.pages) == 1 and _legacy_visual_impact_allowed(
            plan.resolved_operations,
            difference.pages[0].changed_bounds,
            document_height=current.document.height,
            dpi=144,
        )
        if not impact_allowed:
            raise UnsupportedLegacyFeature(
                "Reference-raster diff escaped the requested operation bounds."
            )
    except (OSError, ValueError, UnicodeError) as error:
        if created:
            destination.unlink(missing_ok=True)
        return _failed_apply(plan, "output-write-or-validation-failed", str(error))

    return {
        "status": "applied",
        "applied": True,
        "input": str(plan.input_path),
        "output": str(destination),
        "source_sha256": plan.patch_plan.source_sha256,
        "output_sha256": hashlib.sha256(disk_data).hexdigest(),
        "replacement_count": len(plan.patch_plan.replacements),
        "compatibility": {
            "before": current.compatibility_report(),
            "after": disk_after.compatibility_report(),
        },
        "validation": {
            "output_reparsed": True,
            "bytes_outside_replacement_spans_identical": disk_bytes_preserved,
            "semantic_impact_allowed": not disk_unexpected,
            "semantic_diff_matches_plan": disk_matches_plan,
            "visual_impact_within_target_bounds": impact_allowed,
        },
        "visual_diff": difference.to_dict(),
        "semantic_diff": disk_diff.to_dict(),
        "warnings": [diagnostic.message for diagnostic in disk_after.diagnostics],
        "stop_reasons": [],
    }


def apply_edit(
    source: str | FilePath, request_data: object, output: str | FilePath
) -> dict[str, object]:
    """Plan and apply an operation manifest through the same validated workflow."""

    return apply_edit_plan(plan_edit(source, request_data), output)
