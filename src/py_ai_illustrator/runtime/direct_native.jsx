#target illustrator
(function () {
    var contract = null;
    var spec = null;
    var destination = null;
    var documentRef = null;
    var previousInteractionLevel = app.userInteractionLevel;
    var previousCoordinateSystem = app.coordinateSystem;
    var errors = [];
    var textOverflowInspections = [];

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

    function resultPayload(payload) {
        var envelope = {
            contract: "py-ai-illustrator.native-compile-result",
            version: 1,
            operation: "compile"
        };
        for (var key in payload) {
            if (payload.hasOwnProperty(key)) envelope[key] = payload[key];
        }
        return toJson(envelope);
    }

    function closeEnough(left, right, tolerance) {
        return Math.abs(left - right) <= tolerance;
    }

    function angleDifference(left, right) {
        var difference = (left - right + 180) % 360;
        if (difference < 0) difference += 360;
        return Math.abs(difference - 180);
    }

    function itemRotation(item) {
        var matrix = item.matrix;
        return Math.atan2(matrix.mValueB, matrix.mValueA) * 180 / Math.PI;
    }

    function normalizedText(value) {
        return String(value).replace(/\r\n/g, "\n").replace(/\r/g, "\n");
    }

    function areaTextOverflows(frame) {
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

    function colorFingerprint(color) {
        if (!color) return null;
        if (color.typename === "RGBColor") {
            return [color.typename, color.red, color.green, color.blue];
        }
        if (color.typename === "CMYKColor") {
            return [color.typename, color.cyan, color.magenta, color.yellow, color.black];
        }
        if (color.typename === "GrayColor") return [color.typename, color.gray];
        return [color.typename];
    }

    function textFrameFingerprint(frame) {
        try {
            var textRange = frame.textRange;
            var attributes = textRange ? textRange.characterAttributes : null;
            var paragraphAttributes = textRange ? textRange.paragraphAttributes : null;
            var matrix = frame.matrix;
            return toJson({
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
                fill_color: attributes ? colorFingerprint(attributes.fillColor) : null,
                justification: paragraphAttributes
                    ? String(paragraphAttributes.justification) : null
            });
        } catch (fingerprintError) {
            return null;
        }
    }

    function makeColor(colorSpec) {
        var color;
        if (colorSpec.type === "rgb") {
            color = new RGBColor();
            color.red = colorSpec.values[0] * 255;
            color.green = colorSpec.values[1] * 255;
            color.blue = colorSpec.values[2] * 255;
        } else {
            color = new CMYKColor();
            color.cyan = colorSpec.values[0] * 100;
            color.magenta = colorSpec.values[1] * 100;
            color.yellow = colorSpec.values[2] * 100;
            color.black = colorSpec.values[3] * 100;
        }
        return color;
    }

    function colorMatches(color, colorSpec) {
        if (colorSpec === null) return color === null;
        var scale = colorSpec.type === "rgb" ? 255 : 100;
        var actual;
        if (colorSpec.type === "rgb") {
            if (color.typename !== "RGBColor") return false;
            actual = [color.red, color.green, color.blue];
        } else {
            if (color.typename !== "CMYKColor") return false;
            actual = [color.cyan, color.magenta, color.yellow, color.black];
        }
        for (var index = 0; index < actual.length; index++) {
            if (!closeEnough(actual[index], colorSpec.values[index] * scale, 0.51)) return false;
        }
        return true;
    }

    function pointMatches(point, pointSpec, reversed) {
        var expectedLeft = reversed ? pointSpec.right : pointSpec.left;
        var expectedRight = reversed ? pointSpec.left : pointSpec.right;
        var pairs = [
            [point.anchor, pointSpec.anchor],
            [point.leftDirection, expectedLeft],
            [point.rightDirection, expectedRight]
        ];
        for (var pairIndex = 0; pairIndex < pairs.length; pairIndex++) {
            if (
                !closeEnough(pairs[pairIndex][0][0], pairs[pairIndex][1][0], 0.01)
                || !closeEnough(pairs[pairIndex][0][1], pairs[pairIndex][1][1], 0.01)
            ) return false;
        }
        return true;
    }

    function pathGeometryMatches(path, pathSpec) {
        var count = path.pathPoints.length;
        var directions = [1, -1];
        for (var directionIndex = 0; directionIndex < directions.length; directionIndex++) {
            var direction = directions[directionIndex];
            var firstStart = pathSpec.closed ? 0 : (direction === 1 ? 0 : count - 1);
            var lastStart = pathSpec.closed ? count - 1 : firstStart;
            for (var start = firstStart; start <= lastStart; start++) {
                var matches = true;
                for (var index = 0; index < count; index++) {
                    var expectedIndex = (start + direction * index + count) % count;
                    if (!pointMatches(
                        path.pathPoints[index],
                        pathSpec.points[expectedIndex],
                        direction === -1
                    )) {
                        matches = false;
                        break;
                    }
                }
                if (matches) return true;
            }
        }
        return false;
    }

    function createPath(parent, pathSpec) {
        var path = parent.pathItems.add();
        path.name = pathSpec.name;
        path.note = pathSpec.note;
        for (var index = 0; index < pathSpec.points.length; index++) {
            var pointSpec = pathSpec.points[index];
            var point = path.pathPoints.add();
            point.anchor = pointSpec.anchor;
            point.leftDirection = pointSpec.left;
            point.rightDirection = pointSpec.right;
            point.pointType = pointSpec.smooth ? PointType.SMOOTH : PointType.CORNER;
        }
        path.closed = pathSpec.closed;
        path.filled = pathSpec.fill !== null;
        if (path.filled) path.fillColor = makeColor(pathSpec.fill);
        path.stroked = pathSpec.stroke !== null;
        if (path.stroked) {
            path.strokeColor = makeColor(pathSpec.stroke);
            path.strokeWidth = pathSpec.stroke_width;
            path.strokeDashes = pathSpec.dash_pattern;
            path.strokeDashOffset = pathSpec.dash_offset;
            path.strokeCap = {
                butt: StrokeCap.BUTTENDCAP,
                round: StrokeCap.ROUNDENDCAP,
                projecting: StrokeCap.PROJECTINGENDCAP
            }[pathSpec.line_cap];
            path.strokeJoin = {
                miter: StrokeJoin.MITERENDJOIN,
                round: StrokeJoin.ROUNDENDJOIN,
                bevel: StrokeJoin.BEVELENDJOIN
            }[pathSpec.line_join];
            path.strokeMiterLimit = pathSpec.miter_limit;
        }
        try {
            path.polarity = pathSpec.polarity === "negative"
                ? PolarityValues.NEGATIVE
                : PolarityValues.POSITIVE;
        } catch (polarityError) {}
        return path;
    }

    function createText(parent, textSpec) {
        var frame;
        if (textSpec.area_width !== null) {
            var textPath = parent.pathItems.rectangle(
                textSpec.y,
                textSpec.x,
                textSpec.area_width,
                textSpec.area_height
            );
            frame = documentRef.textFrames.areaText(textPath);
        } else {
            frame = parent.textFrames.pointText([textSpec.x, textSpec.y]);
        }
        ensureParent(frame, parent, textSpec.id);
        frame.name = textSpec.name;
        frame.note = textSpec.note;
        frame.contents = textSpec.contents;
        var attributes = frame.textRange.characterAttributes;
        attributes.textFont = app.textFonts.getByName(textSpec.font_name);
        attributes.size = textSpec.font_size;
        attributes.tracking = textSpec.tracking;
        attributes.fillColor = makeColor(textSpec.fill);
        if (textSpec.leading !== null) {
            attributes.autoLeading = false;
            attributes.leading = textSpec.leading;
        }
        frame.textRange.paragraphAttributes.justification = {
            left: Justification.LEFT,
            center: Justification.CENTER,
            right: Justification.RIGHT
        }[textSpec.alignment];
        if (Math.abs(textSpec.rotation) > 0.0001) {
            var position = [frame.position[0], frame.position[1]];
            frame.rotate(textSpec.rotation);
            frame.position = position;
        }
        return frame;
    }

    function createImage(parent, imageSpec) {
        var imageFile = new File(imageSpec.file);
        if (!imageFile.exists) throw new Error("Linked image does not exist: " + imageSpec.file);
        var image = documentRef.placedItems.add();
        image.file = imageFile;
        ensureParent(image, parent, imageSpec.id);
        image.name = imageSpec.name;
        image.note = imageSpec.note;
        image.width = imageSpec.width;
        image.height = imageSpec.height;
        image.position = [imageSpec.x, imageSpec.y];
        if (Math.abs(imageSpec.rotation) > 0.0001) {
            var position = [image.position[0], image.position[1]];
            image.rotate(-imageSpec.rotation);
            image.position = position;
        }
        return image;
    }

    function ensureParent(item, parent, itemId) {
        if (item.parent !== parent) {
            item.move(parent, ElementPlacement.PLACEATBEGINNING);
        }
        if (item.parent !== parent) {
            throw new Error("Could not preserve native parent for " + itemId);
        }
        return item;
    }

    function createCompound(parent, compoundSpec) {
        var compound = parent.compoundPathItems.add();
        compound.name = compoundSpec.name;
        compound.note = compoundSpec.note;
        for (var index = 0; index < compoundSpec.paths.length; index++) {
            createPath(compound, compoundSpec.paths[index]);
        }
        return compound;
    }

    function createClippingGroup(parent, clippingSpec) {
        var group = parent.groupItems.add();
        group.name = clippingSpec.name;
        group.note = clippingSpec.note;
        for (var index = 0; index < clippingSpec.paths.length; index++) {
            createPath(group, clippingSpec.paths[index]);
        }
        var clippingPath = createPath(group, clippingSpec.clipping_path);
        clippingPath.clipping = true;
        group.clipped = true;
        return group;
    }

    function createItems(parent, items) {
        for (var index = 0; index < items.length; index++) {
            var itemSpec = items[index];
            if (itemSpec.kind === "path") createPath(parent, itemSpec);
            else if (itemSpec.kind === "text") createText(parent, itemSpec);
            else if (itemSpec.kind === "image") createImage(parent, itemSpec);
            else if (itemSpec.kind === "compound_path") createCompound(parent, itemSpec);
            else if (itemSpec.kind === "clipping_group") createClippingGroup(parent, itemSpec);
            else if (itemSpec.kind === "group") {
                var group = parent.groupItems.add();
                group.name = itemSpec.name;
                group.note = itemSpec.note;
                createItems(group, itemSpec.items);
            } else throw new Error("Unsupported item kind: " + itemSpec.kind);
        }
    }

    function expectedType(kind) {
        if (kind === "path") return "PathItem";
        if (kind === "text") return "TextFrame";
        if (kind === "image") return "PlacedItem";
        if (kind === "compound_path") return "CompoundPathItem";
        return "GroupItem";
    }

    function directPageItems(container) {
        var result = [];
        for (var index = 0; index < container.pageItems.length; index++) {
            var item = container.pageItems[index];
            if (item.parent === container) result.push(item);
        }
        return result;
    }

    function pathMismatch(path, pathSpec) {
        if (path.pathPoints.length !== pathSpec.points.length) return "point count";
        if (path.closed !== pathSpec.closed) return "closed flag";
        if (path.filled !== (pathSpec.fill !== null)) return "filled flag";
        if (path.stroked !== (pathSpec.stroke !== null)) return "stroked flag";
        if (path.filled && !colorMatches(path.fillColor, pathSpec.fill)) return "fill color";
        if (path.stroked) {
            if (!colorMatches(path.strokeColor, pathSpec.stroke)) return "stroke color";
            if (!closeEnough(path.strokeWidth, pathSpec.stroke_width, 0.01)) {
                return "stroke width " + path.strokeWidth;
            }
            if (!closeEnough(path.strokeDashOffset, pathSpec.dash_offset, 0.01)) {
                return "dash offset " + path.strokeDashOffset;
            }
            var actualDashLength = typeof path.strokeDashes.length === "number"
                ? path.strokeDashes.length
                : 0;
            if (actualDashLength !== pathSpec.dash_pattern.length) return "dash count";
            for (var dashIndex = 0; dashIndex < path.strokeDashes.length; dashIndex++) {
                if (!closeEnough(
                    path.strokeDashes[dashIndex],
                    pathSpec.dash_pattern[dashIndex],
                    0.01
                )) return "dash value";
            }
            var expectedCap = {
                butt: "StrokeCap.BUTTENDCAP",
                round: "StrokeCap.ROUNDENDCAP",
                projecting: "StrokeCap.PROJECTINGENDCAP"
            }[pathSpec.line_cap];
            if (String(path.strokeCap) !== expectedCap) return "line cap";
            var expectedJoin = {
                miter: "StrokeJoin.MITERENDJOIN",
                round: "StrokeJoin.ROUNDENDJOIN",
                bevel: "StrokeJoin.BEVELENDJOIN"
            }[pathSpec.line_join];
            if (String(path.strokeJoin) !== expectedJoin) return "line join";
            if (!closeEnough(path.strokeMiterLimit, pathSpec.miter_limit, 0.01)) {
                return "miter limit";
            }
        }
        if (!pathGeometryMatches(path, pathSpec)) return "point geometry";
        return null;
    }

    function textMismatch(frame, textSpec) {
        var fingerprintBefore = textFrameFingerprint(frame);
        var overflow = areaTextOverflows(frame);
        var fingerprintAfter = textFrameFingerprint(frame);
        var inspectionPreserved = fingerprintBefore !== null
            && fingerprintBefore === fingerprintAfter;
        textOverflowInspections.push({
            id: textSpec.id,
            overflows: overflow,
            inspection_preserved: inspectionPreserved
        });
        if (!inspectionPreserved) return "overflow inspection changed text frame";
        var actualContents = frame.kind === TextType.AREATEXT && frame.story
            ? frame.story.textRange.contents : frame.contents;
        if (normalizedText(actualContents) !== normalizedText(textSpec.contents)) return "contents";
        var attributes = frame.textRange.characterAttributes;
        if (attributes.textFont.name !== textSpec.font_name) return "font";
        if (!closeEnough(attributes.size, textSpec.font_size, 0.01)) return "font size";
        if (!closeEnough(attributes.tracking, textSpec.tracking, 0.01)) return "tracking";
        if (!colorMatches(attributes.fillColor, textSpec.fill)) return "fill color";
        if (
            textSpec.leading !== null
            && !closeEnough(attributes.leading, textSpec.leading, 0.01)
        ) return "leading";
        var justification = String(frame.textRange.paragraphAttributes.justification);
        if (justification !== "Justification." + textSpec.alignment.toUpperCase()) {
            return "justification";
        }
        if (angleDifference(itemRotation(frame), textSpec.rotation) > 0.01) return "rotation";
        if (textSpec.area_width !== null) {
            if (frame.kind !== TextType.AREATEXT) return "text kind";
            if (!closeEnough(frame.width, textSpec.area_width, 0.1)) return "width";
            if (!closeEnough(frame.height, textSpec.area_height, 0.1)) return "height";
            if (!closeEnough(frame.position[0], textSpec.x, 0.1)) return "x position";
            if (!closeEnough(frame.position[1], textSpec.y, 0.1)) return "y position";
            if (overflow !== false) return "area text overflow " + String(overflow);
        } else {
            if (frame.kind !== TextType.POINTTEXT) return "text kind";
            if (Math.abs(textSpec.rotation) <= 0.0001) {
                if (!closeEnough(frame.anchor[0], textSpec.x, 0.1)) return "anchor x";
                if (!closeEnough(frame.anchor[1], textSpec.y, 0.1)) return "anchor y";
            }
        }
        return null;
    }

    function imageMismatch(image, imageSpec) {
        var expectedFile = new File(imageSpec.file);
        if (!image.file.exists) return "linked file does not exist";
        if (image.file.fsName !== expectedFile.fsName) return "linked file path";
        if (!closeEnough(image.position[0], imageSpec.x, 0.1)) {
            return "x position " + image.position[0];
        }
        if (!closeEnough(image.position[1], imageSpec.y, 0.1)) {
            return "y position " + image.position[1];
        }
        if (!closeEnough(image.width, imageSpec.dom_width, 0.1)) return "width " + image.width;
        if (!closeEnough(image.height, imageSpec.dom_height, 0.1)) {
            return "height " + image.height;
        }
        if (angleDifference(itemRotation(image), imageSpec.rotation) > 0.01) {
            return "rotation " + itemRotation(image);
        }
        return null;
    }

    function verifyCompound(compound, compoundSpec, path) {
        if (compound.pathItems.length !== compoundSpec.paths.length) {
            errors.push(path + ": compound component count mismatch for " + compoundSpec.id);
            return;
        }
        for (var index = 0; index < compoundSpec.paths.length; index++) {
            var pathSpec = compoundSpec.paths[compoundSpec.paths.length - index - 1];
            var component = compound.pathItems[index];
            if (component.note !== pathSpec.note) {
                errors.push(path + ": compound identity mismatch for " + pathSpec.id);
            }
            var reason = pathMismatch(component, pathSpec);
            if (reason !== null) {
                errors.push(
                    path + ": compound path mismatch for " + pathSpec.id
                    + " (" + reason + ")"
                );
            }
        }
    }

    function verifyContainer(container, items, path) {
        var actual = directPageItems(container);
        if (actual.length !== items.length) {
            errors.push(path + ": item count " + actual.length + " != " + items.length);
            return;
        }
        for (var index = 0; index < items.length; index++) {
            var itemSpec = items[items.length - index - 1];
            var item = actual[index];
            if (item.typename !== expectedType(itemSpec.kind)) {
                errors.push(
                    path + ": typename mismatch for " + itemSpec.id
                    + " (actual " + item.typename + ", note " + item.note + ")"
                );
                continue;
            }
            if (item.note !== itemSpec.note) {
                errors.push(path + ": identity mismatch for " + itemSpec.id);
            }
            if (item.name !== itemSpec.name) {
                errors.push(path + ": name mismatch for " + itemSpec.id);
            }
            if (itemSpec.kind === "path") {
                var pathReason = pathMismatch(item, itemSpec);
                if (pathReason !== null) {
                    errors.push(
                        path + ": path attributes mismatch for " + itemSpec.id
                        + " (" + pathReason + ")"
                    );
                }
            } else if (itemSpec.kind === "text") {
                var textReason = textMismatch(item, itemSpec);
                if (textReason !== null) {
                    errors.push(
                        path + ": text attributes mismatch for " + itemSpec.id
                        + " (" + textReason + ")"
                    );
                }
            } else if (itemSpec.kind === "image") {
                var imageReason = imageMismatch(item, itemSpec);
                if (imageReason !== null) {
                    errors.push(
                        path + ": linked image mismatch for " + itemSpec.id
                        + " (" + imageReason + ")"
                    );
                }
            } else if (itemSpec.kind === "group") {
                verifyContainer(item, itemSpec.items, path + "/" + itemSpec.id);
            } else if (itemSpec.kind === "compound_path") {
                verifyCompound(item, itemSpec, path);
            } else if (itemSpec.kind === "clipping_group") {
                var clippingItems = itemSpec.paths.slice(0);
                clippingItems.push(itemSpec.clipping_path);
                verifyContainer(item, clippingItems, path + "/" + itemSpec.id);
            }
        }
    }

    function verifyDocument() {
        if (documentRef.layers.length !== spec.layers.length) {
            errors.push("layer count mismatch");
        }
        for (var layerIndex = 0; layerIndex < spec.layers.length; layerIndex++) {
            var layerSpec = spec.layers[layerIndex];
            var layer = documentRef.layers[layerIndex];
            if (layer.name !== layerSpec.name) errors.push("layer name mismatch: " + layerSpec.id);
            if (layer.visible !== layerSpec.visible) {
                errors.push("layer visibility mismatch: " + layerSpec.id);
            }
            if (layer.locked !== layerSpec.locked) {
                errors.push("layer lock mismatch: " + layerSpec.id);
            }
            verifyContainer(layer, layerSpec.items, "layer:" + layerSpec.id);
        }
        if (documentRef.artboards.length !== spec.artboards.length) {
            errors.push("artboard count mismatch");
        }
        for (var artboardIndex = 0; artboardIndex < spec.artboards.length; artboardIndex++) {
            var artboardSpec = spec.artboards[artboardIndex];
            var artboard = documentRef.artboards[artboardIndex];
            if (artboard.name !== artboardSpec.name) {
                errors.push("artboard name mismatch: " + artboardSpec.id);
            }
            var rect = artboard.artboardRect;
            for (var rectIndex = 0; rectIndex < 4; rectIndex++) {
                if (!closeEnough(rect[rectIndex], artboardSpec.rect[rectIndex], 0.01)) {
                    errors.push("artboard geometry mismatch: " + artboardSpec.id);
                    break;
                }
            }
        }
        return {
            structure_and_order: errors.length === 0,
            stable_identity: errors.length === 0,
            geometry_and_style: errors.length === 0,
            linked_resources: errors.length === 0,
            native_editability: documentRef.legacyTextItems.length === 0,
            pdf_compatible_ai: destination.exists
        };
    }

    try {
        var requestFile = new File(
            new File($.fileName).parent.fsName + "/py-ai-native-request.json"
        );
        if (!requestFile.exists) throw new Error("Native compile request file does not exist");
        requestFile.encoding = "UTF-8";
        if (!requestFile.open("r")) throw new Error("Could not open native compile request file");
        var requestSource = requestFile.read();
        requestFile.close();
        if (typeof JSON !== "undefined" && JSON.parse) {
            contract = JSON.parse(requestSource);
        } else {
            contract = eval("(" + requestSource + ")");
        }
        if (
            !contract
            || contract.contract !== "py-ai-illustrator.native-compile"
            || contract.version !== 1
            || contract.operation !== "compile"
            || typeof contract.destination !== "string"
            || !contract.document
        ) throw new Error("Unsupported native compile request contract");
        spec = contract.document;
        destination = new File(contract.destination);
        app.userInteractionLevel = UserInteractionLevel.DONTDISPLAYALERTS;
        app.coordinateSystem = CoordinateSystem.DOCUMENTCOORDINATESYSTEM;
        if (destination.exists) throw new Error("Temporary destination already exists");

        var colorSpace = spec.color_space === "cmyk"
            ? DocumentColorSpace.CMYK
            : DocumentColorSpace.RGB;
        documentRef = app.documents.add(colorSpace, spec.width, spec.height);

        while (documentRef.artboards.length > 1) {
            documentRef.artboards.remove(documentRef.artboards.length - 1);
        }
        for (var artboardIndex = 0; artboardIndex < spec.artboards.length; artboardIndex++) {
            var artboardSpec = spec.artboards[artboardIndex];
            var artboard = artboardIndex === 0
                ? documentRef.artboards[0]
                : documentRef.artboards.add(artboardSpec.rect);
            artboard.artboardRect = artboardSpec.rect;
            artboard.name = artboardSpec.name;
        }

        while (documentRef.layers.length > 1) documentRef.layers[0].remove();
        for (var layerIndex = spec.layers.length - 1; layerIndex >= 0; layerIndex--) {
            var layerSpec = spec.layers[layerIndex];
            var layer = layerIndex === spec.layers.length - 1
                ? documentRef.layers[0]
                : documentRef.layers.add();
            layer.name = layerSpec.name;
            createItems(layer, layerSpec.items);
            layer.visible = layerSpec.visible;
            layer.locked = layerSpec.locked;
        }

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
        for (var key in checks) {
            if (checks.hasOwnProperty(key) && !checks[key]) passed = false;
        }
        return resultPayload({
            ok: passed,
            illustrator_version: app.version,
            checks: checks,
            errors: errors,
            text_overflows: textOverflowInspections,
            counts: {
                layers: documentRef.layers.length,
                artboards: documentRef.artboards.length,
                groups: documentRef.groupItems.length,
                paths: documentRef.pathItems.length,
                texts: documentRef.textFrames.length,
                linked_images: documentRef.placedItems.length,
                legacy_texts: documentRef.legacyTextItems.length
            }
        });
    } catch (error) {
        return resultPayload({
            ok: false,
            error: String(error),
            line: error.line || null,
            errors: errors,
            text_overflows: textOverflowInspections
        });
    } finally {
        if (documentRef !== null) documentRef.close(SaveOptions.DONOTSAVECHANGES);
        app.coordinateSystem = previousCoordinateSystem;
        app.userInteractionLevel = previousInteractionLevel;
    }
}());
