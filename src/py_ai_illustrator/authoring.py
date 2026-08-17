"""Semantic Python authoring components that compile to the graphic IR."""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any
from unicodedata import east_asian_width

from .model import (
    Color,
    ControlPoint,
    Group,
    Layer,
    LayerItemRef,
    Path,
    Point,
    ProcessColor,
    TextFrame,
)

CellFormatter = Callable[[Any], str]
CellAccessor = Callable[[Mapping[str, Any]], Any]


@dataclass(frozen=True, slots=True)
class AffineTransform:
    """A 2D affine matrix used to place reusable rendered components."""

    a: float = 1.0
    b: float = 0.0
    c: float = 0.0
    d: float = 1.0
    tx: float = 0.0
    ty: float = 0.0

    def __post_init__(self) -> None:
        if not all(
            math.isfinite(value) for value in (self.a, self.b, self.c, self.d, self.tx, self.ty)
        ):
            raise ValueError("Affine transform values must be finite")

    @classmethod
    def rotation(
        cls,
        degrees: float,
        *,
        origin_x: float = 0.0,
        origin_y: float = 0.0,
    ) -> AffineTransform:
        radians = math.radians(degrees)
        cosine = math.cos(radians)
        sine = math.sin(radians)
        return cls(
            a=cosine,
            b=sine,
            c=-sine,
            d=cosine,
            tx=origin_x - cosine * origin_x + sine * origin_y,
            ty=origin_y - sine * origin_x - cosine * origin_y,
        )

    @classmethod
    def translation(cls, x: float, y: float) -> AffineTransform:
        return cls(tx=x, ty=y)

    @property
    def rotation_degrees(self) -> float:
        if not self.is_rigid:
            raise ValueError("Text rotation requires a rigid transform")
        return math.degrees(math.atan2(self.b, self.a))

    @property
    def is_rigid(self) -> bool:
        tolerance = 1e-9
        return (
            math.isclose(self.a * self.a + self.b * self.b, 1.0, abs_tol=tolerance)
            and math.isclose(self.c * self.c + self.d * self.d, 1.0, abs_tol=tolerance)
            and math.isclose(self.a * self.c + self.b * self.d, 0.0, abs_tol=tolerance)
            and math.isclose(self.a * self.d - self.b * self.c, 1.0, abs_tol=tolerance)
        )

    def apply(self, x: float, y: float) -> tuple[float, float]:
        return (
            self.a * x + self.c * y + self.tx,
            self.b * x + self.d * y + self.ty,
        )


def _transform_control(
    point: ControlPoint | None, transform: AffineTransform
) -> ControlPoint | None:
    if point is None:
        return None
    x, y = transform.apply(point.x, point.y)
    return ControlPoint(x, y)


def transform_path(path: Path, transform: AffineTransform) -> Path:
    """Return an editable path with anchors and Bézier handles transformed."""

    return replace(
        path,
        points=[
            Point(
                *transform.apply(point.x, point.y),
                in_handle=_transform_control(point.in_handle, transform),
                out_handle=_transform_control(point.out_handle, transform),
                smooth=point.smooth,
            )
            for point in path.points
        ],
    )


def transform_text(text: TextFrame, transform: AffineTransform) -> TextFrame:
    """Return editable point text placed by a rigid affine transform."""

    if not transform.is_rigid:
        raise ValueError("TextFrame currently supports rigid transforms only")
    x, y = transform.apply(text.x, text.y)
    return replace(
        text,
        x=x,
        y=y,
        rotation=text.rotation + transform.rotation_degrees,
    )


def transform_group(group: Group, transform: AffineTransform) -> Group:
    """Transform every editable descendant while preserving group semantics."""

    return replace(
        group,
        paths=[transform_path(path, transform) for path in group.paths],
        text_frames=[transform_text(text, transform) for text in group.text_frames],
        compound_paths=[
            replace(
                compound,
                paths=[transform_path(path, transform) for path in compound.paths],
            )
            for compound in group.compound_paths
        ],
        clipping_groups=[
            replace(
                clipping,
                clipping_path=transform_path(clipping.clipping_path, transform),
                paths=[transform_path(path, transform) for path in clipping.paths],
            )
            for clipping in group.clipping_groups
        ],
        groups=[transform_group(child, transform) for child in group.groups],
    )


@dataclass(slots=True)
class RenderedComponent:
    """A component result that can be composed before becoming an IR layer."""

    width: float
    height: float
    paths: list[Path] = field(default_factory=list)
    text_frames: list[TextFrame] = field(default_factory=list)
    groups: list[Group] = field(default_factory=list)
    item_order: list[LayerItemRef] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.width < 0 or self.height < 0:
            raise ValueError("Rendered component dimensions must not be negative")
        if not self.item_order:
            self.item_order = [
                *(LayerItemRef("path", path.id) for path in self.paths),
                *(LayerItemRef("text", text.id) for text in self.text_frames),
                *(LayerItemRef("group", group.id) for group in self.groups),
            ]

    def as_layer(self, *, layer_id: str, layer_name: str) -> Layer:
        return Layer(
            id=layer_id,
            name=layer_name,
            paths=list(self.paths),
            text_frames=list(self.text_frames),
            groups=list(self.groups),
            item_order=list(self.item_order),
        )

    def as_group(self, *, group_id: str, group_name: str | None = None) -> Group:
        return Group(
            id=group_id,
            name=group_name,
            paths=list(self.paths),
            text_frames=list(self.text_frames),
            groups=list(self.groups),
            item_order=list(self.item_order),
        )

    def transformed(self, transform: AffineTransform) -> RenderedComponent:
        """Place a component without discarding editable child identities."""

        if self.text_frames and not transform.is_rigid:
            raise ValueError("Components containing text currently require a rigid transform")
        width = abs(transform.a) * self.width + abs(transform.c) * self.height
        height = abs(transform.b) * self.width + abs(transform.d) * self.height
        return RenderedComponent(
            width=width,
            height=height,
            paths=[transform_path(path, transform) for path in self.paths],
            text_frames=[transform_text(text, transform) for text in self.text_frames],
            groups=[transform_group(group, transform) for group in self.groups],
            item_order=list(self.item_order),
        )


@dataclass(slots=True)
class LayerBuilder:
    """Compose independently rendered semantic components into one editable layer."""

    id: str
    name: str
    _paths: list[Path] = field(default_factory=list, init=False, repr=False)
    _text_frames: list[TextFrame] = field(default_factory=list, init=False, repr=False)
    _groups: list[Group] = field(default_factory=list, init=False, repr=False)
    _item_order: list[LayerItemRef] = field(default_factory=list, init=False, repr=False)
    _ids: set[str] = field(default_factory=set, init=False, repr=False)

    def _claim(self, item_id: str) -> None:
        if item_id in self._ids:
            raise ValueError(f"Duplicate item id in layer {self.id!r}: {item_id!r}")
        self._ids.add(item_id)

    def add_path(self, path: Path) -> None:
        self._claim(path.id)
        self._paths.append(path)
        self._item_order.append(LayerItemRef("path", path.id))

    def add_text(self, text: TextFrame) -> None:
        self._claim(text.id)
        self._text_frames.append(text)
        self._item_order.append(LayerItemRef("text", text.id))

    def add_group(self, group: Group) -> None:
        self._claim(group.id)
        self._groups.append(group)
        self._item_order.append(LayerItemRef("group", group.id))

    def add_grouped(
        self,
        component: RenderedComponent,
        *,
        group_id: str,
        group_name: str | None = None,
    ) -> Group:
        """Keep a rendered component movable as one editable Illustrator group."""

        group = component.as_group(group_id=group_id, group_name=group_name)
        self.add_group(group)
        return group

    def add(self, component: RenderedComponent) -> None:
        paths = {path.id: path for path in component.paths}
        text_frames = {text.id: text for text in component.text_frames}
        groups = {group.id: group for group in component.groups}
        for reference in component.item_order:
            if reference.kind == "path":
                self.add_path(paths[reference.id])
            elif reference.kind == "text":
                self.add_text(text_frames[reference.id])
            elif reference.kind == "group":
                self.add_group(groups[reference.id])
            else:
                raise ValueError(f"Rendered components do not yet support {reference.kind!r} items")

    def build(self) -> Layer:
        return Layer(
            id=self.id,
            name=self.name,
            paths=list(self._paths),
            text_frames=list(self._text_frames),
            groups=list(self._groups),
            item_order=list(self._item_order),
        )


@dataclass(frozen=True, slots=True)
class FontSpec:
    """A font that works in both the legacy bridge and native Illustrator.

    ``postscript_name`` is Illustrator's installed font name. ``legacy_name``
    is only needed when the AI7 bridge requires a different composite font
    name, as it does for RKSJ-encoded Japanese text.
    """

    postscript_name: str
    family: str | None = None
    style: str | None = None
    legacy_name: str | None = None

    def __post_init__(self) -> None:
        for label, value in (
            ("postscript_name", self.postscript_name),
            ("legacy_name", self.legacy_name),
        ):
            if value is not None and (not value or any(char.isspace() for char in value)):
                raise ValueError(f"{label} must be a non-empty PostScript name")

    @property
    def ai7_name(self) -> str:
        return self.legacy_name or self.postscript_name


@dataclass(frozen=True, slots=True)
class TextStyle:
    """Reusable typography for semantic text blocks."""

    font_size: float = 12.0
    font_name: str = "Helvetica"
    font: FontSpec | None = None
    tracking: float = 0.0
    rotation: float = 0.0
    fill: ProcessColor = field(default_factory=lambda: Color(0.0, 0.0, 0.0))
    line_height_ratio: float = 1.25

    def __post_init__(self) -> None:
        if self.font_size <= 0 or self.line_height_ratio <= 0:
            raise ValueError("Text size and line height must be positive")
        if not math.isfinite(self.tracking):
            raise ValueError("tracking must be finite")
        if not math.isfinite(self.rotation):
            raise ValueError("rotation must be finite")

    @property
    def ai7_font_name(self) -> str:
        return self.font.ai7_name if self.font is not None else self.font_name

    @property
    def native_font_name(self) -> str:
        return self.font.postscript_name if self.font is not None else self.font_name


@dataclass(frozen=True, slots=True)
class TextBlock:
    """Meaningful text that wraps into editable, natively aligned point text."""

    id: str
    text: str
    width: float
    style: TextStyle = field(default_factory=TextStyle)
    alignment: str = "left"
    wrap: bool = True
    name: str | None = None

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("A text block id must not be empty")
        if self.width <= 0:
            raise ValueError("A text block width must be positive")
        if self.alignment not in {"left", "center", "right"}:
            raise ValueError("alignment must be 'left', 'center', or 'right'")

    @property
    def lines(self) -> tuple[str, ...]:
        if self.wrap:
            return _wrap_text(
                self.text,
                max_width=self.width,
                font_size=self.style.font_size,
            )
        return tuple(self.text.replace("\r\n", "\n").replace("\r", "\n").split("\n"))

    @property
    def height(self) -> float:
        return self.style.font_size + (len(self.lines) - 1) * (
            self.style.font_size * self.style.line_height_ratio
        )

    def render(self, *, x: float, top: float) -> RenderedComponent:
        if self.alignment == "right":
            anchor_x = x + self.width
        elif self.alignment == "center":
            anchor_x = x + self.width / 2
        else:
            anchor_x = x
        line_height = self.style.font_size * self.style.line_height_ratio
        frames = [
            TextFrame(
                id=f"{self.id}.line-{index}",
                name=self.name or self.id,
                text=value,
                x=anchor_x,
                y=top - self.style.font_size * 0.8 - index * line_height,
                font_size=self.style.font_size,
                font_name=self.style.ai7_font_name,
                native_font_name=self.style.native_font_name,
                tracking=self.style.tracking,
                rotation=self.style.rotation,
                fill=self.style.fill,
                alignment=self.alignment,
            )
            for index, value in enumerate(self.lines)
        ]
        return RenderedComponent(
            width=self.width,
            height=self.height,
            text_frames=frames,
        )


@dataclass(frozen=True, slots=True)
class AreaTextBlock:
    """A paragraph that becomes one reflowable native Illustrator area text."""

    id: str
    text: str
    width: float
    height: float
    style: TextStyle = field(default_factory=TextStyle)
    alignment: str = "left"
    name: str | None = None

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("An area text block id must not be empty")
        if self.width <= 0 or self.height <= 0:
            raise ValueError("Area text block dimensions must be positive")
        if self.alignment not in {"left", "center", "right"}:
            raise ValueError("alignment must be 'left', 'center', or 'right'")

    def render(self, *, x: float, top: float) -> RenderedComponent:
        frame = TextFrame(
            id=self.id,
            name=self.name or self.id,
            text=self.text.replace("\r\n", "\n").replace("\r", "\n"),
            x=x,
            y=top,
            font_size=self.style.font_size,
            font_name=self.style.ai7_font_name,
            native_font_name=self.style.native_font_name,
            tracking=self.style.tracking,
            rotation=self.style.rotation,
            area_width=self.width,
            area_height=self.height,
            leading=self.style.font_size * self.style.line_height_ratio,
            fill=self.style.fill,
            alignment=self.alignment,
        )
        return RenderedComponent(
            width=self.width,
            height=self.height,
            text_frames=[frame],
        )


def rectangle_path(
    item_id: str,
    *,
    x: float,
    top: float,
    width: float,
    height: float,
    fill: ProcessColor | None,
    stroke: ProcessColor | None = None,
    stroke_width: float = 1.0,
    name: str | None = None,
) -> Path:
    """Create an editable rectangle using top-left page coordinates."""

    if width <= 0 or height <= 0:
        raise ValueError("Rectangle dimensions must be positive")
    return Path(
        id=item_id,
        name=name,
        points=[
            Point(x, top - height),
            Point(x + width, top - height),
            Point(x + width, top),
            Point(x, top),
        ],
        fill=fill,
        stroke=stroke,
        stroke_width=stroke_width,
    )


def polyline_path(
    item_id: str,
    *,
    points: Sequence[tuple[float, float]],
    stroke: ProcessColor,
    stroke_width: float = 1.0,
    dash_pattern: Sequence[float] = (),
    dash_offset: float = 0.0,
    line_cap: str = "butt",
    line_join: str = "miter",
    name: str | None = None,
) -> Path:
    """Create an editable open polyline with native Illustrator stroke styling."""

    return Path(
        id=item_id,
        name=name,
        points=[Point(x, y) for x, y in points],
        closed=False,
        fill=None,
        stroke=stroke,
        stroke_width=stroke_width,
        dash_pattern=list(dash_pattern),
        dash_offset=dash_offset,
        line_cap=line_cap,
        line_join=line_join,
    )


def ellipse_path(
    item_id: str,
    *,
    center_x: float,
    center_y: float,
    radius_x: float,
    radius_y: float,
    fill: ProcessColor | None,
    stroke: ProcessColor | None = None,
    stroke_width: float = 1.0,
    name: str | None = None,
) -> Path:
    """Create an editable four-segment cubic Bézier ellipse."""

    if radius_x <= 0 or radius_y <= 0:
        raise ValueError("Ellipse radii must be positive")
    kappa = 0.5522847498307936
    x_handle = radius_x * kappa
    y_handle = radius_y * kappa
    return Path(
        id=item_id,
        name=name,
        points=[
            Point(
                center_x + radius_x,
                center_y,
                in_handle=ControlPoint(center_x + radius_x, center_y - y_handle),
                out_handle=ControlPoint(center_x + radius_x, center_y + y_handle),
                smooth=True,
            ),
            Point(
                center_x,
                center_y + radius_y,
                in_handle=ControlPoint(center_x + x_handle, center_y + radius_y),
                out_handle=ControlPoint(center_x - x_handle, center_y + radius_y),
                smooth=True,
            ),
            Point(
                center_x - radius_x,
                center_y,
                in_handle=ControlPoint(center_x - radius_x, center_y + y_handle),
                out_handle=ControlPoint(center_x - radius_x, center_y - y_handle),
                smooth=True,
            ),
            Point(
                center_x,
                center_y - radius_y,
                in_handle=ControlPoint(center_x - x_handle, center_y - radius_y),
                out_handle=ControlPoint(center_x + x_handle, center_y - radius_y),
                smooth=True,
            ),
        ],
        fill=fill,
        stroke=stroke,
        stroke_width=stroke_width,
    )


def _character_width_units(character: str) -> float:
    if east_asian_width(character) in {"F", "W"}:
        return 1.0
    if character in " .,:;!|'`ijlItfr()[]":
        return 0.3
    if character in "MW@%&QG":
        return 0.85
    if character.isupper():
        return 0.67
    if character.isdigit() or character in "$+-=/":
        return 0.56
    return 0.52


def _estimated_text_width(value: str, font_size: float) -> float:
    """Estimate point-text width with Latin and East Asian width classes."""

    return sum(_character_width_units(character) for character in value) * font_size


def _wrap_text(value: str, *, max_width: float, font_size: float) -> tuple[str, ...]:
    """Greedily wrap Latin words and East Asian text to a measured width."""

    lines: list[str] = []
    for paragraph in value.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if not paragraph:
            lines.append("")
            continue
        current = ""
        for character in paragraph:
            candidate = current + character
            if not current or _estimated_text_width(candidate, font_size) <= max_width:
                current = candidate
                continue
            break_at = current.rfind(" ")
            if break_at >= 0:
                line = current[:break_at].rstrip()
                remainder = current[break_at + 1 :].lstrip() + character
                if line:
                    lines.append(line)
                    current = remainder
                    continue
            lines.append(current.rstrip())
            current = character.lstrip() if character.isspace() else character
        lines.append(current.rstrip())
    return tuple(lines or [""])


@dataclass(frozen=True, slots=True)
class _TableLayout:
    header_lines: tuple[tuple[str, ...], ...]
    header_height: float
    row_lines: tuple[tuple[tuple[str, ...], ...], ...]
    row_heights: tuple[float, ...]


@dataclass(frozen=True, slots=True)
class TableColumn:
    """A semantic table column, including value lookup and presentation."""

    key: str
    title: str
    width: float
    alignment: str = "left"
    wrap: bool = False
    formatter: CellFormatter | None = None
    accessor: CellAccessor | None = None

    def __post_init__(self) -> None:
        if not self.key:
            raise ValueError("A table column key must not be empty")
        if self.width <= 0:
            raise ValueError("A table column width must be positive")
        if self.alignment not in {"left", "center", "right"}:
            raise ValueError("alignment must be 'left', 'center', or 'right'")

    def text_for(self, row: Mapping[str, Any]) -> str:
        value = self.accessor(row) if self.accessor is not None else row.get(self.key, "")
        return str(self.formatter(value)) if self.formatter is not None else str(value)


@dataclass(frozen=True, slots=True)
class TableStyle:
    """Reusable visual rules for a family of tables."""

    header_height: float = 34.0
    row_height: float = 30.0
    padding_x: float = 10.0
    padding_y: float = 6.0
    line_height_ratio: float = 1.25
    header_fill: ProcessColor = field(default_factory=lambda: Color(0.08, 0.16, 0.28))
    body_fill: ProcessColor = field(default_factory=lambda: Color(1.0, 1.0, 1.0))
    alternate_fill: ProcessColor | None = field(default_factory=lambda: Color(0.96, 0.97, 0.98))
    variant_fills: Mapping[str, ProcessColor] = field(default_factory=dict)
    header_text_color: ProcessColor = field(default_factory=lambda: Color(1.0, 1.0, 1.0))
    body_text_color: ProcessColor = field(default_factory=lambda: Color(0.12, 0.15, 0.2))
    variant_text_colors: Mapping[str, ProcessColor] = field(default_factory=dict)
    border_color: ProcessColor = field(default_factory=lambda: Color(0.72, 0.75, 0.8))
    border_width: float = 0.75
    header_font_name: str = "Helvetica-Bold"
    body_font_name: str = "Helvetica"
    header_font: FontSpec | None = None
    body_font: FontSpec | None = None
    header_tracking: float = 0.0
    body_tracking: float = 0.0
    header_font_size: float = 11.0
    body_font_size: float = 10.0

    def __post_init__(self) -> None:
        positive = (
            self.header_height,
            self.row_height,
            self.header_font_size,
            self.body_font_size,
            self.line_height_ratio,
        )
        if not all(value > 0 for value in positive):
            raise ValueError("Table heights and font sizes must be positive")
        if self.padding_x < 0 or self.padding_y < 0 or self.border_width < 0:
            raise ValueError("Table padding and border width must not be negative")
        if not all(math.isfinite(value) for value in (self.header_tracking, self.body_tracking)):
            raise ValueError("Table tracking must be finite")


@dataclass(slots=True)
class Table:
    """Meaningful rows and columns that deterministically render to editable art."""

    id: str
    columns: Sequence[TableColumn]
    rows: Sequence[Mapping[str, Any]]
    style: TableStyle = field(default_factory=TableStyle)
    variant_key: str | None = None

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("A table id must not be empty")
        if not self.columns:
            raise ValueError("A table needs at least one column")
        keys = [column.key for column in self.columns]
        if len(set(keys)) != len(keys):
            raise ValueError("Table column keys must be unique")

    @property
    def width(self) -> float:
        return sum(column.width for column in self.columns)

    def _layout(self) -> _TableLayout:
        def lines_for(column: TableColumn, value: str, font_size: float) -> tuple[str, ...]:
            if not column.wrap:
                return tuple(value.replace("\r\n", "\n").replace("\r", "\n").split("\n"))
            return _wrap_text(
                value,
                max_width=max(column.width - 2 * self.style.padding_x, 1.0),
                font_size=font_size,
            )

        def required_height(lines: Sequence[Sequence[str]], size: float) -> float:
            count = max((len(cell) for cell in lines), default=1)
            content = size + (count - 1) * size * self.style.line_height_ratio
            return content + 2 * self.style.padding_y

        header_lines = tuple(
            lines_for(column, column.title, self.style.header_font_size) for column in self.columns
        )
        header_height = max(
            self.style.header_height,
            required_height(header_lines, self.style.header_font_size),
        )
        row_lines = tuple(
            tuple(
                lines_for(column, column.text_for(row), self.style.body_font_size)
                for column in self.columns
            )
            for row in self.rows
        )
        row_heights = tuple(
            max(
                self.style.row_height,
                required_height(lines, self.style.body_font_size),
            )
            for lines in row_lines
        )
        return _TableLayout(header_lines, header_height, row_lines, row_heights)

    @property
    def height(self) -> float:
        layout = self._layout()
        return layout.header_height + sum(layout.row_heights)

    def render(
        self,
        *,
        x: float,
        top: float,
    ) -> RenderedComponent:
        """Compile the table into composable paths and point text."""

        layout = self._layout()
        paths: list[Path] = []
        text_frames: list[TextFrame] = []
        order: list[LayerItemRef] = []

        def rectangle(item_id: str, row_top: float, height: float, fill: ProcessColor) -> None:
            path = Path(
                id=item_id,
                name=item_id,
                points=[
                    Point(x, row_top - height),
                    Point(x + self.width, row_top - height),
                    Point(x + self.width, row_top),
                    Point(x, row_top),
                ],
                fill=fill,
                stroke=None,
            )
            paths.append(path)
            order.append(LayerItemRef("path", path.id))

        rectangle(
            f"{self.id}.background.header",
            top,
            layout.header_height,
            self.style.header_fill,
        )
        row_tops: list[float] = []
        row_top = top - layout.header_height
        for row_index, row in enumerate(self.rows):
            row_tops.append(row_top)
            variant = str(row.get(self.variant_key, "")) if self.variant_key else ""
            fill = self.style.variant_fills.get(variant)
            if fill is None:
                fill = (
                    self.style.alternate_fill
                    if row_index % 2 and self.style.alternate_fill is not None
                    else self.style.body_fill
                )
            rectangle(
                f"{self.id}.background.row-{row_index}",
                row_top,
                layout.row_heights[row_index],
                fill,
            )
            row_top -= layout.row_heights[row_index]

        horizontal_positions = [
            top,
            top - layout.header_height,
            *(row_top - layout.row_heights[index] for index, row_top in enumerate(row_tops)),
        ]
        for line_index, line_y in enumerate(horizontal_positions):
            line = Path(
                id=f"{self.id}.grid.horizontal-{line_index}",
                points=[Point(x, line_y), Point(x + self.width, line_y)],
                closed=False,
                fill=None,
                stroke=self.style.border_color,
                stroke_width=self.style.border_width,
            )
            paths.append(line)
            order.append(LayerItemRef("path", line.id))

        column_edges = [x]
        for column in self.columns:
            column_edges.append(column_edges[-1] + column.width)
        for line_index, line_x in enumerate(column_edges):
            line = Path(
                id=f"{self.id}.grid.vertical-{line_index}",
                points=[
                    Point(line_x, top),
                    Point(line_x, top - layout.header_height - sum(layout.row_heights)),
                ],
                closed=False,
                fill=None,
                stroke=self.style.border_color,
                stroke_width=self.style.border_width,
            )
            paths.append(line)
            order.append(LayerItemRef("path", line.id))

        def text_x(
            column_index: int,
            alignment: str,
        ) -> float:
            left = column_edges[column_index]
            right = column_edges[column_index + 1]
            if alignment == "right":
                return right - self.style.padding_x
            if alignment == "center":
                return (left + right) / 2
            return left + self.style.padding_x

        def baselines(
            row_top: float,
            height: float,
            size: float,
            line_count: int,
        ) -> tuple[float, ...]:
            line_height = size * self.style.line_height_ratio
            content_height = size + (line_count - 1) * line_height
            first = row_top - (height - content_height) / 2 - size * 0.8
            return tuple(first - index * line_height for index in range(line_count))

        for column_index, column in enumerate(self.columns):
            lines = layout.header_lines[column_index]
            line_baselines = baselines(
                top,
                layout.header_height,
                self.style.header_font_size,
                len(lines),
            )
            for line_index, (value, line_y) in enumerate(zip(lines, line_baselines, strict=True)):
                suffix = f".line-{line_index}" if len(lines) > 1 else ""
                text = TextFrame(
                    id=f"{self.id}.header.{column.key}{suffix}",
                    name=f"Header: {column.title}",
                    text=value,
                    x=text_x(
                        column_index,
                        column.alignment,
                    ),
                    y=line_y,
                    font_size=self.style.header_font_size,
                    font_name=(
                        self.style.header_font.ai7_name
                        if self.style.header_font is not None
                        else self.style.header_font_name
                    ),
                    native_font_name=(
                        self.style.header_font.postscript_name
                        if self.style.header_font is not None
                        else self.style.header_font_name
                    ),
                    tracking=self.style.header_tracking,
                    fill=self.style.header_text_color,
                    alignment=column.alignment,
                )
                text_frames.append(text)
                order.append(LayerItemRef("text", text.id))

        for row_index, row in enumerate(self.rows):
            variant = str(row.get(self.variant_key, "")) if self.variant_key else ""
            color = self.style.variant_text_colors.get(variant, self.style.body_text_color)
            row_top = row_tops[row_index]
            row_height = layout.row_heights[row_index]
            for column_index, column in enumerate(self.columns):
                lines = layout.row_lines[row_index][column_index]
                line_baselines = baselines(
                    row_top,
                    row_height,
                    self.style.body_font_size,
                    len(lines),
                )
                for line_index, (value, line_y) in enumerate(
                    zip(lines, line_baselines, strict=True)
                ):
                    suffix = f".line-{line_index}" if len(lines) > 1 else ""
                    text = TextFrame(
                        id=f"{self.id}.row-{row_index}.{column.key}{suffix}",
                        name=f"Row {row_index + 1}: {column.title}",
                        text=value,
                        x=text_x(
                            column_index,
                            column.alignment,
                        ),
                        y=line_y,
                        font_size=self.style.body_font_size,
                        font_name=(
                            self.style.body_font.ai7_name
                            if self.style.body_font is not None
                            else self.style.body_font_name
                        ),
                        native_font_name=(
                            self.style.body_font.postscript_name
                            if self.style.body_font is not None
                            else self.style.body_font_name
                        ),
                        tracking=self.style.body_tracking,
                        fill=color,
                        alignment=column.alignment,
                    )
                    text_frames.append(text)
                    order.append(LayerItemRef("text", text.id))

        return RenderedComponent(
            width=self.width,
            height=self.height,
            paths=paths,
            text_frames=text_frames,
            item_order=order,
        )

    def render_layer(
        self,
        *,
        x: float,
        top: float,
        layer_id: str | None = None,
        layer_name: str = "Table",
    ) -> Layer:
        """Compile the table into a standalone editable IR layer."""

        return self.render(x=x, top=top).as_layer(
            layer_id=layer_id or f"{self.id}.layer",
            layer_name=layer_name,
        )
