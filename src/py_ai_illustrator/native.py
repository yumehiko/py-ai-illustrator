"""Compile the project-owned graphic IR directly through Illustrator's DOM."""

from __future__ import annotations

import json
import math
import os
import platform
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from .assets import PackagedLink, package_linked_images
from .format import FileFormat, inspect_file
from .illustrator import _execute_javascript
from .model import (
    ClippingGroup,
    CmykColor,
    CompoundPath,
    Document,
    Group,
    Layer,
    LinkedImage,
    ProcessColor,
    TextFrame,
)
from .model import Path as AIPath


@dataclass(frozen=True, slots=True)
class NativeCompileProfile:
    """Explicit settings needed when creating a new Illustrator document."""

    color_space: Literal["rgb", "cmyk"] = "rgb"
    pdf_compatible: bool = True
    embed_linked_files: bool = False

    def __post_init__(self) -> None:
        if self.color_space not in {"rgb", "cmyk"}:
            raise ValueError("color_space must be 'rgb' or 'cmyk'")
        if not self.pdf_compatible:
            raise ValueError("Direct native compile requires a PDF-compatible AI output")
        if self.embed_linked_files:
            raise ValueError("Direct native compile currently preserves images as external links")


def _javascript_literal(value: Any) -> str:
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, str):
        codepoints = ",".join(str(ord(character)) for character in value)
        return f"String.fromCharCode({codepoints})"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("Native compile values must be finite")
        return repr(value)
    if isinstance(value, list | tuple):
        return "[" + ",".join(_javascript_literal(item) for item in value) + "]"
    if isinstance(value, dict):
        return "{" + ",".join(
            f"{json.dumps(str(key))}:{_javascript_literal(item)}"
            for key, item in value.items()
        ) + "}"
    raise TypeError(f"Unsupported native compile literal: {type(value).__name__}")


def _identity_note(kind: str, item_id: str, name: str | None) -> str:
    payload = json.dumps(
        {"id": item_id, "name": name},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return f"py-ai-{kind}:{payload}"


def _color_spec(color: ProcessColor | None) -> dict[str, object] | None:
    if color is None:
        return None
    if isinstance(color, CmykColor):
        return {
            "type": "cmyk",
            "values": [color.cyan, color.magenta, color.yellow, color.black],
        }
    return {"type": "rgb", "values": [color.red, color.green, color.blue]}


def _path_spec(path: AIPath) -> dict[str, object]:
    return {
        "kind": "path",
        "id": path.id,
        "name": path.name or path.id,
        "note": _identity_note("path", path.id, path.name),
        "points": [
            {
                "anchor": [point.x, point.y],
                "left": (
                    [point.in_handle.x, point.in_handle.y]
                    if point.in_handle is not None
                    else [point.x, point.y]
                ),
                "right": (
                    [point.out_handle.x, point.out_handle.y]
                    if point.out_handle is not None
                    else [point.x, point.y]
                ),
                "smooth": point.smooth,
            }
            for point in path.points
        ],
        "closed": path.closed,
        "fill": _color_spec(path.fill),
        "stroke": _color_spec(path.stroke),
        "stroke_width": path.stroke_width,
        "dash_pattern": path.dash_pattern,
        "dash_offset": path.dash_offset,
        "line_cap": path.line_cap,
        "line_join": path.line_join,
        "miter_limit": path.miter_limit,
        "polarity": path.polarity,
    }


def _text_spec(text: TextFrame) -> dict[str, object]:
    font_name = text.native_font_name or text.font_name
    if "RKSJ-" in font_name:
        raise ValueError(
            f"Text {text.id!r} requires a native PostScript font name for direct compile"
        )
    return {
        "kind": "text",
        "id": text.id,
        "name": text.name or text.id,
        "note": _identity_note("text", text.id, text.name),
        "contents": text.text,
        "x": text.x,
        "y": text.y,
        "font_name": font_name,
        "font_size": text.font_size,
        "tracking": text.tracking,
        "rotation": text.rotation,
        "area_width": text.area_width,
        "area_height": text.area_height,
        "leading": text.leading,
        "fill": _color_spec(text.fill),
        "alignment": text.alignment,
    }


def _image_spec(image: LinkedImage, destination_directory: Path) -> dict[str, object]:
    linked_file = (destination_directory / image.source).resolve()
    return {
        "kind": "image",
        "id": image.id,
        "name": image.name or image.id,
        "note": _identity_note("image", image.id, image.name),
        "file": str(linked_file),
        "x": image.x,
        "y": image.y,
        "width": image.width,
        "height": image.height,
        "rotation": image.rotation,
    }


def _compound_spec(compound: CompoundPath) -> dict[str, object]:
    return {
        "kind": "compound_path",
        "id": compound.id,
        "name": compound.name or compound.id,
        "note": _identity_note("compound-path", compound.id, compound.name),
        "paths": [_path_spec(path) for path in compound.paths],
    }


def _clipping_spec(clipping: ClippingGroup) -> dict[str, object]:
    return {
        "kind": "clipping_group",
        "id": clipping.id,
        "name": clipping.name or clipping.id,
        "note": _identity_note("clipping-group", clipping.id, clipping.name),
        "clipping_path": _path_spec(clipping.clipping_path),
        "paths": [_path_spec(path) for path in clipping.paths],
    }


def _group_spec(group: Group, destination_directory: Path) -> dict[str, object]:
    return {
        "kind": "group",
        "id": group.id,
        "name": group.name or group.id,
        "note": _identity_note("group", group.id, group.name),
        "items": _ordered_item_specs(group, destination_directory),
    }


def _ordered_item_specs(
    container: Layer | Group,
    destination_directory: Path,
) -> list[dict[str, object]]:
    specs: list[dict[str, object]] = []
    for item in container.ordered_items():
        if isinstance(item, AIPath):
            specs.append(_path_spec(item))
        elif isinstance(item, TextFrame):
            specs.append(_text_spec(item))
        elif isinstance(item, LinkedImage):
            specs.append(_image_spec(item, destination_directory))
        elif isinstance(item, CompoundPath):
            specs.append(_compound_spec(item))
        elif isinstance(item, ClippingGroup):
            specs.append(_clipping_spec(item))
        elif isinstance(item, Group):
            specs.append(_group_spec(item, destination_directory))
        else:  # pragma: no cover - model union makes this defensive
            raise TypeError(f"Unsupported native item type: {type(item).__name__}")
    return specs


def _document_spec(
    document: Document,
    destination_directory: Path,
    profile: NativeCompileProfile,
) -> dict[str, object]:
    artboards = [
        {
            "id": artboard.id,
            "name": artboard.name,
            "rect": [
                artboard.left,
                artboard.top,
                artboard.left + artboard.width,
                artboard.top - artboard.height,
            ],
        }
        for artboard in document.artboards
    ]
    if not artboards:
        artboards = [
            {
                "id": "artboard-1",
                "name": "Artboard 1",
                "rect": [0.0, document.height, document.width, 0.0],
            }
        ]
    return {
        "title": document.title,
        "width": document.width,
        "height": document.height,
        "color_space": profile.color_space,
        "pdf_compatible": profile.pdf_compatible,
        "embed_linked_files": profile.embed_linked_files,
        "artboards": artboards,
        "layers": [
            {
                "id": layer.id,
                "name": layer.name,
                "visible": layer.visible,
                "locked": layer.locked,
                "items": _ordered_item_specs(layer, destination_directory),
            }
            for layer in document.layers
        ],
    }


def _walk_items(
    container: Layer | Group,
) -> list[AIPath | TextFrame | LinkedImage | CompoundPath | ClippingGroup | Group]:
    result: list[
        AIPath | TextFrame | LinkedImage | CompoundPath | ClippingGroup | Group
    ] = []
    for item in container.ordered_items():
        if isinstance(item, Group):
            result.append(item)
            result.extend(_walk_items(item))
        elif isinstance(item, CompoundPath):
            result.append(item)
            result.extend(item.paths)
        elif isinstance(item, ClippingGroup):
            result.append(item)
            result.append(item.clipping_path)
            result.extend(item.paths)
        else:
            result.append(item)
    return result


def _validate_document(document: Document) -> None:
    if not document.layers:
        raise ValueError("Native compile requires at least one layer")
    if not all(layer.id and layer.name for layer in document.layers):
        raise ValueError("Native compile requires non-empty layer ids and names")

    identities: dict[str, str] = {}
    for artboard in document.artboards:
        identities[artboard.id] = "Artboard"
    for layer in document.layers:
        if layer.id in identities:
            raise ValueError(f"Duplicate stable id {layer.id!r} in document containers")
        identities[layer.id] = "Layer"
        if layer.unknown:
            raise ValueError(f"Layer {layer.id!r} contains unsupported unknown data")
        for item in _walk_items(layer):
            item_id = item.id
            if not item_id:
                raise ValueError(f"{type(item).__name__} has an empty stable id")
            if item_id in identities:
                raise ValueError(
                    f"Duplicate stable id {item_id!r} in {identities[item_id]} and "
                    f"{type(item).__name__}"
                )
            identities[item_id] = type(item).__name__
            if item.unknown:
                raise ValueError(
                    f"{type(item).__name__} {item_id!r} contains unsupported unknown data"
                )
            if isinstance(item, TextFrame):
                _text_spec(item)


def _build_direct_native_javascript(spec: dict[str, object], destination: Path) -> str:
    spec_literal = _javascript_literal(spec)
    destination_literal = _javascript_literal(str(destination))
    return f"""#target illustrator
(function () {{
    var spec = {spec_literal};
    var destination = new File({destination_literal});
    var documentRef = null;
    var previousInteractionLevel = app.userInteractionLevel;
    var previousCoordinateSystem = app.coordinateSystem;
    var errors = [];

    function quoteString(value) {{
        var slash = String.fromCharCode(92);
        var quote = String.fromCharCode(34);
        var carriageReturn = String.fromCharCode(13);
        var lineFeed = String.fromCharCode(10);
        return quote + String(value)
            .split(slash).join(slash + slash)
            .split(quote).join(slash + quote)
            .split(carriageReturn).join(slash + "r")
            .split(lineFeed).join(slash + "n") + quote;
    }}

    function toJson(value) {{
        if (value === null || typeof value === "undefined") return "null";
        if (typeof value === "string") return quoteString(value);
        if (typeof value === "number" || typeof value === "boolean") return String(value);
        var parts = [];
        var index;
        if (value instanceof Array) {{
            for (index = 0; index < value.length; index++) parts.push(toJson(value[index]));
            return "[" + parts.join(",") + "]";
        }}
        for (var key in value) {{
            if (value.hasOwnProperty(key)) parts.push(quoteString(key) + ":" + toJson(value[key]));
        }}
        return "{{" + parts.join(",") + "}}";
    }}

    function closeEnough(left, right, tolerance) {{
        return Math.abs(left - right) <= tolerance;
    }}

    function angleDifference(left, right) {{
        var difference = (left - right + 180) % 360;
        if (difference < 0) difference += 360;
        return Math.abs(difference - 180);
    }}

    function itemRotation(item) {{
        var matrix = item.matrix;
        return Math.atan2(matrix.mValueB, matrix.mValueA) * 180 / Math.PI;
    }}

    function normalizedText(value) {{
        return String(value).replace(/\\r\\n/g, "\\n").replace(/\\r/g, "\\n");
    }}

    function makeColor(colorSpec) {{
        var color;
        if (colorSpec.type === "rgb") {{
            color = new RGBColor();
            color.red = colorSpec.values[0] * 255;
            color.green = colorSpec.values[1] * 255;
            color.blue = colorSpec.values[2] * 255;
        }} else {{
            color = new CMYKColor();
            color.cyan = colorSpec.values[0] * 100;
            color.magenta = colorSpec.values[1] * 100;
            color.yellow = colorSpec.values[2] * 100;
            color.black = colorSpec.values[3] * 100;
        }}
        return color;
    }}

    function colorMatches(color, colorSpec) {{
        if (colorSpec === null) return color === null;
        var scale = colorSpec.type === "rgb" ? 255 : 100;
        var actual;
        if (colorSpec.type === "rgb") {{
            if (color.typename !== "RGBColor") return false;
            actual = [color.red, color.green, color.blue];
        }} else {{
            if (color.typename !== "CMYKColor") return false;
            actual = [color.cyan, color.magenta, color.yellow, color.black];
        }}
        for (var index = 0; index < actual.length; index++) {{
            if (!closeEnough(actual[index], colorSpec.values[index] * scale, 0.51)) return false;
        }}
        return true;
    }}

    function pointMatches(point, pointSpec, reversed) {{
        var expectedLeft = reversed ? pointSpec.right : pointSpec.left;
        var expectedRight = reversed ? pointSpec.left : pointSpec.right;
        var pairs = [
            [point.anchor, pointSpec.anchor],
            [point.leftDirection, expectedLeft],
            [point.rightDirection, expectedRight]
        ];
        for (var pairIndex = 0; pairIndex < pairs.length; pairIndex++) {{
            if (
                !closeEnough(pairs[pairIndex][0][0], pairs[pairIndex][1][0], 0.01)
                || !closeEnough(pairs[pairIndex][0][1], pairs[pairIndex][1][1], 0.01)
            ) return false;
        }}
        return true;
    }}

    function pathGeometryMatches(path, pathSpec) {{
        var count = path.pathPoints.length;
        var directions = [1, -1];
        for (var directionIndex = 0; directionIndex < directions.length; directionIndex++) {{
            var direction = directions[directionIndex];
            var firstStart = pathSpec.closed ? 0 : (direction === 1 ? 0 : count - 1);
            var lastStart = pathSpec.closed ? count - 1 : firstStart;
            for (var start = firstStart; start <= lastStart; start++) {{
                var matches = true;
                for (var index = 0; index < count; index++) {{
                    var expectedIndex = (start + direction * index + count) % count;
                    if (!pointMatches(
                        path.pathPoints[index],
                        pathSpec.points[expectedIndex],
                        direction === -1
                    )) {{
                        matches = false;
                        break;
                    }}
                }}
                if (matches) return true;
            }}
        }}
        return false;
    }}

    function createPath(parent, pathSpec) {{
        var path = parent.pathItems.add();
        path.name = pathSpec.name;
        path.note = pathSpec.note;
        for (var index = 0; index < pathSpec.points.length; index++) {{
            var pointSpec = pathSpec.points[index];
            var point = path.pathPoints.add();
            point.anchor = pointSpec.anchor;
            point.leftDirection = pointSpec.left;
            point.rightDirection = pointSpec.right;
            point.pointType = pointSpec.smooth ? PointType.SMOOTH : PointType.CORNER;
        }}
        path.closed = pathSpec.closed;
        path.filled = pathSpec.fill !== null;
        if (path.filled) path.fillColor = makeColor(pathSpec.fill);
        path.stroked = pathSpec.stroke !== null;
        if (path.stroked) {{
            path.strokeColor = makeColor(pathSpec.stroke);
            path.strokeWidth = pathSpec.stroke_width;
            path.strokeDashes = pathSpec.dash_pattern;
            path.strokeDashOffset = pathSpec.dash_offset;
            path.strokeCap = {{
                butt: StrokeCap.BUTTENDCAP,
                round: StrokeCap.ROUNDENDCAP,
                projecting: StrokeCap.PROJECTINGENDCAP
            }}[pathSpec.line_cap];
            path.strokeJoin = {{
                miter: StrokeJoin.MITERENDJOIN,
                round: StrokeJoin.ROUNDENDJOIN,
                bevel: StrokeJoin.BEVELENDJOIN
            }}[pathSpec.line_join];
            path.strokeMiterLimit = pathSpec.miter_limit;
        }}
        try {{
            path.polarity = pathSpec.polarity === "negative"
                ? PolarityValues.NEGATIVE
                : PolarityValues.POSITIVE;
        }} catch (polarityError) {{}}
        return path;
    }}

    function createText(parent, textSpec) {{
        var frame;
        if (textSpec.area_width !== null) {{
            var textPath = parent.pathItems.rectangle(
                textSpec.y,
                textSpec.x,
                textSpec.area_width,
                textSpec.area_height
            );
            frame = documentRef.textFrames.areaText(textPath);
        }} else {{
            frame = parent.textFrames.pointText([textSpec.x, textSpec.y]);
        }}
        frame.name = textSpec.name;
        frame.note = textSpec.note;
        frame.contents = textSpec.contents;
        var attributes = frame.textRange.characterAttributes;
        attributes.textFont = app.textFonts.getByName(textSpec.font_name);
        attributes.size = textSpec.font_size;
        attributes.tracking = textSpec.tracking;
        attributes.fillColor = makeColor(textSpec.fill);
        if (textSpec.leading !== null) {{
            attributes.autoLeading = false;
            attributes.leading = textSpec.leading;
        }}
        frame.textRange.paragraphAttributes.justification = {{
            left: Justification.LEFT,
            center: Justification.CENTER,
            right: Justification.RIGHT
        }}[textSpec.alignment];
        if (Math.abs(textSpec.rotation) > 0.0001) {{
            var position = [frame.position[0], frame.position[1]];
            frame.rotate(textSpec.rotation);
            frame.position = position;
        }}
        return frame;
    }}

    function createImage(parent, imageSpec) {{
        var imageFile = new File(imageSpec.file);
        if (!imageFile.exists) throw new Error("Linked image does not exist: " + imageSpec.file);
        var image = parent.placedItems.add();
        image.file = imageFile;
        image.name = imageSpec.name;
        image.note = imageSpec.note;
        image.width = imageSpec.width;
        image.height = imageSpec.height;
        image.position = [imageSpec.x, imageSpec.y];
        if (Math.abs(imageSpec.rotation) > 0.0001) {{
            var position = [image.position[0], image.position[1]];
            image.rotate(imageSpec.rotation);
            image.position = position;
        }}
        return image;
    }}

    function createCompound(parent, compoundSpec) {{
        var compound = parent.compoundPathItems.add();
        compound.name = compoundSpec.name;
        compound.note = compoundSpec.note;
        for (var index = 0; index < compoundSpec.paths.length; index++) {{
            createPath(compound, compoundSpec.paths[index]);
        }}
        return compound;
    }}

    function createClippingGroup(parent, clippingSpec) {{
        var group = parent.groupItems.add();
        group.name = clippingSpec.name;
        group.note = clippingSpec.note;
        for (var index = 0; index < clippingSpec.paths.length; index++) {{
            createPath(group, clippingSpec.paths[index]);
        }}
        var clippingPath = createPath(group, clippingSpec.clipping_path);
        clippingPath.clipping = true;
        group.clipped = true;
        return group;
    }}

    function createItems(parent, items) {{
        for (var index = 0; index < items.length; index++) {{
            var itemSpec = items[index];
            if (itemSpec.kind === "path") createPath(parent, itemSpec);
            else if (itemSpec.kind === "text") createText(parent, itemSpec);
            else if (itemSpec.kind === "image") createImage(parent, itemSpec);
            else if (itemSpec.kind === "compound_path") createCompound(parent, itemSpec);
            else if (itemSpec.kind === "clipping_group") createClippingGroup(parent, itemSpec);
            else if (itemSpec.kind === "group") {{
                var group = parent.groupItems.add();
                group.name = itemSpec.name;
                group.note = itemSpec.note;
                createItems(group, itemSpec.items);
            }} else throw new Error("Unsupported item kind: " + itemSpec.kind);
        }}
    }}

    function expectedType(kind) {{
        if (kind === "path") return "PathItem";
        if (kind === "text") return "TextFrame";
        if (kind === "image") return "PlacedItem";
        if (kind === "compound_path") return "CompoundPathItem";
        return "GroupItem";
    }}

    function directPageItems(container) {{
        var result = [];
        for (var index = 0; index < container.pageItems.length; index++) {{
            var item = container.pageItems[index];
            if (item.parent === container) result.push(item);
        }}
        return result;
    }}

    function pathMismatch(path, pathSpec) {{
        if (path.pathPoints.length !== pathSpec.points.length) return "point count";
        if (path.closed !== pathSpec.closed) return "closed flag";
        if (path.filled !== (pathSpec.fill !== null)) return "filled flag";
        if (path.stroked !== (pathSpec.stroke !== null)) return "stroked flag";
        if (path.filled && !colorMatches(path.fillColor, pathSpec.fill)) return "fill color";
        if (path.stroked) {{
            if (!colorMatches(path.strokeColor, pathSpec.stroke)) return "stroke color";
            if (!closeEnough(path.strokeWidth, pathSpec.stroke_width, 0.01)) {{
                return "stroke width " + path.strokeWidth;
            }}
            if (!closeEnough(path.strokeDashOffset, pathSpec.dash_offset, 0.01)) {{
                return "dash offset " + path.strokeDashOffset;
            }}
            var actualDashLength = typeof path.strokeDashes.length === "number"
                ? path.strokeDashes.length
                : 0;
            if (actualDashLength !== pathSpec.dash_pattern.length) return "dash count";
            for (var dashIndex = 0; dashIndex < path.strokeDashes.length; dashIndex++) {{
                if (!closeEnough(
                    path.strokeDashes[dashIndex],
                    pathSpec.dash_pattern[dashIndex],
                    0.01
                )) return "dash value";
            }}
            var expectedCap = {{
                butt: "StrokeCap.BUTTENDCAP",
                round: "StrokeCap.ROUNDENDCAP",
                projecting: "StrokeCap.PROJECTINGENDCAP"
            }}[pathSpec.line_cap];
            if (String(path.strokeCap) !== expectedCap) return "line cap";
            var expectedJoin = {{
                miter: "StrokeJoin.MITERENDJOIN",
                round: "StrokeJoin.ROUNDENDJOIN",
                bevel: "StrokeJoin.BEVELENDJOIN"
            }}[pathSpec.line_join];
            if (String(path.strokeJoin) !== expectedJoin) return "line join";
            if (!closeEnough(path.strokeMiterLimit, pathSpec.miter_limit, 0.01)) {{
                return "miter limit";
            }}
        }}
        if (!pathGeometryMatches(path, pathSpec)) return "point geometry";
        return null;
    }}

    function verifyText(frame, textSpec) {{
        if (normalizedText(frame.contents) !== normalizedText(textSpec.contents)) return false;
        var attributes = frame.textRange.characterAttributes;
        if (attributes.textFont.name !== textSpec.font_name) return false;
        if (!closeEnough(attributes.size, textSpec.font_size, 0.01)) return false;
        if (!closeEnough(attributes.tracking, textSpec.tracking, 0.01)) return false;
        if (!colorMatches(attributes.fillColor, textSpec.fill)) return false;
        if (
            textSpec.leading !== null
            && !closeEnough(attributes.leading, textSpec.leading, 0.01)
        ) return false;
        var justification = String(frame.textRange.paragraphAttributes.justification);
        if (justification !== "Justification." + textSpec.alignment.toUpperCase()) return false;
        if (angleDifference(itemRotation(frame), textSpec.rotation) > 0.01) return false;
        if (textSpec.area_width !== null) {{
            if (frame.kind !== TextType.AREATEXT) return false;
            if (!closeEnough(frame.width, textSpec.area_width, 0.1)) return false;
            if (!closeEnough(frame.height, textSpec.area_height, 0.1)) return false;
            if (!closeEnough(frame.position[0], textSpec.x, 0.1)) return false;
            if (!closeEnough(frame.position[1], textSpec.y, 0.1)) return false;
            if (frame.overflows) return false;
        }} else {{
            if (frame.kind !== TextType.POINTTEXT) return false;
            if (Math.abs(textSpec.rotation) <= 0.0001) {{
                if (!closeEnough(frame.anchor[0], textSpec.x, 0.1)) return false;
                if (!closeEnough(frame.anchor[1], textSpec.y, 0.1)) return false;
            }}
        }}
        return true;
    }}

    function verifyImage(image, imageSpec) {{
        var expectedFile = new File(imageSpec.file);
        return image.file.exists
            && image.file.fsName === expectedFile.fsName
            && closeEnough(image.position[0], imageSpec.x, 0.1)
            && closeEnough(image.position[1], imageSpec.y, 0.1)
            && closeEnough(image.width, imageSpec.width, 0.1)
            && closeEnough(image.height, imageSpec.height, 0.1)
            && angleDifference(itemRotation(image), imageSpec.rotation) <= 0.01;
    }}

    function verifyCompound(compound, compoundSpec, path) {{
        if (compound.pathItems.length !== compoundSpec.paths.length) {{
            errors.push(path + ": compound component count mismatch for " + compoundSpec.id);
            return;
        }}
        for (var index = 0; index < compoundSpec.paths.length; index++) {{
            var pathSpec = compoundSpec.paths[compoundSpec.paths.length - index - 1];
            var component = compound.pathItems[index];
            if (component.note !== pathSpec.note) {{
                errors.push(path + ": compound identity mismatch for " + pathSpec.id);
            }}
            var reason = pathMismatch(component, pathSpec);
            if (reason !== null) {{
                errors.push(
                    path + ": compound path mismatch for " + pathSpec.id
                    + " (" + reason + ")"
                );
            }}
        }}
    }}

    function verifyContainer(container, items, path) {{
        var actual = directPageItems(container);
        if (actual.length !== items.length) {{
            errors.push(path + ": item count " + actual.length + " != " + items.length);
            return;
        }}
        for (var index = 0; index < items.length; index++) {{
            var itemSpec = items[items.length - index - 1];
            var item = actual[index];
            if (item.typename !== expectedType(itemSpec.kind)) {{
                errors.push(
                    path + ": typename mismatch for " + itemSpec.id
                    + " (actual " + item.typename + ", note " + item.note + ")"
                );
                continue;
            }}
            if (item.note !== itemSpec.note) {{
                errors.push(path + ": identity mismatch for " + itemSpec.id);
            }}
            if (item.name !== itemSpec.name) {{
                errors.push(path + ": name mismatch for " + itemSpec.id);
            }}
            if (itemSpec.kind === "path") {{
                var pathReason = pathMismatch(item, itemSpec);
                if (pathReason !== null) {{
                    errors.push(
                        path + ": path attributes mismatch for " + itemSpec.id
                        + " (" + pathReason + ")"
                    );
                }}
            }} else if (itemSpec.kind === "text" && !verifyText(item, itemSpec)) {{
                errors.push(path + ": text attributes mismatch for " + itemSpec.id);
            }} else if (itemSpec.kind === "image" && !verifyImage(item, itemSpec)) {{
                errors.push(path + ": linked image mismatch for " + itemSpec.id);
            }} else if (itemSpec.kind === "group") {{
                verifyContainer(item, itemSpec.items, path + "/" + itemSpec.id);
            }} else if (itemSpec.kind === "compound_path") {{
                verifyCompound(item, itemSpec, path);
            }} else if (itemSpec.kind === "clipping_group") {{
                var clippingItems = itemSpec.paths.slice(0);
                clippingItems.push(itemSpec.clipping_path);
                verifyContainer(item, clippingItems, path + "/" + itemSpec.id);
            }}
        }}
    }}

    function verifyDocument() {{
        if (documentRef.layers.length !== spec.layers.length) {{
            errors.push("layer count mismatch");
        }}
        for (var layerIndex = 0; layerIndex < spec.layers.length; layerIndex++) {{
            var layerSpec = spec.layers[layerIndex];
            var layer = documentRef.layers[layerIndex];
            if (layer.name !== layerSpec.name) errors.push("layer name mismatch: " + layerSpec.id);
            if (layer.visible !== layerSpec.visible) {{
                errors.push("layer visibility mismatch: " + layerSpec.id);
            }}
            if (layer.locked !== layerSpec.locked) {{
                errors.push("layer lock mismatch: " + layerSpec.id);
            }}
            verifyContainer(layer, layerSpec.items, "layer:" + layerSpec.id);
        }}
        if (documentRef.artboards.length !== spec.artboards.length) {{
            errors.push("artboard count mismatch");
        }}
        for (var artboardIndex = 0; artboardIndex < spec.artboards.length; artboardIndex++) {{
            var artboardSpec = spec.artboards[artboardIndex];
            var artboard = documentRef.artboards[artboardIndex];
            if (artboard.name !== artboardSpec.name) {{
                errors.push("artboard name mismatch: " + artboardSpec.id);
            }}
            var rect = artboard.artboardRect;
            for (var rectIndex = 0; rectIndex < 4; rectIndex++) {{
                if (!closeEnough(rect[rectIndex], artboardSpec.rect[rectIndex], 0.01)) {{
                    errors.push("artboard geometry mismatch: " + artboardSpec.id);
                    break;
                }}
            }}
        }}
        return {{
            structure_and_order: errors.length === 0,
            stable_identity: errors.length === 0,
            geometry_and_style: errors.length === 0,
            linked_resources: errors.length === 0,
            native_editability: documentRef.legacyTextItems.length === 0,
            pdf_compatible_ai: destination.exists
        }};
    }}

    try {{
        app.userInteractionLevel = UserInteractionLevel.DONTDISPLAYALERTS;
        app.coordinateSystem = CoordinateSystem.DOCUMENTCOORDINATESYSTEM;
        if (destination.exists) throw new Error("Temporary destination already exists");

        var colorSpace = spec.color_space === "cmyk"
            ? DocumentColorSpace.CMYK
            : DocumentColorSpace.RGB;
        documentRef = app.documents.add(colorSpace, spec.width, spec.height);

        while (documentRef.artboards.length > 1) {{
            documentRef.artboards.remove(documentRef.artboards.length - 1);
        }}
        for (var artboardIndex = 0; artboardIndex < spec.artboards.length; artboardIndex++) {{
            var artboardSpec = spec.artboards[artboardIndex];
            var artboard = artboardIndex === 0
                ? documentRef.artboards[0]
                : documentRef.artboards.add(artboardSpec.rect);
            artboard.artboardRect = artboardSpec.rect;
            artboard.name = artboardSpec.name;
        }}

        while (documentRef.layers.length > 1) documentRef.layers[0].remove();
        for (var layerIndex = spec.layers.length - 1; layerIndex >= 0; layerIndex--) {{
            var layerSpec = spec.layers[layerIndex];
            var layer = layerIndex === spec.layers.length - 1
                ? documentRef.layers[0]
                : documentRef.layers.add();
            layer.name = layerSpec.name;
            createItems(layer, layerSpec.items);
            layer.visible = layerSpec.visible;
            layer.locked = layerSpec.locked;
        }}

        var options = new IllustratorSaveOptions();
        options.pdfCompatible = spec.pdf_compatible;
        options.embedLinkedFiles = spec.embed_linked_files;
        options.flattenOutput = OutputFlattening.PRESERVEAPPEARANCE;
        documentRef.saveAs(destination, options);
        documentRef.close(SaveOptions.DONOTSAVECHANGES);
        documentRef = null;

        documentRef = app.open(destination);
        var checks = verifyDocument();
        var passed = true;
        for (var key in checks) {{
            if (checks.hasOwnProperty(key) && !checks[key]) passed = false;
        }}
        return toJson({{
            ok: passed,
            illustrator_version: app.version,
            checks: checks,
            errors: errors,
            counts: {{
                layers: documentRef.layers.length,
                artboards: documentRef.artboards.length,
                groups: documentRef.groupItems.length,
                paths: documentRef.pathItems.length,
                texts: documentRef.textFrames.length,
                linked_images: documentRef.placedItems.length,
                legacy_texts: documentRef.legacyTextItems.length
            }}
        }});
    }} catch (error) {{
        return toJson({{
            ok: false,
            error: String(error),
            line: error.line || null,
            errors: errors
        }});
    }} finally {{
        if (documentRef !== null) documentRef.close(SaveOptions.DONOTSAVECHANGES);
        app.coordinateSystem = previousCoordinateSystem;
        app.userInteractionLevel = previousInteractionLevel;
    }}
}}());
"""


def _load_document(source: Document | str | Path) -> tuple[Document, Path | None]:
    if isinstance(source, Document):
        return source, None
    path = Path(source).resolve()
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Document IR JSON must contain an object")
    return Document.from_dict(data), path.parent


def _result_with_links(
    result: dict[str, Any],
    packaged_links: list[PackagedLink],
) -> dict[str, Any]:
    result["packaged_links"] = [link.to_dict() for link in packaged_links]
    return result


def compile_native_ai(
    source: Document | str | Path,
    destination: str | Path,
    *,
    source_base: str | Path | None = None,
    profile: NativeCompileProfile | None = None,
    timeout: float = 120.0,
    application_name: str = "Adobe Illustrator",
) -> dict[str, Any]:
    """Compile a ``Document`` IR directly to a verified native Illustrator file."""

    destination_path = Path(destination).resolve()
    if platform.system() != "Darwin":
        return {
            "status": "environment-unavailable",
            "error": "Direct native compile is currently supported on macOS only.",
        }
    if destination_path.exists():
        return {
            "status": "invalid-input",
            "error": f"Refusing to overwrite existing output: {destination_path}",
        }
    if timeout <= 0:
        return {"status": "invalid-input", "error": "timeout must be positive"}

    compile_profile = profile or NativeCompileProfile()
    if destination_path.suffix.lower() != ".ai":
        return {"status": "invalid-input", "error": "Native output must use the .ai suffix"}
    try:
        document, inferred_source_base = _load_document(source)
        _validate_document(document)
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as error:
        return {"status": "invalid-input", "error": str(error)}
    effective_source_base = source_base if source_base is not None else inferred_source_base

    destination_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        packaged_document, packaged_links = package_linked_images(
            document,
            destination_path.parent,
            source_base=effective_source_base,
        )
    except ValueError as error:
        return {"status": "invalid-input", "error": str(error)}

    spec = _document_spec(packaged_document, destination_path.parent, compile_profile)
    with tempfile.TemporaryDirectory(
        prefix="py-ai-direct-native-",
        dir=destination_path.parent,
    ) as temp_directory:
        temp_path = Path(temp_directory)
        temporary_output = temp_path / destination_path.name
        javascript = _build_direct_native_javascript(spec, temporary_output)
        try:
            completed = _execute_javascript(
                javascript,
                temp_path,
                timeout=timeout,
                application_name=application_name,
            )
        except subprocess.TimeoutExpired:
            return _result_with_links(
                {
                    "status": "environment-unavailable",
                    "error": f"Illustrator did not answer within {timeout:g} seconds.",
                },
                packaged_links,
            )

        if completed.returncode != 0:
            return _result_with_links(
                {
                    "status": "environment-unavailable",
                    "error": completed.stderr.strip() or "Illustrator AppleScript failed.",
                },
                packaged_links,
            )
        try:
            illustrator_result = json.loads(completed.stdout.strip())
        except json.JSONDecodeError:
            return _result_with_links(
                {
                    "status": "failed",
                    "error": "Illustrator returned a non-JSON response.",
                    "illustrator_response": completed.stdout.strip(),
                },
                packaged_links,
            )
        if not illustrator_result.get("ok"):
            return _result_with_links(
                {
                    "status": "mismatch" if illustrator_result.get("checks") else "failed",
                    "illustrator": illustrator_result,
                },
                packaged_links,
            )
        if not temporary_output.is_file():
            return _result_with_links(
                {
                    "status": "failed",
                    "error": "Illustrator reported success but did not create the native AI file.",
                    "illustrator": illustrator_result,
                },
                packaged_links,
            )
        format_report = inspect_file(temporary_output)
        if format_report.format is not FileFormat.PDF_COMPATIBLE_AI:
            return _result_with_links(
                {
                    "status": "mismatch",
                    "error": "Direct compile output is not a PDF-compatible Illustrator file.",
                    "format": format_report.to_dict(),
                    "illustrator": illustrator_result,
                },
                packaged_links,
            )
        try:
            os.link(temporary_output, destination_path)
        except FileExistsError:
            return _result_with_links(
                {
                    "status": "invalid-input",
                    "error": f"Refusing to overwrite existing output: {destination_path}",
                },
                packaged_links,
            )
        temporary_output.unlink()

    return _result_with_links(
        {
            "status": "passed",
            "output": str(destination_path),
            "profile": {
                "color_space": compile_profile.color_space,
                "pdf_compatible": compile_profile.pdf_compatible,
                "embed_linked_files": compile_profile.embed_linked_files,
            },
            "illustrator": illustrator_result,
            "format": inspect_file(destination_path).to_dict(),
        },
        packaged_links,
    )
