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
    polarity: str = "positive"
    unknown: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if len(self.points) < 2:
            raise ValueError("A path needs at least two points")
        if self.stroke_width < 0:
            raise ValueError("stroke_width must not be negative")
        if self.polarity not in {"positive", "negative"}:
            raise ValueError("polarity must be 'positive' or 'negative'")

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
            polarity=str(data.get("polarity", "positive")),
            unknown=dict(data.get("unknown", {})),
        )


@dataclass(slots=True)
class TextFrame:
    """Editable point text positioned in document coordinates."""

    id: str
    text: str
    x: float
    y: float
    font_size: float = 12.0
    font_name: str = "Helvetica"
    fill: ProcessColor = field(default_factory=lambda: Color(0.0, 0.0, 0.0))
    alignment: str = "left"
    name: str | None = None
    unknown: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.font_size <= 0:
            raise ValueError("font_size must be positive")
        if self.alignment not in {"left", "center", "right"}:
            raise ValueError("alignment must be 'left', 'center', or 'right'")

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TextFrame:
        return cls(
            id=str(data["id"]),
            text=str(data["text"]),
            x=float(data["x"]),
            y=float(data["y"]),
            font_size=float(data.get("font_size", 12.0)),
            font_name=str(data.get("font_name", "Helvetica")),
            fill=_process_color_from_dict(
                data.get("fill", {"red": 0.0, "green": 0.0, "blue": 0.0})
            ),
            alignment=str(data.get("alignment", "left")),
            name=data.get("name"),
            unknown=dict(data.get("unknown", {})),
        )


@dataclass(slots=True)
class CompoundPath:
    id: str
    paths: list[Path]
    name: str | None = None
    unknown: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if len(self.paths) < 2:
            raise ValueError("A compound path needs at least two component paths")

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CompoundPath:
        return cls(
            id=str(data["id"]),
            name=data.get("name"),
            paths=[Path.from_dict(path) for path in data.get("paths", [])],
            unknown=dict(data.get("unknown", {})),
        )


@dataclass(slots=True)
class ClippingGroup:
    id: str
    clipping_path: Path
    paths: list[Path]
    name: str | None = None
    unknown: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.paths:
            raise ValueError("A clipping group needs at least one content path")

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ClippingGroup:
        return cls(
            id=str(data["id"]),
            name=data.get("name"),
            clipping_path=Path.from_dict(data["clipping_path"]),
            paths=[Path.from_dict(path) for path in data.get("paths", [])],
            unknown=dict(data.get("unknown", {})),
        )


@dataclass(frozen=True, slots=True)
class LayerItemRef:
    kind: str
    id: str

    def __post_init__(self) -> None:
        if self.kind not in {
            "path",
            "text",
            "compound_path",
            "clipping_group",
            "group",
        }:
            raise ValueError(f"Unsupported layer item kind: {self.kind}")

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> LayerItemRef:
        return cls(kind=str(data["kind"]), id=str(data["id"]))


@dataclass(slots=True)
class Group:
    """An editable group that may contain heterogeneous and nested artwork."""

    id: str
    name: str | None = None
    paths: list[Path] = field(default_factory=list)
    text_frames: list[TextFrame] = field(default_factory=list)
    compound_paths: list[CompoundPath] = field(default_factory=list)
    clipping_groups: list[ClippingGroup] = field(default_factory=list)
    groups: list[Group] = field(default_factory=list)
    item_order: list[LayerItemRef] = field(default_factory=list)
    unknown: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("A group id must not be empty")
        if not self.item_order:
            self.item_order = [
                *(LayerItemRef("path", path.id) for path in self.paths),
                *(LayerItemRef("text", text.id) for text in self.text_frames),
                *(
                    LayerItemRef("compound_path", compound.id)
                    for compound in self.compound_paths
                ),
                *(
                    LayerItemRef("clipping_group", group.id)
                    for group in self.clipping_groups
                ),
                *(LayerItemRef("group", group.id) for group in self.groups),
            ]

    def ordered_items(
        self,
    ) -> list[Path | TextFrame | CompoundPath | ClippingGroup | Group]:
        typed_items: list[
            tuple[str, Path | TextFrame | CompoundPath | ClippingGroup | Group]
        ] = [
            *(("path", path) for path in self.paths),
            *(("text", text) for text in self.text_frames),
            *(("compound_path", compound) for compound in self.compound_paths),
            *(("clipping_group", group) for group in self.clipping_groups),
            *(("group", group) for group in self.groups),
        ]
        remaining = list(typed_items)
        ordered: list[Path | TextFrame | CompoundPath | ClippingGroup | Group] = []
        for reference in self.item_order:
            for index, (kind, item) in enumerate(remaining):
                if kind == reference.kind and item.id == reference.id:
                    ordered.append(item)
                    remaining.pop(index)
                    break
        ordered.extend(item for _, item in remaining)
        return ordered

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Group:
        return cls(
            id=str(data["id"]),
            name=data.get("name"),
            paths=[Path.from_dict(path) for path in data.get("paths", [])],
            text_frames=[
                TextFrame.from_dict(text) for text in data.get("text_frames", [])
            ],
            compound_paths=[
                CompoundPath.from_dict(path) for path in data.get("compound_paths", [])
            ],
            clipping_groups=[
                ClippingGroup.from_dict(group) for group in data.get("clipping_groups", [])
            ],
            groups=[Group.from_dict(group) for group in data.get("groups", [])],
            item_order=[
                LayerItemRef.from_dict(reference)
                for reference in data.get("item_order", [])
            ],
            unknown=dict(data.get("unknown", {})),
        )


@dataclass(slots=True)
class Layer:
    id: str
    name: str
    paths: list[Path] = field(default_factory=list)
    text_frames: list[TextFrame] = field(default_factory=list)
    compound_paths: list[CompoundPath] = field(default_factory=list)
    clipping_groups: list[ClippingGroup] = field(default_factory=list)
    groups: list[Group] = field(default_factory=list)
    item_order: list[LayerItemRef] = field(default_factory=list)
    visible: bool = True
    locked: bool = False
    unknown: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.item_order:
            self.item_order = [
                *(LayerItemRef("path", path.id) for path in self.paths),
                *(LayerItemRef("text", text.id) for text in self.text_frames),
                *(
                    LayerItemRef("compound_path", compound.id)
                    for compound in self.compound_paths
                ),
                *(
                    LayerItemRef("clipping_group", group.id)
                    for group in self.clipping_groups
                ),
                *(LayerItemRef("group", group.id) for group in self.groups),
            ]

    def ordered_items(
        self,
    ) -> list[Path | TextFrame | CompoundPath | ClippingGroup | Group]:
        typed_items: list[
            tuple[str, Path | TextFrame | CompoundPath | ClippingGroup | Group]
        ] = [
            *(("path", path) for path in self.paths),
            *(("text", text) for text in self.text_frames),
            *(("compound_path", compound) for compound in self.compound_paths),
            *(("clipping_group", group) for group in self.clipping_groups),
            *(("group", group) for group in self.groups),
        ]
        remaining = list(typed_items)
        ordered: list[Path | TextFrame | CompoundPath | ClippingGroup | Group] = []
        for reference in self.item_order:
            for index, (kind, item) in enumerate(remaining):
                if kind == reference.kind and item.id == reference.id:
                    ordered.append(item)
                    remaining.pop(index)
                    break
        ordered.extend(item for _, item in remaining)
        return ordered

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Layer:
        return cls(
            id=str(data["id"]),
            name=str(data["name"]),
            paths=[Path.from_dict(path) for path in data.get("paths", [])],
            text_frames=[
                TextFrame.from_dict(text) for text in data.get("text_frames", [])
            ],
            compound_paths=[
                CompoundPath.from_dict(path) for path in data.get("compound_paths", [])
            ],
            clipping_groups=[
                ClippingGroup.from_dict(group) for group in data.get("clipping_groups", [])
            ],
            groups=[Group.from_dict(group) for group in data.get("groups", [])],
            item_order=[
                LayerItemRef.from_dict(reference)
                for reference in data.get("item_order", [])
            ],
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
