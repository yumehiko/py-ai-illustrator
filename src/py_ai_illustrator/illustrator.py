"""Opt-in compatibility checks against a locally installed Adobe Illustrator."""

from __future__ import annotations

import json
import math
import platform
import shutil
import subprocess
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any

from .format import FileFormat, inspect_file
from .legacy import load_ai7
from .model import (
    ClippingGroup,
    CmykColor,
    Color,
    CompoundPath,
    Document,
    Group,
    ProcessColor,
    TextFrame,
)
from .model import Path as AIPath


def _character_code_expression(value: str | Path) -> str:
    codepoints = ",".join(str(ord(character)) for character in str(value))
    return f"String.fromCharCode({codepoints})"


def _text_identity_note(text: TextFrame) -> str:
    return "py-ai-text:" + json.dumps(
        {"id": text.id, "name": text.name},
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _group_paths(group: Group) -> list[AIPath]:
    paths: list[AIPath] = []
    paths.extend(group.paths)
    for compound in group.compound_paths:
        paths.extend(compound.paths)
    for clipping_group in group.clipping_groups:
        paths.append(clipping_group.clipping_path)
        paths.extend(clipping_group.paths)
    for child in group.groups:
        paths.extend(_group_paths(child))
    return paths


def _document_paths(document: Document) -> list[AIPath]:
    paths: list[AIPath] = []
    for layer in document.layers:
        paths.extend(layer.paths)
        for compound in layer.compound_paths:
            paths.extend(compound.paths)
        for group in layer.clipping_groups:
            paths.append(group.clipping_path)
            paths.extend(group.paths)
        for group in layer.groups:
            paths.extend(_group_paths(group))
    return paths


def _document_text_frames(document: Document) -> list[TextFrame]:
    def group_text(group: Group) -> list[TextFrame]:
        return [
            *group.text_frames,
            *(text for child in group.groups for text in group_text(child)),
        ]

    return [
        text
        for layer in document.layers
        for text in [
            *layer.text_frames,
            *(text for group in layer.groups for text in group_text(group)),
        ]
    ]


def _document_text_frames_dom_order(document: Document) -> list[TextFrame]:
    def group_text(group: Group) -> list[TextFrame]:
        texts: list[TextFrame] = []
        for item in reversed(group.ordered_items()):
            if isinstance(item, TextFrame):
                texts.append(item)
            elif isinstance(item, Group):
                texts.extend(group_text(item))
        return texts

    texts: list[TextFrame] = []
    for layer in document.layers:
        for item in reversed(layer.ordered_items()):
            if isinstance(item, TextFrame):
                texts.append(item)
            elif isinstance(item, Group):
                texts.extend(group_text(item))
    return texts


def _group_descendants(group: Group) -> list[Group]:
    return [
        group,
        *(nested for child in group.groups for nested in _group_descendants(child)),
    ]


def _document_groups(document: Document) -> list[Group]:
    return [
        group
        for layer in document.layers
        for root in layer.groups
        for group in _group_descendants(root)
    ]


def _document_compound_paths(document: Document) -> list[CompoundPath]:
    return [
        compound
        for layer in document.layers
        for compound in [
            *layer.compound_paths,
            *(
                compound
                for root in layer.groups
                for group in _group_descendants(root)
                for compound in group.compound_paths
            ),
        ]
    ]


def _document_clipping_groups(document: Document) -> list[ClippingGroup]:
    return [
        clipping_group
        for layer in document.layers
        for clipping_group in [
            *layer.clipping_groups,
            *(
                clipping_group
                for root in layer.groups
                for group in _group_descendants(root)
                for clipping_group in group.clipping_groups
            ),
        ]
    ]


def _group_signature(group: Group) -> tuple[Any, ...]:
    child_groups = {child.id: child for child in group.groups}
    return tuple(
        (reference.kind, _group_signature(child_groups[reference.id]))
        if reference.kind == "group"
        else (reference.kind,)
        for reference in group.item_order
    )


def _expected_structure(source: Path) -> dict[str, Any] | None:
    report = inspect_file(source)
    if report.format is not FileFormat.LEGACY_AI:
        return None
    try:
        document = load_ai7(source)
    except ValueError:
        return None
    paths = _document_paths(document)
    text_frames = _document_text_frames(document)
    return {
        "layer_count": len(document.layers),
        "layer_names": [layer.name for layer in document.layers],
        "layer_page_item_types": [
            [
                {
                    "path": "PathItem",
                    "text": "TextFrame",
                    "compound_path": "CompoundPathItem",
                    "clipping_group": "GroupItem",
                    "group": "GroupItem",
                }[reference.kind]
                for reference in reversed(layer.item_order)
            ]
            for layer in document.layers
        ],
        "path_item_count": len(paths),
        "text_frame_count": len(text_frames),
        "point_counts": sorted(len(path.points) for path in paths),
        "closed_count": sum(path.closed for path in paths),
        "filled_count": sum(path.fill is not None for path in paths),
        "stroked_count": sum(path.stroke is not None for path in paths),
        "compound_path_item_count": len(_document_compound_paths(document)),
        "clipping_group_count": len(_document_clipping_groups(document)),
        "group_item_count": len(_document_groups(document)),
    }


def _build_javascript(source: Path) -> str:
    # Illustrator's Japanese ExtendScript parser can display and parse the ASCII
    # reverse solidus as a yen sign.  Build both the path and JSON escapes from
    # character codes so the generated JSX contains no ambiguous character.
    source_literal = _character_code_expression(source)
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
        for (
            var artboardIndex = 0;
            artboardIndex < documentRef.artboards.length;
            artboardIndex++
        ) {{
            var artboard = documentRef.artboards[artboardIndex];
            artboards.push({{
                name: artboard.name,
                rect: [
                    artboard.artboardRect[0],
                    artboard.artboardRect[1],
                    artboard.artboardRect[2],
                    artboard.artboardRect[3]
                ]
            }});
        }}

        var textFrames = [];
        function collectTextFrames(container) {{
            for (
                var textFrameIndex = 0;
                textFrameIndex < container.pageItems.length;
                textFrameIndex++
            ) {{
                var textFrame = container.pageItems[textFrameIndex];
                if (textFrame.parent !== container) continue;
                if (textFrame.typename === "GroupItem") {{
                    collectTextFrames(textFrame);
                    continue;
                }}
                if (
                    textFrame.typename !== "TextFrame"
                    && textFrame.typename !== "LegacyTextItem"
                ) continue;
                var textRange = textFrame.textRange;
                var attributes = textRange ? textRange.characterAttributes : null;
                var paragraphAttributes = textRange ? textRange.paragraphAttributes : null;
                textFrames.push({{
                    name: textFrame.name,
                    note: textFrame.note,
                    contents: textFrame.contents,
                    position: [textFrame.position[0], textFrame.position[1]],
                    font_size: attributes && typeof attributes.size === "number"
                        ? attributes.size
                        : null,
                    font_name: attributes && attributes.textFont ? attributes.textFont.name : null,
                    tracking: attributes && typeof attributes.tracking === "number"
                        ? attributes.tracking
                        : null,
                    fill_color: attributes ? colorToObject(attributes.fillColor) : null,
                    justification: paragraphAttributes
                        ? String(paragraphAttributes.justification)
                        : null
                }});
            }}
        }}
        for (
            var textLayerIndex = 0;
            textLayerIndex < documentRef.layers.length;
            textLayerIndex++
        ) {{
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
            artboards: artboards
        }});
    }} catch (error) {{
        return toJson({{
            ok: false,
            error: String(error),
            line: error.line || null
        }});
    }} finally {{
        if (documentRef !== null) {{
            documentRef.close(SaveOptions.DONOTSAVECHANGES);
        }}
        app.userInteractionLevel = previousInteractionLevel;
    }}
}}());
"""


def _build_export_javascript(destination: Path, fixture: str) -> str:
    destination_literal = _character_code_expression(destination)
    if fixture == "rgb-rectangle":
        fixture_javascript = """
        documentRef = app.documents.add(DocumentColorSpace.RGB, 320, 240);
        var layer = documentRef.layers[0];
        layer.name = "Illustrator Native";

        var path = layer.pathItems.add();
        path.name = "Native Rectangle";
        path.setEntirePath([[40, 40], [280, 40], [280, 200], [40, 200]]);
        path.closed = true;
        path.filled = true;
        path.stroked = true;
        path.strokeWidth = 3;

        var fill = new RGBColor();
        fill.red = 255;
        fill.green = 77;
        fill.blue = 0;
        path.fillColor = fill;

        var stroke = new RGBColor();
        stroke.red = 38;
        stroke.green = 26;
        stroke.blue = 13;
        path.strokeColor = stroke;
"""
    elif fixture == "cmyk-curve":
        fixture_javascript = """
        documentRef = app.documents.add(DocumentColorSpace.CMYK, 200, 200);
        var layer = documentRef.layers[0];
        layer.name = "Illustrator Native Curves";

        var path = layer.pathItems.add();
        path.name = "Native CMYK Curve";
        var first = path.pathPoints.add();
        first.anchor = [20, 20];
        first.leftDirection = [20, 20];
        first.rightDirection = [20, 150];
        first.pointType = PointType.SMOOTH;
        var second = path.pathPoints.add();
        second.anchor = [180, 180];
        second.leftDirection = [180, 50];
        second.rightDirection = [180, 180];
        second.pointType = PointType.SMOOTH;
        path.closed = false;
        path.filled = false;
        path.stroked = true;
        path.strokeWidth = 4;

        var stroke = new CMYKColor();
        stroke.cyan = 100;
        stroke.magenta = 25;
        stroke.yellow = 0;
        stroke.black = 10;
        path.strokeColor = stroke;
"""
    elif fixture == "stroke-style":
        fixture_javascript = """
        documentRef = app.documents.add(DocumentColorSpace.RGB, 300, 200);
        var layer = documentRef.layers[0];
        layer.name = "Illustrator Native Stroke Style";

        var path = layer.pathItems.add();
        path.name = "Native Dashed Route";
        path.setEntirePath([[30, 40], [150, 160], [270, 40]]);
        path.closed = false;
        path.filled = false;
        path.stroked = true;
        path.strokeWidth = 6;
        path.strokeDashes = [18, 8, 4, 8];
        path.strokeDashOffset = 3;
        path.strokeCap = StrokeCap.ROUNDENDCAP;
        path.strokeJoin = StrokeJoin.BEVELENDJOIN;
        path.strokeMiterLimit = 7;

        var stroke = new RGBColor();
        stroke.red = 38;
        stroke.green = 102;
        stroke.blue = 204;
        path.strokeColor = stroke;
"""
    elif fixture == "compound-path":
        fixture_javascript = """
        documentRef = app.documents.add(DocumentColorSpace.RGB, 300, 300);
        var layer = documentRef.layers[0];
        layer.name = "Illustrator Native Compound";

        var compound = layer.compoundPathItems.add();
        compound.name = "Native Compound Path";
        var outer = compound.pathItems.add();
        outer.setEntirePath([[20, 20], [280, 20], [280, 280], [20, 280]]);
        outer.closed = true;
        var inner = compound.pathItems.add();
        inner.setEntirePath([[90, 90], [90, 210], [210, 210], [210, 90]]);
        inner.closed = true;

        var fill = new RGBColor();
        fill.red = 64;
        fill.green = 128;
        fill.blue = 255;
        outer.filled = true;
        outer.stroked = false;
        outer.fillColor = fill;
"""
    elif fixture == "clipping-group":
        fixture_javascript = """
        documentRef = app.documents.add(DocumentColorSpace.RGB, 300, 300);
        var layer = documentRef.layers[0];
        layer.name = "Illustrator Native Clipping";

        var group = layer.groupItems.add();
        group.name = "Native Clipping Group";
        var content = group.pathItems.add();
        content.setEntirePath([[20, 20], [280, 20], [280, 280], [20, 280]]);
        content.closed = true;
        content.filled = true;
        content.stroked = false;
        var contentFill = new RGBColor();
        contentFill.red = 255;
        contentFill.green = 64;
        contentFill.blue = 128;
        content.fillColor = contentFill;

        var clip = group.pathItems.add();
        clip.setEntirePath([[80, 80], [220, 80], [220, 220], [80, 220]]);
        clip.closed = true;
        clip.filled = false;
        clip.stroked = false;
        clip.clipping = true;
        group.clipped = true;
"""
    elif fixture == "group":
        fixture_javascript = """
        documentRef = app.documents.add(DocumentColorSpace.RGB, 300, 200);
        var layer = documentRef.layers[0];
        layer.name = "Illustrator Native Group";

        var group = layer.groupItems.add();
        group.name = "Native Product Card";
        var background = group.pathItems.add();
        background.name = "Card Background";
        background.setEntirePath([[20, 20], [280, 20], [280, 180], [20, 180]]);
        background.closed = true;
        background.filled = true;
        background.stroked = false;
        var backgroundFill = new RGBColor();
        backgroundFill.red = 242;
        backgroundFill.green = 245;
        backgroundFill.blue = 250;
        background.fillColor = backgroundFill;

        var accent = group.pathItems.add();
        accent.name = "Card Accent";
        accent.setEntirePath([[20, 160], [280, 160], [280, 180], [20, 180]]);
        accent.closed = true;
        accent.filled = true;
        accent.stroked = false;
        var accentFill = new RGBColor();
        accentFill.red = 38;
        accentFill.green = 102;
        accentFill.blue = 204;
        accent.fillColor = accentFill;
"""
    elif fixture == "point-text":
        fixture_javascript = """
        documentRef = app.documents.add(DocumentColorSpace.RGB, 320, 240);
        var layer = documentRef.layers[0];
        layer.name = "Illustrator Native Text";

        var text = layer.textFrames.add();
        text.name = "Native Table Header";
        text.contents = "Table Header";
        text.position = [40, 180];
        text.textRange.characterAttributes.size = 14;
        text.textRange.paragraphAttributes.justification = Justification.CENTER;
        var textFill = new RGBColor();
        textFill.red = 26;
        textFill.green = 51;
        textFill.blue = 77;
        text.textRange.characterAttributes.fillColor = textFill;
"""
    elif fixture == "unicode-text":
        fixture_javascript = """
        documentRef = app.documents.add(DocumentColorSpace.RGB, 420, 240);
        var layer = documentRef.layers[0];
        layer.name = "Illustrator Native Unicode";

        var text = layer.textFrames.add();
        text.name = "Native Japanese Table Header";
        text.contents = String.fromCharCode(
            26085, 26412, 35486, 12398, 34920, 35211, 20986, 12375
        );
        text.position = [40, 180];
        text.textRange.characterAttributes.size = 16;
        var textFill = new RGBColor();
        textFill.red = 26;
        textFill.green = 51;
        textFill.blue = 77;
        text.textRange.characterAttributes.fillColor = textFill;
"""
    else:
        raise ValueError(f"Unknown Illustrator fixture: {fixture}")
    return f"""#target illustrator
(function () {{
    var destination = new File({destination_literal});
    var documentRef = null;
    var previousInteractionLevel = app.userInteractionLevel;

    try {{
        app.userInteractionLevel = UserInteractionLevel.DONTDISPLAYALERTS;
{fixture_javascript}

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
        if (documentRef !== null) {{
            documentRef.close(SaveOptions.DONOTSAVECHANGES);
        }}
        app.userInteractionLevel = previousInteractionLevel;
    }}
}}());
"""


def _build_roundtrip_javascript(source: Path, destination: Path) -> str:
    source_literal = _character_code_expression(source)
    destination_literal = _character_code_expression(destination)
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
        options.compatibility = Compatibility.ILLUSTRATOR8;
        options.pdfCompatible = false;
        options.embedLinkedFiles = false;
        options.flattenOutput = OutputFlattening.PRESERVEAPPEARANCE;
        documentRef.saveAs(destination, options);
        return "ok:" + app.version;
    }} catch (error) {{
        return "error:" + String(error) + ":line:" + String(error.line || "");
    }} finally {{
        if (documentRef !== null) {{
            documentRef.close(SaveOptions.DONOTSAVECHANGES);
        }}
        app.userInteractionLevel = previousInteractionLevel;
    }}
}}());
"""


def _build_native_materialization_javascript(
    source: Path,
    destination: Path,
    *,
    text_notes: tuple[str, ...] = (),
    text_contents: tuple[str, ...] = (),
    desired_font_names: tuple[str, ...] = (),
    desired_trackings: tuple[float, ...] = (),
) -> str:
    source_literal = _character_code_expression(source)
    destination_literal = _character_code_expression(destination)
    text_notes_literal = ",".join(_character_code_expression(note) for note in text_notes)
    text_contents_literal = ",".join(
        _character_code_expression(contents) for contents in text_contents
    )
    desired_font_names_literal = ",".join(
        _character_code_expression(name) for name in desired_font_names
    )
    desired_trackings_literal = ",".join(str(value) for value in desired_trackings)
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
        var desiredTrackings = [{desired_trackings_literal}];
        var converted = legacyTextCount === 0
            ? true
            : documentRef.legacyTextItems.convertToNative();
        var nativeTextCount = documentRef.textFrames.length;
        var assignedNoteCount = 0;
        var identityContentMatchCount = 0;
        var requestedFontCount = 0;
        var assignedFontCount = 0;
        var matchingFontCount = 0;
        var missingFonts = [];
        var matchingTrackingCount = 0;
        for (
            var noteIndex = 0;
            noteIndex < nativeTextCount && noteIndex < textNotes.length;
            noteIndex++
        ) {{
            documentRef.textFrames[noteIndex].note = textNotes[noteIndex];
            assignedNoteCount++;
            if (
                noteIndex < desiredFontNames.length
                && desiredFontNames[noteIndex] !== ""
            ) {{
                requestedFontCount++;
                try {{
                    var desiredFont = app.textFonts.getByName(
                        desiredFontNames[noteIndex]
                    );
                    documentRef.textFrames[noteIndex].textRange.characterAttributes.textFont = (
                        desiredFont
                    );
                    assignedFontCount++;
                    if (
                        documentRef.textFrames[noteIndex].textRange.characterAttributes
                            .textFont.name === desiredFontNames[noteIndex]
                    ) matchingFontCount++;
                }} catch (fontError) {{
                    missingFonts.push(desiredFontNames[noteIndex]);
                }}
            }}
            if (noteIndex < desiredTrackings.length) {{
                documentRef.textFrames[noteIndex].textRange.characterAttributes.tracking = (
                    desiredTrackings[noteIndex]
                );
                if (
                    Math.abs(
                        documentRef.textFrames[noteIndex].textRange.characterAttributes.tracking
                            - desiredTrackings[noteIndex]
                    ) < 0.01
                ) matchingTrackingCount++;
            }}
            if (
                noteIndex < textContents.length
                && documentRef.textFrames[noteIndex].contents === textContents[noteIndex]
            ) identityContentMatchCount++;
        }}
        var justifications = [];
        var nativeNoteCount = 0;
        for (var index = 0; index < documentRef.textFrames.length; index++) {{
            if (documentRef.textFrames[index].note.indexOf("py-ai-text:") === 0) {{
                nativeNoteCount++;
            }}
            try {{
                justifications.push(String(
                    documentRef.textFrames[index].textRange.paragraphAttributes.justification
                ));
            }} catch (attributeError) {{
                justifications.push("unavailable");
            }}
        }}

        var options = new IllustratorSaveOptions();
        options.pdfCompatible = true;
        options.embedLinkedFiles = false;
        options.flattenOutput = OutputFlattening.PRESERVEAPPEARANCE;
        documentRef.saveAs(destination, options);
        return [
            "ok",
            app.version,
            legacyTextCount,
            nativeTextCount,
            converted,
            justifications.join(","),
            assignedNoteCount,
            nativeNoteCount,
            identityContentMatchCount,
            requestedFontCount,
            assignedFontCount,
            matchingFontCount,
            missingFonts.join(","),
            matchingTrackingCount
        ].join(":");
    }} catch (error) {{
        return "error:" + String(error) + ":line:" + String(error.line || "");
    }} finally {{
        if (documentRef !== null) {{
            documentRef.close(SaveOptions.DONOTSAVECHANGES);
        }}
        app.userInteractionLevel = previousInteractionLevel;
    }}
}}());
"""


def _build_font_catalog_javascript() -> str:
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


def _execute_javascript(
    javascript: str,
    directory: Path,
    *,
    timeout: float,
    application_name: str,
) -> subprocess.CompletedProcess[str]:
    script_path = directory / "illustrator-test.jsx"
    script_path.write_text(javascript, encoding="utf-8")
    escaped_app = application_name.replace("\\", "\\\\").replace('"', '\\"')
    escaped_script = str(script_path).replace("\\", "\\\\").replace('"', '\\"')
    apple_script = f"""with timeout of {max(1, int(timeout))} seconds
    set scriptFile to POSIX file "{escaped_script}"
    tell application "{escaped_app}"
        return do javascript scriptFile
    end tell
end timeout
"""
    return subprocess.run(
        ["osascript", "-e", apple_script],
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout + 5,
    )


def list_illustrator_fonts(
    *,
    query: str | None = None,
    required: tuple[str, ...] = (),
    timeout: float = 30.0,
    application_name: str = "Adobe Illustrator",
) -> dict[str, Any]:
    """List installed Illustrator fonts and validate exact PostScript names."""

    if platform.system() != "Darwin":
        return {
            "status": "environment-unavailable",
            "error": "Illustrator font discovery is currently supported on macOS only.",
        }
    if timeout <= 0:
        return {"status": "invalid-input", "error": "timeout must be positive"}

    with tempfile.TemporaryDirectory(prefix="py-ai-illustrator-fonts-") as directory:
        try:
            completed = _execute_javascript(
                _build_font_catalog_javascript(),
                Path(directory),
                timeout=timeout,
                application_name=application_name,
            )
        except subprocess.TimeoutExpired:
            return {
                "status": "environment-unavailable",
                "error": f"Illustrator did not answer within {timeout:g} seconds.",
            }

    if completed.returncode != 0:
        return {
            "status": "environment-unavailable",
            "error": completed.stderr.strip() or "Illustrator AppleScript failed.",
        }
    lines = completed.stdout.rstrip("\r\n").splitlines()
    if not lines or not lines[0].startswith("ok\t"):
        return {
            "status": "failed",
            "illustrator_response": completed.stdout.strip(),
        }
    header = lines[0].split("\t")
    if len(header) != 3:
        return {"status": "failed", "illustrator_response": lines[0]}

    fonts = []
    for line in lines[1:]:
        values = line.split("\t")
        if len(values) == 3:
            fonts.append({"postscript_name": values[0], "family": values[1], "style": values[2]})
    installed_names = {font["postscript_name"] for font in fonts}
    missing = [name for name in required if name not in installed_names]
    if query:
        folded_query = query.casefold()
        fonts = [
            font
            for font in fonts
            if folded_query
            in " ".join((font["postscript_name"], font["family"], font["style"])).casefold()
        ]
    return {
        "status": "passed" if not missing else "mismatch",
        "illustrator_version": header[1],
        "total_font_count": int(header[2]),
        "match_count": len(fonts),
        "query": query,
        "required": list(required),
        "missing": missing,
        "fonts": fonts,
    }


def _compare_structure(expected: dict[str, Any], actual: dict[str, Any]) -> dict[str, bool]:
    keys = (
        "layer_count",
        "layer_names",
        "layer_page_item_types",
        "path_item_count",
        "text_frame_count",
        "point_counts",
        "closed_count",
        "filled_count",
        "stroked_count",
        "compound_path_item_count",
        "clipping_group_count",
        "group_item_count",
    )
    return {key: actual.get(key) == expected[key] for key in keys}


def _color_close(
    expected: ProcessColor | None,
    actual: ProcessColor | None,
    *,
    tolerance: float,
) -> bool:
    if expected is None or actual is None:
        return expected is actual
    if type(expected) is not type(actual):
        return False
    if isinstance(expected, Color) and isinstance(actual, Color):
        expected_values = (expected.red, expected.green, expected.blue)
        actual_values = (actual.red, actual.green, actual.blue)
    elif isinstance(expected, CmykColor) and isinstance(actual, CmykColor):
        expected_values = (expected.cyan, expected.magenta, expected.yellow, expected.black)
        actual_values = (actual.cyan, actual.magenta, actual.yellow, actual.black)
    else:
        return False
    return all(
        math.isclose(left, right, rel_tol=0.0, abs_tol=tolerance)
        for left, right in zip(expected_values, actual_values, strict=True)
    )


def _path_geometry_close(expected: AIPath, actual: AIPath, *, tolerance: float) -> bool:
    if len(expected.points) != len(actual.points):
        return False
    expected_origin = expected.points[0]
    actual_origin = actual.points[0]
    for expected_point, actual_point in zip(expected.points, actual.points, strict=True):
        coordinates = (
            (expected_point.x - expected_origin.x, actual_point.x - actual_origin.x),
            (expected_point.y - expected_origin.y, actual_point.y - actual_origin.y),
        )
        if not all(
            math.isclose(left, right, rel_tol=0.0, abs_tol=tolerance) for left, right in coordinates
        ):
            return False
        for expected_handle, actual_handle in (
            (expected_point.in_handle, actual_point.in_handle),
            (expected_point.out_handle, actual_point.out_handle),
        ):
            if expected_handle is None or actual_handle is None:
                if expected_handle is not actual_handle:
                    return False
                continue
            handle_coordinates = (
                (expected_handle.x - expected_point.x, actual_handle.x - actual_point.x),
                (expected_handle.y - expected_point.y, actual_handle.y - actual_point.y),
            )
            if not all(
                math.isclose(left, right, rel_tol=0.0, abs_tol=tolerance)
                for left, right in handle_coordinates
            ):
                return False
        if expected_point.smooth != actual_point.smooth:
            return False
    return True


def _compare_roundtrip_semantics(
    expected: Document,
    actual: Document,
    *,
    tolerance: float = 1 / 255 + 1e-6,
) -> dict[str, bool]:
    expected_paths = _document_paths(expected)
    actual_paths = _document_paths(actual)
    expected_compounds = _document_compound_paths(expected)
    actual_compounds = _document_compound_paths(actual)
    expected_clipping_groups = _document_clipping_groups(expected)
    actual_clipping_groups = _document_clipping_groups(actual)
    expected_groups = _document_groups(expected)
    actual_groups = _document_groups(actual)
    expected_text = _document_text_frames(expected)
    actual_text = _document_text_frames(actual)
    paired_text = list(zip(expected_text, actual_text, strict=False))
    same_text_count = len(expected_text) == len(actual_text)
    paired_paths = list(zip(expected_paths, actual_paths, strict=False))
    same_path_count = len(expected_paths) == len(actual_paths)
    return {
        "layer_count": len(expected.layers) == len(actual.layers),
        "layer_names": [layer.name for layer in expected.layers]
        == [layer.name for layer in actual.layers],
        "layer_visibility": [layer.visible for layer in expected.layers]
        == [layer.visible for layer in actual.layers],
        "layer_item_types": [
            [reference.kind for reference in layer.item_order] for layer in expected.layers
        ]
        == [[reference.kind for reference in layer.item_order] for layer in actual.layers],
        "path_item_count": same_path_count,
        "text_frame_count": same_text_count,
        "text_contents": same_text_count
        and all(left.text == right.text for left, right in paired_text),
        "text_font_sizes": same_text_count
        and all(
            math.isclose(
                left.font_size,
                right.font_size,
                rel_tol=0.0,
                abs_tol=tolerance,
            )
            for left, right in paired_text
        ),
        "text_font_names": same_text_count
        and all(left.font_name == right.font_name for left, right in paired_text),
        "text_alignments": same_text_count
        and all(left.alignment == right.alignment for left, right in paired_text),
        "text_fill_colors": same_text_count
        and all(
            _color_close(left.fill, right.fill, tolerance=tolerance) for left, right in paired_text
        ),
        "text_positions": same_text_count
        and (
            not expected_text
            or all(
                math.isclose(
                    left.x - expected_text[0].x,
                    right.x - actual_text[0].x,
                    rel_tol=0.0,
                    abs_tol=tolerance,
                )
                and math.isclose(
                    left.y - expected_text[0].y,
                    right.y - actual_text[0].y,
                    rel_tol=0.0,
                    abs_tol=tolerance,
                )
                for left, right in paired_text
            )
        ),
        "path_ids": same_path_count and all(left.id == right.id for left, right in paired_paths),
        "path_names": same_path_count
        and all(left.name == right.name for left, right in paired_paths),
        "point_counts": same_path_count
        and all(len(left.points) == len(right.points) for left, right in paired_paths),
        "path_flags": same_path_count
        and all(
            (left.closed, left.fill is not None, left.stroke is not None)
            == (right.closed, right.fill is not None, right.stroke is not None)
            for left, right in paired_paths
        ),
        "path_polarities": same_path_count
        and all(left.polarity == right.polarity for left, right in paired_paths),
        "compound_path_count": len(expected_compounds) == len(actual_compounds),
        "compound_component_counts": len(expected_compounds) == len(actual_compounds)
        and all(
            len(left.paths) == len(right.paths)
            for left, right in zip(expected_compounds, actual_compounds, strict=True)
        ),
        "clipping_group_count": len(expected_clipping_groups) == len(actual_clipping_groups),
        "clipping_content_counts": len(expected_clipping_groups) == len(actual_clipping_groups)
        and all(
            len(left.paths) == len(right.paths)
            for left, right in zip(expected_clipping_groups, actual_clipping_groups, strict=True)
        ),
        "group_item_count": len(expected_groups) == len(actual_groups),
        "group_structure": len(expected_groups) == len(actual_groups)
        and [_group_signature(group) for group in expected_groups]
        == [_group_signature(group) for group in actual_groups],
        "stroke_widths": same_path_count
        and all(
            math.isclose(
                left.stroke_width,
                right.stroke_width,
                rel_tol=0.0,
                abs_tol=tolerance,
            )
            for left, right in paired_paths
        ),
        "dash_patterns": same_path_count
        and all(left.dash_pattern == right.dash_pattern for left, right in paired_paths),
        "dash_offsets": same_path_count
        and all(
            math.isclose(
                left.dash_offset,
                right.dash_offset,
                rel_tol=0.0,
                abs_tol=tolerance,
            )
            for left, right in paired_paths
        ),
        "line_caps": same_path_count
        and all(left.line_cap == right.line_cap for left, right in paired_paths),
        "line_joins": same_path_count
        and all(left.line_join == right.line_join for left, right in paired_paths),
        "miter_limits": same_path_count
        and all(
            math.isclose(
                left.miter_limit,
                right.miter_limit,
                rel_tol=0.0,
                abs_tol=tolerance,
            )
            for left, right in paired_paths
        ),
        "fill_colors": same_path_count
        and all(
            _color_close(left.fill, right.fill, tolerance=tolerance) for left, right in paired_paths
        ),
        "stroke_colors": same_path_count
        and all(
            _color_close(left.stroke, right.stroke, tolerance=tolerance)
            for left, right in paired_paths
        ),
        "path_geometry": same_path_count
        and all(
            _path_geometry_close(left, right, tolerance=tolerance) for left, right in paired_paths
        ),
    }


def run_illustrator_test(
    source: str | Path,
    *,
    timeout: float = 90.0,
    application_name: str = "Adobe Illustrator",
) -> dict[str, Any]:
    """Open a temporary copy in Illustrator, inspect it, and close only that copy."""

    source_path = Path(source).resolve()
    if platform.system() != "Darwin":
        return {
            "status": "environment-unavailable",
            "error": "Illustrator compatibility testing is currently supported on macOS only.",
        }
    if not source_path.is_file():
        return {"status": "invalid-input", "error": f"File does not exist: {source_path}"}
    if timeout <= 0:
        return {"status": "invalid-input", "error": "timeout must be positive"}

    expected = _expected_structure(source_path)
    with tempfile.TemporaryDirectory(prefix="py-ai-illustrator-") as temp_directory:
        temp_path = Path(temp_directory)
        fixture_path = temp_path / f"fixture{source_path.suffix or '.ai'}"
        shutil.copy2(source_path, fixture_path)
        try:
            completed = _execute_javascript(
                _build_javascript(fixture_path),
                temp_path,
                timeout=timeout,
                application_name=application_name,
            )
        except subprocess.TimeoutExpired:
            return {
                "status": "environment-unavailable",
                "error": f"Illustrator did not answer within {timeout:g} seconds.",
                "next_action": (
                    "Open Illustrator manually, sign in to Creative Cloud, finish onboarding, "
                    "and rerun this command after the Home screen is responsive."
                ),
            }

    if completed.returncode != 0:
        return {
            "status": "environment-unavailable",
            "error": completed.stderr.strip() or "Illustrator AppleScript failed.",
            "next_action": (
                "Confirm Illustrator is open, signed in, and responds to its Home screen."
            ),
        }

    try:
        actual = json.loads(completed.stdout.strip())
    except json.JSONDecodeError:
        return {
            "status": "error",
            "error": "Illustrator returned a non-JSON response.",
            "response": completed.stdout.strip(),
        }
    if not actual.get("ok"):
        return {"status": "failed", "illustrator": actual, "expected": expected}

    checks = _compare_structure(expected, actual) if expected is not None else {}
    passed = bool(actual.get("ok")) and all(checks.values())
    return {
        "status": "passed" if passed else "mismatch",
        "input": str(source_path),
        "expected": expected,
        "checks": checks,
        "illustrator": actual,
    }


def materialize_native_ai(
    source: str | Path,
    destination: str | Path,
    *,
    timeout: float = 90.0,
    application_name: str = "Adobe Illustrator",
) -> dict[str, Any]:
    """Convert a legacy AI copy to a modern AI with native editable text."""

    source_path = Path(source).resolve()
    destination_path = Path(destination).resolve()
    if platform.system() != "Darwin":
        return {
            "status": "environment-unavailable",
            "error": "Native AI materialization is currently supported on macOS only.",
        }
    if not source_path.is_file():
        return {"status": "invalid-input", "error": f"File does not exist: {source_path}"}
    if destination_path.exists():
        return {
            "status": "invalid-input",
            "error": f"Refusing to overwrite existing output: {destination_path}",
        }
    if timeout <= 0:
        return {"status": "invalid-input", "error": "timeout must be positive"}
    if inspect_file(source_path).format is not FileFormat.LEGACY_AI:
        return {
            "status": "invalid-input",
            "error": "Native materialization currently accepts legacy AI input only.",
        }
    source_document = load_ai7(source_path)
    expected_justifications = Counter(
        f"Justification.{text.alignment.upper()}" for text in _document_text_frames(source_document)
    )
    dom_ordered_text = _document_text_frames_dom_order(source_document)
    text_notes = tuple(_text_identity_note(text) for text in dom_ordered_text)
    text_contents = tuple(text.text for text in dom_ordered_text)
    desired_font_names = tuple(
        text.native_font_name or ("" if "RKSJ-" in text.font_name else text.font_name)
        for text in dom_ordered_text
    )
    desired_trackings = tuple(text.tracking for text in dom_ordered_text)

    destination_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="py-ai-illustrator-native-") as temp_directory:
        temp_path = Path(temp_directory)
        input_copy = temp_path / "python-generated.ai"
        shutil.copy2(source_path, input_copy)
        try:
            completed = _execute_javascript(
                _build_native_materialization_javascript(
                    input_copy,
                    destination_path,
                    text_notes=text_notes,
                    text_contents=text_contents,
                    desired_font_names=desired_font_names,
                    desired_trackings=desired_trackings,
                ),
                temp_path,
                timeout=timeout,
                application_name=application_name,
            )
        except subprocess.TimeoutExpired:
            return {
                "status": "environment-unavailable",
                "error": f"Illustrator did not answer within {timeout:g} seconds.",
            }

    if completed.returncode != 0:
        return {
            "status": "environment-unavailable",
            "error": completed.stderr.strip() or "Illustrator AppleScript failed.",
        }
    response = completed.stdout.strip()
    if not response.startswith("ok:"):
        return {"status": "failed", "illustrator_response": response}
    if not destination_path.is_file():
        return {
            "status": "failed",
            "error": "Illustrator reported success but did not create the native AI file.",
        }

    parts = response.split(":", 13)
    if len(parts) != 14:
        return {"status": "failed", "illustrator_response": response}
    (
        _,
        version,
        legacy_count,
        native_count,
        converted,
        justifications,
        assigned_notes,
        native_notes,
        identity_content_matches,
        requested_fonts,
        assigned_fonts,
        matching_fonts,
        missing_fonts,
        matching_trackings,
    ) = parts
    legacy_text_count = int(legacy_count)
    native_text_count = int(native_count)
    native_justifications = justifications.split(",") if justifications else []
    assigned_note_count = int(assigned_notes)
    native_note_count = int(native_notes)
    identity_content_match_count = int(identity_content_matches)
    requested_font_count = int(requested_fonts)
    assigned_font_count = int(assigned_fonts)
    matching_font_count = int(matching_fonts)
    missing_font_names = missing_fonts.split(",") if missing_fonts else []
    matching_tracking_count = int(matching_trackings)
    checks = {
        "legacy_conversion_succeeded": converted == "true",
        "text_frame_count": native_text_count == legacy_text_count,
        "paragraph_justifications": Counter(native_justifications) == expected_justifications,
        "text_identity_notes": assigned_note_count == legacy_text_count
        and native_note_count == legacy_text_count,
        "text_identity_mapping": identity_content_match_count == legacy_text_count,
        "requested_fonts_available": assigned_font_count == requested_font_count,
        "native_font_names": matching_font_count == requested_font_count,
        "native_tracking": matching_tracking_count == legacy_text_count,
    }
    return {
        "status": "passed" if all(checks.values()) else "mismatch",
        "input": str(source_path),
        "output": str(destination_path),
        "illustrator_version": version,
        "legacy_text_count": legacy_text_count,
        "native_text_count": native_text_count,
        "native_text_identity_note_count": native_note_count,
        "text_identity_content_match_count": identity_content_match_count,
        "requested_font_count": requested_font_count,
        "assigned_font_count": assigned_font_count,
        "matching_font_count": matching_font_count,
        "missing_fonts": missing_font_names,
        "matching_tracking_count": matching_tracking_count,
        "native_justifications": native_justifications,
        "checks": checks,
        "format": inspect_file(destination_path).to_dict(),
    }


def run_illustrator_export_test(
    *,
    fixture: str = "rgb-rectangle",
    output: str | Path | None = None,
    timeout: float = 90.0,
    application_name: str = "Adobe Illustrator",
) -> dict[str, Any]:
    """Create an AI8 fixture in Illustrator and read it back through the Python IR."""

    if platform.system() != "Darwin":
        return {
            "status": "environment-unavailable",
            "error": "Illustrator export testing is currently supported on macOS only.",
        }
    if timeout <= 0:
        return {"status": "invalid-input", "error": "timeout must be positive"}
    if fixture not in {
        "rgb-rectangle",
        "cmyk-curve",
        "stroke-style",
        "compound-path",
        "clipping-group",
        "group",
        "point-text",
        "unicode-text",
    }:
        return {"status": "invalid-input", "error": f"Unknown fixture: {fixture}"}

    output_path = Path(output).resolve() if output is not None else None
    if output_path is not None and output_path.exists():
        return {
            "status": "invalid-input",
            "error": f"Refusing to overwrite existing output: {output_path}",
        }

    with tempfile.TemporaryDirectory(prefix="py-ai-illustrator-export-") as temp_directory:
        temp_path = Path(temp_directory)
        fixture_path = temp_path / "illustrator-native.ai"
        try:
            completed = _execute_javascript(
                _build_export_javascript(fixture_path, fixture),
                temp_path,
                timeout=timeout,
                application_name=application_name,
            )
        except subprocess.TimeoutExpired:
            return {
                "status": "environment-unavailable",
                "error": f"Illustrator did not answer within {timeout:g} seconds.",
            }
        if completed.returncode != 0:
            return {
                "status": "environment-unavailable",
                "error": completed.stderr.strip() or "Illustrator AppleScript failed.",
            }

        response = completed.stdout.strip()
        if not response.startswith("ok:"):
            return {"status": "failed", "illustrator_response": response}
        if not fixture_path.is_file():
            return {
                "status": "failed",
                "error": "Illustrator reported success but did not create the AI fixture.",
            }
        if output_path is not None:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(fixture_path, output_path)

        format_report = inspect_file(fixture_path)
        format_details = format_report.to_dict()
        format_details["path"] = str(output_path) if output_path is not None else None
        try:
            document = load_ai7(fixture_path)
        except (ValueError, UnicodeError) as error:
            return {
                "status": "failed",
                "illustrator_version": response[3:],
                "format": format_details,
                "reader_error": str(error),
            }

        paths = _document_paths(document)
        path = paths[0] if paths else None
        expected_layer_name = {
            "rgb-rectangle": "Illustrator Native",
            "cmyk-curve": "Illustrator Native Curves",
            "stroke-style": "Illustrator Native Stroke Style",
            "compound-path": "Illustrator Native Compound",
            "clipping-group": "Illustrator Native Clipping",
            "group": "Illustrator Native Group",
            "point-text": "Illustrator Native Text",
            "unicode-text": "Illustrator Native Unicode",
        }[fixture]
        checks: dict[str, bool] = {
            "legacy_ai_detected": format_report.format is FileFormat.LEGACY_AI,
            "artwork_bounds": document.width > 0 and document.height > 0,
            "layer_count": len(document.layers) == 1,
            "layer_name": bool(document.layers) and document.layers[0].name == expected_layer_name,
            "path_item_count": len(paths)
            == (
                2
                if fixture in {"compound-path", "clipping-group", "group"}
                else 0
                if fixture in {"point-text", "unicode-text"}
                else 1
            ),
        }
        if fixture == "rgb-rectangle":
            checks.update(
                {
                    "stroked": path is not None and path.stroke is not None,
                    "point_count": path is not None and len(path.points) == 4,
                    "closed": path is not None and path.closed,
                    "filled": path is not None and path.fill is not None,
                    "stroke_width": path is not None and path.stroke_width == 3.0,
                    "rgb_fill": path is not None and isinstance(path.fill, Color),
                    "rgb_stroke": path is not None and isinstance(path.stroke, Color),
                }
            )
        elif fixture == "cmyk-curve":
            checks.update(
                {
                    "stroked": path is not None and path.stroke is not None,
                    "point_count": path is not None and len(path.points) == 2,
                    "open": path is not None and not path.closed,
                    "unfilled": path is not None and path.fill is None,
                    "stroke_width": path is not None and path.stroke_width == 4.0,
                    "cmyk_stroke": path is not None and isinstance(path.stroke, CmykColor),
                    "bezier_handles": path is not None
                    and path.points[0].out_handle is not None
                    and path.points[1].in_handle is not None,
                }
            )
        elif fixture == "stroke-style":
            checks.update(
                {
                    "dash_pattern": path is not None and path.dash_pattern == [18.0, 8.0, 4.0, 8.0],
                    "dash_offset": path is not None and path.dash_offset == 3.0,
                    "line_cap": path is not None and path.line_cap == "round",
                    "line_join": path is not None and path.line_join == "bevel",
                    "miter_limit": path is not None and path.miter_limit == 7.0,
                }
            )
        elif fixture == "compound-path":
            compound_paths = document.layers[0].compound_paths if document.layers else []
            compound = compound_paths[0] if compound_paths else None
            checks.update(
                {
                    "compound_path_count": len(compound_paths) == 1,
                    "component_count": compound is not None and len(compound.paths) == 2,
                    "component_polarities": compound is not None
                    and [path.polarity for path in compound.paths] == ["positive", "negative"],
                    "filled": compound is not None
                    and all(path.fill is not None for path in compound.paths),
                    "unstroked": compound is not None
                    and all(path.stroke is None for path in compound.paths),
                }
            )
        elif fixture == "clipping-group":
            clipping_groups = document.layers[0].clipping_groups if document.layers else []
            group = clipping_groups[0] if clipping_groups else None
            checks.update(
                {
                    "clipping_group_count": len(clipping_groups) == 1,
                    "content_path_count": group is not None and len(group.paths) == 1,
                    "mask_closed": group is not None and group.clipping_path.closed,
                    "mask_unpainted": group is not None
                    and group.clipping_path.fill is None
                    and group.clipping_path.stroke is None,
                    "content_filled": group is not None and group.paths[0].fill is not None,
                }
            )
        elif fixture == "group":
            groups = document.layers[0].groups if document.layers else []
            group = groups[0] if groups else None
            checks.update(
                {
                    "group_item_count": len(groups) == 1,
                    "group_child_count": group is not None and len(group.item_order) == 2,
                    "group_path_count": group is not None and len(group.paths) == 2,
                }
            )
        elif fixture in {"point-text", "unicode-text"}:
            text_frames = _document_text_frames(document)
            expected_text = "Table Header" if fixture == "point-text" else "日本語の表見出し"
            expected_size = 14.0 if fixture == "point-text" else 16.0
            checks.update(
                {
                    "text_frame_count": bool(text_frames),
                    "text_contents": "".join(text.text for text in text_frames) == expected_text,
                    "font_size": all(text.font_size == expected_size for text in text_frames),
                    "rgb_fill": all(isinstance(text.fill, Color) for text in text_frames),
                }
            )
        passed = all(checks.values())

        return {
            "status": "passed" if passed else "mismatch",
            "fixture": fixture,
            "illustrator_version": response[3:],
            "format": format_details,
            "checks": checks,
            "python_ir": document.to_dict(),
            "output": str(output_path) if output_path is not None else None,
        }


def run_illustrator_roundtrip_test(
    source: str | Path,
    *,
    output: str | Path | None = None,
    timeout: float = 90.0,
    application_name: str = "Adobe Illustrator",
) -> dict[str, Any]:
    """Resave a Python-readable AI file in Illustrator and compare semantic IRs."""

    source_path = Path(source).resolve()
    if platform.system() != "Darwin":
        return {
            "status": "environment-unavailable",
            "error": "Illustrator round-trip testing is currently supported on macOS only.",
        }
    if not source_path.is_file():
        return {"status": "invalid-input", "error": f"File does not exist: {source_path}"}
    if timeout <= 0:
        return {"status": "invalid-input", "error": "timeout must be positive"}
    if inspect_file(source_path).format is not FileFormat.LEGACY_AI:
        return {
            "status": "invalid-input",
            "error": "Round-trip testing currently requires a legacy AI input.",
        }
    try:
        expected = load_ai7(source_path)
    except (ValueError, UnicodeError) as error:
        return {"status": "invalid-input", "error": str(error)}

    output_path = Path(output).resolve() if output is not None else None
    if output_path is not None and output_path.exists():
        return {
            "status": "invalid-input",
            "error": f"Refusing to overwrite existing output: {output_path}",
        }

    with tempfile.TemporaryDirectory(prefix="py-ai-illustrator-roundtrip-") as temp_directory:
        temp_path = Path(temp_directory)
        input_copy = temp_path / "python-generated.ai"
        resaved_path = temp_path / "illustrator-resaved.ai"
        shutil.copy2(source_path, input_copy)
        try:
            completed = _execute_javascript(
                _build_roundtrip_javascript(input_copy, resaved_path),
                temp_path,
                timeout=timeout,
                application_name=application_name,
            )
        except subprocess.TimeoutExpired:
            return {
                "status": "environment-unavailable",
                "error": f"Illustrator did not answer within {timeout:g} seconds.",
            }
        if completed.returncode != 0:
            return {
                "status": "environment-unavailable",
                "error": completed.stderr.strip() or "Illustrator AppleScript failed.",
            }

        response = completed.stdout.strip()
        if not response.startswith("ok:"):
            return {"status": "failed", "illustrator_response": response}
        if not resaved_path.is_file():
            return {
                "status": "failed",
                "error": "Illustrator reported success but did not create the resaved AI file.",
            }
        if output_path is not None:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(resaved_path, output_path)

        format_report = inspect_file(resaved_path)
        format_details = format_report.to_dict()
        format_details["path"] = str(output_path) if output_path is not None else None
        try:
            actual = load_ai7(resaved_path)
        except (ValueError, UnicodeError) as error:
            return {
                "status": "failed",
                "illustrator_version": response[3:],
                "format": format_details,
                "reader_error": str(error),
                "output": str(output_path) if output_path is not None else None,
            }

        checks = _compare_roundtrip_semantics(expected, actual)
        advisory_keys = {"text_font_names", "text_alignments"}
        advisory_checks = {key: checks.pop(key) for key in advisory_keys if key in checks}
        passed = format_report.format is FileFormat.LEGACY_AI and all(checks.values())
        return {
            "status": "passed" if passed else "mismatch",
            "input": str(source_path),
            "illustrator_version": response[3:],
            "format": format_details,
            "checks": {
                "legacy_ai_detected": format_report.format is FileFormat.LEGACY_AI,
                **checks,
            },
            "advisory_checks": advisory_checks,
            "compatibility_notes": (
                [
                    "AI8 legacy point text may substitute font names and normalize "
                    "paragraph alignment; visual placement remains a required check."
                ]
                if advisory_checks and not all(advisory_checks.values())
                else []
            ),
            "expected_ir": expected.to_dict(),
            "roundtrip_ir": actual.to_dict(),
            "output": str(output_path) if output_path is not None else None,
        }
