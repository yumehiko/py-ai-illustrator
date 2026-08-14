"""Reader and writer for a deliberately small Illustrator 7 compatible subset."""

from __future__ import annotations

import base64
import json
import re
from pathlib import Path as FilePath

from .lossless import tokenize_legacy
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
    ProcessColor,
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
_POLARITY_RE = re.compile(r"^([01])\s+D$")
_BOUNDS_RE = re.compile(r"^%%(?:HiRes)?BoundingBox:\s+(.+)$")
_LAYER_NAME_RE = re.compile(r"^\((.*)\)\s+Ln$")
_LAYER_RE = re.compile(r"^([01])\s+1\s+([01])\s+1\s+0\s+0\s+.+\s+Lb$")
_PATH_NOTE_PREFIX = "py-ai:"


class UnsupportedLegacyFeature(ValueError):
    """Raised when data falls outside the Phase 0 legacy subset."""


def _number(value: float) -> str:
    return f"{value:.6f}".rstrip("0").rstrip(".") or "0"


def _escape_postscript_string(value: str) -> str:
    return value.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _unescape_postscript_string(value: str) -> str:
    return value.replace("\\)", ")").replace("\\(", "(").replace("\\\\", "\\")


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
        decoded = base64.b64decode(
            note.removeprefix(_PATH_NOTE_PREFIX), validate=True
        ).decode("utf-8")
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
    lines = [f"%AI7_Tag: ({_escape_postscript_string(path.id)})"]
    if path.name is not None:
        lines.append(f"%%py-ai-path-name: ({_escape_postscript_string(path.name)})")
    lines.extend(["1 A" if locked else "0 A", "1 D" if path.polarity == "positive" else "0 D"])
    note = _path_note(path)
    if note is not None:
        lines.append(f"%AI3_Note:{note}")
    if path.fill is not None:
        lines.append(_color_operator(path.fill, stroke=False))
    if path.stroke is not None:
        lines.append(_color_operator(path.stroke, stroke=True))
    lines.append(f"{_number(path.stroke_width)} w")
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
    lines = [
        f"%AI7_Tag: ({_escape_postscript_string(path.id)})",
        "1 A" if locked else "0 A",
        "1 D" if path.polarity == "positive" else "0 D",
    ]
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


def dumps_ai7(document: Document) -> bytes:
    """Serialize the supported IR subset as editable legacy Illustrator data."""

    width = _number(document.width)
    height = _number(document.height)
    title = _escape_postscript_string(document.title)
    lines = [
        "%!PS-Adobe-3.0",
        "%%Creator: py-ai-illustrator (Adobe Illustrator 7 compatible subset)",
        f"%%Title: ({title})",
        f"%%BoundingBox: 0 0 {int(document.width + 0.999999)} {int(document.height + 0.999999)}",
        f"%%HiResBoundingBox: 0 0 {width} {height}",
        "%%DocumentProcessColors: Cyan Magenta Yellow Black",
        "%AI5_FileFormat 3.0",
        "%AI3_ColorUsage: Color",
        "%AI3_DocumentPreview: None",
        "%%py-ai-metadata: "
        + base64.b64encode(
            json.dumps(document.metadata, ensure_ascii=False, separators=(",", ":")).encode()
        ).decode("ascii"),
        "%%EndComments",
        "%%BeginProlog",
        "%%EndProlog",
        "%%BeginSetup",
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
            if isinstance(item, Path):
                lines.extend(_serialized_path(item, locked=layer.locked))
            elif isinstance(item, CompoundPath):
                lines.extend(
                    [
                        f"%%py-ai-compound-id: ({_escape_postscript_string(item.id)})",
                        *(
                            [
                                "%%py-ai-compound-name: "
                                f"({_escape_postscript_string(item.name)})"
                            ]
                            if item.name is not None
                            else []
                        ),
                        "*u",
                    ]
                )
                for path in item.paths:
                    lines.extend(_serialized_path(path, locked=layer.locked))
                lines.append("*U")
            else:
                lines.extend(
                    [
                        f"%%py-ai-clipping-id: ({_escape_postscript_string(item.id)})",
                        *(
                            [
                                "%%py-ai-clipping-name: "
                                f"({_escape_postscript_string(item.name)})"
                            ]
                            if item.name is not None
                            else []
                        ),
                        "q",
                    ]
                )
                lines.extend(
                    _serialized_clipping_path(item.clipping_path, locked=layer.locked)
                )
                for path in item.paths:
                    lines.extend(_serialized_path(path, locked=layer.locked))
                lines.append("Q")
        lines.extend(["LB", "%AI5_EndLayer"])

    lines.extend(["%%Trailer", "%%EOF", ""])
    return "\n".join(lines).encode("ascii", errors="strict")


def dump_ai7(document: Document, destination: str | FilePath) -> None:
    FilePath(destination).write_bytes(dumps_ai7(document))


def loads_ai7(data: bytes) -> Document:
    """Parse files emitted by this project and a conservative AI7 path subset."""

    source = tokenize_legacy(data)
    text = data.decode("latin-1")
    if not text.startswith("%!PS-Adobe") or "%AI" not in text:
        raise UnsupportedLegacyFeature("Not a recognizable legacy Illustrator document")

    width = height = None
    title = "Untitled"
    title_seen = False
    layers: list[Layer] = []
    current_layer: Layer | None = None
    current_compound_paths: list[Path] | None = None
    current_compound_id: str | None = None
    current_compound_name: str | None = None
    current_clipping_paths: list[Path] | None = None
    current_clipping_path: Path | None = None
    current_clipping_id: str | None = None
    current_clipping_name: str | None = None
    clipping_mask_closed: bool | None = None
    current_points: list[Point] = []
    fill: ProcessColor | None = None
    stroke: ProcessColor | None = None
    stroke_width = 1.0
    pending_id: str | None = None
    pending_name: str | None = None
    pending_compound_id: str | None = None
    pending_compound_name: str | None = None
    pending_clipping_id: str | None = None
    pending_clipping_name: str | None = None
    polarity = "positive"
    path_counter = 0
    metadata: dict[str, object] = {}

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
        bounds_match = _BOUNDS_RE.match(line)
        if bounds_match and width is None:
            values = bounds_match.group(1).split()
            if len(values) == 4 and values[0] != "(atend)":
                llx, lly, urx, ury = map(float, values)
                width, height = urx - llx, ury - lly
            continue
        if line == "%AI5_BeginLayer":
            current_layer = Layer(id=f"layer-{len(layers) + 1}", name=f"Layer {len(layers) + 1}")
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
            layers.append(current_layer)
            current_layer = None
            continue
        if line.startswith("%%py-ai-compound-id: (") and line.endswith(")"):
            value = line.removeprefix("%%py-ai-compound-id: (")[:-1]
            pending_compound_id = _unescape_postscript_string(value)
            continue
        if line.startswith("%%py-ai-compound-name: (") and line.endswith(")"):
            value = line.removeprefix("%%py-ai-compound-name: (")[:-1]
            pending_compound_name = _unescape_postscript_string(value)
            continue
        if line == "*u":
            current_compound_paths = []
            current_compound_id = pending_compound_id or f"compound-{len(layers) + 1}"
            current_compound_name = pending_compound_name
            pending_compound_id = None
            pending_compound_name = None
            continue
        if line == "*U" and current_compound_paths is not None:
            if current_layer is None:
                current_layer = Layer(id="layer-1", name="Layer 1")
            if len(current_compound_paths) >= 2:
                compound = CompoundPath(
                    id=current_compound_id or f"compound-{len(layers) + 1}",
                    name=current_compound_name,
                    paths=current_compound_paths,
                )
                current_layer.compound_paths.append(compound)
                current_layer.item_order.append(LayerItemRef("compound_path", compound.id))
            else:
                current_layer.paths.extend(current_compound_paths)
                current_layer.item_order.extend(
                    LayerItemRef("path", path.id) for path in current_compound_paths
                )
            current_compound_paths = None
            current_compound_id = None
            current_compound_name = None
            continue
        if line.startswith("%%py-ai-clipping-id: (") and line.endswith(")"):
            value = line.removeprefix("%%py-ai-clipping-id: (")[:-1]
            pending_clipping_id = _unescape_postscript_string(value)
            continue
        if line.startswith("%%py-ai-clipping-name: (") and line.endswith(")"):
            value = line.removeprefix("%%py-ai-clipping-name: (")[:-1]
            pending_clipping_name = _unescape_postscript_string(value)
            continue
        if line == "q":
            current_clipping_paths = []
            current_clipping_path = None
            current_clipping_id = pending_clipping_id or f"clipping-{len(layers) + 1}"
            current_clipping_name = pending_clipping_name
            clipping_mask_closed = None
            pending_clipping_id = None
            pending_clipping_name = None
            continue
        if line == "Q" and current_clipping_paths is not None:
            if current_layer is None:
                current_layer = Layer(id="layer-1", name="Layer 1")
            if current_clipping_path is not None and current_clipping_paths:
                group = ClippingGroup(
                    id=current_clipping_id or f"clipping-{len(layers) + 1}",
                    name=current_clipping_name,
                    clipping_path=current_clipping_path,
                    paths=current_clipping_paths,
                )
                current_layer.clipping_groups.append(group)
                current_layer.item_order.append(LayerItemRef("clipping_group", group.id))
            else:
                if current_clipping_path is not None:
                    current_layer.paths.append(current_clipping_path)
                    current_layer.item_order.append(
                        LayerItemRef("path", current_clipping_path.id)
                    )
                current_layer.paths.extend(current_clipping_paths)
                current_layer.item_order.extend(
                    LayerItemRef("path", path.id) for path in current_clipping_paths
                )
            current_clipping_paths = None
            current_clipping_path = None
            current_clipping_id = None
            current_clipping_name = None
            clipping_mask_closed = None
            continue
        if line.startswith("%AI7_Tag: (") and line.endswith(")"):
            pending_id = _unescape_postscript_string(line[11:-1])
            continue
        if line.startswith("%%py-ai-path-name: (") and line.endswith(")"):
            pending_name = _unescape_postscript_string(line[20:-1])
            continue
        if line.startswith("%AI3_Note:"):
            note_id, note_name = _parse_path_note(line[10:].lstrip())
            if note_id is not None:
                pending_id = note_id
            if note_name is not None:
                pending_name = note_name
            continue
        ai8_rgb_match = _AI8_RGB_COLOR_RE.match(line)
        if ai8_rgb_match:
            color = Color(*(float(ai8_rgb_match.group(index)) for index in range(5, 8)))
            if ai8_rgb_match.group(8) == "Xa":
                fill = color
            else:
                stroke = color
            continue
        color_match = _COLOR_RE.match(line)
        if color_match:
            color = Color(*(float(color_match.group(index)) for index in range(1, 4)))
            if color_match.group(4) == "Xa":
                fill = color
            else:
                stroke = color
            continue
        cmyk_match = _CMYK_COLOR_RE.match(line)
        if cmyk_match:
            color = CmykColor(*(float(cmyk_match.group(index)) for index in range(1, 5)))
            if cmyk_match.group(5) == "k":
                fill = color
            else:
                stroke = color
            continue
        width_match = _WIDTH_RE.search(line)
        if width_match:
            stroke_width = float(width_match.group(1))
            continue
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
            else:
                current_points.append(point)
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
            continue
        if line in {"b", "f", "s", "n", "B", "F", "S", "N"} and current_points:
            if current_layer is None:
                current_layer = Layer(id="layer-1", name="Layer 1")
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
            parsed_path = Path(
                id=pending_id or f"path-{path_counter}",
                points=path_points,
                closed=is_closed,
                fill=fill if has_fill else None,
                stroke=stroke if has_stroke else None,
                stroke_width=stroke_width,
                name=pending_name,
                polarity=polarity,
            )
            if is_clipping_mask:
                current_clipping_path = parsed_path
                clipping_mask_closed = None
            elif current_compound_paths is not None:
                current_compound_paths.append(parsed_path)
            elif current_clipping_paths is not None:
                current_clipping_paths.append(parsed_path)
            else:
                current_layer.paths.append(parsed_path)
                current_layer.item_order.append(LayerItemRef("path", parsed_path.id))
            current_points = []
            pending_id = None
            pending_name = None
            polarity = "positive"

    if current_layer is not None:
        layers.append(current_layer)
    if width is None or height is None:
        raise UnsupportedLegacyFeature("A numeric %%BoundingBox is required in Phase 0")
    return Document(width=width, height=height, title=title, layers=layers, metadata=metadata)


def load_ai7(source: str | FilePath) -> Document:
    return loads_ai7(FilePath(source).read_bytes())
