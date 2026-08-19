"""Canonical serializer for the supported Illustrator 7 document subset."""

from __future__ import annotations

import base64
import hashlib
import json
import math
from pathlib import Path as FilePath

from ._legacy_codec import (
    _POSTSCRIPT_NAME_RE,
    UnsupportedLegacyFeature,
    _artboard_comment,
    _color_operator,
    _escape_postscript_string,
    _escape_postscript_text,
    _linked_image_comment,
    _number,
    _path_note,
    linked_image_placeholder_note,
)
from .model import (
    ClippingGroup,
    CompoundPath,
    ControlPoint,
    Document,
    Group,
    LinkedImage,
    Path,
    Point,
    TextFrame,
)

_TEXT_ALIGNMENT_CODES = {"left": 0, "center": 1, "right": 2}
_LINE_CAP_CODES = {"butt": 0, "round": 1, "projecting": 2}
_LINE_JOIN_CODES = {"miter": 0, "round": 1, "bevel": 2}


def _path_geometry(path: Path) -> list[str]:
    first, *rest = path.points
    lines = [f"{_number(first.x)} {_number(first.y)} m"]
    previous = first
    targets = [*rest, first] if path.closed else rest
    for point in targets:
        if previous.out_handle is not None or point.in_handle is not None:
            control1 = previous.out_handle or ControlPoint(previous.x, previous.y)
            control2 = point.in_handle or ControlPoint(point.x, point.y)
            operator = "c" if point.smooth else "C"
            lines.append(
                " ".join(
                    [
                        _number(control1.x),
                        _number(control1.y),
                        _number(control2.x),
                        _number(control2.y),
                        _number(point.x),
                        _number(point.y),
                        operator,
                    ]
                )
            )
        else:
            operator = "l" if point.smooth else "L"
            lines.append(f"{_number(point.x)} {_number(point.y)} {operator}")
        previous = point
    return lines


def _serialized_path(path: Path, *, locked: bool) -> list[str]:
    if path.id.isascii():
        lines = [f"%AI7_Tag: ({_escape_postscript_string(path.id)})"]
    else:
        encoded_id = base64.b64encode(path.id.encode("utf-8")).decode("ascii")
        lines = [f"%%py-ai-path-id-utf8: {encoded_id}"]
    if path.name is not None:
        if path.name.isascii():
            lines.append(f"%%py-ai-path-name: ({_escape_postscript_string(path.name)})")
        else:
            encoded_name = base64.b64encode(path.name.encode("utf-8")).decode("ascii")
            lines.append(f"%%py-ai-path-name-utf8: {encoded_name}")
    lines.extend(["1 A" if locked else "0 A", "1 D" if path.polarity == "positive" else "0 D"])
    note = _path_note(path)
    if note is not None:
        lines.append(f"%AI3_Note:{note}")
    if path.fill is not None:
        lines.append(_color_operator(path.fill, stroke=False))
    if path.stroke is not None:
        lines.append(_color_operator(path.stroke, stroke=True))
    dash_values = " ".join(_number(value) for value in path.dash_pattern)
    lines.extend(
        [
            f"{_LINE_CAP_CODES[path.line_cap]} J",
            f"{_LINE_JOIN_CODES[path.line_join]} j",
            f"{_number(path.stroke_width)} w",
            f"{_number(path.miter_limit)} M",
            f"[{dash_values}] {_number(path.dash_offset)} d",
        ]
    )
    lines.extend(_path_geometry(path))
    render = {
        (True, True, True): "b",
        (True, True, False): "f",
        (True, False, True): "s",
        (True, False, False): "n",
        (False, True, True): "B",
        (False, True, False): "F",
        (False, False, True): "S",
        (False, False, False): "N",
    }[(path.closed, path.fill is not None, path.stroke is not None)]
    lines.append(render)
    return lines


def _serialized_clipping_path(path: Path, *, locked: bool) -> list[str]:
    if path.id.isascii():
        lines = [f"%AI7_Tag: ({_escape_postscript_string(path.id)})"]
    else:
        encoded_id = base64.b64encode(path.id.encode("utf-8")).decode("ascii")
        lines = [f"%%py-ai-path-id-utf8: {encoded_id}"]
    lines.extend(
        [
            "1 A" if locked else "0 A",
            "1 D" if path.polarity == "positive" else "0 D",
            f"{_LINE_CAP_CODES[path.line_cap]} J",
            f"{_LINE_JOIN_CODES[path.line_join]} j",
            f"{_number(path.stroke_width)} w",
            f"{_number(path.miter_limit)} M",
            "["
            + " ".join(_number(value) for value in path.dash_pattern)
            + f"] {_number(path.dash_offset)} d",
        ]
    )
    note = _path_note(path)
    if note is not None:
        lines.append(f"%AI3_Note:{note}")
    lines.extend(
        [
            *_path_geometry(path),
            "h" if path.closed else "H",
            "W",
            "n" if path.closed else "N",
        ]
    )
    return lines


def _serialized_text_frame(text: TextFrame, *, locked: bool) -> list[str]:
    if not _POSTSCRIPT_NAME_RE.fullmatch(text.font_name):
        raise UnsupportedLegacyFeature(
            f"Invalid PostScript font name for AI7 text: {text.font_name!r}"
        )
    lines = [f"%%py-ai-text-alignment: ({text.alignment})"]
    if text.tracking != 0:
        lines.append(f"%%py-ai-text-tracking: {_number(text.tracking)}")
    if text.rotation != 0:
        lines.append(f"%%py-ai-text-rotation: {_number(text.rotation)}")
    if text.is_area_text:
        lines.append(
            f"%%py-ai-text-area: {_number(text.area_width or 0)} {_number(text.area_height or 0)}"
        )
    if text.leading is not None:
        lines.append(f"%%py-ai-text-leading: {_number(text.leading)}")
    if text.native_font_name is not None:
        if not _POSTSCRIPT_NAME_RE.fullmatch(text.native_font_name):
            raise UnsupportedLegacyFeature(
                f"Invalid native PostScript font name for AI7 text: {text.native_font_name!r}"
            )
        lines.append(f"%%py-ai-text-native-font: ({text.native_font_name})")
    if text.id.isascii():
        lines.insert(0, f"%%py-ai-text-id: ({_escape_postscript_string(text.id)})")
    else:
        encoded_id = base64.b64encode(text.id.encode("utf-8")).decode("ascii")
        lines.insert(0, f"%%py-ai-text-id-utf8: {encoded_id}")
    if text.name is not None:
        if text.name.isascii():
            lines.append(f"%%py-ai-text-name: ({_escape_postscript_string(text.name)})")
        else:
            encoded_name = base64.b64encode(text.name.encode("utf-8")).decode("ascii")
            lines.append(f"%%py-ai-text-name-utf8: {encoded_name}")
    radians = math.radians(text.rotation)
    cosine = math.cos(radians)
    sine = math.sin(radians)
    lines.extend(
        [
            "1 A" if locked else "0 A",
            "0 To",
            f"1 0 0 1 {_number(text.x)} {_number(text.y)} 0 Tp",
            "TP",
            f"{_number(cosine)} {_number(sine)} {_number(-sine)} "
            f"{_number(cosine)} {_number(text.x)} {_number(text.y)} Tm",
            "0 Tr",
            _color_operator(text.fill, stroke=False),
            f"/{text.font_name} {_number(text.font_size)} 0 0 Tf",
            f"{_TEXT_ALIGNMENT_CODES[text.alignment]} Ta",
            f"({_escape_postscript_text(text.text, font_name=text.font_name)}) Tx",
            "TO",
        ]
    )
    return lines


def _serialized_linked_image(image: LinkedImage, *, locked: bool) -> list[str]:
    placeholder_id = "pyai-image-" + hashlib.sha256(image.id.encode("utf-8")).hexdigest()[:16]
    placeholder = Path(
        id=placeholder_id,
        name=None,
        points=[
            Point(image.x, image.y - image.height),
            Point(image.x + image.width, image.y - image.height),
            Point(image.x + image.width, image.y),
            Point(image.x, image.y),
        ],
        fill=None,
        stroke=None,
    )
    lines = [_linked_image_comment(image), *_serialized_path(placeholder, locked=locked)]
    placeholder_note = linked_image_placeholder_note(image.id)
    for index, line in enumerate(lines):
        if line.startswith("%AI3_Note:"):
            lines[index] = "%AI3_Note:" + placeholder_note
            break
    return lines


def _serialized_item(
    item: Path | TextFrame | LinkedImage | CompoundPath | ClippingGroup | Group,
    *,
    locked: bool,
) -> list[str]:
    if isinstance(item, Path):
        return _serialized_path(item, locked=locked)
    if isinstance(item, TextFrame):
        return _serialized_text_frame(item, locked=locked)
    if isinstance(item, LinkedImage):
        return _serialized_linked_image(item, locked=locked)
    if isinstance(item, CompoundPath):
        lines = [f"%%py-ai-compound-id: ({_escape_postscript_string(item.id)})"]
        if item.name is not None:
            lines.append(f"%%py-ai-compound-name: ({_escape_postscript_string(item.name)})")
        lines.append("*u")
        for path in item.paths:
            lines.extend(_serialized_path(path, locked=locked))
        lines.append("*U")
        return lines
    if isinstance(item, ClippingGroup):
        lines = [f"%%py-ai-clipping-id: ({_escape_postscript_string(item.id)})"]
        if item.name is not None:
            lines.append(f"%%py-ai-clipping-name: ({_escape_postscript_string(item.name)})")
        lines.append("q")
        lines.extend(_serialized_clipping_path(item.clipping_path, locked=locked))
        for path in item.paths:
            lines.extend(_serialized_path(path, locked=locked))
        lines.append("Q")
        return lines

    if item.id.isascii():
        lines = [f"%%py-ai-group-id: ({_escape_postscript_string(item.id)})"]
    else:
        encoded_id = base64.b64encode(item.id.encode("utf-8")).decode("ascii")
        lines = [f"%%py-ai-group-id-utf8: {encoded_id}"]
    if item.name is not None:
        if item.name.isascii():
            lines.append(f"%%py-ai-group-name: ({_escape_postscript_string(item.name)})")
        else:
            encoded_name = base64.b64encode(item.name.encode("utf-8")).decode("ascii")
            lines.append(f"%%py-ai-group-name-utf8: {encoded_name}")
    lines.append("u")
    for child in item.ordered_items():
        lines.extend(_serialized_item(child, locked=locked))
    lines.append("U")
    return lines


def _group_text_frames(group: Group) -> list[TextFrame]:
    return [
        *group.text_frames,
        *(text for nested in group.groups for text in _group_text_frames(nested)),
    ]


def _text_encoding_setup(document: Document) -> list[str]:
    fonts = {
        text.font_name
        for layer in document.layers
        for text in [
            *layer.text_frames,
            *(text for group in layer.groups for text in _group_text_frames(group)),
        ]
        if "RKSJ-" in text.font_name
    }
    lines: list[str] = []
    for font_name in sorted(fonts):
        base_name = font_name.removeprefix("_")
        lines.extend(
            [
                f"%AI3_BeginEncoding: {font_name} {base_name}",
                f"[/{font_name}/{base_name} 0 1 0 TZ",
                "%AI3_EndEncoding AdobeType",
            ]
        )
    return lines


def dumps_ai7(document: Document) -> bytes:
    """Serialize the supported IR subset as editable legacy Illustrator data."""

    width = _number(document.width)
    height = _number(document.height)
    title = _escape_postscript_string(document.title)
    # Current Illustrator places header-only AI7 artboards in negative global Y.
    # The TemplateBox establishes the ruler origin; this offset keeps the IR's
    # conventional positive-up 0..height coordinates inside that artboard.
    template_center_y = _number(document.height * 1.5)
    lines = [
        "%!PS-Adobe-3.0",
        "%%Creator: py-ai-illustrator (Adobe Illustrator 7 compatible subset)",
        f"%%Title: ({title})",
        f"%%BoundingBox: 0 0 {int(document.width + 0.999999)} {int(document.height + 0.999999)}",
        f"%%HiResBoundingBox: 0 0 {width} {height}",
        "%%DocumentProcessColors: Cyan Magenta Yellow Black",
        "%AI5_FileFormat 3.0",
        "%AI3_ColorUsage: Color",
        f"%AI3_TemplateBox: {_number(document.width / 2)} "
        f"{template_center_y} {_number(document.width / 2)} {template_center_y}",
        "%AI3_DocumentPreview: None",
        f"%AI5_ArtSize: {width} {height}",
        "%AI5_RulerUnits: 2",
        "%AI5_ArtFlags: 0 0 0 1 0 0 1 0 0",
        f"%AI5_NumLayers: {len(document.layers)}",
        "%%PageOrigin:0 0",
        "%%py-ai-metadata: "
        + base64.b64encode(
            json.dumps(document.metadata, ensure_ascii=False, separators=(",", ":")).encode()
        ).decode("ascii"),
        *(_artboard_comment(artboard) for artboard in document.artboards),
        "%%EndComments",
        "%%BeginProlog",
        "%%EndProlog",
        "%%BeginSetup",
        *_text_encoding_setup(document),
        "%%EndSetup",
    ]

    for layer_index, layer in enumerate(document.layers):
        layer_name = _escape_postscript_string(layer.name)
        visible = 1 if layer.visible else 0
        enabled = 0 if layer.locked else 1
        lines.extend(
            [
                "%AI5_BeginLayer",
                f"%%py-ai-layer-id: ({_escape_postscript_string(layer.id)})",
                f"{visible} 1 {enabled} 1 0 0 {layer_index % 27} 79 128 255 Lb",
                f"({layer_name}) Ln",
            ]
        )
        for item in layer.ordered_items():
            lines.extend(_serialized_item(item, locked=layer.locked))
        lines.extend(["LB", "%AI5_EndLayer"])

    lines.extend(["%%Trailer", "%%EOF", ""])
    return "\n".join(lines).encode("ascii", errors="strict")


def dump_ai7(
    document: Document,
    destination: str | FilePath,
    *,
    package_links: bool = True,
    source_base: str | FilePath | None = None,
) -> None:
    destination_path = FilePath(destination)
    serialized_document = document
    if package_links:
        from .assets import package_linked_images

        serialized_document, _ = package_linked_images(
            document,
            destination_path.parent,
            source_base=source_base,
        )
    destination_path.write_bytes(dumps_ai7(serialized_document))
