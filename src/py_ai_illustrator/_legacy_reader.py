"""Conservative reader and semantic diagnostics for legacy Illustrator data."""

from __future__ import annotations

import base64
import json
import math
import re
from contextlib import suppress
from typing import Literal

from ._legacy_codec import (
    UnsupportedLegacyFeature,
    _number,
    _parse_path_note,
    _structured_resource_node_types,
    _structured_resource_supported,
    _unescape_postscript_string,
    _unescape_postscript_text,
)
from .compatibility import (
    LegacyDiagnostic,
    LegacyFieldOrigin,
    LegacyNodeOrigin,
    LegacyReadResult,
    analyze_legacy_source,
)
from .lossless import LegacySource, tokenize_legacy
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


def _semantic_legacy_diagnostics(
    source: LegacySource, origins: tuple[LegacyNodeOrigin, ...]
) -> tuple[LegacyDiagnostic, ...]:
    """Find known operators whose syntax or context is not modeled without loss."""

    diagnostics: list[LegacyDiagnostic] = []
    in_text = False
    path_open = False
    text_font: tuple[str, str] | None = None
    text_alignment = "0"
    fill_signature: tuple[str, ...] | None = None
    text_run_signatures: set[tuple[object, ...]] = set()
    text_start: tuple[int, int, int, str] | None = None
    path_start: tuple[int, int, int, str] | None = None
    group_starts: list[tuple[int, int, int, str]] = []
    compound_start: tuple[int, int, int, str] | None = None
    clipping_start: tuple[int, int, int, str] | None = None
    layer_start: tuple[int, int, int, str] | None = None
    layer_locked = False

    def add(
        token_start: int,
        token_end: int,
        line_number: int,
        name: str,
        message: str,
        *,
        feature_kind: Literal["operator", "resource"] = "operator",
        code: str = "unmodeled-operator-semantics",
    ) -> None:
        diagnostics.append(
            LegacyDiagnostic(
                code=code,
                severity="warning",
                message=message,
                line_number=line_number,
                start=token_start,
                end=token_end,
                feature_kind=feature_kind,
                feature_name=name,
            )
        )

    for token in source.lines:
        if token.kind == "comment":
            line = source.line_content(token).decode("latin-1").strip()
            resource_supported = _structured_resource_supported(line)
            if resource_supported is False:
                add(
                    token.start,
                    token.end,
                    token.line_number,
                    line.split(":", 1)[0].split()[0],
                    "Recognized legacy resource contains metadata the current IR cannot preserve.",
                    feature_kind="resource",
                    code="unmodeled-resource-semantics",
                )
            elif resource_supported is True:
                expected_node_types = _structured_resource_node_types(line)
                if expected_node_types is not None and not any(
                    origin.node_type in expected_node_types
                    and origin.start <= token.start
                    and token.end <= origin.end
                    for origin in origins
                ):
                    add(
                        token.start,
                        token.end,
                        token.line_number,
                        line.split(":", 1)[0].split()[0],
                        "Recognized legacy metadata is not attached to a modeled IR node.",
                        feature_kind="resource",
                        code="unmodeled-resource-semantics",
                    )
            if line == "%AI5_BeginLayer":
                if layer_start is not None:
                    add(
                        token.start,
                        token.end,
                        token.line_number,
                        "%AI5_BeginLayer",
                        "A legacy layer begins before the previous layer is closed.",
                        feature_kind="resource",
                        code="unmodeled-resource-semantics",
                    )
                else:
                    layer_start = (token.start, token.end, token.line_number, "%AI5_BeginLayer")
            elif line == "%AI5_EndLayer":
                if layer_start is None:
                    add(
                        token.start,
                        token.end,
                        token.line_number,
                        "%AI5_EndLayer",
                        "A legacy layer close has no matching begin resource.",
                        feature_kind="resource",
                        code="unmodeled-resource-semantics",
                    )
                else:
                    layer_start = None
            continue
        if token.kind != "statement":
            continue
        raw_operator = source.operator(token)
        if raw_operator is None:
            continue
        operator = raw_operator.decode("latin-1")
        line = source.line_content(token).decode("latin-1").strip()
        supported = True

        if operator == "To":
            supported = line == "0 To" and not in_text and not path_open
            if supported:
                in_text = True
                text_start = (token.start, token.end, token.line_number, operator)
                text_font = None
                text_alignment = "0"
                text_run_signatures = set()
        elif operator == "TO":
            supported = line == "TO" and in_text
            if supported:
                if len(text_run_signatures) > 1:
                    add(
                        token.start,
                        token.end,
                        token.line_number,
                        operator,
                        "Text uses multiple styled Tx runs that the current IR cannot represent.",
                    )
                in_text = False
                text_start = None
        elif operator == "Tp":
            supported = in_text and _TEXT_POSITION_RE.fullmatch(line) is not None
        elif operator == "Tm":
            supported = in_text and _TEXT_MATRIX_RE.fullmatch(line) is not None
        elif operator == "Tf":
            match = _TEXT_FONT_RE.fullmatch(line) if in_text else None
            supported = match is not None
            if match is not None:
                text_font = (match.group(1), _number(float(match.group(2))))
        elif operator == "Ta":
            match = _TEXT_ALIGNMENT_RE.fullmatch(line) if in_text else None
            supported = match is not None and match.group(1) in {"0", "1", "2"}
            if supported and match is not None:
                text_alignment = match.group(1)
        elif operator == "Tx":
            match = _TEXT_CONTENT_RE.fullmatch(line) if in_text else None
            supported = match is not None
            if supported:
                text_run_signatures.add((text_font, text_alignment, fill_signature))
        elif operator == "TP":
            supported = in_text and line == "TP"
        elif operator == "Tr":
            supported = in_text and line == "0 Tr"
        elif operator in {"Xa", "XA"}:
            rgb_match = _COLOR_RE.fullmatch(line)
            ai8_match = _AI8_RGB_COLOR_RE.fullmatch(line)
            match = rgb_match or ai8_match
            supported = match is not None
            if match is not None and operator == "Xa":
                if rgb_match is not None:
                    components = rgb_match.groups()[:3]
                elif ai8_match is not None:
                    components = ai8_match.groups()[4:7]
                else:
                    raise AssertionError("matched RGB operator has no recognized form")
                fill_signature = tuple(_number(float(value)) for value in components)
        elif operator in {"k", "K"}:
            match = _CMYK_COLOR_RE.fullmatch(line)
            supported = match is not None
            if match is not None and operator == "k":
                fill_signature = tuple(_number(float(value)) for value in match.groups()[:4])
        elif operator == "m":
            supported = not in_text and not path_open and _POINT_RE.fullmatch(line) is not None
            if supported:
                path_open = True
                path_start = (token.start, token.end, token.line_number, operator)
        elif operator in {"l", "L"}:
            supported = path_open and _POINT_RE.fullmatch(line) is not None
        elif operator in {"c", "C"}:
            supported = path_open and _CUBIC_RE.fullmatch(line) is not None
        elif operator in {"v", "V", "y", "Y"}:
            supported = path_open and _SHORT_CUBIC_RE.fullmatch(line) is not None
        elif operator in {"b", "f", "s", "n", "B", "F", "S", "N"}:
            supported = path_open and line == operator
            if supported:
                path_open = False
                path_start = None
        elif operator in {"h", "H", "W"}:
            supported = path_open and line == operator
        elif operator == "w":
            supported = re.fullmatch(rf"{_NUMBER}\s+w", line) is not None
        elif operator == "J":
            supported = re.fullmatch(r"[012]\s+J", line) is not None
        elif operator == "j":
            supported = re.fullmatch(r"[012]\s+j", line) is not None
        elif operator == "M":
            supported = re.fullmatch(rf"{_NUMBER}\s+M", line) is not None
        elif operator == "d":
            supported = _DASH_RE.fullmatch(line) is not None
        elif operator == "D":
            supported = _POLARITY_RE.fullmatch(line) is not None
        elif operator == "A":
            match = re.fullmatch(r"([01])\s+A", line)
            supported = (
                not in_text
                and not path_open
                and match is not None
                and (match.group(1) == "1") == layer_locked
            )
        elif operator == "Lb":
            match = _LAYER_RE.fullmatch(line)
            supported = (
                match is not None and layer_start is not None and not in_text and not path_open
            )
            if match is not None:
                layer_locked = match.group(2) == "0"
        elif operator == "Ln":
            supported = (
                layer_start is not None
                and not in_text
                and not path_open
                and _LAYER_NAME_RE.fullmatch(line) is not None
            )
        elif operator == "u":
            supported = line == operator and not in_text and not path_open
            if supported:
                group_starts.append((token.start, token.end, token.line_number, operator))
        elif operator == "U":
            supported = line == operator and bool(group_starts) and not in_text and not path_open
            if supported:
                group_starts.pop()
        elif operator == "*u":
            supported = (
                line == operator and compound_start is None and not in_text and not path_open
            )
            if supported:
                compound_start = (token.start, token.end, token.line_number, operator)
        elif operator == "*U":
            supported = (
                line == operator and compound_start is not None and not in_text and not path_open
            )
            if supported:
                compound_start = None
        elif operator == "q":
            supported = (
                line == operator and clipping_start is None and not in_text and not path_open
            )
            if supported:
                clipping_start = (token.start, token.end, token.line_number, operator)
        elif operator == "Q":
            supported = (
                line == operator and clipping_start is not None and not in_text and not path_open
            )
            if supported:
                clipping_start = None
        elif operator == "LB":
            supported = (
                line == operator and layer_start is not None and not in_text and not path_open
            )
        elif operator == "TZ":
            supported = re.fullmatch(r"\[/\S+/\S+\s+0\s+1\s+0\s+TZ", line) is not None

        if not supported:
            add(
                token.start,
                token.end,
                token.line_number,
                operator,
                f"Legacy operator {operator!r} uses syntax or context not represented by the IR.",
            )
    open_constructs = [
        item
        for item in (text_start, path_start, compound_start, clipping_start, layer_start)
        if item is not None
    ]
    for start, end, line_number, feature_name in [*open_constructs, *group_starts]:
        feature_kind: Literal["operator", "resource"] = (
            "resource" if feature_name.startswith("%") else "operator"
        )
        add(
            start,
            end,
            line_number,
            feature_name,
            f"Legacy construct beginning with {feature_name!r} is not terminated.",
            feature_kind=feature_kind,
            code=(
                "unmodeled-resource-semantics"
                if feature_kind == "resource"
                else "unmodeled-operator-semantics"
            ),
        )
    return tuple(diagnostics)


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
    pending_path_source_start: int | None = None
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
    current_text_position_origin: LegacyFieldOrigin | None = None
    current_text_matrix_origin: LegacyFieldOrigin | None = None
    pending_text_id: str | None = None
    pending_text_name: str | None = None
    pending_text_source_start: int | None = None
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
            pending_path_source_start = pending_path_source_start or token.start
            pending_id = _unescape_postscript_string(line[11:-1])
            continue
        if line.startswith("%%py-ai-path-id-utf8: "):
            pending_path_source_start = pending_path_source_start or token.start
            with suppress(ValueError, UnicodeError):
                pending_id = base64.b64decode(
                    line.removeprefix("%%py-ai-path-id-utf8: "), validate=True
                ).decode("utf-8")
            continue
        if line.startswith("%%py-ai-path-name: (") and line.endswith(")"):
            pending_path_source_start = pending_path_source_start or token.start
            pending_name = _unescape_postscript_string(line[20:-1])
            continue
        if line.startswith("%%py-ai-path-name-utf8: "):
            pending_path_source_start = pending_path_source_start or token.start
            with suppress(ValueError, UnicodeError):
                pending_name = base64.b64decode(
                    line.removeprefix("%%py-ai-path-name-utf8: "), validate=True
                ).decode("utf-8")
            continue
        if line.startswith("%AI3_Note:"):
            note_id, note_name = _parse_path_note(line[10:].lstrip())
            if note_id is not None or note_name is not None:
                pending_path_source_start = pending_path_source_start or token.start
            if note_id is not None:
                pending_id = note_id
            if note_name is not None:
                pending_name = note_name
            continue
        if line.startswith("%%py-ai-text-id: (") and line.endswith(")"):
            pending_text_source_start = pending_text_source_start or token.start
            value = line.removeprefix("%%py-ai-text-id: (")[:-1]
            pending_text_id = _unescape_postscript_string(value)
            continue
        if line.startswith("%%py-ai-text-id-utf8: "):
            pending_text_source_start = pending_text_source_start or token.start
            with suppress(ValueError, UnicodeError):
                pending_text_id = base64.b64decode(
                    line.removeprefix("%%py-ai-text-id-utf8: "), validate=True
                ).decode("utf-8")
            continue
        if line.startswith("%%py-ai-text-name: (") and line.endswith(")"):
            pending_text_source_start = pending_text_source_start or token.start
            value = line.removeprefix("%%py-ai-text-name: (")[:-1]
            pending_text_name = _unescape_postscript_string(value)
            continue
        if line.startswith("%%py-ai-text-name-utf8: "):
            pending_text_source_start = pending_text_source_start or token.start
            with suppress(ValueError, UnicodeError):
                pending_text_name = base64.b64decode(
                    line.removeprefix("%%py-ai-text-name-utf8: "), validate=True
                ).decode("utf-8")
            continue
        if line.startswith("%%py-ai-text-alignment: (") and line.endswith(")"):
            pending_text_source_start = pending_text_source_start or token.start
            value = line.removeprefix("%%py-ai-text-alignment: (")[:-1]
            candidate = _unescape_postscript_string(value)
            if candidate in {"left", "center", "right"}:
                pending_text_alignment = candidate
            continue
        if line.startswith("%%py-ai-text-native-font: (") and line.endswith(")"):
            pending_text_source_start = pending_text_source_start or token.start
            value = line.removeprefix("%%py-ai-text-native-font: (")[:-1]
            pending_text_native_font_name = _unescape_postscript_string(value)
            continue
        if line.startswith("%%py-ai-text-tracking: "):
            pending_text_source_start = pending_text_source_start or token.start
            with suppress(ValueError):
                pending_text_tracking = float(line.removeprefix("%%py-ai-text-tracking: "))
            continue
        if line.startswith("%%py-ai-text-rotation: "):
            pending_text_source_start = pending_text_source_start or token.start
            with suppress(ValueError):
                pending_text_rotation = float(line.removeprefix("%%py-ai-text-rotation: "))
            continue
        if line.startswith("%%py-ai-text-area: "):
            pending_text_source_start = pending_text_source_start or token.start
            values = line.removeprefix("%%py-ai-text-area: ").split()
            if len(values) == 2:
                with suppress(ValueError):
                    area_width, area_height = map(float, values)
                    if area_width > 0 and area_height > 0:
                        pending_text_area_width = area_width
                        pending_text_area_height = area_height
            continue
        if line.startswith("%%py-ai-text-leading: "):
            pending_text_source_start = pending_text_source_start or token.start
            with suppress(ValueError):
                candidate = float(line.removeprefix("%%py-ai-text-leading: "))
                if candidate > 0:
                    pending_text_leading = candidate
            continue
        if _TEXT_BEGIN_RE.match(line):
            in_text = True
            text_parts = []
            current_text_source_start = pending_text_source_start or token.start
            current_text_content_origins = []
            current_text_position_origin = None
            current_text_matrix_origin = None
            text_x = 0.0
            text_y = 0.0
            text_rotation = 0.0
            continue
        if in_text:
            text_position_match = _TEXT_POSITION_RE.match(line)
            if text_position_match:
                text_x = float(text_position_match.group(1))
                text_y = float(text_position_match.group(2))
                if token.operator_end is not None:
                    current_text_position_origin = statement_field_origin(
                        "position", token.start, token.content_end, token.operator_end
                    )
                continue
            text_matrix_match = _TEXT_MATRIX_RE.match(line)
            if text_matrix_match:
                text_rotation = math.degrees(
                    math.atan2(
                        float(text_matrix_match.group(2)),
                        float(text_matrix_match.group(1)),
                    )
                )
                if token.operator_end is not None:
                    current_text_matrix_origin = statement_field_origin(
                        "matrix", token.start, token.content_end, token.operator_end
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
                    text_content_fields = tuple(current_text_content_origins)
                    if len(text_content_fields) == 1:
                        text_content_fields = (
                            LegacyFieldOrigin(
                                field="text",
                                start=text_content_fields[0].start,
                                end=text_content_fields[0].end,
                                expected=text_content_fields[0].expected,
                            ),
                        )
                    text_fields = (
                        *(
                            field
                            for field in (
                                current_text_position_origin,
                                current_text_matrix_origin,
                            )
                            if field is not None
                        ),
                        *text_content_fields,
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
                pending_text_source_start = None
                pending_text_alignment = None
                pending_text_native_font_name = None
                pending_text_tracking = 0.0
                pending_text_rotation = None
                pending_text_area_width = None
                pending_text_area_height = None
                pending_text_leading = None
                current_text_source_start = None
                current_text_content_origins = []
                current_text_position_origin = None
                current_text_matrix_origin = None
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
                current_path_source_start = pending_path_source_start or token.start
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
                            fields=(
                                pending_linked_image_metadata_origin,
                                *current_geometry_origins,
                            ),
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
            pending_path_source_start = None
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
    diagnostics = tuple(
        sorted(
            (*diagnostics, *_semantic_legacy_diagnostics(source, tuple(origins))),
            key=lambda diagnostic: (diagnostic.start, diagnostic.end, diagnostic.code),
        )
    )
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
