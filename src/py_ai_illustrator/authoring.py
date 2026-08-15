"""Semantic Python authoring components that compile to the graphic IR."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from .model import Color, Layer, LayerItemRef, Path, Point, ProcessColor, TextFrame

CellFormatter = Callable[[Any], str]
CellAccessor = Callable[[Mapping[str, Any]], Any]


def _estimated_text_width(value: str, font_size: float) -> float:
    """Estimate Latin point-text width using conservative Helvetica-like metrics."""

    narrow = " .,:;!|'`ijlItfr()[]"
    wide = "MW@%&QG"
    units = 0.0
    for character in value:
        if character in narrow:
            units += 0.3
        elif character in wide:
            units += 0.85
        elif character.isupper():
            units += 0.67
        elif character.isdigit() or character in "$+-=/":
            units += 0.56
        else:
            units += 0.52
    return units * font_size


@dataclass(frozen=True, slots=True)
class TableColumn:
    """A semantic table column, including value lookup and presentation."""

    key: str
    title: str
    width: float
    alignment: str = "left"
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
    header_fill: ProcessColor = field(
        default_factory=lambda: Color(0.08, 0.16, 0.28)
    )
    body_fill: ProcessColor = field(default_factory=lambda: Color(1.0, 1.0, 1.0))
    alternate_fill: ProcessColor | None = field(
        default_factory=lambda: Color(0.96, 0.97, 0.98)
    )
    variant_fills: Mapping[str, ProcessColor] = field(default_factory=dict)
    header_text_color: ProcessColor = field(
        default_factory=lambda: Color(1.0, 1.0, 1.0)
    )
    body_text_color: ProcessColor = field(
        default_factory=lambda: Color(0.12, 0.15, 0.2)
    )
    variant_text_colors: Mapping[str, ProcessColor] = field(default_factory=dict)
    border_color: ProcessColor = field(
        default_factory=lambda: Color(0.72, 0.75, 0.8)
    )
    border_width: float = 0.75
    header_font_name: str = "Helvetica-Bold"
    body_font_name: str = "Helvetica"
    header_font_size: float = 11.0
    body_font_size: float = 10.0

    def __post_init__(self) -> None:
        positive = (
            self.header_height,
            self.row_height,
            self.header_font_size,
            self.body_font_size,
        )
        if not all(value > 0 for value in positive):
            raise ValueError("Table heights and font sizes must be positive")
        if self.padding_x < 0 or self.border_width < 0:
            raise ValueError("Table padding and border width must not be negative")


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

    @property
    def height(self) -> float:
        return self.style.header_height + self.style.row_height * len(self.rows)

    def render_layer(
        self,
        *,
        x: float,
        top: float,
        layer_id: str | None = None,
        layer_name: str = "Table",
    ) -> Layer:
        """Compile the table into paths, point text, and explicit stacking order."""

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
            self.style.header_height,
            self.style.header_fill,
        )
        for row_index, row in enumerate(self.rows):
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
                top - self.style.header_height - self.style.row_height * row_index,
                self.style.row_height,
                fill,
            )

        horizontal_positions = [
            top,
            top - self.style.header_height,
            *(
                top - self.style.header_height - self.style.row_height * index
                for index in range(1, len(self.rows) + 1)
            ),
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
                points=[Point(line_x, top), Point(line_x, top - self.height)],
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
            value: str,
            font_size: float,
        ) -> float:
            left = column_edges[column_index]
            right = column_edges[column_index + 1]
            width = _estimated_text_width(value, font_size)
            if alignment == "right":
                return right - self.style.padding_x - width
            if alignment == "center":
                return (left + right - width) / 2
            return left + self.style.padding_x

        def baseline(row_top: float, height: float, size: float) -> float:
            return row_top - height / 2 - size * 0.3

        for column_index, column in enumerate(self.columns):
            value = column.title
            text = TextFrame(
                id=f"{self.id}.header.{column.key}",
                name=f"Header: {column.title}",
                text=value,
                x=text_x(
                    column_index,
                    column.alignment,
                    value,
                    self.style.header_font_size,
                ),
                y=baseline(top, self.style.header_height, self.style.header_font_size),
                font_size=self.style.header_font_size,
                font_name=self.style.header_font_name,
                fill=self.style.header_text_color,
                alignment=column.alignment,
            )
            text_frames.append(text)
            order.append(LayerItemRef("text", text.id))

        for row_index, row in enumerate(self.rows):
            variant = str(row.get(self.variant_key, "")) if self.variant_key else ""
            color = self.style.variant_text_colors.get(
                variant, self.style.body_text_color
            )
            row_top = top - self.style.header_height - self.style.row_height * row_index
            for column_index, column in enumerate(self.columns):
                value = column.text_for(row)
                text = TextFrame(
                    id=f"{self.id}.row-{row_index}.{column.key}",
                    name=f"Row {row_index + 1}: {column.title}",
                    text=value,
                    x=text_x(
                        column_index,
                        column.alignment,
                        value,
                        self.style.body_font_size,
                    ),
                    y=baseline(row_top, self.style.row_height, self.style.body_font_size),
                    font_size=self.style.body_font_size,
                    font_name=self.style.body_font_name,
                    fill=color,
                    alignment=column.alignment,
                )
                text_frames.append(text)
                order.append(LayerItemRef("text", text.id))

        return Layer(
            id=layer_id or f"{self.id}.layer",
            name=layer_name,
            paths=paths,
            text_frames=text_frames,
            item_order=order,
        )
