"""Python tools for inspecting and translating Adobe Illustrator files."""

from .format import FileFormat, FormatReport, inspect_file
from .model import Color, Document, Layer, Path, Point

__all__ = [
    "Color",
    "Document",
    "FileFormat",
    "FormatReport",
    "Layer",
    "Path",
    "Point",
    "inspect_file",
]

__version__ = "0.1.0.dev0"
