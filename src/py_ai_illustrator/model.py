"""Small, stable intermediate representation for the Phase 0 feature profile."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class ControlPoint:
    """A Bézier control handle in document coordinates."""

    x: float
    y: float

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ControlPoint:
        return cls(float(data["x"]), float(data["y"]))


@dataclass(frozen=True, slots=True)
class Point:
    """A path anchor and its optional incoming/outgoing Bézier handles."""

    x: float
    y: float
    in_handle: ControlPoint | None = None
    out_handle: ControlPoint | None = None
    smooth: bool = False

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Point:
        return cls(
            x=float(data["x"]),
            y=float(data["y"]),
            in_handle=(
                ControlPoint.from_dict(data["in_handle"])
                if data.get("in_handle") is not None
                else None
            ),
            out_handle=(
                ControlPoint.from_dict(data["out_handle"])
                if data.get("out_handle") is not None
                else None
            ),
            smooth=bool(data.get("smooth", False)),
        )

    def with_out_handle(self, handle: ControlPoint | None) -> Point:
        return Point(self.x, self.y, self.in_handle, handle, self.smooth)


@dataclass(frozen=True, slots=True)
class Color:
    """An RGB process color with normalized 0..1 components."""

    red: float
    green: float
    blue: float

    def __post_init__(self) -> None:
        if not all(0.0 <= value <= 1.0 for value in (self.red, self.green, self.blue)):
            raise ValueError("RGB components must be between 0.0 and 1.0")

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Color:
        return cls(float(data["red"]), float(data["green"]), float(data["blue"]))


@dataclass(frozen=True, slots=True)
class CmykColor:
    """A CMYK process color with normalized 0..1 components."""

    cyan: float
    magenta: float
    yellow: float
    black: float

    def __post_init__(self) -> None:
        values = (self.cyan, self.magenta, self.yellow, self.black)
        if not all(0.0 <= value <= 1.0 for value in values):
            raise ValueError("CMYK components must be between 0.0 and 1.0")

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CmykColor:
        return cls(
            float(data["cyan"]),
            float(data["magenta"]),
            float(data["yellow"]),
            float(data["black"]),
        )


ProcessColor = Color | CmykColor


def _process_color_from_dict(data: dict[str, Any]) -> ProcessColor:
    if {"cyan", "magenta", "yellow", "black"}.issubset(data):
        return CmykColor.from_dict(data)
    if {"red", "green", "blue"}.issubset(data):
        return Color.from_dict(data)
    raise ValueError("A process color must contain RGB or CMYK components")


@dataclass(slots=True)
class Path:
    id: str
    points: list[Point]
    closed: bool = True
    fill: ProcessColor | None = None
    stroke: ProcessColor | None = None
    stroke_width: float = 1.0
    name: str | None = None
    unknown: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if len(self.points) < 2:
            raise ValueError("A path needs at least two points")
        if self.stroke_width < 0:
            raise ValueError("stroke_width must not be negative")

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Path:
        return cls(
            id=str(data["id"]),
            name=data.get("name"),
            points=[Point.from_dict(point) for point in data["points"]],
            closed=bool(data.get("closed", True)),
            fill=(_process_color_from_dict(data["fill"]) if data.get("fill") is not None else None),
            stroke=(
                _process_color_from_dict(data["stroke"]) if data.get("stroke") is not None else None
            ),
            stroke_width=float(data.get("stroke_width", 1.0)),
            unknown=dict(data.get("unknown", {})),
        )


@dataclass(slots=True)
class Layer:
    id: str
    name: str
    paths: list[Path] = field(default_factory=list)
    visible: bool = True
    locked: bool = False
    unknown: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Layer:
        return cls(
            id=str(data["id"]),
            name=str(data["name"]),
            paths=[Path.from_dict(path) for path in data.get("paths", [])],
            visible=bool(data.get("visible", True)),
            locked=bool(data.get("locked", False)),
            unknown=dict(data.get("unknown", {})),
        )


@dataclass(slots=True)
class Document:
    width: float
    height: float
    layers: list[Layer] = field(default_factory=list)
    title: str = "Untitled"
    version: int = 1
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.width <= 0 or self.height <= 0:
            raise ValueError("Document width and height must be positive")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Document:
        return cls(
            width=float(data["width"]),
            height=float(data["height"]),
            title=str(data.get("title", "Untitled")),
            version=int(data.get("version", 1)),
            layers=[Layer.from_dict(layer) for layer in data.get("layers", [])],
            metadata=dict(data.get("metadata", {})),
        )
