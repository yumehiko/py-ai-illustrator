"""Python tools for inspecting and translating Adobe Illustrator files."""

from .authoring import Table, TableColumn, TableStyle
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
    "Layer",
    "LayerItemRef",
    "LegacyLineToken",
    "LegacySource",
    "Path",
    "Point",
    "TextFrame",
    "SourceLimitExceeded",
    "SourceReplacement",
    "Table",
    "TableColumn",
    "TableStyle",
    "inspect_file",
    "tokenize_legacy",
]

__version__ = "0.1.0.dev0"
