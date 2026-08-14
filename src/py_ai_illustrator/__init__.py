"""Python tools for inspecting and translating Adobe Illustrator files."""

from .format import FileFormat, FormatReport, inspect_file
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
    "Path",
    "Point",
    "inspect_file",
]

__version__ = "0.1.0.dev0"
