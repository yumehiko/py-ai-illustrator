"""Agent-independent planning and application of safe legacy AI edits."""

from __future__ import annotations

import hashlib
import math
from dataclasses import asdict, dataclass
from pathlib import Path as FilePath
from typing import Any, Literal

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
class Selector:
    """The first public selector boundary: an exact node type and stable id."""

    type: SelectorType
    id: str

    @classmethod
    def from_dict(cls, data: object, *, location: str) -> Selector:
        mapping = _mapping(data, location=location, required={"type", "id"})
        node_type = mapping["type"]
        node_id = mapping["id"]
        if not isinstance(node_type, str) or node_type not in _SELECTOR_TYPES:
            raise OperationRequestError(f"{location}.type is not a supported selector type")
        if not isinstance(node_id, str) or not node_id:
            raise OperationRequestError(f"{location}.id must be a non-empty string")
        return cls(type=node_type, id=node_id)  # type: ignore[arg-type]

    def to_dict(self) -> dict[str, str]:
        return {"type": self.type, "id": self.id}


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

    @property
    def name(self) -> str | None:
        return getattr(self.node, "name", None)


class SelectorResolver:
    """Resolve public selectors without guesses or fallback matching."""

    def __init__(self, result: LegacyReadResult) -> None:
        self.result = result
        self.nodes = tuple(_document_nodes(result))

    def resolve(self, selector: Selector) -> ResolvedNode:
        matches = [
            node for node in self.nodes if node.type == selector.type and node.id == selector.id
        ]
        if len(matches) != 1:
            raise UnsupportedLegacyFeature(
                f"Selector type={selector.type!r} id={selector.id!r} matched "
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
        _container_nodes(layer, prefix, output)
    return output


def _container_nodes(container: Layer | Group, prefix: str, output: list[ResolvedNode]) -> None:
    for path in container.paths:
        child = _identity_path(prefix, "paths", path.id)
        output.append(ResolvedNode("path", path.id, path, child))
    for text in container.text_frames:
        child = _identity_path(prefix, "text_frames", text.id)
        output.append(ResolvedNode("text", text.id, text, child))
    for image in container.linked_images:
        child = _identity_path(prefix, "linked_images", image.id)
        output.append(ResolvedNode("linked_image", image.id, image, child))
    for compound in container.compound_paths:
        child = _identity_path(prefix, "compound_paths", compound.id)
        output.append(ResolvedNode("compound_path", compound.id, compound, child))
        for path in compound.paths:
            path_prefix = _identity_path(child, "paths", path.id)
            output.append(ResolvedNode("path", path.id, path, path_prefix))
    for clipping in container.clipping_groups:
        child = _identity_path(prefix, "clipping_groups", clipping.id)
        output.append(ResolvedNode("clipping_group", clipping.id, clipping, child))
        output.append(
            ResolvedNode(
                "path",
                clipping.clipping_path.id,
                clipping.clipping_path,
                child + ".clipping_path",
            )
        )
        for path in clipping.paths:
            path_prefix = _identity_path(child, "paths", path.id)
            output.append(ResolvedNode("path", path.id, path, path_prefix))
    for group in container.groups:
        child = _identity_path(prefix, "groups", group.id)
        output.append(ResolvedNode("group", group.id, group, child))
        _container_nodes(group, child, output)


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


def _origin_span(result: LegacyReadResult, node: ResolvedNode) -> dict[str, int] | None:
    matches = [
        origin
        for origin in result.origins
        if origin.node_type == node.type and origin.node_id == node.id
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
            SetPathFill(target.id, request.color, target.node.fill),
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
            SetPathStroke(target.id, request.color, target.node.stroke),
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
            ReplaceText(target.id, request.text, target.node.text),
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
            ReplaceLinkedImageSource(target.id, request.source, target.node.source),
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
            TranslatePath(target.id, request.dx, request.dy, tuple(target.node.points)),
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


def plan_edit(source: str | FilePath, request_data: object) -> LegacyEditPlan:
    """Resolve, dry-run, and validate a public edit request without writing a file."""

    path = FilePath(source)
    report, input_format = _base_report(path)
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
        },
        "semantic_diff": None,
        "warnings": plan.report.get("warnings", []),
        "stop_reasons": [_stop(code, message)],
    }


def apply_edit_plan(plan: LegacyEditPlan, output: str | FilePath) -> dict[str, object]:
    """Atomically apply a prepared plan to a distinct, non-existing output path."""

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
        },
        "semantic_diff": disk_diff.to_dict(),
        "warnings": [diagnostic.message for diagnostic in disk_after.diagnostics],
        "stop_reasons": [],
    }


def apply_edit(
    source: str | FilePath, request_data: object, output: str | FilePath
) -> dict[str, object]:
    """Plan and apply an operation manifest through the same validated workflow."""

    return apply_edit_plan(plan_edit(source, request_data), output)
