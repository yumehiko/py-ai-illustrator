"""Reader and writer for a deliberately small Illustrator 7 compatible subset."""

from __future__ import annotations

import base64
import hashlib
import json
import math
import re
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path as FilePath
from typing import Literal

from .compatibility import (
    LegacyFieldOrigin,
    LegacyNodeOrigin,
    LegacyReadResult,
    analyze_legacy_source,
)
from .lossless import LegacySource, SourceReplacement, tokenize_legacy
from .model import (
    Artboard,
    ClippingGroup,
    CmykColor,
    Color,
    CompoundPath,
    ControlPoint,
    Document,
    Group,
    Layer,
    LayerItemRef,
    LinkedImage,
    Path,
    Point,
    ProcessColor,
    TextFrame,
)

_NUMBER = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?"
_POINT_RE = re.compile(rf"^({_NUMBER})\s+({_NUMBER})\s+([mLl])$")
_COLOR_RE = re.compile(rf"^({_NUMBER})\s+({_NUMBER})\s+({_NUMBER})\s+(Xa|XA)$")
_AI8_RGB_COLOR_RE = re.compile(
    rf"^({_NUMBER})\s+({_NUMBER})\s+({_NUMBER})\s+({_NUMBER})\s+"
    rf"({_NUMBER})\s+({_NUMBER})\s+({_NUMBER})\s+(Xa|XA)$"
)
_CMYK_COLOR_RE = re.compile(rf"^({_NUMBER})\s+({_NUMBER})\s+({_NUMBER})\s+({_NUMBER})\s+([kK])$")
_CUBIC_RE = re.compile(
    rf"^({_NUMBER})\s+({_NUMBER})\s+({_NUMBER})\s+({_NUMBER})\s+"
    rf"({_NUMBER})\s+({_NUMBER})\s+([cC])$"
)
_SHORT_CUBIC_RE = re.compile(rf"^({_NUMBER})\s+({_NUMBER})\s+({_NUMBER})\s+({_NUMBER})\s+([vVyY])$")
_WIDTH_RE = re.compile(rf"(?:^|\s)({_NUMBER})\s+w(?:\s|$)")
_LINE_CAP_RE = re.compile(r"(?:^|\s)([012])\s+J(?:\s|$)")
_LINE_JOIN_RE = re.compile(r"(?:^|\s)([012])\s+j(?:\s|$)")
_MITER_LIMIT_RE = re.compile(rf"(?:^|\s)({_NUMBER})\s+M(?:\s|$)")
_DASH_RE = re.compile(rf"\[((?:\s*{_NUMBER})*\s*)\]\s*({_NUMBER})\s+d(?:\s|$)")
_POLARITY_RE = re.compile(r"^([01])\s+D$")
_BOUNDS_RE = re.compile(r"^%%(?:HiRes)?BoundingBox:\s+(.+)$")
_LAYER_NAME_RE = re.compile(r"^\((.*)\)\s+Ln$")
_LAYER_RE = re.compile(r"^([01])\s+1\s+([01])\s+1\s+0\s+0\s+.+\s+Lb$")
_TEXT_BEGIN_RE = re.compile(r"^0\s+To$")
_TEXT_POSITION_RE = re.compile(
    rf"^{_NUMBER}\s+{_NUMBER}\s+{_NUMBER}\s+{_NUMBER}\s+"
    rf"({_NUMBER})\s+({_NUMBER})\s+{_NUMBER}\s+Tp$"
)
_TEXT_MATRIX_RE = re.compile(
    rf"^({_NUMBER})\s+({_NUMBER})\s+({_NUMBER})\s+({_NUMBER})\s+"
    rf"({_NUMBER})\s+({_NUMBER})\s+Tm$"
)
_TEXT_FONT_RE = re.compile(rf"^/(\S+)\s+({_NUMBER})\s+{_NUMBER}\s+{_NUMBER}\s+Tf$")
_TEXT_ALIGNMENT_RE = re.compile(r"^([0-4])\s+Ta$")
_TEXT_CONTENT_RE = re.compile(r"^\((.*)\)\s+Tx(?:\s+.*)?$")
_TEXT_ALIGNMENT_CODES = {"left": 0, "center": 1, "right": 2}
_LINE_CAP_CODES = {"butt": 0, "round": 1, "projecting": 2}
_LINE_JOIN_CODES = {"miter": 0, "round": 1, "bevel": 2}
_POSTSCRIPT_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
_PATH_NOTE_PREFIX = "py-ai:"


class UnsupportedLegacyFeature(ValueError):
    """Raised when data falls outside the Phase 0 legacy subset."""


def _number(value: float) -> str:
    if abs(value) < 0.0000005:
        return "0"
    return f"{value:.6f}".rstrip("0").rstrip(".") or "0"


def _escape_postscript_string(value: str) -> str:
    return value.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _unescape_postscript_string(value: str) -> str:
    return value.replace("\\)", ")").replace("\\(", "(").replace("\\\\", "\\")


def _text_encoding(font_name: str) -> str:
    return "cp932" if "RKSJ-" in font_name else "ascii"


def _escape_postscript_text(value: str, *, font_name: str) -> str:
    normalized = value.replace("\r\n", "\r").replace("\n", "\r")
    encoding = _text_encoding(font_name)
    try:
        encoded = normalized.encode(encoding)
    except UnicodeEncodeError as error:
        raise UnsupportedLegacyFeature(
            f"Text cannot be encoded for AI7 font {font_name!r}; "
            "use an RKSJ-H/RKSJ-V font for Japanese text"
        ) from error

    output: list[str] = []
    for byte in encoded:
        if byte in {ord("("), ord(")"), ord("\\")}:
            output.append("\\" + chr(byte))
        elif 32 <= byte <= 126:
            output.append(chr(byte))
        else:
            output.append(f"\\{byte:03o}")
    return "".join(output)


def _unescape_postscript_bytes(value: str) -> bytes:
    output = bytearray()
    index = 0
    simple_escapes = {
        "n": b"\n",
        "r": b"\r",
        "t": b"\t",
        "b": b"\b",
        "f": b"\f",
        "(": b"(",
        ")": b")",
        "\\": b"\\",
    }
    while index < len(value):
        character = value[index]
        if character != "\\":
            output.extend(character.encode("latin-1"))
            index += 1
            continue
        index += 1
        if index >= len(value):
            break
        escaped = value[index]
        if escaped in simple_escapes:
            output.extend(simple_escapes[escaped])
            index += 1
            continue
        if escaped in "01234567":
            octal = escaped
            index += 1
            while index < len(value) and len(octal) < 3 and value[index] in "01234567":
                octal += value[index]
                index += 1
            output.append(int(octal, 8))
            continue
        output.extend(escaped.encode("latin-1"))
        index += 1
    return bytes(output)


def _unescape_postscript_text(value: str, *, font_name: str) -> str:
    raw = _unescape_postscript_bytes(value)
    encoding = "cp932" if _text_encoding(font_name) == "cp932" else "latin-1"
    try:
        decoded = raw.decode(encoding)
    except UnicodeDecodeError:
        decoded = raw.decode("latin-1")
    return decoded.replace("\r\n", "\n").replace("\r", "\n")


def _path_note(path: Path) -> str | None:
    payload = {"id": path.id}
    if path.name is not None:
        payload["name"] = path.name
    encoded = base64.b64encode(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).decode("ascii")
    note = _PATH_NOTE_PREFIX + encoded
    return note if len(note) <= 254 else None


def _parse_path_note(note: str) -> tuple[str | None, str | None]:
    if not note.startswith(_PATH_NOTE_PREFIX):
        return None, None
    try:
        decoded = base64.b64decode(note.removeprefix(_PATH_NOTE_PREFIX), validate=True).decode(
            "utf-8"
        )
        payload = json.loads(decoded)
    except (ValueError, UnicodeError, json.JSONDecodeError):
        return None, None
    if not isinstance(payload, dict):
        return None, None
    path_id = payload.get("id")
    path_name = payload.get("name")
    return (
        path_id if isinstance(path_id, str) and path_id else None,
        path_name if isinstance(path_name, str) else None,
    )


def _artboard_comment(artboard: Artboard) -> str:
    payload = {
        "id": artboard.id,
        "name": artboard.name,
        "left": artboard.left,
        "top": artboard.top,
        "width": artboard.width,
        "height": artboard.height,
    }
    encoded = base64.b64encode(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
    ).decode("ascii")
    return "%%py-ai-artboard: " + encoded


def _linked_image_comment(image: LinkedImage) -> str:
    payload = {
        "id": image.id,
        "source": image.source,
        "x": image.x,
        "y": image.y,
        "width": image.width,
        "height": image.height,
        "rotation": image.rotation,
        "name": image.name,
    }
    encoded = base64.b64encode(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
    ).decode("ascii")
    return "%%py-ai-linked-image: " + encoded


def linked_image_placeholder_note(image_id: str) -> str:
    """Return the native note used to locate an image's legacy placeholder."""

    digest = hashlib.sha256(image_id.encode("utf-8")).hexdigest()
    return "py-ai-image-placeholder:" + digest


def _color_operator(color: ProcessColor, *, stroke: bool) -> str:
    if isinstance(color, CmykColor):
        operator = "K" if stroke else "k"
        values = (color.cyan, color.magenta, color.yellow, color.black)
    else:
        operator = "XA" if stroke else "Xa"
        values = (color.red, color.green, color.blue)
    return " ".join([*(_number(value) for value in values), operator])


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


def _loads_ai7_source(
    source: LegacySource,
    *,
    origins: list[LegacyNodeOrigin] | None = None,
) -> Document:
    """Parse files emitted by this project and a conservative AI7 path subset."""

    data = source.data
    text = data.decode("latin-1")
    if not text.startswith("%!PS-Adobe") or "%AI" not in text:
        raise UnsupportedLegacyFeature("Not a recognizable legacy Illustrator document")

    width = height = None
    title = "Untitled"
    title_seen = False
    layers: list[Layer] = []
    current_layer: Layer | None = None
    current_layer_source_start: int | None = None
    current_layer_source_end: int | None = None
    current_compound_paths: list[Path] | None = None
    current_compound_id: str | None = None
    current_compound_name: str | None = None
    current_compound_source_start: int | None = None
    current_clipping_paths: list[Path] | None = None
    current_clipping_path: Path | None = None
    current_clipping_id: str | None = None
    current_clipping_name: str | None = None
    current_clipping_source_start: int | None = None
    clipping_mask_closed: bool | None = None
    current_points: list[Point] = []
    fill: ProcessColor | None = None
    stroke: ProcessColor | None = None
    stroke_width = 1.0
    dash_pattern: list[float] = []
    dash_offset = 0.0
    line_cap = "butt"
    line_join = "miter"
    miter_limit = 4.0
    pending_id: str | None = None
    pending_name: str | None = None
    pending_compound_id: str | None = None
    pending_compound_name: str | None = None
    pending_compound_source_start: int | None = None
    pending_clipping_id: str | None = None
    pending_clipping_name: str | None = None
    pending_clipping_source_start: int | None = None
    pending_group_id: str | None = None
    pending_group_name: str | None = None
    pending_group_source_start: int | None = None
    group_stack: list[tuple[Group, bool, int]] = []
    group_counter = 0
    polarity = "positive"
    path_counter = 0
    text_counter = 0
    in_text = False
    text_parts: list[str] = []
    text_x = 0.0
    text_y = 0.0
    text_font_name = "Helvetica"
    text_font_size = 12.0
    text_alignment = "left"
    text_rotation = 0.0
    current_text_source_start: int | None = None
    current_text_content_origins: list[LegacyFieldOrigin] = []
    pending_text_id: str | None = None
    pending_text_name: str | None = None
    pending_text_alignment: str | None = None
    pending_text_native_font_name: str | None = None
    pending_text_tracking = 0.0
    pending_text_rotation: float | None = None
    pending_text_area_width: float | None = None
    pending_text_area_height: float | None = None
    pending_text_leading: float | None = None
    pending_linked_image: LinkedImage | None = None
    pending_linked_image_source_start: int | None = None
    pending_linked_image_metadata_origin: LegacyFieldOrigin | None = None
    metadata: dict[str, object] = {}
    artboards: list[Artboard] = []
    current_path_source_start: int | None = None
    current_geometry_origins: list[LegacyFieldOrigin] = []
    current_fill_origin: LegacyFieldOrigin | None = None
    current_stroke_origin: LegacyFieldOrigin | None = None
    path_origin_candidates: list[
        tuple[
            str,
            int,
            int,
            LegacyFieldOrigin | None,
            LegacyFieldOrigin | None,
            tuple[LegacyFieldOrigin, ...],
        ]
    ] = []
    fill_origin_use_counts: dict[tuple[int, int], int] = {}
    stroke_origin_use_counts: dict[tuple[int, int], int] = {}

    def statement_field_origin(
        field: str, token_start: int, token_content_end: int, token_operator_end: int
    ) -> LegacyFieldOrigin:
        start = token_start
        while start < token_content_end and data[start] in b"\x00\t\x0c ":
            start += 1
        return LegacyFieldOrigin(
            field=field,
            start=start,
            end=token_operator_end,
            expected=data[start:token_operator_end],
        )

    def count_fill_origin_use() -> None:
        if current_fill_origin is None:
            return
        key = (current_fill_origin.start, current_fill_origin.end)
        fill_origin_use_counts[key] = fill_origin_use_counts.get(key, 0) + 1

    def count_stroke_origin_use() -> None:
        if current_stroke_origin is None:
            return
        key = (current_stroke_origin.start, current_stroke_origin.end)
        stroke_origin_use_counts[key] = stroke_origin_use_counts.get(key, 0) + 1

    def active_container() -> Layer | Group:
        nonlocal current_layer
        if group_stack:
            return group_stack[-1][0]
        if current_layer is None:
            current_layer = Layer(id="layer-1", name="Layer 1")
        return current_layer

    def append_item(
        kind: str,
        item: Path | TextFrame | LinkedImage | CompoundPath | ClippingGroup | Group,
        *,
        source_start: int | None = None,
        source_end: int | None = None,
    ) -> None:
        nonlocal current_layer_source_start, current_layer_source_end
        container = active_container()
        if isinstance(item, Path):
            container.paths.append(item)
        elif isinstance(item, TextFrame):
            container.text_frames.append(item)
        elif isinstance(item, LinkedImage):
            container.linked_images.append(item)
        elif isinstance(item, CompoundPath):
            container.compound_paths.append(item)
        elif isinstance(item, ClippingGroup):
            container.clipping_groups.append(item)
        else:
            container.groups.append(item)
        container.item_order.append(LayerItemRef(kind, item.id))
        if isinstance(container, Layer) and source_start is not None and source_end is not None:
            if current_layer_source_start is None:
                current_layer_source_start = source_start
            current_layer_source_end = source_end

    for token in source.lines:
        line = source.line_content(token).decode("latin-1").strip()
        if not title_seen and line.startswith("%%Title: (") and line.endswith(")"):
            title = _unescape_postscript_string(line[10:-1])
            title_seen = True
            continue
        if line.startswith("%%py-ai-metadata: "):
            try:
                decoded = base64.b64decode(line[18:], validate=True).decode("utf-8")
                candidate = json.loads(decoded)
                if isinstance(candidate, dict):
                    metadata = candidate
            except (ValueError, UnicodeError, json.JSONDecodeError):
                pass
            continue
        if line.startswith("%%py-ai-artboard: "):
            try:
                decoded = base64.b64decode(
                    line.removeprefix("%%py-ai-artboard: "), validate=True
                ).decode("utf-8")
                candidate = json.loads(decoded)
                if isinstance(candidate, dict):
                    artboard = Artboard.from_dict(candidate)
                    artboards.append(artboard)
                    if origins is not None:
                        origins.append(
                            LegacyNodeOrigin(
                                node_type="artboard",
                                node_id=artboard.id,
                                start=token.start,
                                end=token.end,
                            )
                        )
            except (ValueError, UnicodeError, json.JSONDecodeError):
                pass
            continue
        if line.startswith("%%py-ai-linked-image: "):
            try:
                prefix = "%%py-ai-linked-image: "
                encoded = line.removeprefix(prefix)
                decoded = base64.b64decode(encoded, validate=True).decode("utf-8")
                candidate = json.loads(decoded)
                if isinstance(candidate, dict):
                    pending_linked_image = LinkedImage.from_dict(candidate)
                    raw_content = source.line_content(token)
                    decoded_content = raw_content.decode("latin-1")
                    leading_length = len(decoded_content) - len(decoded_content.lstrip())
                    metadata_start = token.start + leading_length + len(prefix)
                    metadata_end = metadata_start + len(encoded)
                    pending_linked_image_source_start = token.start
                    pending_linked_image_metadata_origin = LegacyFieldOrigin(
                        field="metadata",
                        start=metadata_start,
                        end=metadata_end,
                        expected=data[metadata_start:metadata_end],
                    )
            except (ValueError, UnicodeError, json.JSONDecodeError):
                pending_linked_image = None
                pending_linked_image_source_start = None
                pending_linked_image_metadata_origin = None
            continue
        bounds_match = _BOUNDS_RE.match(line)
        if bounds_match and width is None:
            values = bounds_match.group(1).split()
            if len(values) == 4 and values[0] != "(atend)":
                llx, lly, urx, ury = map(float, values)
                width, height = urx - llx, ury - lly
            continue
        if line == "%AI5_BeginLayer":
            current_layer = Layer(id=f"layer-{len(layers) + 1}", name=f"Layer {len(layers) + 1}")
            current_layer_source_start = token.start
            current_layer_source_end = token.end
            continue
        if line.startswith("%%py-ai-layer-id: (") and line.endswith(")") and current_layer:
            current_layer.id = _unescape_postscript_string(line[19:-1])
            continue
        layer_match = _LAYER_RE.match(line)
        if layer_match and current_layer is not None:
            current_layer.visible = layer_match.group(1) == "1"
            current_layer.locked = layer_match.group(2) == "0"
            continue
        layer_name_match = _LAYER_NAME_RE.match(line)
        if layer_name_match and current_layer is not None:
            current_layer.name = _unescape_postscript_string(layer_name_match.group(1))
            continue
        if line == "%AI5_EndLayer" and current_layer is not None:
            group_stack.clear()
            current_layer_source_end = token.end
            if origins is not None and current_layer_source_start is not None:
                origins.append(
                    LegacyNodeOrigin(
                        node_type="layer",
                        node_id=current_layer.id,
                        start=current_layer_source_start,
                        end=current_layer_source_end,
                    )
                )
            layers.append(current_layer)
            current_layer = None
            current_layer_source_start = None
            current_layer_source_end = None
            continue
        if line.startswith("%%py-ai-group-id: (") and line.endswith(")"):
            pending_group_source_start = pending_group_source_start or token.start
            value = line.removeprefix("%%py-ai-group-id: (")[:-1]
            pending_group_id = _unescape_postscript_string(value)
            continue
        if line.startswith("%%py-ai-group-id-utf8: "):
            pending_group_source_start = pending_group_source_start or token.start
            with suppress(ValueError, UnicodeError):
                pending_group_id = base64.b64decode(
                    line.removeprefix("%%py-ai-group-id-utf8: "), validate=True
                ).decode("utf-8")
            continue
        if line.startswith("%%py-ai-group-name: (") and line.endswith(")"):
            pending_group_source_start = pending_group_source_start or token.start
            value = line.removeprefix("%%py-ai-group-name: (")[:-1]
            pending_group_name = _unescape_postscript_string(value)
            continue
        if line.startswith("%%py-ai-group-name-utf8: "):
            pending_group_source_start = pending_group_source_start or token.start
            with suppress(ValueError, UnicodeError):
                pending_group_name = base64.b64decode(
                    line.removeprefix("%%py-ai-group-name-utf8: "), validate=True
                ).decode("utf-8")
            continue
        if line == "u" and current_layer is not None:
            group_counter += 1
            explicit = pending_group_id is not None or pending_group_name is not None
            group_stack.append(
                (
                    Group(
                        id=pending_group_id or f"group-{group_counter}",
                        name=pending_group_name,
                    ),
                    explicit,
                    pending_group_source_start or token.start,
                )
            )
            pending_group_id = None
            pending_group_name = None
            pending_group_source_start = None
            continue
        if line == "U" and group_stack:
            group, explicit, group_source_start = group_stack.pop()
            children = group.ordered_items()
            if not explicit and len(children) == 1:
                child = children[0]
                kind = group.item_order[0].kind
                append_item(kind, child, source_start=group_source_start, source_end=token.end)
            elif children or explicit:
                append_item("group", group, source_start=group_source_start, source_end=token.end)
                if origins is not None:
                    origins.append(
                        LegacyNodeOrigin(
                            node_type="group",
                            node_id=group.id,
                            start=group_source_start,
                            end=token.end,
                        )
                    )
            continue
        if line.startswith("%%py-ai-compound-id: (") and line.endswith(")"):
            pending_compound_source_start = pending_compound_source_start or token.start
            value = line.removeprefix("%%py-ai-compound-id: (")[:-1]
            pending_compound_id = _unescape_postscript_string(value)
            continue
        if line.startswith("%%py-ai-compound-name: (") and line.endswith(")"):
            pending_compound_source_start = pending_compound_source_start or token.start
            value = line.removeprefix("%%py-ai-compound-name: (")[:-1]
            pending_compound_name = _unescape_postscript_string(value)
            continue
        if line == "*u":
            current_compound_paths = []
            current_compound_id = pending_compound_id or f"compound-{len(layers) + 1}"
            current_compound_name = pending_compound_name
            current_compound_source_start = pending_compound_source_start or token.start
            pending_compound_id = None
            pending_compound_name = None
            pending_compound_source_start = None
            continue
        if line == "*U" and current_compound_paths is not None:
            if len(current_compound_paths) >= 2:
                compound = CompoundPath(
                    id=current_compound_id or f"compound-{len(layers) + 1}",
                    name=current_compound_name,
                    paths=current_compound_paths,
                )
                append_item(
                    "compound_path",
                    compound,
                    source_start=current_compound_source_start,
                    source_end=token.end,
                )
                if origins is not None and current_compound_source_start is not None:
                    origins.append(
                        LegacyNodeOrigin(
                            node_type="compound_path",
                            node_id=compound.id,
                            start=current_compound_source_start,
                            end=token.end,
                        )
                    )
            else:
                for path in current_compound_paths:
                    append_item("path", path)
            current_compound_paths = None
            current_compound_id = None
            current_compound_name = None
            current_compound_source_start = None
            continue
        if line.startswith("%%py-ai-clipping-id: (") and line.endswith(")"):
            pending_clipping_source_start = pending_clipping_source_start or token.start
            value = line.removeprefix("%%py-ai-clipping-id: (")[:-1]
            pending_clipping_id = _unescape_postscript_string(value)
            continue
        if line.startswith("%%py-ai-clipping-name: (") and line.endswith(")"):
            pending_clipping_source_start = pending_clipping_source_start or token.start
            value = line.removeprefix("%%py-ai-clipping-name: (")[:-1]
            pending_clipping_name = _unescape_postscript_string(value)
            continue
        if line == "q":
            current_clipping_paths = []
            current_clipping_path = None
            current_clipping_id = pending_clipping_id or f"clipping-{len(layers) + 1}"
            current_clipping_name = pending_clipping_name
            current_clipping_source_start = pending_clipping_source_start or token.start
            clipping_mask_closed = None
            pending_clipping_id = None
            pending_clipping_name = None
            pending_clipping_source_start = None
            continue
        if line == "Q" and current_clipping_paths is not None:
            if current_clipping_path is not None and current_clipping_paths:
                group = ClippingGroup(
                    id=current_clipping_id or f"clipping-{len(layers) + 1}",
                    name=current_clipping_name,
                    clipping_path=current_clipping_path,
                    paths=current_clipping_paths,
                )
                append_item(
                    "clipping_group",
                    group,
                    source_start=current_clipping_source_start,
                    source_end=token.end,
                )
                if origins is not None and current_clipping_source_start is not None:
                    origins.append(
                        LegacyNodeOrigin(
                            node_type="clipping_group",
                            node_id=group.id,
                            start=current_clipping_source_start,
                            end=token.end,
                        )
                    )
            else:
                if current_clipping_path is not None:
                    append_item("path", current_clipping_path)
                for path in current_clipping_paths:
                    append_item("path", path)
            current_clipping_paths = None
            current_clipping_path = None
            current_clipping_id = None
            current_clipping_name = None
            current_clipping_source_start = None
            clipping_mask_closed = None
            continue
        if line.startswith("%AI7_Tag: (") and line.endswith(")"):
            pending_id = _unescape_postscript_string(line[11:-1])
            continue
        if line.startswith("%%py-ai-path-id-utf8: "):
            with suppress(ValueError, UnicodeError):
                pending_id = base64.b64decode(
                    line.removeprefix("%%py-ai-path-id-utf8: "), validate=True
                ).decode("utf-8")
            continue
        if line.startswith("%%py-ai-path-name: (") and line.endswith(")"):
            pending_name = _unescape_postscript_string(line[20:-1])
            continue
        if line.startswith("%%py-ai-path-name-utf8: "):
            with suppress(ValueError, UnicodeError):
                pending_name = base64.b64decode(
                    line.removeprefix("%%py-ai-path-name-utf8: "), validate=True
                ).decode("utf-8")
            continue
        if line.startswith("%AI3_Note:"):
            note_id, note_name = _parse_path_note(line[10:].lstrip())
            if note_id is not None:
                pending_id = note_id
            if note_name is not None:
                pending_name = note_name
            continue
        if line.startswith("%%py-ai-text-id: (") and line.endswith(")"):
            value = line.removeprefix("%%py-ai-text-id: (")[:-1]
            pending_text_id = _unescape_postscript_string(value)
            continue
        if line.startswith("%%py-ai-text-id-utf8: "):
            with suppress(ValueError, UnicodeError):
                pending_text_id = base64.b64decode(
                    line.removeprefix("%%py-ai-text-id-utf8: "), validate=True
                ).decode("utf-8")
            continue
        if line.startswith("%%py-ai-text-name: (") and line.endswith(")"):
            value = line.removeprefix("%%py-ai-text-name: (")[:-1]
            pending_text_name = _unescape_postscript_string(value)
            continue
        if line.startswith("%%py-ai-text-name-utf8: "):
            with suppress(ValueError, UnicodeError):
                pending_text_name = base64.b64decode(
                    line.removeprefix("%%py-ai-text-name-utf8: "), validate=True
                ).decode("utf-8")
            continue
        if line.startswith("%%py-ai-text-alignment: (") and line.endswith(")"):
            value = line.removeprefix("%%py-ai-text-alignment: (")[:-1]
            candidate = _unescape_postscript_string(value)
            if candidate in {"left", "center", "right"}:
                pending_text_alignment = candidate
            continue
        if line.startswith("%%py-ai-text-native-font: (") and line.endswith(")"):
            value = line.removeprefix("%%py-ai-text-native-font: (")[:-1]
            pending_text_native_font_name = _unescape_postscript_string(value)
            continue
        if line.startswith("%%py-ai-text-tracking: "):
            with suppress(ValueError):
                pending_text_tracking = float(line.removeprefix("%%py-ai-text-tracking: "))
            continue
        if line.startswith("%%py-ai-text-rotation: "):
            with suppress(ValueError):
                pending_text_rotation = float(line.removeprefix("%%py-ai-text-rotation: "))
            continue
        if line.startswith("%%py-ai-text-area: "):
            values = line.removeprefix("%%py-ai-text-area: ").split()
            if len(values) == 2:
                with suppress(ValueError):
                    area_width, area_height = map(float, values)
                    if area_width > 0 and area_height > 0:
                        pending_text_area_width = area_width
                        pending_text_area_height = area_height
            continue
        if line.startswith("%%py-ai-text-leading: "):
            with suppress(ValueError):
                candidate = float(line.removeprefix("%%py-ai-text-leading: "))
                if candidate > 0:
                    pending_text_leading = candidate
            continue
        if _TEXT_BEGIN_RE.match(line):
            in_text = True
            text_parts = []
            current_text_source_start = token.start
            current_text_content_origins = []
            text_x = 0.0
            text_y = 0.0
            text_rotation = 0.0
            continue
        if in_text:
            text_position_match = _TEXT_POSITION_RE.match(line)
            if text_position_match:
                text_x = float(text_position_match.group(1))
                text_y = float(text_position_match.group(2))
                continue
            text_matrix_match = _TEXT_MATRIX_RE.match(line)
            if text_matrix_match:
                text_rotation = math.degrees(
                    math.atan2(
                        float(text_matrix_match.group(2)),
                        float(text_matrix_match.group(1)),
                    )
                )
                continue
            text_font_match = _TEXT_FONT_RE.match(line)
            if text_font_match:
                text_font_name = text_font_match.group(1)
                text_font_size = float(text_font_match.group(2))
                continue
            text_alignment_match = _TEXT_ALIGNMENT_RE.match(line)
            if text_alignment_match:
                text_alignment = {"0": "left", "1": "center", "2": "right"}.get(
                    text_alignment_match.group(1), "left"
                )
                continue
            text_content_match = _TEXT_CONTENT_RE.match(line)
            if text_content_match:
                text_parts.append(
                    _unescape_postscript_text(text_content_match.group(1), font_name=text_font_name)
                )
                raw_content = source.line_content(token)
                decoded_content = raw_content.decode("latin-1")
                leading_length = len(decoded_content) - len(decoded_content.lstrip())
                content_start = token.start + leading_length + text_content_match.start(1)
                content_end = token.start + leading_length + text_content_match.end(1)
                current_text_content_origins.append(
                    LegacyFieldOrigin(
                        field=f"text.{len(current_text_content_origins)}",
                        start=content_start,
                        end=content_end,
                        expected=data[content_start:content_end],
                    )
                )
                continue
            if line == "TO":
                text_counter += 1
                count_fill_origin_use()
                text_frame = TextFrame(
                    id=pending_text_id or f"text-{text_counter}",
                    name=pending_text_name,
                    text="".join(text_parts),
                    x=text_x,
                    y=text_y,
                    font_size=text_font_size,
                    font_name=text_font_name,
                    native_font_name=pending_text_native_font_name,
                    tracking=pending_text_tracking,
                    rotation=(
                        pending_text_rotation
                        if pending_text_rotation is not None
                        else text_rotation
                    ),
                    area_width=pending_text_area_width,
                    area_height=pending_text_area_height,
                    leading=pending_text_leading,
                    fill=fill or Color(0.0, 0.0, 0.0),
                    alignment=pending_text_alignment or text_alignment,
                )
                append_item(
                    "text",
                    text_frame,
                    source_start=current_text_source_start or token.start,
                    source_end=token.end,
                )
                if origins is not None:
                    text_fields = tuple(current_text_content_origins)
                    if len(text_fields) == 1:
                        text_fields = (
                            LegacyFieldOrigin(
                                field="text",
                                start=text_fields[0].start,
                                end=text_fields[0].end,
                                expected=text_fields[0].expected,
                            ),
                        )
                    origins.append(
                        LegacyNodeOrigin(
                            node_type="text",
                            node_id=text_frame.id,
                            start=(
                                current_text_source_start
                                if current_text_source_start is not None
                                else token.start
                            ),
                            end=token.end,
                            fields=text_fields,
                        )
                    )
                pending_text_id = None
                pending_text_name = None
                pending_text_alignment = None
                pending_text_native_font_name = None
                pending_text_tracking = 0.0
                pending_text_rotation = None
                pending_text_area_width = None
                pending_text_area_height = None
                pending_text_leading = None
                current_text_source_start = None
                current_text_content_origins = []
                in_text = False
                continue
        ai8_rgb_match = _AI8_RGB_COLOR_RE.match(line)
        if ai8_rgb_match:
            color = Color(*(float(ai8_rgb_match.group(index)) for index in range(5, 8)))
            if ai8_rgb_match.group(8) == "Xa":
                fill = color
                if token.operator_end is not None:
                    current_fill_origin = statement_field_origin(
                        "fill", token.start, token.content_end, token.operator_end
                    )
            else:
                stroke = color
                if token.operator_end is not None:
                    current_stroke_origin = statement_field_origin(
                        "stroke", token.start, token.content_end, token.operator_end
                    )
            continue
        color_match = _COLOR_RE.match(line)
        if color_match:
            color = Color(*(float(color_match.group(index)) for index in range(1, 4)))
            if color_match.group(4) == "Xa":
                fill = color
                if token.operator_end is not None:
                    current_fill_origin = statement_field_origin(
                        "fill", token.start, token.content_end, token.operator_end
                    )
            else:
                stroke = color
                if token.operator_end is not None:
                    current_stroke_origin = statement_field_origin(
                        "stroke", token.start, token.content_end, token.operator_end
                    )
            continue
        cmyk_match = _CMYK_COLOR_RE.match(line)
        if cmyk_match:
            color = CmykColor(*(float(cmyk_match.group(index)) for index in range(1, 5)))
            if cmyk_match.group(5) == "k":
                fill = color
                if token.operator_end is not None:
                    current_fill_origin = statement_field_origin(
                        "fill", token.start, token.content_end, token.operator_end
                    )
            else:
                stroke = color
                if token.operator_end is not None:
                    current_stroke_origin = statement_field_origin(
                        "stroke", token.start, token.content_end, token.operator_end
                    )
            continue
        width_match = _WIDTH_RE.search(line)
        if width_match:
            stroke_width = float(width_match.group(1))
        line_cap_match = _LINE_CAP_RE.search(line)
        if line_cap_match:
            line_cap = {"0": "butt", "1": "round", "2": "projecting"}[line_cap_match.group(1)]
        line_join_match = _LINE_JOIN_RE.search(line)
        if line_join_match:
            line_join = {"0": "miter", "1": "round", "2": "bevel"}[line_join_match.group(1)]
        miter_limit_match = _MITER_LIMIT_RE.search(line)
        if miter_limit_match:
            miter_limit = float(miter_limit_match.group(1))
        dash_match = _DASH_RE.search(line)
        if dash_match:
            dash_pattern = [float(value) for value in dash_match.group(1).split()]
            dash_offset = float(dash_match.group(2))
        polarity_match = _POLARITY_RE.match(line)
        if polarity_match:
            polarity = "positive" if polarity_match.group(1) == "1" else "negative"
            continue
        if line in {"h", "H"} and current_clipping_paths is not None and current_points:
            clipping_mask_closed = line == "h"
            continue
        if line == "W" and current_clipping_paths is not None:
            continue
        point_match = _POINT_RE.match(line)
        if point_match:
            operator = point_match.group(3)
            point = Point(
                float(point_match.group(1)),
                float(point_match.group(2)),
                smooth=operator == "l",
            )
            if operator == "m":
                current_points = [point]
                current_path_source_start = token.start
                current_geometry_origins = []
            else:
                current_points.append(point)
            if token.operator_end is not None:
                current_geometry_origins.append(
                    statement_field_origin(
                        f"geometry.{len(current_geometry_origins)}",
                        token.start,
                        token.content_end,
                        token.operator_end,
                    )
                )
            continue
        cubic_match = _CUBIC_RE.match(line)
        if cubic_match and current_points:
            values = [float(cubic_match.group(index)) for index in range(1, 7)]
            current_points[-1] = current_points[-1].with_out_handle(
                ControlPoint(values[0], values[1])
            )
            current_points.append(
                Point(
                    values[4],
                    values[5],
                    in_handle=ControlPoint(values[2], values[3]),
                    smooth=cubic_match.group(7) == "c",
                )
            )
            if token.operator_end is not None:
                current_geometry_origins.append(
                    statement_field_origin(
                        f"geometry.{len(current_geometry_origins)}",
                        token.start,
                        token.content_end,
                        token.operator_end,
                    )
                )
            continue
        short_cubic_match = _SHORT_CUBIC_RE.match(line)
        if short_cubic_match and current_points:
            values = [float(short_cubic_match.group(index)) for index in range(1, 5)]
            operator = short_cubic_match.group(5)
            if operator in {"y", "Y"}:
                current_points[-1] = current_points[-1].with_out_handle(
                    ControlPoint(values[0], values[1])
                )
                in_handle = None
            else:
                in_handle = ControlPoint(values[0], values[1])
            current_points.append(
                Point(
                    values[2],
                    values[3],
                    in_handle=in_handle,
                    smooth=operator.islower(),
                )
            )
            if token.operator_end is not None:
                current_geometry_origins.append(
                    statement_field_origin(
                        f"geometry.{len(current_geometry_origins)}",
                        token.start,
                        token.content_end,
                        token.operator_end,
                    )
                )
            continue
        if line in {"b", "f", "s", "n", "B", "F", "S", "N"} and current_points:
            path_counter += 1
            is_clipping_mask = (
                current_clipping_paths is not None
                and current_clipping_path is None
                and clipping_mask_closed is not None
            )
            is_closed = clipping_mask_closed if is_clipping_mask else line.islower()
            path_points = current_points
            if (
                is_closed
                and len(current_points) > 2
                and current_points[-1].x == current_points[0].x
                and current_points[-1].y == current_points[0].y
            ):
                closing_point = current_points[-1]
                opening_point = current_points[0]
                path_points = [
                    Point(
                        opening_point.x,
                        opening_point.y,
                        in_handle=closing_point.in_handle,
                        out_handle=opening_point.out_handle,
                        smooth=closing_point.smooth,
                    ),
                    *current_points[1:-1],
                ]
            has_fill = not is_clipping_mask and line in {"b", "f", "B", "F"}
            has_stroke = not is_clipping_mask and line in {"b", "s", "B", "S"}
            if has_fill:
                count_fill_origin_use()
            if has_stroke:
                count_stroke_origin_use()
            parsed_path = Path(
                id=pending_id or f"path-{path_counter}",
                points=path_points,
                closed=is_closed,
                fill=fill if has_fill else None,
                stroke=stroke if has_stroke else None,
                stroke_width=stroke_width,
                dash_pattern=list(dash_pattern),
                dash_offset=dash_offset,
                line_cap=line_cap,
                line_join=line_join,
                miter_limit=miter_limit,
                name=pending_name,
                polarity=polarity,
            )
            if pending_linked_image is not None and not is_clipping_mask:
                linked_image = pending_linked_image
                append_item(
                    "image",
                    linked_image,
                    source_start=pending_linked_image_source_start or token.start,
                    source_end=token.end,
                )
                if origins is not None and pending_linked_image_metadata_origin is not None:
                    origins.append(
                        LegacyNodeOrigin(
                            node_type="linked_image",
                            node_id=linked_image.id,
                            start=(
                                pending_linked_image_source_start
                                if pending_linked_image_source_start is not None
                                else pending_linked_image_metadata_origin.start
                            ),
                            end=token.end,
                            fields=(pending_linked_image_metadata_origin,),
                        )
                    )
                pending_linked_image = None
                pending_linked_image_source_start = None
                pending_linked_image_metadata_origin = None
            else:
                if origins is not None:
                    path_origin_candidates.append(
                        (
                            parsed_path.id,
                            (
                                current_path_source_start
                                if current_path_source_start is not None
                                else token.start
                            ),
                            token.end,
                            current_fill_origin if has_fill else None,
                            current_stroke_origin if has_stroke else None,
                            tuple(current_geometry_origins),
                        )
                    )
                if is_clipping_mask:
                    current_clipping_path = parsed_path
                    clipping_mask_closed = None
                elif current_compound_paths is not None:
                    current_compound_paths.append(parsed_path)
                elif current_clipping_paths is not None:
                    current_clipping_paths.append(parsed_path)
                else:
                    append_item(
                        "path",
                        parsed_path,
                        source_start=current_path_source_start or token.start,
                        source_end=token.end,
                    )
            current_points = []
            current_path_source_start = None
            current_geometry_origins = []
            pending_id = None
            pending_name = None
            polarity = "positive"

    if current_layer is not None:
        if origins is not None and current_layer_source_start is not None:
            origins.append(
                LegacyNodeOrigin(
                    node_type="layer",
                    node_id=current_layer.id,
                    start=current_layer_source_start,
                    end=current_layer_source_end or len(data),
                )
            )
        layers.append(current_layer)
    if width is None or height is None:
        raise UnsupportedLegacyFeature("A numeric %%BoundingBox is required in Phase 0")
    if origins is not None:
        for (
            path_id,
            geometry_start,
            end,
            fill_origin,
            stroke_origin,
            geometry_origins,
        ) in path_origin_candidates:
            unique_fill_origin = (
                fill_origin
                if fill_origin is not None
                and fill_origin_use_counts[(fill_origin.start, fill_origin.end)] == 1
                else None
            )
            unique_stroke_origin = (
                stroke_origin
                if stroke_origin is not None
                and stroke_origin_use_counts[(stroke_origin.start, stroke_origin.end)] == 1
                else None
            )
            fields = (
                *(
                    origin
                    for origin in (unique_fill_origin, unique_stroke_origin)
                    if origin is not None
                ),
                *geometry_origins,
            )
            origins.append(
                LegacyNodeOrigin(
                    node_type="path",
                    node_id=path_id,
                    start=min((geometry_start, *(origin.start for origin in fields))),
                    end=end,
                    fields=fields,
                )
            )
        origins.append(
            LegacyNodeOrigin(
                node_type="document",
                node_id="document",
                start=0,
                end=len(data),
            )
        )
        origins.sort(
            key=lambda origin: (origin.start, -origin.end, origin.node_type, origin.node_id)
        )
    return Document(
        width=width,
        height=height,
        title=title,
        layers=layers,
        metadata=metadata,
        artboards=artboards,
    )


def reads_ai7(data: bytes) -> LegacyReadResult:
    """Parse legacy data and retain exact source, coverage, and diagnostics."""

    source = tokenize_legacy(data)
    origins: list[LegacyNodeOrigin] = []
    document = _loads_ai7_source(source, origins=origins)
    coverage, diagnostics = analyze_legacy_source(source)
    return LegacyReadResult(
        document=document,
        source=source,
        coverage=coverage,
        diagnostics=diagnostics,
        origins=tuple(origins),
    )


def loads_ai7(data: bytes) -> Document:
    """Parse only the modeled IR; use :func:`reads_ai7` for safety evidence."""

    return _loads_ai7_source(tokenize_legacy(data))


@dataclass(frozen=True, slots=True)
class SetPathFill:
    """Typed local edit with an explicit semantic precondition."""

    path_id: str
    fill: ProcessColor
    expected_fill: ProcessColor

    def __post_init__(self) -> None:
        if not self.path_id:
            raise ValueError("path_id must not be empty")


@dataclass(frozen=True, slots=True)
class SetPathStroke:
    """Typed local stroke-color edit with an explicit semantic precondition."""

    path_id: str
    stroke: ProcessColor
    expected_stroke: ProcessColor

    def __post_init__(self) -> None:
        if not self.path_id:
            raise ValueError("path_id must not be empty")


@dataclass(frozen=True, slots=True)
class TranslatePath:
    """Typed local path translation with an explicit geometry precondition."""

    path_id: str
    dx: float
    dy: float
    expected_points: tuple[Point, ...]

    def __post_init__(self) -> None:
        if not self.path_id:
            raise ValueError("path_id must not be empty")
        if not math.isfinite(self.dx) or not math.isfinite(self.dy):
            raise ValueError("translation offsets must be finite")
        if not self.expected_points:
            raise ValueError("expected_points must not be empty")


@dataclass(frozen=True, slots=True)
class ReplaceLinkedImageSource:
    """Typed local linked-image source edit with an explicit precondition."""

    image_id: str
    source: str
    expected_source: str

    def __post_init__(self) -> None:
        if not self.image_id:
            raise ValueError("image_id must not be empty")
        if not self.source or "\x00" in self.source:
            raise ValueError("source must be a non-empty path without NUL bytes")
        if not self.expected_source or "\x00" in self.expected_source:
            raise ValueError("expected_source must be a non-empty path without NUL bytes")


@dataclass(frozen=True, slots=True)
class ReplaceText:
    """Typed local text edit with an explicit semantic precondition."""

    text_id: str
    text: str
    expected_text: str

    def __post_init__(self) -> None:
        if not self.text_id:
            raise ValueError("text_id must not be empty")


def _container_paths(container: Layer | Group) -> list[Path]:
    paths = [
        *container.paths,
        *(path for compound in container.compound_paths for path in compound.paths),
        *(
            path
            for clipping in container.clipping_groups
            for path in [clipping.clipping_path, *clipping.paths]
        ),
    ]
    for group in container.groups:
        paths.extend(_container_paths(group))
    return paths


def _container_text_frames(container: Layer | Group) -> list[TextFrame]:
    text_frames = list(container.text_frames)
    for group in container.groups:
        text_frames.extend(_container_text_frames(group))
    return text_frames


def _container_linked_images(container: Layer | Group) -> list[LinkedImage]:
    linked_images = list(container.linked_images)
    for group in container.groups:
        linked_images.extend(_container_linked_images(group))
    return linked_images


def _matching_paths(result: LegacyReadResult, path_id: str) -> list[Path]:
    return [
        path
        for layer in result.document.layers
        for path in _container_paths(layer)
        if path.id == path_id
    ]


def _unique_origin(
    result: LegacyReadResult, *, node_type: str, node_id: str
) -> LegacyNodeOrigin:
    matching_origins = [
        origin
        for origin in result.origins
        if origin.node_type == node_type and origin.node_id == node_id
    ]
    if len(matching_origins) != 1:
        raise UnsupportedLegacyFeature(
            f"{node_type.capitalize()} {node_id!r} has {len(matching_origins)} source origins; "
            "exactly one is required."
        )
    return matching_origins[0]


def _validate_patch_field(
    result: LegacyReadResult,
    *,
    origin: LegacyNodeOrigin,
    field_name: str,
    node_label: str,
) -> LegacyFieldOrigin:
    field_origin = origin.field(field_name)
    if field_origin is None:
        raise UnsupportedLegacyFeature(
            f"{node_label} does not have an exclusive source {field_name} span."
        )
    actual = result.source.data[field_origin.start : field_origin.end]
    if actual != field_origin.expected:
        raise UnsupportedLegacyFeature(
            f"{node_label} source precondition failed; the {field_name} span changed."
        )
    intersecting = [
        diagnostic
        for diagnostic in result.diagnostics
        if diagnostic.start < field_origin.end and diagnostic.end > field_origin.start
    ]
    if intersecting:
        raise UnsupportedLegacyFeature(
            f"{node_label} {field_name} span intersects unsupported source syntax."
        )
    return field_origin


def patch_path_fill(result: LegacyReadResult, operation: SetPathFill) -> LegacySource:
    """Patch one uniquely selected path fill while preserving all other source bytes."""

    matching_paths = _matching_paths(result, operation.path_id)
    if len(matching_paths) != 1:
        raise UnsupportedLegacyFeature(
            f"Path selector id={operation.path_id!r} matched {len(matching_paths)} nodes; "
            "exactly one is required."
        )
    path = matching_paths[0]
    if path.fill != operation.expected_fill:
        raise UnsupportedLegacyFeature(
            f"Path {operation.path_id!r} fill precondition failed: "
            f"expected {operation.expected_fill!r}, found {path.fill!r}."
        )

    origin = _unique_origin(result, node_type="path", node_id=operation.path_id)
    fill_origin = _validate_patch_field(
        result,
        origin=origin,
        field_name="fill",
        node_label=f"Path {operation.path_id!r}",
    )

    replacement = _color_operator(operation.fill, stroke=False).encode("ascii")
    return result.source.patched(
        [SourceReplacement(fill_origin.start, fill_origin.end, replacement)]
    )


def patch_path_stroke(result: LegacyReadResult, operation: SetPathStroke) -> LegacySource:
    """Patch one uniquely selected path stroke while preserving all other source bytes."""

    matching_paths = _matching_paths(result, operation.path_id)
    if len(matching_paths) != 1:
        raise UnsupportedLegacyFeature(
            f"Path selector id={operation.path_id!r} matched {len(matching_paths)} nodes; "
            "exactly one is required."
        )
    path = matching_paths[0]
    if path.stroke != operation.expected_stroke:
        raise UnsupportedLegacyFeature(
            f"Path {operation.path_id!r} stroke precondition failed: "
            f"expected {operation.expected_stroke!r}, found {path.stroke!r}."
        )

    origin = _unique_origin(result, node_type="path", node_id=operation.path_id)
    stroke_origin = _validate_patch_field(
        result,
        origin=origin,
        field_name="stroke",
        node_label=f"Path {operation.path_id!r}",
    )

    replacement = _color_operator(operation.stroke, stroke=True).encode("ascii")
    return result.source.patched(
        [SourceReplacement(stroke_origin.start, stroke_origin.end, replacement)]
    )


def _translated_geometry_statement(statement: bytes, *, dx: float, dy: float) -> bytes:
    line = statement.decode("latin-1")
    point_match = _POINT_RE.fullmatch(line)
    if point_match:
        return (
            f"{_number(float(point_match.group(1)) + dx)} "
            f"{_number(float(point_match.group(2)) + dy)} {point_match.group(3)}"
        ).encode("ascii")

    cubic_match = _CUBIC_RE.fullmatch(line)
    if cubic_match:
        values = [float(cubic_match.group(index)) for index in range(1, 7)]
        translated = [
            value + (dx if index % 2 == 0 else dy) for index, value in enumerate(values)
        ]
        return " ".join(
            [*(_number(value) for value in translated), cubic_match.group(7)]
        ).encode("ascii")

    short_cubic_match = _SHORT_CUBIC_RE.fullmatch(line)
    if short_cubic_match:
        values = [float(short_cubic_match.group(index)) for index in range(1, 5)]
        translated = [
            value + (dx if index % 2 == 0 else dy) for index, value in enumerate(values)
        ]
        return " ".join(
            [*(_number(value) for value in translated), short_cubic_match.group(5)]
        ).encode("ascii")

    raise UnsupportedLegacyFeature(
        "Path geometry source precondition failed; a geometry statement is no longer recognized."
    )


def patch_path_translate(result: LegacyReadResult, operation: TranslatePath) -> LegacySource:
    """Translate one path through statement-local replacements."""

    matching_paths = _matching_paths(result, operation.path_id)
    if len(matching_paths) != 1:
        raise UnsupportedLegacyFeature(
            f"Path selector id={operation.path_id!r} matched {len(matching_paths)} nodes; "
            "exactly one is required."
        )
    path = matching_paths[0]
    if tuple(path.points) != operation.expected_points:
        raise UnsupportedLegacyFeature(
            f"Path {operation.path_id!r} geometry precondition failed: expected points do not "
            "match the parsed path."
        )

    origin = _unique_origin(result, node_type="path", node_id=operation.path_id)
    intersecting = [
        diagnostic
        for diagnostic in result.diagnostics
        if diagnostic.start < origin.end and diagnostic.end > origin.start
    ]
    if intersecting:
        raise UnsupportedLegacyFeature(
            f"Path {operation.path_id!r} source span intersects unsupported source syntax."
        )

    geometry_origins = origin.fields_with_prefix("geometry.")
    if not geometry_origins:
        raise UnsupportedLegacyFeature(
            f"Path {operation.path_id!r} does not have local source geometry spans."
        )

    replacements: list[SourceReplacement] = []
    for index, geometry_origin in enumerate(geometry_origins):
        if geometry_origin.field != f"geometry.{index}":
            raise UnsupportedLegacyFeature(
                f"Path {operation.path_id!r} has incomplete source geometry spans."
            )
        validated = _validate_patch_field(
            result,
            origin=origin,
            field_name=geometry_origin.field,
            node_label=f"Path {operation.path_id!r}",
        )
        replacements.append(
            SourceReplacement(
                validated.start,
                validated.end,
                (
                    validated.expected
                    if operation.dx == 0 and operation.dy == 0
                    else _translated_geometry_statement(
                        validated.expected,
                        dx=operation.dx,
                        dy=operation.dy,
                    )
                ),
            )
        )
    return result.source.patched(replacements)


def patch_linked_image_source(
    result: LegacyReadResult, operation: ReplaceLinkedImageSource
) -> LegacySource:
    """Patch one linked-image source in its private legacy metadata."""

    matching_images = [
        image
        for layer in result.document.layers
        for image in _container_linked_images(layer)
        if image.id == operation.image_id
    ]
    if len(matching_images) != 1:
        raise UnsupportedLegacyFeature(
            f"Linked image selector id={operation.image_id!r} matched {len(matching_images)} "
            "nodes; exactly one is required."
        )
    image = matching_images[0]
    if image.source != operation.expected_source:
        raise UnsupportedLegacyFeature(
            f"Linked image {operation.image_id!r} source precondition failed: "
            f"expected {operation.expected_source!r}, found {image.source!r}."
        )

    origin = _unique_origin(result, node_type="linked_image", node_id=operation.image_id)
    metadata_origin = _validate_patch_field(
        result,
        origin=origin,
        field_name="metadata",
        node_label=f"Linked image {operation.image_id!r}",
    )
    try:
        decoded = base64.b64decode(metadata_origin.expected, validate=True).decode("utf-8")
        payload = json.loads(decoded)
    except (ValueError, UnicodeError, json.JSONDecodeError) as error:
        raise UnsupportedLegacyFeature(
            f"Linked image {operation.image_id!r} metadata precondition failed."
        ) from error
    if (
        not isinstance(payload, dict)
        or payload.get("id") != operation.image_id
        or payload.get("source") != operation.expected_source
    ):
        raise UnsupportedLegacyFeature(
            f"Linked image {operation.image_id!r} metadata precondition failed."
        )

    payload["source"] = operation.source
    replacement = base64.b64encode(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    )
    return result.source.patched(
        [SourceReplacement(metadata_origin.start, metadata_origin.end, replacement)]
    )


def patch_text(result: LegacyReadResult, operation: ReplaceText) -> LegacySource:
    """Patch one uniquely selected text frame while preserving all other source bytes."""

    matching_text_frames = [
        text_frame
        for layer in result.document.layers
        for text_frame in _container_text_frames(layer)
        if text_frame.id == operation.text_id
    ]
    if len(matching_text_frames) != 1:
        raise UnsupportedLegacyFeature(
            f"Text selector id={operation.text_id!r} matched {len(matching_text_frames)} nodes; "
            "exactly one is required."
        )
    text_frame = matching_text_frames[0]
    if text_frame.text != operation.expected_text:
        raise UnsupportedLegacyFeature(
            f"Text {operation.text_id!r} content precondition failed: "
            f"expected {operation.expected_text!r}, found {text_frame.text!r}."
        )

    origin = _unique_origin(result, node_type="text", node_id=operation.text_id)
    text_origin = _validate_patch_field(
        result,
        origin=origin,
        field_name="text",
        node_label=f"Text {operation.text_id!r}",
    )
    replacement = _escape_postscript_text(
        operation.text, font_name=text_frame.font_name
    ).encode("ascii")
    return result.source.patched(
        [SourceReplacement(text_origin.start, text_origin.end, replacement)]
    )


def reserialize_ai7(
    result: LegacyReadResult,
    *,
    loss_policy: Literal["reject", "discard"] = "reject",
) -> bytes:
    """Serialize parsed IR, rejecting unsupported source features by default."""

    if loss_policy not in {"reject", "discard"}:
        raise ValueError("loss_policy must be 'reject' or 'discard'")
    if loss_policy == "reject" and not result.safe_to_reserialize:
        unsupported = sorted(
            {
                diagnostic.feature_name
                for diagnostic in result.diagnostics
                if diagnostic.code.startswith("unsupported-")
            }
        )
        detail = ", ".join(repr(name) for name in unsupported[:5])
        if len(unsupported) > 5:
            detail += f", and {len(unsupported) - 5} more"
        raise UnsupportedLegacyFeature(
            "Refusing to reserialize parsed legacy IR because unsupported source features "
            f"would be discarded: {detail}. Use loss_policy='discard' explicitly to allow loss."
        )
    return dumps_ai7(result.document)


def read_ai7(source: str | FilePath) -> LegacyReadResult:
    """Read a legacy file with exact source and compatibility evidence."""

    return reads_ai7(FilePath(source).read_bytes())


def load_ai7(source: str | FilePath) -> Document:
    return loads_ai7(FilePath(source).read_bytes())
