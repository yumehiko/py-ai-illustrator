"""Pure JavaScript builders used by Illustrator adapters."""

# Generated ExtendScript is kept readable for the target runtime; its source
# lines are intentionally not constrained by the Python project's line length.
# ruff: noqa: E501

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_AREA_TEXT_OVERFLOW_JAVASCRIPT = """    function areaTextOverflows(frame) {
        if (
            !frame
            || frame.typename !== "TextFrame"
            || frame.kind !== TextType.AREATEXT
        ) return null;
        try {
            var story = frame.story;
            var frameRange = frame.textRange;
            if (!story || !frameRange) return null;
            var storyRange = story.textRange;
            if (!storyRange) return null;
            var frameStart = frameRange.start;
            var frameEnd = frameRange.end;
            var storyStart = storyRange.start;
            var storyEnd = storyRange.end;
            var storyLength = storyRange.length;
            if (
                typeof frameStart !== "number"
                || typeof frameEnd !== "number"
                || typeof storyStart !== "number"
                || typeof storyEnd !== "number"
                || typeof storyLength !== "number"
                || storyLength < 0
                || frameStart !== storyStart
                || frameEnd !== storyEnd
                || storyEnd < storyStart
            ) return null;
            if (storyLength === 0) return false;
            var lines = frame.lines;
            if (!lines || typeof lines.length !== "number") return null;
            if (lines.length === 0) return true;
            var visibleStart = lines[0].start;
            var visibleEnd = lines[lines.length - 1].end;
            if (
                typeof visibleStart !== "number"
                || typeof visibleEnd !== "number"
                || visibleStart !== storyStart
                || visibleEnd < visibleStart
                || visibleEnd > storyEnd
            ) return null;
            return visibleEnd < storyEnd;
        } catch (overflowError) {
            return null;
        }
    }
"""


def character_code_expression(value: str | Path) -> str:
    codepoints = ",".join(str(ord(character)) for character in str(value))
    return f"String.fromCharCode({codepoints})"


def text_identity_note(text: Any) -> str:
    return "py-ai-text:" + json.dumps(
        {"id": text.id, "name": text.name},
        ensure_ascii=False,
        separators=(",", ":"),
    )


def native_fill_spec(color: Any) -> dict[str, Any]:
    if hasattr(color, "cyan"):
        return {
            "type": "cmyk",
            "values": [color.cyan, color.magenta, color.yellow, color.black],
        }
    return {"type": "rgb", "values": [color.red, color.green, color.blue]}


def build_javascript(source: Path) -> str:
    """Build the read-only DOM inspection script."""

    source_literal = character_code_expression(source)
    overflow_javascript = _AREA_TEXT_OVERFLOW_JAVASCRIPT
    return f"""#target illustrator
(function () {{
    var source = new File({source_literal});
    var documentRef = null;
    var previousInteractionLevel = app.userInteractionLevel;

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
        if (value === null) return "null";
        if (typeof value === "undefined") return "null";
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

    function colorToObject(color) {{
        if (!color) return null;
        var value = {{type: color.typename}};
        if (color.typename === "RGBColor") {{
            value.red = color.red;
            value.green = color.green;
            value.blue = color.blue;
        }} else if (color.typename === "CMYKColor") {{
            value.cyan = color.cyan;
            value.magenta = color.magenta;
            value.yellow = color.yellow;
            value.black = color.black;
        }} else if (color.typename === "GrayColor") {{
            value.gray = color.gray;
        }}
        return value;
    }}

    function itemRotation(item) {{
        if (!item.matrix) return null;
        var matrix = item.matrix;
        return Math.atan2(matrix.mValueB, matrix.mValueA) * 180 / Math.PI;
    }}

{overflow_javascript}

    function textFrameFingerprint(frame) {{
        try {{
            var textRange = frame.textRange;
            var attributes = textRange ? textRange.characterAttributes : null;
            var paragraphAttributes = textRange ? textRange.paragraphAttributes : null;
            var matrix = frame.matrix;
            return toJson({{
                contents: frame.contents,
                story_contents: frame.story ? frame.story.textRange.contents : null,
                frame_range: textRange ? [textRange.start, textRange.end] : null,
                story_range: frame.story
                    ? [frame.story.textRange.start, frame.story.textRange.end] : null,
                kind: String(frame.kind),
                position: [frame.position[0], frame.position[1]],
                width: typeof frame.width === "number" ? frame.width : null,
                height: typeof frame.height === "number" ? frame.height : null,
                matrix: matrix ? [matrix.mValueA, matrix.mValueB, matrix.mValueC,
                    matrix.mValueD, matrix.mValueTX, matrix.mValueTY] : null,
                font_size: attributes && typeof attributes.size === "number"
                    ? attributes.size : null,
                font_name: attributes && attributes.textFont ? attributes.textFont.name : null,
                tracking: attributes && typeof attributes.tracking === "number"
                    ? attributes.tracking : null,
                leading: attributes && typeof attributes.leading === "number"
                    ? attributes.leading : null,
                auto_leading: attributes && typeof attributes.autoLeading === "boolean"
                    ? attributes.autoLeading : null,
                fill_color: attributes ? colorToObject(attributes.fillColor) : null,
                justification: paragraphAttributes
                    ? String(paragraphAttributes.justification) : null
            }});
        }} catch (fingerprintError) {{
            return null;
        }}
    }}

    try {{
        app.userInteractionLevel = UserInteractionLevel.DONTDISPLAYALERTS;
        if (!source.exists) throw new Error("Temporary fixture does not exist");
        documentRef = app.open(source);

        var layers = [];
        for (var layerIndex = 0; layerIndex < documentRef.layers.length; layerIndex++) {{
            var layer = documentRef.layers[layerIndex];
            var pageItemTypes = [];
            for (var pageItemIndex = 0; pageItemIndex < layer.pageItems.length; pageItemIndex++) {{
                var layerItem = layer.pageItems[pageItemIndex];
                if (layerItem.parent === layer) pageItemTypes.push(layerItem.typename);
            }}
            layers.push({{
                name: layer.name,
                visible: layer.visible,
                locked: layer.locked,
                page_item_count: layer.pageItems.length,
                page_item_types: pageItemTypes
            }});
        }}

        var paths = [];
        for (var pathIndex = 0; pathIndex < documentRef.pathItems.length; pathIndex++) {{
            var path = documentRef.pathItems[pathIndex];
            var anchors = [];
            var strokeDashes = [];
            for (var dashIndex = 0; dashIndex < path.strokeDashes.length; dashIndex++) {{
                strokeDashes.push(path.strokeDashes[dashIndex]);
            }}
            for (var pointIndex = 0; pointIndex < path.pathPoints.length; pointIndex++) {{
                var point = path.pathPoints[pointIndex];
                anchors.push({{
                    anchor: [point.anchor[0], point.anchor[1]],
                    left_direction: [point.leftDirection[0], point.leftDirection[1]],
                    right_direction: [point.rightDirection[0], point.rightDirection[1]],
                    point_type: String(point.pointType)
                }});
            }}
            paths.push({{
                name: path.name,
                note: path.note,
                closed: path.closed,
                filled: path.filled,
                stroked: path.stroked,
                stroke_width: path.strokeWidth,
                dash_pattern: strokeDashes,
                dash_offset: path.strokeDashOffset,
                line_cap: String(path.strokeCap),
                line_join: String(path.strokeJoin),
                miter_limit: path.strokeMiterLimit,
                fill_color: path.filled ? colorToObject(path.fillColor) : null,
                stroke_color: path.stroked ? colorToObject(path.strokeColor) : null,
                anchors: anchors
            }});
        }}

        var artboards = [];
        for (var artboardIndex = 0; artboardIndex < documentRef.artboards.length; artboardIndex++) {{
            var artboard = documentRef.artboards[artboardIndex];
            artboards.push({{
                name: artboard.name,
                rect: [artboard.artboardRect[0], artboard.artboardRect[1],
                    artboard.artboardRect[2], artboard.artboardRect[3]]
            }});
        }}

        var placedImages = [];
        for (var placedIndex = 0; placedIndex < documentRef.placedItems.length; placedIndex++) {{
            var placed = documentRef.placedItems[placedIndex];
            var placedFile = null;
            var placedFileExists = false;
            try {{
                placedFile = placed.file.fsName;
                placedFileExists = placed.file.exists;
            }} catch (placedFileError) {{
                placedFile = null;
            }}
            placedImages.push({{
                name: placed.name,
                note: placed.note,
                file: placedFile,
                file_exists: placedFileExists,
                position: [placed.position[0], placed.position[1]],
                width: placed.width,
                height: placed.height,
                rotation: itemRotation(placed)
            }});
        }}

        var textFrames = [];
        function collectTextFrames(container) {{
            for (var textFrameIndex = 0; textFrameIndex < container.pageItems.length; textFrameIndex++) {{
                var textFrame = container.pageItems[textFrameIndex];
                if (textFrame.parent !== container) continue;
                if (textFrame.typename === "GroupItem") {{
                    collectTextFrames(textFrame);
                    continue;
                }}
                if (textFrame.typename !== "TextFrame" && textFrame.typename !== "LegacyTextItem") continue;
                var textRange = textFrame.textRange;
                var attributes = textRange ? textRange.characterAttributes : null;
                var paragraphAttributes = textRange ? textRange.paragraphAttributes : null;
                var fingerprintBefore = textFrameFingerprint(textFrame);
                var overflow = areaTextOverflows(textFrame);
                var fingerprintAfter = textFrameFingerprint(textFrame);
                textFrames.push({{
                    kind: textFrame.typename === "TextFrame" ? String(textFrame.kind) : "LegacyTextItem",
                    name: textFrame.name,
                    note: textFrame.note,
                    contents: textFrame.contents,
                    story_contents: textFrame.typename === "TextFrame" && textFrame.story
                        ? textFrame.story.textRange.contents : null,
                    position: [textFrame.position[0], textFrame.position[1]],
                    font_size: attributes && typeof attributes.size === "number" ? attributes.size : null,
                    font_name: attributes && attributes.textFont ? attributes.textFont.name : null,
                    tracking: attributes && typeof attributes.tracking === "number" ? attributes.tracking : null,
                    leading: attributes && typeof attributes.leading === "number" ? attributes.leading : null,
                    rotation: itemRotation(textFrame),
                    width: typeof textFrame.width === "number" ? textFrame.width : null,
                    height: typeof textFrame.height === "number" ? textFrame.height : null,
                    overflows: overflow,
                    overflow_inspection_preserved: fingerprintBefore !== null
                        && fingerprintBefore === fingerprintAfter,
                    fill_color: attributes ? colorToObject(attributes.fillColor) : null,
                    justification: paragraphAttributes ? String(paragraphAttributes.justification) : null
                }});
            }}
        }}
        for (var textLayerIndex = 0; textLayerIndex < documentRef.layers.length; textLayerIndex++) {{
            collectTextFrames(documentRef.layers[textLayerIndex]);
        }}

        var layerNames = [];
        var layerPageItemTypes = [];
        for (var layerNameIndex = 0; layerNameIndex < layers.length; layerNameIndex++) {{
            layerNames.push(layers[layerNameIndex].name);
            layerPageItemTypes.push(layers[layerNameIndex].page_item_types);
        }}
        var pointCounts = [];
        var closedCount = 0;
        var filledCount = 0;
        var strokedCount = 0;
        for (var pathCountIndex = 0; pathCountIndex < paths.length; pathCountIndex++) {{
            pointCounts.push(paths[pathCountIndex].anchors.length);
            if (paths[pathCountIndex].closed) closedCount++;
            if (paths[pathCountIndex].filled) filledCount++;
            if (paths[pathCountIndex].stroked) strokedCount++;
        }}
        pointCounts.sort(function (left, right) {{ return left - right; }});
        var clippingGroupCount = 0;
        for (var groupIndex = 0; groupIndex < documentRef.groupItems.length; groupIndex++) {{
            if (documentRef.groupItems[groupIndex].clipped) clippingGroupCount++;
        }}

        return toJson({{
            ok: true,
            illustrator_version: app.version,
            document_name: documentRef.name,
            document_color_space: String(documentRef.documentColorSpace),
            layer_count: documentRef.layers.length,
            path_item_count: documentRef.pathItems.length,
            text_frame_count: textFrames.length,
            placed_item_count: placedImages.length,
            page_item_count: documentRef.pageItems.length,
            artboard_count: documentRef.artboards.length,
            compound_path_item_count: documentRef.compoundPathItems.length,
            clipping_group_count: clippingGroupCount,
            group_item_count: documentRef.groupItems.length - clippingGroupCount,
            layer_names: layerNames,
            layer_page_item_types: layerPageItemTypes,
            point_counts: pointCounts,
            closed_count: closedCount,
            filled_count: filledCount,
            stroked_count: strokedCount,
            layers: layers,
            paths: paths,
            text_frames: textFrames,
            placed_images: placedImages,
            artboards: artboards
        }});
    }} catch (error) {{
        return toJson({{ok: false, error: String(error), line: error.line || null}});
    }} finally {{
        if (documentRef !== null) documentRef.close(SaveOptions.DONOTSAVECHANGES);
        app.userInteractionLevel = previousInteractionLevel;
    }}
}}());
"""


def build_export_javascript(destination: Path, fixture: str) -> str:
    destination_literal = character_code_expression(destination)
    fixtures = {
        "rgb-rectangle": """
        documentRef = app.documents.add(DocumentColorSpace.RGB, 320, 240);
        var layer = documentRef.layers[0];
        layer.name = "Illustrator Native";
        var path = layer.pathItems.add();
        path.name = "Native Rectangle";
        path.setEntirePath([[40, 40], [280, 40], [280, 200], [40, 200]]);
        path.closed = true; path.filled = true; path.stroked = true; path.strokeWidth = 3;
        var fill = new RGBColor(); fill.red = 255; fill.green = 77; fill.blue = 0; path.fillColor = fill;
        var stroke = new RGBColor(); stroke.red = 38; stroke.green = 26; stroke.blue = 13; path.strokeColor = stroke;
""",
        "cmyk-curve": """
        documentRef = app.documents.add(DocumentColorSpace.CMYK, 200, 200);
        var layer = documentRef.layers[0]; layer.name = "Illustrator Native Curves";
        var path = layer.pathItems.add(); path.name = "Native CMYK Curve";
        var first = path.pathPoints.add(); first.anchor = [20, 20]; first.leftDirection = [20, 20];
        first.rightDirection = [20, 150]; first.pointType = PointType.SMOOTH;
        var second = path.pathPoints.add(); second.anchor = [180, 180]; second.leftDirection = [180, 50];
        second.rightDirection = [180, 180]; second.pointType = PointType.SMOOTH;
        path.closed = false; path.filled = false; path.stroked = true; path.strokeWidth = 4;
        var stroke = new CMYKColor(); stroke.cyan = 100; stroke.magenta = 25; stroke.yellow = 0;
        stroke.black = 10; path.strokeColor = stroke;
""",
        "stroke-style": """
        documentRef = app.documents.add(DocumentColorSpace.RGB, 300, 200);
        var layer = documentRef.layers[0]; layer.name = "Illustrator Native Stroke Style";
        var path = layer.pathItems.add(); path.name = "Native Dashed Route";
        path.setEntirePath([[30, 40], [150, 160], [270, 40]]); path.closed = false; path.filled = false;
        path.stroked = true; path.strokeWidth = 6; path.strokeDashes = [18, 8, 4, 8];
        path.strokeDashOffset = 3; path.strokeCap = StrokeCap.ROUNDENDCAP;
        path.strokeJoin = StrokeJoin.BEVELENDJOIN; path.strokeMiterLimit = 7;
        var stroke = new RGBColor(); stroke.red = 38; stroke.green = 102; stroke.blue = 204; path.strokeColor = stroke;
""",
        "compound-path": """
        documentRef = app.documents.add(DocumentColorSpace.RGB, 300, 300);
        var layer = documentRef.layers[0]; layer.name = "Illustrator Native Compound";
        var compound = layer.compoundPathItems.add(); compound.name = "Native Compound Path";
        var outer = compound.pathItems.add(); outer.setEntirePath([[20, 20], [280, 20], [280, 280], [20, 280]]); outer.closed = true;
        var inner = compound.pathItems.add(); inner.setEntirePath([[90, 90], [90, 210], [210, 210], [210, 90]]); inner.closed = true;
        var fill = new RGBColor(); fill.red = 64; fill.green = 128; fill.blue = 255;
        outer.filled = true; outer.stroked = false; outer.fillColor = fill;
""",
        "clipping-group": """
        documentRef = app.documents.add(DocumentColorSpace.RGB, 300, 300);
        var layer = documentRef.layers[0]; layer.name = "Illustrator Native Clipping";
        var group = layer.groupItems.add(); group.name = "Native Clipping Group";
        var content = group.pathItems.add(); content.setEntirePath([[20, 20], [280, 20], [280, 280], [20, 280]]);
        content.closed = true; content.filled = true; content.stroked = false;
        var contentFill = new RGBColor(); contentFill.red = 255; contentFill.green = 64; contentFill.blue = 128; content.fillColor = contentFill;
        var clip = group.pathItems.add(); clip.setEntirePath([[80, 80], [220, 80], [220, 220], [80, 220]]);
        clip.closed = true; clip.filled = false; clip.stroked = false; clip.clipping = true; group.clipped = true;
""",
        "group": """
        documentRef = app.documents.add(DocumentColorSpace.RGB, 300, 200);
        var layer = documentRef.layers[0]; layer.name = "Illustrator Native Group";
        var group = layer.groupItems.add(); group.name = "Native Product Card";
        var background = group.pathItems.add(); background.name = "Card Background";
        background.setEntirePath([[20, 20], [280, 20], [280, 180], [20, 180]]); background.closed = true;
        background.filled = true; background.stroked = false;
        var backgroundFill = new RGBColor(); backgroundFill.red = 242; backgroundFill.green = 245; backgroundFill.blue = 250; background.fillColor = backgroundFill;
        var accent = group.pathItems.add(); accent.name = "Card Accent";
        accent.setEntirePath([[20, 160], [280, 160], [280, 180], [20, 180]]); accent.closed = true;
        accent.filled = true; accent.stroked = false;
        var accentFill = new RGBColor(); accentFill.red = 38; accentFill.green = 102; accentFill.blue = 204; accent.fillColor = accentFill;
""",
        "point-text": """
        documentRef = app.documents.add(DocumentColorSpace.RGB, 320, 240);
        var layer = documentRef.layers[0]; layer.name = "Illustrator Native Text";
        var text = layer.textFrames.add(); text.name = "Native Table Header"; text.contents = "Table Header";
        text.position = [40, 180]; text.textRange.characterAttributes.size = 14;
        text.textRange.paragraphAttributes.justification = Justification.CENTER;
        var textFill = new RGBColor(); textFill.red = 26; textFill.green = 51; textFill.blue = 77; text.textRange.characterAttributes.fillColor = textFill;
""",
        "unicode-text": """
        documentRef = app.documents.add(DocumentColorSpace.RGB, 420, 240);
        var layer = documentRef.layers[0]; layer.name = "Illustrator Native Unicode";
        var text = layer.textFrames.add(); text.name = "Native Japanese Table Header";
        text.contents = String.fromCharCode(26085, 26412, 35486, 12398, 34920, 35211, 20986, 12375);
        text.position = [40, 180]; text.textRange.characterAttributes.size = 16;
        var textFill = new RGBColor(); textFill.red = 26; textFill.green = 51; textFill.blue = 77; text.textRange.characterAttributes.fillColor = textFill;
""",
    }
    if fixture not in fixtures:
        raise ValueError(f"Unknown Illustrator fixture: {fixture}")
    return f"""#target illustrator
(function () {{
    var destination = new File({destination_literal});
    var documentRef = null;
    var previousInteractionLevel = app.userInteractionLevel;
    try {{
        app.userInteractionLevel = UserInteractionLevel.DONTDISPLAYALERTS;
{fixtures[fixture]}
        var options = new IllustratorSaveOptions();
        options.compatibility = Compatibility.ILLUSTRATOR8;
        options.pdfCompatible = false;
        options.embedLinkedFiles = false;
        options.flattenOutput = OutputFlattening.PRESERVEAPPEARANCE;
        documentRef.saveAs(destination, options);
        return "ok:" + app.version;
    }} catch (error) {{
        return "error:" + String(error) + ":line:" + String(error.line || "");
    }} finally {{
        if (documentRef !== null) documentRef.close(SaveOptions.DONOTSAVECHANGES);
        app.userInteractionLevel = previousInteractionLevel;
    }}
}}());
"""


def build_roundtrip_javascript(source: Path, destination: Path) -> str:
    return _build_save_javascript(source, destination, compatibility=True)


def build_modern_roundtrip_javascript(source: Path, destination: Path) -> str:
    return _build_save_javascript(source, destination, compatibility=False)


def _native_local_dom_helpers() -> str:
    """Shared deterministic DOM snapshot helpers for native local editing."""

    return r'''
    function quoteString(value) {
        var slash = String.fromCharCode(92);
        var quote = String.fromCharCode(34);
        var carriageReturn = String.fromCharCode(13);
        var lineFeed = String.fromCharCode(10);
        return quote + String(value)
            .split(slash).join(slash + slash)
            .split(quote).join(slash + quote)
            .split(carriageReturn).join(slash + "r")
            .split(lineFeed).join(slash + "n") + quote;
    }

    function toJson(value) {
        if (value === null || typeof value === "undefined") return "null";
        if (typeof value === "string") return quoteString(value);
        if (typeof value === "number" || typeof value === "boolean") return String(value);
        var parts = [];
        var index;
        if (value instanceof Array) {
            for (index = 0; index < value.length; index++) parts.push(toJson(value[index]));
            return "[" + parts.join(",") + "]";
        }
        for (var key in value) {
            if (value.hasOwnProperty(key)) parts.push(quoteString(key) + ":" + toJson(value[key]));
        }
        return "{" + parts.join(",") + "}";
    }

    function colorToObject(color) {
        if (!color) return null;
        var value = {type: color.typename};
        if (color.typename === "RGBColor") {
            value.red = color.red; value.green = color.green; value.blue = color.blue;
        } else if (color.typename === "CMYKColor") {
            value.cyan = color.cyan; value.magenta = color.magenta;
            value.yellow = color.yellow; value.black = color.black;
        } else if (color.typename === "GrayColor") {
            value.gray = color.gray;
        }
        return value;
    }

    function arrayValue(value) {
        if (!value || typeof value.length !== "number") return null;
        var result = [];
        for (var index = 0; index < value.length; index++) result.push(value[index]);
        return result;
    }

    function itemMatrix(item) {
        try {
            var matrix = item.matrix;
            return [matrix.mValueA, matrix.mValueB, matrix.mValueC,
                matrix.mValueD, matrix.mValueTX, matrix.mValueTY];
        } catch (error) { return null; }
    }

    function itemParent(item) {
        try {
            return {type: item.parent.typename, name: item.parent.name || ""};
        } catch (error) { return null; }
    }

    function textStyle(frame) {
        var range = frame.textRange;
        var attributes = range ? range.characterAttributes : null;
        var paragraph = range ? range.paragraphAttributes : null;
        return {
            font_name: attributes && attributes.textFont ? attributes.textFont.name : null,
            font_size: attributes && typeof attributes.size === "number" ? attributes.size : null,
            tracking: attributes && typeof attributes.tracking === "number" ? attributes.tracking : null,
            leading: attributes && typeof attributes.leading === "number" ? attributes.leading : null,
            auto_leading: attributes && typeof attributes.autoLeading === "boolean"
                ? attributes.autoLeading : null,
            fill_color: attributes ? colorToObject(attributes.fillColor) : null,
            justification: paragraph ? String(paragraph.justification) : null
        };
    }

    function textSnapshot(frame, index) {
        return {
            type: "text", id: "illustrator-dom-text-" + index, dom_index: index,
            name: frame.name || "", note: frame.note || "", contents: frame.contents,
            kind: String(frame.kind), parent: itemParent(frame),
            position: arrayValue(frame.position), geometric_bounds: arrayValue(frame.geometricBounds),
            visible_bounds: arrayValue(frame.visibleBounds), width: frame.width, height: frame.height,
            matrix: itemMatrix(frame), style: textStyle(frame)
        };
    }

    function imageSnapshot(item, index) {
        var source = null; var sourceExists = false;
        try { source = item.file.fsName; sourceExists = item.file.exists; } catch (error) {}
        var parent = itemParent(item);
        var clipped = false;
        try { clipped = item.parent.typename === "GroupItem" && item.parent.clipped; } catch (error) {}
        return {
            type: "linked_image", id: "illustrator-dom-linked-image-" + index,
            dom_index: index, name: item.name || "", note: item.note || "",
            source: source, source_exists: sourceExists, parent: parent, clipped: clipped,
            position: arrayValue(item.position), geometric_bounds: arrayValue(item.geometricBounds),
            visible_bounds: arrayValue(item.visibleBounds), width: item.width, height: item.height,
            matrix: itemMatrix(item)
        };
    }

    function pathSnapshot(item, index) {
        var anchors = [];
        for (var pointIndex = 0; pointIndex < item.pathPoints.length; pointIndex++) {
            var point = item.pathPoints[pointIndex];
            anchors.push({anchor: arrayValue(point.anchor), left: arrayValue(point.leftDirection),
                right: arrayValue(point.rightDirection), point_type: String(point.pointType)});
        }
        return {id: "illustrator-dom-path-" + index, dom_index: index,
            name: item.name || "", note: item.note || "", parent: itemParent(item),
            closed: item.closed, clipping: item.clipping, filled: item.filled, stroked: item.stroked,
            fill_color: item.filled ? colorToObject(item.fillColor) : null,
            stroke_color: item.stroked ? colorToObject(item.strokeColor) : null,
            stroke_width: item.strokeWidth, position: arrayValue(item.position),
            geometric_bounds: arrayValue(item.geometricBounds), visible_bounds: arrayValue(item.visibleBounds),
            matrix: itemMatrix(item), anchors: anchors};
    }

    function structureSnapshot(documentRef) {
        var layers = [];
        for (var layerIndex = 0; layerIndex < documentRef.layers.length; layerIndex++) {
            var layer = documentRef.layers[layerIndex]; var types = [];
            for (var itemIndex = 0; itemIndex < layer.pageItems.length; itemIndex++) {
                var item = layer.pageItems[itemIndex];
                if (item.parent === layer) types.push(item.typename);
            }
            layers.push({name: layer.name, visible: layer.visible, locked: layer.locked,
                page_item_types: types, page_item_count: layer.pageItems.length});
        }
        var artboards = [];
        for (var artboardIndex = 0; artboardIndex < documentRef.artboards.length; artboardIndex++) {
            artboards.push({name: documentRef.artboards[artboardIndex].name,
                rect: arrayValue(documentRef.artboards[artboardIndex].artboardRect)});
        }
        return {layer_count: documentRef.layers.length, page_item_count: documentRef.pageItems.length,
            path_item_count: documentRef.pathItems.length, group_item_count: documentRef.groupItems.length,
            compound_path_item_count: documentRef.compoundPathItems.length,
            text_frame_count: documentRef.textFrames.length,
            placed_item_count: documentRef.placedItems.length, artboards: artboards, layers: layers};
    }

    function documentSnapshot(documentRef) {
        var texts = []; var images = []; var paths = [];
        for (var textIndex = 0; textIndex < documentRef.textFrames.length; textIndex++)
            texts.push(textSnapshot(documentRef.textFrames[textIndex], textIndex));
        for (var imageIndex = 0; imageIndex < documentRef.placedItems.length; imageIndex++)
            images.push(imageSnapshot(documentRef.placedItems[imageIndex], imageIndex));
        for (var pathIndex = 0; pathIndex < documentRef.pathItems.length; pathIndex++)
            paths.push(pathSnapshot(documentRef.pathItems[pathIndex], pathIndex));
        return {structure: structureSnapshot(documentRef), texts: texts,
            linked_images: images, paths: paths};
    }

    function reportSnapshot(snapshot) {
        return {structure: snapshot.structure, texts: snapshot.texts,
            linked_images: snapshot.linked_images, verified_path_count: snapshot.paths.length};
    }

    function closeNumber(left, right) {
        return typeof left === "number" && typeof right === "number" && Math.abs(left - right) < 0.02;
    }

    function closeArray(left, right) {
        if (left === null || right === null || left.length !== right.length) return left === right;
        for (var index = 0; index < left.length; index++) if (!closeNumber(left[index], right[index])) return false;
        return true;
    }

    function textPrecondition(actual, expected) {
        return actual.id === expected.id && actual.contents === expected.contents
            && actual.name === expected.name && actual.note === expected.note
            && actual.kind === expected.kind && toJson(actual.parent) === toJson(expected.parent)
            && closeArray(actual.position, expected.position)
            && closeArray(actual.geometric_bounds, expected.geometric_bounds)
            && closeArray(actual.matrix, expected.matrix)
            && toJson(actual.style) === toJson(expected.style);
    }

    function imagePrecondition(actual, expected) {
        return actual.id === expected.id && actual.name === expected.name && actual.note === expected.note
            && toJson(actual.parent) === toJson(expected.parent) && actual.clipped === expected.clipped
            && closeArray(actual.position, expected.position)
            && closeArray(actual.geometric_bounds, expected.geometric_bounds)
            && closeArray(actual.visible_bounds, expected.visible_bounds)
            && closeNumber(actual.width, expected.width) && closeNumber(actual.height, expected.height)
            && closeArray(actual.matrix, expected.matrix);
    }

    function imageGeometryPreserved(actual, expected) {
        var actualLinear = actual.matrix === null ? null : actual.matrix.slice(0, 4);
        var expectedLinear = expected.matrix === null ? null : expected.matrix.slice(0, 4);
        return actual.name === expected.name && actual.note === expected.note
            && toJson(actual.parent) === toJson(expected.parent) && actual.clipped === expected.clipped
            && closeArray(actual.position, expected.position)
            && closeArray(actual.geometric_bounds, expected.geometric_bounds)
            && closeArray(actual.visible_bounds, expected.visible_bounds)
            && closeNumber(actual.width, expected.width) && closeNumber(actual.height, expected.height)
            && closeArray(actualLinear, expectedLinear);
    }
'''


def build_native_local_inspection_javascript(source: Path) -> str:
    """Build a licensed-runtime selector inventory for one existing modern AI."""

    source_literal = character_code_expression(source)
    helpers = _native_local_dom_helpers()
    return f'''#target illustrator
(function () {{
    var source = new File({source_literal});
    var documentRef = null; var previousInteractionLevel = app.userInteractionLevel;
{helpers}
    try {{
        app.userInteractionLevel = UserInteractionLevel.DONTDISPLAYALERTS;
        if (!source.exists) throw new Error("Temporary fixture does not exist");
        documentRef = app.open(source);
        var snapshot = documentSnapshot(documentRef);
        return toJson({{ok: true, illustrator_version: app.version,
            document_name: documentRef.name, snapshot: reportSnapshot(snapshot)}});
    }} catch (error) {{
        return toJson({{ok: false, error: String(error), line: error.line || null}});
    }} finally {{
        if (documentRef !== null) documentRef.close(SaveOptions.DONOTSAVECHANGES);
        app.userInteractionLevel = previousInteractionLevel;
    }}
}}());
'''


def build_native_local_apply_javascript(
    source: Path,
    destination: Path,
    request: dict[str, object],
) -> str:
    """Build one atomic DOM edit/save/reopen transaction for a modern AI copy."""

    source_literal = character_code_expression(source)
    destination_literal = character_code_expression(destination)
    request_literal = character_code_expression(
        json.dumps(request, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    )
    helpers = _native_local_dom_helpers()
    return f'''#target illustrator
(function () {{
    var source = new File({source_literal}); var destination = new File({destination_literal});
    var request = eval("(" + {request_literal} + ")");
    var documentRef = null; var previousInteractionLevel = app.userInteractionLevel;
{helpers}
    function isTarget(type, index) {{
        for (var i = 0; i < request.operations.length; i++)
            if (request.operations[i].type === type && request.operations[i].dom_index === index) return true;
        return false;
    }}
    function nonTargetsEqual(before, after) {{
        if (before.texts.length !== after.texts.length
            || before.linked_images.length !== after.linked_images.length
            || before.paths.length !== after.paths.length) return false;
        var index;
        for (index = 0; index < before.texts.length; index++)
            if (!isTarget("text", index) && toJson(before.texts[index]) !== toJson(after.texts[index])) return false;
        for (index = 0; index < before.linked_images.length; index++)
            if (!isTarget("linked_image", index) && toJson(before.linked_images[index]) !== toJson(after.linked_images[index])) return false;
        for (index = 0; index < before.paths.length; index++)
            if (toJson(before.paths[index]) !== toJson(after.paths[index])) return false;
        return true;
    }}
    function targetsMatch(snapshot, phase) {{
        for (var i = 0; i < request.operations.length; i++) {{
            var operation = request.operations[i];
            if (operation.type === "text") {{
                var text = snapshot.texts[operation.dom_index];
                if (!text || text.contents !== operation.after) return false;
                if (toJson(text.style) !== toJson(operation.before.style)) return false;
                if (!closeArray(text.matrix, operation.before.matrix)) return false;
                if (text.parent === null || toJson(text.parent) !== toJson(operation.before.parent)) return false;
            }} else {{
                var image = snapshot.linked_images[operation.dom_index];
                if (!image || !image.source_exists || image.source !== operation.after) return false;
                if (!imageGeometryPreserved(image, operation.before)) return false;
            }}
        }}
        return true;
    }}
    try {{
        app.userInteractionLevel = UserInteractionLevel.DONTDISPLAYALERTS;
        if (!source.exists) throw new Error("Temporary fixture does not exist");
        if (destination.exists) throw new Error("Temporary destination already exists");
        documentRef = app.open(source);
        var before = documentSnapshot(documentRef);
        for (var operationIndex = 0; operationIndex < request.operations.length; operationIndex++) {{
            var operation = request.operations[operationIndex];
            if (operation.type === "text") {{
                var frame = documentRef.textFrames[operation.dom_index];
                if (!frame || !textPrecondition(textSnapshot(frame, operation.dom_index), operation.before))
                    throw new Error("text precondition mismatch for " + operation.id);
                frame.contents = operation.after;
            }} else if (operation.type === "linked_image") {{
                var placed = documentRef.placedItems[operation.dom_index];
                if (!placed || !imagePrecondition(imageSnapshot(placed, operation.dom_index), operation.before))
                    throw new Error("linked image precondition mismatch for " + operation.id);
                var replacement = new File(operation.after);
                if (!replacement.exists) throw new Error("replacement linked image does not exist");
                placed.relink(replacement);
            }} else throw new Error("unsupported native local operation type");
        }}
        var afterMutation = documentSnapshot(documentRef);
        var checks = {{
            structure_preserved_before_save: toJson(before.structure) === toJson(afterMutation.structure),
            non_targets_preserved_before_save: nonTargetsEqual(before, afterMutation),
            targets_match_before_save: targetsMatch(afterMutation, "before-save")
        }};
        var options = new IllustratorSaveOptions();
        options.pdfCompatible = true; options.embedLinkedFiles = false; options.compressed = true;
        documentRef.saveAs(destination, options);
        documentRef.close(SaveOptions.DONOTSAVECHANGES); documentRef = null;
        documentRef = app.open(destination);
        var afterReopen = documentSnapshot(documentRef);
        checks.structure_preserved_after_reopen = toJson(before.structure) === toJson(afterReopen.structure);
        checks.non_targets_preserved_after_reopen = nonTargetsEqual(before, afterReopen);
        checks.targets_match_after_reopen = targetsMatch(afterReopen, "after-reopen");
        var allPassed = true; for (var check in checks) if (!checks[check]) allPassed = false;
        return toJson({{ok: allPassed, illustrator_version: app.version, checks: checks,
            before: reportSnapshot(before), after_mutation: reportSnapshot(afterMutation),
            after_reopen: reportSnapshot(afterReopen)}});
    }} catch (error) {{
        return toJson({{ok: false, error: String(error), line: error.line || null}});
    }} finally {{
        if (documentRef !== null) documentRef.close(SaveOptions.DONOTSAVECHANGES);
        app.userInteractionLevel = previousInteractionLevel;
    }}
}}());
'''


def _build_save_javascript(source: Path, destination: Path, *, compatibility: bool) -> str:
    source_literal = character_code_expression(source)
    destination_literal = character_code_expression(destination)
    options = (
        "options.compatibility = Compatibility.ILLUSTRATOR8;\n"
        "        options.pdfCompatible = false;\n"
        "        options.embedLinkedFiles = false;\n"
        "        options.flattenOutput = OutputFlattening.PRESERVEAPPEARANCE;"
        if compatibility
        else "options.pdfCompatible = true;\n        options.embedLinkedFiles = false;\n        options.compressed = true;"
    )
    return f"""#target illustrator
(function () {{
    var source = new File({source_literal});
    var destination = new File({destination_literal});
    var documentRef = null;
    var previousInteractionLevel = app.userInteractionLevel;
    try {{
        app.userInteractionLevel = UserInteractionLevel.DONTDISPLAYALERTS;
        if (!source.exists) throw new Error("Temporary fixture does not exist");
        documentRef = app.open(source);
        var options = new IllustratorSaveOptions();
        {options}
        documentRef.saveAs(destination, options);
        return "ok:" + app.version;
    }} catch (error) {{
        return "error:" + String(error) + ":line:" + String(error.line || "");
    }} finally {{
        if (documentRef !== null) documentRef.close(SaveOptions.DONOTSAVECHANGES);
        app.userInteractionLevel = previousInteractionLevel;
    }}
}}());
"""


def build_font_catalog_javascript() -> str:
    return """#target illustrator
(function () {
    try {
        var lines = ["ok\\t" + app.version + "\\t" + app.textFonts.length];
        for (var index = 0; index < app.textFonts.length; index++) {
            var font = app.textFonts[index];
            lines.push(font.name + "\\t" + font.family + "\\t" + font.style);
        }
        return lines.join("\\n");
    } catch (error) {
        return "error\\t" + String(error) + "\\t" + String(error.line || "");
    }
}());
"""


def build_native_materialization_javascript(
    source: Path,
    destination: Path,
    *,
    text_notes: tuple[str, ...] = (),
    text_contents: tuple[str, ...] = (),
    desired_font_names: tuple[str, ...] = (),
    desired_font_sizes: tuple[float, ...] = (),
    desired_fills: tuple[dict[str, Any], ...] = (),
    desired_trackings: tuple[float, ...] = (),
    desired_rotations: tuple[float, ...] = (),
    desired_alignments: tuple[str, ...] = (),
    desired_area_widths: tuple[float | None, ...] = (),
    desired_area_heights: tuple[float | None, ...] = (),
    desired_leadings: tuple[float | None, ...] = (),
    desired_artboards: tuple[dict[str, Any], ...] = (),
    desired_images: tuple[dict[str, Any], ...] = (),
    source_document_height: float = 0.0,
) -> str:
    source_literal = character_code_expression(source)
    destination_literal = character_code_expression(destination)
    text_notes_literal = ",".join(character_code_expression(note) for note in text_notes)
    text_contents_literal = ",".join(
        character_code_expression(contents) for contents in text_contents
    )
    desired_font_names_literal = ",".join(
        character_code_expression(name) for name in desired_font_names
    )
    desired_font_sizes_literal = ",".join(str(value) for value in desired_font_sizes)
    desired_fills_literal = ",".join(
        json.dumps(value, separators=(",", ":")) for value in desired_fills
    )
    desired_trackings_literal = ",".join(str(value) for value in desired_trackings)
    desired_rotations_literal = ",".join(str(value) for value in desired_rotations)
    desired_alignments_literal = ",".join(
        character_code_expression(value) for value in desired_alignments
    )
    desired_area_widths_literal = ",".join(
        "null" if value is None else str(value) for value in desired_area_widths
    )
    desired_area_heights_literal = ",".join(
        "null" if value is None else str(value) for value in desired_area_heights
    )
    desired_leadings_literal = ",".join(
        "null" if value is None else str(value) for value in desired_leadings
    )
    desired_artboards_literal = ",".join(
        "{name:"
        + character_code_expression(str(value["name"]))
        + f",left:{value['left']},top:{value['top']},width:{value['width']},height:{value['height']}"
        + "}"
        for value in desired_artboards
    )
    desired_images_literal = ",".join(
        "{id:"
        + character_code_expression(str(value["id"]))
        + ",name:"
        + character_code_expression(str(value["name"]))
        + ",path:"
        + character_code_expression(str(value["path"]))
        + ",placeholderNote:"
        + character_code_expression(str(value["placeholder_note"]))
        + f",width:{value['width']},height:{value['height']},rotation:{value['rotation']}"
        + "}"
        for value in desired_images
    )
    return f"""#target illustrator
(function () {{
    var source = new File({source_literal});
    var destination = new File({destination_literal});
    var documentRef = null;
    var previousInteractionLevel = app.userInteractionLevel;
    try {{
        app.userInteractionLevel = UserInteractionLevel.DONTDISPLAYALERTS;
        if (!source.exists) throw new Error("Temporary fixture does not exist");
        documentRef = app.open(source);
        var legacyTextCount = documentRef.legacyTextItems.length;
        var textNotes = [{text_notes_literal}];
        var textContents = [{text_contents_literal}];
        var desiredFontNames = [{desired_font_names_literal}];
        var desiredFontSizes = [{desired_font_sizes_literal}];
        var desiredFills = [{desired_fills_literal}];
        var desiredTrackings = [{desired_trackings_literal}];
        var desiredRotations = [{desired_rotations_literal}];
        var desiredAlignments = [{desired_alignments_literal}];
        var desiredAreaWidths = [{desired_area_widths_literal}];
        var desiredAreaHeights = [{desired_area_heights_literal}];
        var desiredLeadings = [{desired_leadings_literal}];
        var desiredArtboards = [{desired_artboards_literal}];
        var desiredImages = [{desired_images_literal}];
        var sourceDocumentHeight = {source_document_height};
        var sourceArtboardRect = [documentRef.artboards[0].artboardRect[0], documentRef.artboards[0].artboardRect[1], documentRef.artboards[0].artboardRect[2], documentRef.artboards[0].artboardRect[3]];
        var converted = legacyTextCount === 0 ? true : documentRef.legacyTextItems.convertToNative();
        var nativeTextCount = documentRef.textFrames.length;
        var assignedNoteCount = 0;
        var identityContentMatchCount = 0;
        var requestedFontCount = 0;
        var assignedFontCount = 0;
        var matchingFontCount = 0;
        var matchingFontSizeCount = 0;
        var matchingFillCount = 0;
        var missingFonts = [];
        var matchingTrackingCount = 0;
        var matchingRotationCount = 0;
        var recreatedAreaTextCount = 0;
        var matchingAreaTextCount = 0;
        var matchingLeadingCount = 0;
        var matchingArtboardCount = 0;
        var foundImagePlaceholderCount = 0;
        var placedImageCount = 0;
        var matchingLinkedImageCount = 0;
        var nativeFrames = [];
        for (var frameIndex = 0; frameIndex < nativeTextCount; frameIndex++) nativeFrames.push(documentRef.textFrames[frameIndex]);
        function itemRotation(item) {{ var matrix = item.matrix; return Math.atan2(matrix.mValueB, matrix.mValueA) * 180 / Math.PI; }}
        function angleDifference(left, right) {{ var difference = (left - right + 180) % 360; if (difference < 0) difference += 360; return Math.abs(difference - 180); }}
        function normalizedText(value) {{ return String(value).replace(/\\r\\n/g, "\\n").replace(/\\r/g, "\\n"); }}
        function applyFill(attributes, spec) {{
            var color;
            if (spec.type === "rgb") {{ color = new RGBColor(); color.red = spec.values[0] * 255; color.green = spec.values[1] * 255; color.blue = spec.values[2] * 255; }}
            else {{ color = new CMYKColor(); color.cyan = spec.values[0] * 100; color.magenta = spec.values[1] * 100; color.yellow = spec.values[2] * 100; color.black = spec.values[3] * 100; }}
            attributes.fillColor = color;
        }}
        function fillMatches(color, spec) {{
            var scale = spec.type === "rgb" ? 255 : 100;
            var actual = spec.type === "rgb" ? [color.red, color.green, color.blue] : [color.cyan, color.magenta, color.yellow, color.black];
            if ((spec.type === "rgb" && color.typename !== "RGBColor") || (spec.type === "cmyk" && color.typename !== "CMYKColor")) return false;
            for (var colorIndex = 0; colorIndex < actual.length; colorIndex++) if (Math.abs(actual[colorIndex] - spec.values[colorIndex] * scale) > 0.51) return false;
            return true;
        }}
        for (var noteIndex = 0; noteIndex < nativeTextCount && noteIndex < textNotes.length; noteIndex++) {{
            var textFrame = nativeFrames[noteIndex];
            if (noteIndex < desiredAreaWidths.length && desiredAreaWidths[noteIndex] !== null && desiredAreaHeights[noteIndex] !== null) {{
                var framePosition = textFrame.anchor ? [textFrame.anchor[0], textFrame.anchor[1]] : [textFrame.position[0], textFrame.position[1]];
                var textPath = textFrame.parent.pathItems.rectangle(framePosition[1], framePosition[0], desiredAreaWidths[noteIndex], desiredAreaHeights[noteIndex]);
                var areaFrame = documentRef.textFrames.areaText(textPath);
                areaFrame.contents = noteIndex < textContents.length ? textContents[noteIndex] : textFrame.contents;
                areaFrame.move(textFrame, ElementPlacement.PLACEBEFORE); textFrame.remove(); textFrame = areaFrame; nativeFrames[noteIndex] = areaFrame; recreatedAreaTextCount++;
                if (textFrame.kind === TextType.AREATEXT && Math.abs(textFrame.width - desiredAreaWidths[noteIndex]) < 0.1 && Math.abs(textFrame.height - desiredAreaHeights[noteIndex]) < 0.1) matchingAreaTextCount++;
            }}
            textFrame.note = textNotes[noteIndex]; assignedNoteCount++;
            if (noteIndex < desiredFontSizes.length) {{ textFrame.textRange.characterAttributes.size = desiredFontSizes[noteIndex]; if (Math.abs(textFrame.textRange.characterAttributes.size - desiredFontSizes[noteIndex]) < 0.01) matchingFontSizeCount++; }}
            if (noteIndex < desiredFills.length) {{ applyFill(textFrame.textRange.characterAttributes, desiredFills[noteIndex]); if (fillMatches(textFrame.textRange.characterAttributes.fillColor, desiredFills[noteIndex])) matchingFillCount++; }}
            if (noteIndex < desiredAlignments.length) {{ var desiredJustification = {{left: Justification.LEFT, center: Justification.CENTER, right: Justification.RIGHT}}[desiredAlignments[noteIndex]]; textFrame.textRange.paragraphAttributes.justification = desiredJustification; }}
            if (noteIndex < desiredFontNames.length && desiredFontNames[noteIndex] !== "") {{
                requestedFontCount++;
                try {{ var desiredFont = app.textFonts.getByName(desiredFontNames[noteIndex]); textFrame.textRange.characterAttributes.textFont = desiredFont; assignedFontCount++; if (textFrame.textRange.characterAttributes.textFont.name === desiredFontNames[noteIndex]) matchingFontCount++; }}
                catch (fontError) {{ missingFonts.push(desiredFontNames[noteIndex]); }}
            }}
            if (noteIndex < desiredTrackings.length) {{ textFrame.textRange.characterAttributes.tracking = desiredTrackings[noteIndex]; if (Math.abs(textFrame.textRange.characterAttributes.tracking - desiredTrackings[noteIndex]) < 0.01) matchingTrackingCount++; }}
            if (noteIndex < desiredLeadings.length && desiredLeadings[noteIndex] !== null) {{ textFrame.textRange.characterAttributes.autoLeading = false; textFrame.textRange.characterAttributes.leading = desiredLeadings[noteIndex]; if (Math.abs(textFrame.textRange.characterAttributes.leading - desiredLeadings[noteIndex]) < 0.01) matchingLeadingCount++; }} else matchingLeadingCount++;
            if (noteIndex < desiredRotations.length) {{ var currentRotation = itemRotation(textFrame); var rotationDelta = desiredRotations[noteIndex] - currentRotation; if (Math.abs(rotationDelta) > 0.0001) {{ var positionBeforeRotation = [textFrame.position[0], textFrame.position[1]]; textFrame.rotate(rotationDelta); textFrame.position = positionBeforeRotation; }} if (angleDifference(itemRotation(textFrame), desiredRotations[noteIndex]) < 0.01) matchingRotationCount++; }}
            if (noteIndex < textContents.length && normalizedText(textFrame.contents) === normalizedText(textContents[noteIndex])) identityContentMatchCount++;
        }}
        var justifications = [];
        var nativeNoteCount = 0;
        for (var index = 0; index < documentRef.textFrames.length; index++) {{
            if (documentRef.textFrames[index].note.indexOf("py-ai-text:") === 0) nativeNoteCount++;
            try {{ justifications.push(String(documentRef.textFrames[index].textRange.paragraphAttributes.justification)); }} catch (attributeError) {{ justifications.push("unavailable"); }}
        }}
        for (var imageIndex = 0; imageIndex < desiredImages.length; imageIndex++) {{
            var imageSpec = desiredImages[imageIndex]; var placeholder = null;
            for (var placeholderIndex = 0; placeholderIndex < documentRef.pathItems.length; placeholderIndex++) {{ if (documentRef.pathItems[placeholderIndex].note === imageSpec.placeholderNote) {{ placeholder = documentRef.pathItems[placeholderIndex]; break; }} }}
            if (placeholder === null) continue;
            foundImagePlaceholderCount++; var imageFile = new File(imageSpec.path); if (!imageFile.exists) throw new Error("Packaged image link does not exist");
            var placeholderPosition = [placeholder.position[0], placeholder.position[1]]; var placedImage = documentRef.placedItems.add(); placedImage.file = imageFile; placedImage.name = imageSpec.name; placedImage.note = "py-ai-image:" + imageSpec.id; placedImage.width = imageSpec.width; placedImage.height = imageSpec.height; placedImage.move(placeholder, ElementPlacement.PLACEBEFORE); placedImage.position = placeholderPosition;
            if (Math.abs(imageSpec.rotation) > 0.0001) {{ placedImage.rotate(imageSpec.rotation); placedImage.position = placeholderPosition; }}
            var imageMatches = placedImage.file.fsName === imageFile.fsName && placedImage.note === "py-ai-image:" + imageSpec.id && Math.abs(placedImage.width - imageSpec.width) < 0.1 && Math.abs(placedImage.height - imageSpec.height) < 0.1 && angleDifference(itemRotation(placedImage), imageSpec.rotation) < 0.01;
            if (imageMatches) matchingLinkedImageCount++; placedImageCount++; placeholder.remove();
        }}
        if (desiredArtboards.length > 0) {{
            while (documentRef.artboards.length > 1) documentRef.artboards.remove(documentRef.artboards.length - 1);
            for (var artboardIndex = 0; artboardIndex < desiredArtboards.length; artboardIndex++) {{
                var artboardSpec = desiredArtboards[artboardIndex]; var artboardLeft = sourceArtboardRect[0] + artboardSpec.left; var artboardTop = sourceArtboardRect[1] + artboardSpec.top - sourceDocumentHeight;
                var artboardRect = [artboardLeft, artboardTop, artboardLeft + artboardSpec.width, artboardTop - artboardSpec.height];
                var artboard = artboardIndex === 0 ? documentRef.artboards[0] : documentRef.artboards.add(artboardRect);
                if (artboardIndex === 0) artboard.artboardRect = artboardRect; artboard.name = artboardSpec.name; var savedRect = artboard.artboardRect;
                if (artboard.name === artboardSpec.name && Math.abs(savedRect[0] - artboardRect[0]) < 0.01 && Math.abs(savedRect[1] - artboardRect[1]) < 0.01 && Math.abs(savedRect[2] - artboardRect[2]) < 0.01 && Math.abs(savedRect[3] - artboardRect[3]) < 0.01) matchingArtboardCount++;
            }}
            documentRef.artboards.setActiveArtboardIndex(0);
        }}
        var options = new IllustratorSaveOptions(); options.pdfCompatible = true; options.embedLinkedFiles = false; options.flattenOutput = OutputFlattening.PRESERVEAPPEARANCE; documentRef.saveAs(destination, options);
        return ["ok", app.version, legacyTextCount, nativeTextCount, converted, justifications.join(","), assignedNoteCount, nativeNoteCount, identityContentMatchCount, requestedFontCount, assignedFontCount, matchingFontCount, matchingFontSizeCount, matchingFillCount, missingFonts.join(","), matchingTrackingCount, matchingRotationCount, recreatedAreaTextCount, matchingAreaTextCount, matchingLeadingCount, matchingArtboardCount, foundImagePlaceholderCount, placedImageCount, matchingLinkedImageCount].join(":");
    }} catch (error) {{
        return "error:" + String(error) + ":line:" + String(error.line || "");
    }} finally {{
        if (documentRef !== null) documentRef.close(SaveOptions.DONOTSAVECHANGES);
        app.userInteractionLevel = previousInteractionLevel;
    }}
}}());
"""
