"""Read-only operation selector resolution and dry-run planning.

This module owns semantic target resolution, capability evidence, and expected
impact planning. Applying a plan belongs to _operation_orchestration.
"""

from __future__ import annotations

import hashlib
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
from ._operation_schema import (
    AncestorSelector,
    OperationManifest,
    OperationRequest,
    OperationRequestError,
    Selector,
    SelectorType,
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
    plan_legacy_patch,
    read_ai7,
    reads_ai7,
)
from .model import (
    ClippingGroup,
    CompoundPath,
    ControlPoint,
    Group,
    Layer,
    LinkedImage,
    Path,
    Point,
    TextFrame,
)
from .semantic import SemanticDiff, SemanticDifference, semantic_diff

_CONTAINER_TYPES = frozenset({"layer", "group", "compound_path", "clipping_group"})

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


def _predict_replacement_bytes(source: bytes, replacements: tuple[Any, ...]) -> bytes:
    """Build the dry-run candidate without invoking the apply backend."""

    cursor = 0
    chunks: list[bytes] = []
    for replacement in replacements:
        if replacement.start < cursor or replacement.end < replacement.start:
            raise ValueError("replacement spans must be ordered and non-overlapping")
        chunks.extend((source[cursor : replacement.start], replacement.data))
        cursor = replacement.end
    chunks.append(source[cursor:])
    return b"".join(chunks)


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
        candidate_data = _predict_replacement_bytes(result.source.data, patch_plan.replacements)
        after = reads_ai7(candidate_data)
        expected_diff = semantic_diff(result.document, after.document)
        unexpected = unexpected_semantic_differences(expected_diff, resolved_tuple)
        bytes_preserved = _outside_replacements_equal(
            result.source.data, candidate_data, patch_plan.replacements
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

__all__ = [
    "AllowedImpact",
    "LegacyEditPlan",
    "ModernEditPlan",
    "ResolvedNode",
    "ResolvedOperation",
    "SelectorResolver",
    "inspect_editable_legacy",
    "inspect_editable_modern",
    "plan_edit",
    "unexpected_semantic_differences",
]
