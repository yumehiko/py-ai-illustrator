"""Python tools for inspecting and translating Adobe Illustrator files."""

from .authoring import (
    LayerBuilder,
    RenderedComponent,
    Table,
    TableColumn,
    TableStyle,
    TextBlock,
    TextStyle,
    ellipse_path,
    polyline_path,
    rectangle_path,
)
from .format import FileFormat, FormatReport, inspect_file
from .lossless import (
    LegacyLineToken,
    LegacySource,
    SourceLimitExceeded,
    SourceReplacement,
    tokenize_legacy,
)
from .model import (
    ClippingGroup,
    CmykColor,
    Color,
    CompoundPath,
    ControlPoint,
    Document,
    Group,
    Layer,
    LayerItemRef,
    Path,
    Point,
    TextFrame,
)

__all__ = [
    "CmykColor",
    "Color",
    "CompoundPath",
    "ControlPoint",
    "ClippingGroup",
    "Document",
    "FileFormat",
    "FormatReport",
    "Group",
    "Layer",
    "LayerBuilder",
    "LayerItemRef",
    "LegacyLineToken",
    "LegacySource",
    "Path",
    "Point",
    "RenderedComponent",
    "TextFrame",
    "TextBlock",
    "TextStyle",
    "SourceLimitExceeded",
    "SourceReplacement",
    "Table",
    "TableColumn",
    "TableStyle",
    "ellipse_path",
    "inspect_file",
    "polyline_path",
    "rectangle_path",
    "tokenize_legacy",
]

__version__ = "0.1.0.dev0"
