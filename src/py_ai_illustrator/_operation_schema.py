"""Versioned operation-manifest schema.

This module owns manifest and selector validation. It has no dependency on
planning or apply orchestration, so schema consumers can validate requests
without importing mutation code.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Literal

from .model import CmykColor, Color, ProcessColor

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

__all__ = [
    "AncestorSelector",
    "ContainerSelectorType",
    "OperationManifest",
    "OperationName",
    "OperationRequest",
    "OperationRequestError",
    "Selector",
    "SelectorType",
]
