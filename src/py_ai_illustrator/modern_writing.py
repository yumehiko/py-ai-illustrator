"""Source-preserving synchronized patches for PDF-compatible Illustrator files."""

from __future__ import annotations

import base64
import hashlib
import re
import zlib
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .model import CmykColor, Color, Group, ProcessColor
from .model import Path as ArtworkPath
from .modern import (
    _ZSTD_MARKER_RE,
    ModernReadLimits,
    PdfName,
    _decode_filter,
    _filter_names,
    _parse_objects,
    read_modern_ai,
)
from .modern_semantic import parse_modern_private_data
from .verification import extract_pdf_display

_PDF_FILL_RE = re.compile(
    rb"(?m)^(?P<values>[+-]?(?:\d+(?:\.\d*)?|\.\d+)"
    rb"(?:[ \t]+[+-]?(?:\d+(?:\.\d*)?|\.\d+)){2,3})[ \t]+(?P<op>rg|k)[ \t]*$"
)
_PDF_RECT_RE = re.compile(
    rb"(?m)^(?P<x>[+-]?(?:\d+(?:\.\d*)?|\.\d+))[ \t]+"
    rb"(?P<y>[+-]?(?:\d+(?:\.\d*)?|\.\d+))[ \t]+"
    rb"(?P<w>[+-]?(?:\d+(?:\.\d*)?|\.\d+))[ \t]+"
    rb"(?P<h>[+-]?(?:\d+(?:\.\d*)?|\.\d+))[ \t]+re[ \t]*$"
)
_PDF_STROKE_RE = re.compile(
    rb"(?m)^(?P<values>[+-]?(?:\d+(?:\.\d*)?|\.\d+)"
    rb"(?:[ \t]+[+-]?(?:\d+(?:\.\d*)?|\.\d+)){2,3})[ \t]+(?P<op>RG|K)[ \t]*$"
)
_LENGTH_RE = re.compile(rb"/Length[ \t\r\n]+(?P<length>[0-9]+)\b")
_PDF_DATE_RE = re.compile(rb"D:[0-9]{14}(?:Z|[+-][0-9]{2}'[0-9]{2}')?")
_STARTXREF_RE = re.compile(rb"startxref[ \t\r\n]+(?P<offset>[0-9]+)")
_XMP_MODIFY_RE = re.compile(
    rb"(<xmp:(?:ModifyDate|MetadataDate)>)[^<]*(</xmp:(?:ModifyDate|MetadataDate)>)"
)
_PDF_TEXT_MATRIX_RE = re.compile(
    rb"(?m)^(?P<a>[+-]?(?:\d+(?:\.\d*)?|\.\d+))[ \t]+"
    rb"(?P<b>[+-]?(?:\d+(?:\.\d*)?|\.\d+))[ \t]+"
    rb"(?P<c>[+-]?(?:\d+(?:\.\d*)?|\.\d+))[ \t]+"
    rb"(?P<d>[+-]?(?:\d+(?:\.\d*)?|\.\d+))[ \t]+"
    rb"(?P<e>[+-]?(?:\d+(?:\.\d*)?|\.\d+))[ \t]+"
    rb"(?P<f>[+-]?(?:\d+(?:\.\d*)?|\.\d+))[ \t]+Tm[ \t]*$"
)


class ModernWriteError(ValueError):
    """Raised before output when a synchronized modern patch cannot be proven safe."""


@dataclass(frozen=True, slots=True)
class ModernWriteResult:
    input: str
    output: str
    source_sha256: str
    output_sha256: str
    selector: dict[str, str]
    operation: str
    private_data_object: str
    pdf_content_object: str
    metadata_objects: tuple[str, ...]
    modification_date: str
    before: object
    after: object
    validation: dict[str, object]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def _number(value: float) -> str:
    if value == 0:
        return "0"
    return format(value, ".15g")


def _rgb(color: ProcessColor) -> Color:
    if isinstance(color, Color):
        return color
    return Color(
        1.0 - min(1.0, color.cyan + color.black),
        1.0 - min(1.0, color.magenta + color.black),
        1.0 - min(1.0, color.yellow + color.black),
    )


def _cmyk(color: ProcessColor) -> CmykColor:
    if isinstance(color, CmykColor):
        return color
    key = 1.0 - max(color.red, color.green, color.blue)
    if key >= 1.0:
        return CmykColor(0.0, 0.0, 0.0, 1.0)
    denominator = 1.0 - key
    return CmykColor(
        (1.0 - color.red - key) / denominator,
        (1.0 - color.green - key) / denominator,
        (1.0 - color.blue - key) / denominator,
        key,
    )


def _private_fill_statement(color: ProcessColor) -> bytes:
    process = _cmyk(color)
    alternate = _rgb(color)
    values = (
        process.cyan,
        process.magenta,
        process.yellow,
        process.black,
        alternate.red,
        alternate.green,
        alternate.blue,
    )
    return (" ".join(_number(value) for value in values) + " Xa").encode("ascii")


def _private_stroke_statement(color: ProcessColor) -> bytes:
    return _private_fill_statement(color)[:-2] + b"XA"


def _pdf_fill_statement(color: ProcessColor) -> bytes:
    if isinstance(color, Color):
        values = (color.red, color.green, color.blue)
        operator = "rg"
    else:
        values = (color.cyan, color.magenta, color.yellow, color.black)
        operator = "k"
    return (" ".join(_number(value) for value in values) + f" {operator}").encode("ascii")


def _pdf_stroke_statement(color: ProcessColor) -> bytes:
    return _pdf_fill_statement(color)[:-2] + (
        b"RG" if isinstance(color, Color) else b"K"
    )


def _pdf_paint_matches(
    statement: bytes,
    color: ProcessColor,
    *,
    stroke: bool,
    alternate_rgb: object = None,
) -> bool:
    pattern = _PDF_STROKE_RE if stroke else _PDF_FILL_RE
    match = pattern.fullmatch(statement)
    if match is None:
        return False
    values = tuple(float(value) for value in match.group("values").split())
    operator = match.group("op")
    if operator in {b"rg", b"RG"} and len(values) == 3:
        pdf_color: ProcessColor = Color(*values)
    elif operator in {b"k", b"K"} and len(values) == 4:
        pdf_color = CmykColor(*values)
    else:
        return False
    expected = _rgb(color)
    expected_channels = (expected.red, expected.green, expected.blue)
    if (
        isinstance(alternate_rgb, list)
        and len(alternate_rgb) == 3
        and all(isinstance(value, int | float) for value in alternate_rgb)
    ):
        expected_channels = tuple(float(value) for value in alternate_rgb)
    actual = _rgb(pdf_color)
    return all(
        _close(left, right, tolerance=0.025)
        for left, right in zip(
            expected_channels,
            (actual.red, actual.green, actual.blue),
            strict=True,
        )
    )


def _all_paths(document: Any) -> list[ArtworkPath]:
    output: list[ArtworkPath] = []

    def visit(container: Any) -> None:
        output.extend(container.paths)
        for compound in container.compound_paths:
            output.extend(compound.paths)
        for clipping in container.clipping_groups:
            output.append(clipping.clipping_path)
            output.extend(clipping.paths)
        for group in container.groups:
            assert isinstance(group, Group)
            visit(group)

    for layer in document.layers:
        visit(layer)
    return output


def _paths_with_ancestors(
    document: Any,
) -> list[tuple[ArtworkPath, tuple[dict[str, str], ...]]]:
    output: list[tuple[ArtworkPath, tuple[dict[str, str], ...]]] = []

    def visit(container: Any, ancestors: tuple[dict[str, str], ...]) -> None:
        output.extend((path, ancestors) for path in container.paths)
        for compound in container.compound_paths:
            nested = (*ancestors, {"type": "compound_path", "id": compound.id})
            output.extend((path, nested) for path in compound.paths)
        for clipping in container.clipping_groups:
            nested = (*ancestors, {"type": "clipping_group", "id": clipping.id})
            output.append((clipping.clipping_path, nested))
            output.extend((path, nested) for path in clipping.paths)
        for group in container.groups:
            visit(group, (*ancestors, {"type": "group", "id": group.id}))

    for layer in document.layers:
        visit(layer, ({"type": "layer", "id": layer.id},))
    return output


def _container_ancestor_paths(
    document: Any,
) -> dict[tuple[str, str], tuple[dict[str, str], ...]]:
    output: dict[tuple[str, str], tuple[dict[str, str], ...]] = {}

    def visit(container: Any, ancestors: tuple[dict[str, str], ...]) -> None:
        for group in container.groups:
            nested = (*ancestors, {"type": "group", "id": group.id})
            output[("group", group.id)] = nested
            visit(group, nested)
        for compound in container.compound_paths:
            output[("compound_path", compound.id)] = (
                *ancestors,
                {"type": "compound_path", "id": compound.id},
            )
        for clipping in container.clipping_groups:
            output[("clipping_group", clipping.id)] = (
                *ancestors,
                {"type": "clipping_group", "id": clipping.id},
            )

    for layer in document.layers:
        ancestors = ({"type": "layer", "id": layer.id},)
        output[("layer", layer.id)] = ancestors
        visit(layer, ancestors)
    return output


def _rectangle_bounds(path: ArtworkPath) -> tuple[float, float, float, float] | None:
    if any(point.in_handle is not None or point.out_handle is not None for point in path.points):
        return None
    coordinates = [(point.x, point.y) for point in path.points]
    if len(coordinates) == 5 and coordinates[0] == coordinates[-1]:
        coordinates = coordinates[:-1]
    if len(coordinates) != 4:
        return None
    xs = sorted({point[0] for point in coordinates})
    ys = sorted({point[1] for point in coordinates})
    if len(xs) != 2 or len(ys) != 2:
        return None
    expected = {(xs[0], ys[0]), (xs[0], ys[1]), (xs[1], ys[0]), (xs[1], ys[1])}
    if set(coordinates) != expected:
        return None
    return xs[0], ys[0], xs[1] - xs[0], ys[1] - ys[0]


def _decode_pdf_stream(raw: bytes, filters: tuple[str, ...]) -> bytes:
    decoded = raw
    for name in filters:
        decoded = _decode_filter(decoded, name, 128 * 1024 * 1024)
    return decoded


def _encode_pdf_stream(decoded: bytes, filters: tuple[str, ...]) -> bytes:
    encoded = decoded
    for name in reversed(filters):
        if name in {"Fl", "FlateDecode"}:
            encoded = zlib.compress(encoded, level=9)
        else:
            raise ModernWriteError(f"PDF display stream filter /{name} is not writable")
    return encoded


def _encode_private_segment(segment: Any, decoded: bytes) -> bytes:
    marker = _ZSTD_MARKER_RE.match(segment.raw_bytes)
    if marker is not None:
        import zstandard

        return segment.raw_bytes[: marker.end()] + zstandard.ZstdCompressor(
            level=9, write_checksum=True, write_content_size=True
        ).compress(decoded)
    if not segment.filters:
        return decoded
    raise ModernWriteError("PrivateData compression profile is not writable")


def _close(left: float, right: float, tolerance: float = 0.02) -> bool:
    return abs(left - right) <= tolerance


def _find_pdf_rectangle_fill(
    decoded: bytes, bounds: tuple[float, float, float, float]
) -> tuple[int, int] | None:
    events: list[tuple[int, str, re.Match[bytes]]] = []
    events.extend((match.start(), "fill", match) for match in _PDF_FILL_RE.finditer(decoded))
    events.extend((match.start(), "rect", match) for match in _PDF_RECT_RE.finditer(decoded))
    events.sort(key=lambda item: item[0])
    current_fill: tuple[int, int] | None = None
    matches: list[tuple[int, int]] = []
    for _position, kind, match in events:
        if kind == "fill":
            current_fill = (match.start(), match.end())
            continue
        values = tuple(float(match.group(name)) for name in ("x", "y", "w", "h"))
        if current_fill is not None and all(
            _close(value, expected) for value, expected in zip(values, bounds, strict=True)
        ):
            matches.append(current_fill)
    unique = list(dict.fromkeys(matches))
    if len(unique) != 1:
        return None
    return unique[0]


def _find_pdf_rectangle_geometry(
    decoded: bytes, bounds: tuple[float, float, float, float]
) -> tuple[int, int] | None:
    matches: list[tuple[int, int]] = []
    for match in _PDF_RECT_RE.finditer(decoded):
        values = tuple(float(match.group(name)) for name in ("x", "y", "w", "h"))
        if all(
            _close(value, expected)
            for value, expected in zip(values, bounds, strict=True)
        ):
            matches.append((match.start(), match.end()))
    return matches[0] if len(matches) == 1 else None


def _display_rectangle_geometry_candidates(
    data: bytes,
    objects: dict[tuple[int, int], Any],
    bounds: tuple[float, float, float, float],
) -> list[tuple[Any, bytes, tuple[int, int], tuple[str, ...]]]:
    candidates: list[tuple[Any, bytes, tuple[int, int], tuple[str, ...]]] = []
    for obj in objects.values():
        if not isinstance(obj.value, dict) or obj.stream_start is None or obj.stream_end is None:
            continue
        filters = _filter_names(obj.value.get("Filter"))
        if filters is None or obj.value.get("DecodeParms") is not None:
            continue
        try:
            decoded = _decode_pdf_stream(data[obj.stream_start : obj.stream_end], filters)
        except (NotImplementedError, ValueError, zlib.error):
            continue
        location = _find_pdf_rectangle_geometry(decoded, bounds)
        if location is not None:
            candidates.append((obj, decoded, location, filters))
    return candidates


def _display_fill_candidates(
    data: bytes,
    objects: dict[tuple[int, int], Any],
    bounds: tuple[float, float, float, float],
) -> list[tuple[Any, bytes, tuple[int, int], tuple[str, ...]]]:
    candidates: list[tuple[Any, bytes, tuple[int, int], tuple[str, ...]]] = []
    for obj in objects.values():
        if not isinstance(obj.value, dict) or obj.stream_start is None or obj.stream_end is None:
            continue
        filters = _filter_names(obj.value.get("Filter"))
        if filters is None or obj.value.get("DecodeParms") is not None:
            continue
        try:
            decoded = _decode_pdf_stream(data[obj.stream_start : obj.stream_end], filters)
        except (NotImplementedError, ValueError, zlib.error):
            continue
        fill_location = _find_pdf_rectangle_fill(decoded, bounds)
        if fill_location is not None:
            candidates.append((obj, decoded, fill_location, filters))
    return candidates


def _transform_point(
    matrix: tuple[float, float, float, float, float, float], x: float, y: float
) -> tuple[float, float]:
    a, b, c, d, e, f = matrix
    return (a * x + c * y + e, b * x + d * y + f)


def _multiply_matrix(
    left: tuple[float, float, float, float, float, float],
    right: tuple[float, float, float, float, float, float],
) -> tuple[float, float, float, float, float, float]:
    a, b, c, d, e, f = left
    g, h, i, j, k, offset_y = right
    return (
        a * g + c * h,
        b * g + d * h,
        a * i + c * j,
        b * i + d * j,
        a * k + c * offset_y + e,
        b * k + d * offset_y + f,
    )


def _path_signature(path: ArtworkPath) -> tuple[tuple[str, tuple[float, ...]], ...]:
    if not path.points:
        return ()
    first = path.points[0]
    output: list[tuple[str, tuple[float, ...]]] = [("m", (first.x, first.y))]
    previous = first
    for point in path.points[1:]:
        if previous.out_handle is not None or point.in_handle is not None:
            first_control = previous.out_handle or previous
            second_control = point.in_handle or point
            output.append(
                (
                    "c",
                    (
                        first_control.x,
                        first_control.y,
                        second_control.x,
                        second_control.y,
                        point.x,
                        point.y,
                    ),
                )
            )
        else:
            output.append(("l", (point.x, point.y)))
        previous = point
    return tuple(output)


def _signature_matches(
    candidate: tuple[tuple[str, tuple[float, ...]], ...],
    target: tuple[tuple[str, tuple[float, ...]], ...],
) -> bool:
    if len(candidate) != len(target) or not candidate:
        return False
    if any(left[0] != right[0] for left, right in zip(candidate, target, strict=True)):
        return False
    candidate_origin = candidate[0][1]
    target_origin = target[0][1]
    dx = candidate_origin[0] - target_origin[0]
    dy = candidate_origin[1] - target_origin[1]
    for (_left_op, left_values), (_right_op, right_values) in zip(
        candidate, target, strict=True
    ):
        if len(left_values) != len(right_values):
            return False
        for index, (left_value, right_value) in enumerate(
            zip(left_values, right_values, strict=True)
        ):
            expected = right_value + (dx if index % 2 == 0 else dy)
            if not _close(left_value, expected):
                return False
    return True


def _pdf_stroke_locations(
    decoded: bytes, target: ArtworkPath
) -> list[tuple[int, int, tuple[float, float, float, float]]]:
    identity = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
    matrix = identity
    stroke_span: tuple[int, int] | None = None
    stack: list[tuple[tuple[float, float, float, float, float, float], tuple[int, int] | None]] = []
    current: list[tuple[str, tuple[float, ...]]] = []
    matches: list[tuple[int, int, tuple[float, float, float, float]]] = []
    target_signature = _path_signature(target)
    position = 0
    for physical_line in decoded.splitlines(keepends=True):
        line = physical_line.rstrip(b"\r\n")
        content = line.strip()
        content_start = position + len(line) - len(line.lstrip())
        content_end = content_start + len(content)
        position += len(physical_line)
        if not content:
            continue
        stroke_match = _PDF_STROKE_RE.fullmatch(content)
        if stroke_match:
            stroke_span = (content_start, content_end)
            continue
        parts = content.split()
        operator = parts[-1]
        if parts[0] == b"q" and operator == b"cm" and len(parts) == 8:
            stack.append((matrix, stroke_span))
            try:
                inline_matrix = tuple(float(value) for value in parts[1:-1])
            except ValueError:
                continue
            matrix = _multiply_matrix(matrix, inline_matrix)  # type: ignore[arg-type]
            continue
        try:
            numbers = [float(value) for value in parts[:-1]]
        except ValueError:
            numbers = []
        if operator == b"q":
            stack.append((matrix, stroke_span))
        elif operator == b"Q":
            matrix, stroke_span = stack.pop() if stack else (identity, stroke_span)
            current = []
        elif operator == b"cm" and len(numbers) == 6:
            matrix = _multiply_matrix(matrix, tuple(numbers))  # type: ignore[arg-type]
        elif operator == b"m" and len(numbers) == 2:
            current = [("m", _transform_point(matrix, numbers[0], numbers[1]))]
        elif operator == b"l" and len(numbers) == 2 and current:
            current.append(("l", _transform_point(matrix, numbers[0], numbers[1])))
        elif operator == b"c" and len(numbers) == 6 and current:
            transformed = (
                *_transform_point(matrix, numbers[0], numbers[1]),
                *_transform_point(matrix, numbers[2], numbers[3]),
                *_transform_point(matrix, numbers[4], numbers[5]),
            )
            current.append(("c", transformed))
        elif operator in {b"S", b"s", b"B", b"b"}:
            if stroke_span is not None and _signature_matches(
                tuple(current), target_signature
            ):
                coordinates = [
                    (values[index], values[index + 1])
                    for _operation, values in current
                    for index in range(0, len(values), 2)
                ]
                xs = [point[0] for point in coordinates]
                ys = [point[1] for point in coordinates]
                matches.append(
                    (
                        stroke_span[0],
                        stroke_span[1],
                        (min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys)),
                    )
                )
            current = []
        elif operator in {b"f", b"F", b"f*", b"n"}:
            current = []
    return list(dict.fromkeys(matches))


def _display_stroke_candidates(
    data: bytes,
    objects: dict[tuple[int, int], Any],
    target: ArtworkPath,
) -> list[tuple[Any, bytes, tuple[int, int], tuple[str, ...], tuple[float, float, float, float]]]:
    candidates: list[
        tuple[Any, bytes, tuple[int, int], tuple[str, ...], tuple[float, float, float, float]]
    ] = []
    for obj in objects.values():
        if not isinstance(obj.value, dict) or obj.stream_start is None or obj.stream_end is None:
            continue
        filters = _filter_names(obj.value.get("Filter"))
        if filters is None or obj.value.get("DecodeParms") is not None:
            continue
        try:
            decoded = _decode_pdf_stream(data[obj.stream_start : obj.stream_end], filters)
        except (NotImplementedError, ValueError, zlib.error):
            continue
        locations = _pdf_stroke_locations(decoded, target)
        if len(locations) == 1:
            start, end, bounds = locations[0]
            candidates.append((obj, decoded, (start, end), filters, bounds))
    return candidates


def inspect_modern_stroke_targets(source: str | Path) -> dict[str, object]:
    """List path stroke selectors with synchronized PDF/PrivateData evidence."""

    source_path = Path(source)
    data = source_path.read_bytes()
    result = read_modern_ai(data)
    selectors: list[dict[str, object]] = []
    if result.semantic is None or result.semantic.document is None:
        return {
            "profile": "modern-ai-synchronized-patch-v1",
            "source_sha256": hashlib.sha256(data).hexdigest(),
            "selectors": selectors,
        }
    diagnostics: list[Any] = []
    objects = _parse_objects(data, ModernReadLimits(), diagnostics)
    for path, ancestors in _paths_with_ancestors(result.semantic.document):
        reasons: list[str] = []
        source_info = path.unknown.get("modern_source")
        style_spans = path.unknown.get("modern_style_spans")
        if path.stroke is None:
            reasons.append("path has no existing stroke")
        if not isinstance(source_info, dict) or not isinstance(style_spans, dict):
            reasons.append("PrivateData source provenance is missing")
        elif not isinstance(style_spans.get("stroke"), dict):
            reasons.append("stroke has no exact PrivateData source span")
        candidates = _display_stroke_candidates(data, objects, path)
        if len(candidates) != 1:
            reasons.append(f"PDF display path matched {len(candidates)} streams instead of one")
        representations_consistent = (
            _pdf_paint_matches(
                candidates[0][1][candidates[0][2][0] : candidates[0][2][1]],
                path.stroke,
                stroke=True,
                alternate_rgb=(
                    style_spans.get("stroke", {}).get("alternate_rgb")
                    if isinstance(style_spans, dict)
                    and isinstance(style_spans.get("stroke"), dict)
                    else None
                ),
            )
            if len(candidates) == 1 and path.stroke is not None
            else None
        )
        if representations_consistent is False:
            reasons.append("PDF display stroke value disagrees with PrivateData stroke")
        display_impact_bounds = list(candidates[0][4]) if len(candidates) == 1 else None
        display_bounds = (
            [
                display_impact_bounds[0],
                display_impact_bounds[1],
                display_impact_bounds[0] + display_impact_bounds[2],
                display_impact_bounds[1] + display_impact_bounds[3],
            ]
            if display_impact_bounds is not None
            else None
        )
        selectors.append(
            {
                "type": "path",
                "id": path.id,
                "name": path.name,
                "before": asdict(path.stroke) if path.stroke is not None else None,
                "selector": {"type": "path", "id": path.id},
                "ancestors": list(ancestors),
                "operations": ["set_stroke"] if not reasons else [],
                "bounds": display_bounds,
                "pdf_impact_bounds": display_impact_bounds,
                "pdf_match_count": len(candidates),
                "representations_consistent": representations_consistent,
                "writable": not reasons,
                "stop_reasons": reasons,
            }
        )
    return {
        "profile": "modern-ai-synchronized-patch-v1",
        "source_sha256": hashlib.sha256(data).hexdigest(),
        "selectors": selectors,
    }


def inspect_modern_fill_targets(source: str | Path) -> dict[str, object]:
    """List path fill selectors and whether synchronized patch evidence is complete."""

    source_path = Path(source)
    data = source_path.read_bytes()
    result = read_modern_ai(data)
    selectors: list[dict[str, object]] = []
    if result.semantic is None or result.semantic.document is None:
        return {
            "profile": "modern-ai-synchronized-patch-v1",
            "source_sha256": hashlib.sha256(data).hexdigest(),
            "selectors": selectors,
        }
    diagnostics: list[Any] = []
    objects = _parse_objects(data, ModernReadLimits(), diagnostics)
    for path, ancestors in _paths_with_ancestors(result.semantic.document):
        reasons: list[str] = []
        bounds = _rectangle_bounds(path)
        source_info = path.unknown.get("modern_source")
        style_spans = path.unknown.get("modern_style_spans")
        if path.fill is None:
            reasons.append("path has no existing fill")
        if bounds is None:
            reasons.append("path is not a proven rectangle")
        if not isinstance(source_info, dict) or not isinstance(style_spans, dict):
            reasons.append("PrivateData source provenance is missing")
        elif not isinstance(style_spans.get("fill"), dict):
            reasons.append("fill has no exact PrivateData source span")
        candidates = _display_fill_candidates(data, objects, bounds) if bounds else []
        candidate_count = len(candidates)
        if bounds is not None and candidate_count != 1:
            reasons.append(
                f"PDF display rectangle matched {candidate_count} streams instead of one"
            )
        representations_consistent = (
            _pdf_paint_matches(
                candidates[0][1][candidates[0][2][0] : candidates[0][2][1]],
                path.fill,
                stroke=False,
                alternate_rgb=(
                    style_spans.get("fill", {}).get("alternate_rgb")
                    if isinstance(style_spans, dict)
                    and isinstance(style_spans.get("fill"), dict)
                    else None
                ),
            )
            if len(candidates) == 1 and path.fill is not None
            else None
        )
        if representations_consistent is False:
            reasons.append("PDF display fill value disagrees with PrivateData fill")
        selectors.append(
            {
                "type": "path",
                "id": path.id,
                "name": path.name,
                "before": asdict(path.fill) if path.fill is not None else None,
                "selector": {"type": "path", "id": path.id},
                "ancestors": list(ancestors),
                "operations": ["set_fill"] if not reasons else [],
                "bounds": (
                    [bounds[0], bounds[1], bounds[0] + bounds[2], bounds[1] + bounds[3]]
                    if bounds is not None
                    else None
                ),
                "pdf_impact_bounds": list(bounds) if bounds is not None else None,
                "pdf_match_count": candidate_count,
                "representations_consistent": representations_consistent,
                "writable": not reasons,
                "stop_reasons": reasons,
            }
        )
    return {
        "profile": "modern-ai-synchronized-patch-v1",
        "source_sha256": hashlib.sha256(data).hexdigest(),
        "selectors": selectors,
    }


def inspect_modern_translate_targets(source: str | Path) -> dict[str, object]:
    """List rectangle paths whose geometry is unique in PrivateData and PDF display."""

    source_path = Path(source)
    data = source_path.read_bytes()
    result = read_modern_ai(data)
    selectors: list[dict[str, object]] = []
    if result.semantic is None or result.semantic.document is None:
        return {
            "profile": "modern-ai-synchronized-patch-v1",
            "source_sha256": hashlib.sha256(data).hexdigest(),
            "selectors": selectors,
        }
    diagnostics: list[Any] = []
    objects = _parse_objects(data, ModernReadLimits(), diagnostics)
    for path, ancestors in _paths_with_ancestors(result.semantic.document):
        reasons: list[str] = []
        bounds = _rectangle_bounds(path)
        source_info = path.unknown.get("modern_source")
        if bounds is None:
            reasons.append("path is not a proven rectangle")
        if not isinstance(source_info, dict) or not isinstance(
            source_info.get("operator_spans"), list
        ):
            reasons.append("path has no exact PrivateData geometry provenance")
        candidates = (
            _display_rectangle_geometry_candidates(data, objects, bounds) if bounds else []
        )
        if bounds is not None and len(candidates) != 1:
            reasons.append(
                f"PDF display rectangle matched {len(candidates)} streams instead of one"
            )
        selector_bounds = (
            [bounds[0], bounds[1], bounds[0] + bounds[2], bounds[1] + bounds[3]]
            if bounds is not None
            else None
        )
        selectors.append(
            {
                "type": "path",
                "id": path.id,
                "name": path.name,
                "before": {
                    "points": [
                        {"x": point.x, "y": point.y} for point in path.points
                    ]
                },
                "selector": {"type": "path", "id": path.id},
                "ancestors": list(ancestors),
                "operations": ["translate"] if not reasons else [],
                "bounds": selector_bounds,
                "pdf_impact_bounds": list(bounds) if bounds else None,
                "representations_consistent": len(candidates) == 1 if bounds else None,
                "writable": not reasons,
                "stop_reasons": reasons,
            }
        )
    return {
        "profile": "modern-ai-synchronized-patch-v1",
        "source_sha256": hashlib.sha256(data).hexdigest(),
        "selectors": selectors,
    }


def inspect_modern_container_translate_targets(source: str | Path) -> dict[str, object]:
    """List containers whose complete proven contents can be translated as path members."""

    source_path = Path(source)
    data = source_path.read_bytes()
    result = read_modern_ai(data)
    selectors: list[dict[str, object]] = []
    if result.semantic is None or result.semantic.document is None:
        return {
            "profile": "modern-ai-synchronized-patch-v1",
            "source_sha256": hashlib.sha256(data).hexdigest(),
            "selectors": selectors,
        }
    path_report = inspect_modern_translate_targets(source_path)
    path_capabilities = {
        (
            str(item["id"]),
            tuple(
                (str(ancestor["type"]), str(ancestor["id"]))
                for ancestor in item.get("ancestors", [])
                if isinstance(ancestor, dict)
            ),
        ): item
        for item in path_report["selectors"]  # type: ignore[union-attr]
        if isinstance(item, dict)
    }
    partial_nodes = result.semantic.partial_nodes

    def descendant_groups(container: Any) -> set[str]:
        output: set[str] = set()
        for group in container.groups:
            output.add(group.id)
            output.update(descendant_groups(group))
        return output

    def projected_paths(
        container: Any, ancestors: tuple[dict[str, str], ...]
    ) -> list[tuple[ArtworkPath, tuple[dict[str, str], ...]]]:
        output = [(path, ancestors) for path in container.paths]
        for group in container.groups:
            output.extend(
                projected_paths(group, (*ancestors, {"type": "group", "id": group.id}))
            )
        return output

    def projected_nonpath_count(container: Any) -> int:
        count = (
            len(container.text_frames)
            + len(container.linked_images)
            + len(container.compound_paths)
            + len(container.clipping_groups)
        )
        return count + sum(projected_nonpath_count(group) for group in container.groups)

    def add_container(
        container: Any,
        container_type: str,
        ancestors: tuple[dict[str, str], ...],
    ) -> None:
        target_ancestors = (
            ancestors[:-1] if container_type == "group" else ()
        )
        paths = projected_paths(container, ancestors)
        members: list[dict[str, object]] = []
        unwritable: list[str] = []
        impact_bounds: list[tuple[float, float, float, float]] = []
        identities: list[str] = []
        for path, path_ancestors in paths:
            key = (
                path.id,
                tuple((item["type"], item["id"]) for item in path_ancestors),
            )
            capability = path_capabilities.get(key)
            identities.append(path.id)
            if capability is None or not capability.get("writable"):
                unwritable.append(path.id)
                continue
            impact = capability.get("pdf_impact_bounds")
            if not isinstance(impact, list) or len(impact) != 4:
                unwritable.append(path.id)
                continue
            x, y, width, height = (float(value) for value in impact)
            impact_bounds.append((x, y, x + width, y + height))
            members.append(
                {
                    "type": "path",
                    "id": path.id,
                    "ancestors": list(path_ancestors),
                }
            )
        group_ids = descendant_groups(container)
        if container_type == "group":
            group_ids.add(container.id)
        partial_count = sum(
            1
            for item in partial_nodes
            if (
                item.parent_kind == "group" and item.parent_id in group_ids
            )
            or (
                container_type == "layer"
                and item.parent_kind == "layer"
                and item.parent_id == container.id
            )
        )
        reasons: list[str] = []
        if not paths:
            reasons.append("container has no projected path members")
        if len(identities) != len(set(identities)):
            reasons.append("container has duplicate path ids")
        if unwritable:
            reasons.append(
                "container has path members without synchronized translate proof: "
                + ", ".join(unwritable)
            )
        nonpath_count = projected_nonpath_count(container)
        if nonpath_count:
            reasons.append(
                f"container has {nonpath_count} projected non-path descendants"
            )
        if partial_count:
            reasons.append(
                f"container has {partial_count} partial descendants without translate proof"
            )
        combined = (
            (
                min(bounds[0] for bounds in impact_bounds),
                min(bounds[1] for bounds in impact_bounds),
                max(bounds[2] for bounds in impact_bounds),
                max(bounds[3] for bounds in impact_bounds),
            )
            if impact_bounds
            else None
        )
        selectors.append(
            {
                "type": container_type,
                "id": container.id,
                "name": container.name,
                "selector": {"type": container_type, "id": container.id},
                "ancestors": list(target_ancestors),
                "operations": ["translate"] if not reasons else [],
                "bounds": list(combined) if combined is not None else None,
                "pdf_impact_bounds": (
                    [
                        combined[0],
                        combined[1],
                        combined[2] - combined[0],
                        combined[3] - combined[1],
                    ]
                    if combined is not None
                    else None
                ),
                "members": members,
                "writable": not reasons,
                "stop_reasons": reasons,
            }
        )
        for group in container.groups:
            add_container(
                group,
                "group",
                (*ancestors, {"type": "group", "id": group.id}),
            )

    for layer in result.semantic.document.layers:
        add_container(layer, "layer", ({"type": "layer", "id": layer.id},))
    return {
        "profile": "modern-ai-synchronized-patch-v1",
        "source_sha256": hashlib.sha256(data).hexdigest(),
        "selectors": selectors,
    }


def _patched_stream_object(source: bytes, obj: Any, raw: bytes) -> bytes:
    if obj.stream_start is None or obj.stream_end is None:
        raise ModernWriteError(f"Object {obj.ref.label()} is not a writable stream")
    object_bytes = source[obj.start : obj.end]
    relative_start = obj.stream_start - obj.start
    relative_end = obj.stream_end - obj.start
    header = object_bytes[:relative_start]
    length_matches = list(_LENGTH_RE.finditer(header))
    if len(length_matches) != 1:
        raise ModernWriteError(
            f"Object {obj.ref.label()} must have one direct /Length for safe patching"
        )
    length_match = length_matches[0]
    header = (
        header[: length_match.start("length")]
        + str(len(raw)).encode("ascii")
        + header[length_match.end("length") :]
    )
    return header + raw + object_bytes[relative_end:]


def _patched_date_object(source: bytes, obj: Any, date: bytes) -> bytes:
    object_bytes = source[obj.start : obj.end]
    matches = list(_PDF_DATE_RE.finditer(object_bytes))
    if not matches:
        return object_bytes
    output = bytearray(object_bytes)
    for match in reversed(matches):
        output[match.start() : match.end()] = date
    return bytes(output)


def _incremental_pdf(
    source: bytes,
    objects: dict[tuple[int, int], Any],
    updated: dict[tuple[int, int], bytes],
) -> bytes:
    startxref_matches = list(_STARTXREF_RE.finditer(source))
    if not startxref_matches:
        raise ModernWriteError("PDF has no readable startxref precondition")
    previous_xref = int(startxref_matches[-1].group("offset"))
    catalogs = [
        obj.ref
        for obj in objects.values()
        if isinstance(obj.value, dict)
        and isinstance(obj.value.get("Type"), PdfName)
        and obj.value["Type"].value == "Catalog"
    ]
    if len(catalogs) != 1:
        raise ModernWriteError("PDF must contain exactly one Catalog for incremental writing")
    output = bytearray(source)
    if not output.endswith(b"\n"):
        output.extend(b"\n")
    offsets: list[tuple[int, int, int]] = []
    for (number, generation), object_bytes in sorted(updated.items()):
        offsets.append((number, generation, len(output)))
        output.extend(object_bytes)
        output.extend(b"\n")
    xref_offset = len(output)
    output.extend(b"xref\n")
    for number, generation, offset in offsets:
        if offset > 9_999_999_999:
            raise ModernWriteError("PDF incremental offset exceeds classic xref capacity")
        output.extend(f"{number} 1\n{offset:010d} {generation:05d} n \n".encode("ascii"))
    size = max(number for number, _generation in objects) + 1
    root = catalogs[0]
    output.extend(
        (
            f"trailer\n<< /Size {size} /Root {root.object_number} {root.generation} R "
            f"/Prev {previous_xref} >>\nstartxref\n{xref_offset}\n%%EOF\n"
        ).encode("ascii")
    )
    return bytes(output)


def _pdf_date(value: datetime | None) -> str:
    timestamp = value or datetime.now(UTC)
    if timestamp.tzinfo is None:
        raise ModernWriteError("modification_time must be timezone-aware")
    utc = timestamp.astimezone(UTC)
    return utc.strftime("D:%Y%m%d%H%M%SZ")


def _xmp_date(pdf_date: str) -> str:
    return (
        f"{pdf_date[2:6]}-{pdf_date[6:8]}-{pdf_date[8:10]}T"
        f"{pdf_date[10:12]}:{pdf_date[12:14]}:{pdf_date[14:16]}Z"
    )


def _metadata_updates(
    data: bytes,
    objects: dict[tuple[int, int], Any],
    xmp_date: str,
) -> tuple[dict[tuple[int, int], bytes], tuple[str, ...]]:
    updates: dict[tuple[int, int], bytes] = {}
    labels: list[str] = []
    for obj in objects.values():
        if (
            not isinstance(obj.value, dict)
            or not isinstance(obj.value.get("Type"), PdfName)
            or obj.value["Type"].value != "Metadata"
            or obj.stream_start is None
            or obj.stream_end is None
        ):
            continue
        filters = _filter_names(obj.value.get("Filter"))
        if filters is None or obj.value.get("DecodeParms") is not None:
            raise ModernWriteError("XMP metadata stream uses an unsupported filter profile")
        decoded = _decode_pdf_stream(data[obj.stream_start : obj.stream_end], filters)
        patched, count = _XMP_MODIFY_RE.subn(
            lambda match: match.group(1) + xmp_date.encode("ascii") + match.group(2),
            decoded,
        )
        if count < 2:
            raise ModernWriteError(
                "XMP metadata must contain both ModifyDate and MetadataDate elements"
            )
        raw = _encode_pdf_stream(patched, filters)
        updates[(obj.ref.object_number, obj.ref.generation)] = _patched_stream_object(
            data, obj, raw
        )
        labels.append(obj.ref.label())
    if not updates:
        raise ModernWriteError("modern AI has no writable XMP metadata stream")
    return updates, tuple(labels)


def _metadata_dates_match(data: bytes, expected: str) -> bool:
    diagnostics: list[Any] = []
    objects = _parse_objects(data, ModernReadLimits(), diagnostics)
    values: list[str] = []
    for obj in objects.values():
        if (
            not isinstance(obj.value, dict)
            or not isinstance(obj.value.get("Type"), PdfName)
            or obj.value["Type"].value != "Metadata"
            or obj.stream_start is None
            or obj.stream_end is None
        ):
            continue
        filters = _filter_names(obj.value.get("Filter"))
        if filters is None:
            return False
        try:
            decoded = _decode_pdf_stream(data[obj.stream_start : obj.stream_end], filters)
        except (NotImplementedError, ValueError, zlib.error):
            return False
        for match in _XMP_MODIFY_RE.finditer(decoded):
            value_start = match.end(1)
            value_end = match.start(2)
            values.append(decoded[value_start:value_end].decode("ascii", errors="replace"))
    return len(values) >= 2 and all(value == expected for value in values)


def _patch_modern_path_paint(
    source: str | Path,
    output: str | Path,
    *,
    path_id: str,
    color: ProcessColor,
    paint: str,
    source_sha256: str | None = None,
    modification_time: datetime | None = None,
) -> ModernWriteResult:
    """Synchronize one proven path paint across PrivateData and PDF display."""

    source_path = Path(source)
    output_path = Path(output)
    if output_path.exists():
        raise FileExistsError(f"refusing to overwrite existing output: {output_path}")
    data = source_path.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    if source_sha256 is not None and source_sha256 != digest:
        raise ModernWriteError("source_sha256 precondition does not match the input")
    result = read_modern_ai(data)
    if result.container_status != "parsed" or result.private_data_status != "extracted":
        raise ModernWriteError("modern AI must have fully decoded PrivateData")
    if result.semantic is None or result.semantic.document is None:
        raise ModernWriteError("modern AI has no semantic Document projection")
    matches = [path for path in _all_paths(result.semantic.document) if path.id == path_id]
    if len(matches) != 1:
        raise ModernWriteError(f"path selector id={path_id!r} matched {len(matches)} paths")
    target = matches[0]
    before_color = target.fill if paint == "fill" else target.stroke
    if before_color is None:
        raise ModernWriteError(f"set_{paint} cannot add paint to an unpainted modern path")
    style_spans = target.unknown.get("modern_style_spans")
    source_info = target.unknown.get("modern_source")
    if not isinstance(style_spans, dict) or not isinstance(source_info, dict):
        raise ModernWriteError("path has no modern source provenance")
    paint_span = style_spans.get(paint)
    segment_key = source_info.get("segment")
    if not isinstance(paint_span, dict) or not isinstance(segment_key, str):
        raise ModernWriteError(f"path {paint} has no exact PrivateData source span")
    start = paint_span.get("start")
    end = paint_span.get("end")
    if not isinstance(start, int) or not isinstance(end, int):
        raise ModernWriteError(f"path {paint} source span is malformed")
    segments = [segment for segment in result.segments if segment.key == segment_key]
    if len(segments) != 1 or segments[0].decoded_bytes is None:
        raise ModernWriteError("path PrivateData segment is not uniquely decoded")
    segment = segments[0]
    private_decoded = segment.decoded_bytes
    private_statement = (
        _private_fill_statement(color)
        if paint == "fill"
        else _private_stroke_statement(color)
    )
    patched_private = private_decoded[:start] + private_statement + private_decoded[end:]

    limits = ModernReadLimits()
    diagnostics: list[Any] = []
    objects = _parse_objects(data, limits, diagnostics)
    private_obj = objects.get((segment.object_ref.object_number, segment.object_ref.generation))
    if private_obj is None:
        raise ModernWriteError("PrivateData stream object disappeared during write planning")
    raw_private = _encode_private_segment(segment, patched_private)

    if paint == "fill":
        bounds = _rectangle_bounds(target)
        if bounds is None:
            raise ModernWriteError(
                "PDF fill synchronization currently requires a proven rectangle path"
            )
        display_candidates = _display_fill_candidates(data, objects, bounds)
    else:
        display_candidates = _display_stroke_candidates(data, objects, target)
    if len(display_candidates) != 1:
        raise ModernWriteError(
            f"PDF display path matched {len(display_candidates)} streams; "
            "exactly one is required"
        )
    selected_display = display_candidates[0]
    content_obj = selected_display[0]
    content_decoded = selected_display[1]
    pdf_start, pdf_end = selected_display[2]
    content_filters = selected_display[3]
    pdf_statement = (
        _pdf_fill_statement(color) if paint == "fill" else _pdf_stroke_statement(color)
    )
    patched_content = content_decoded[:pdf_start] + pdf_statement + content_decoded[pdf_end:]
    raw_content = _encode_pdf_stream(patched_content, content_filters)

    updated: dict[tuple[int, int], bytes] = {
        (private_obj.ref.object_number, private_obj.ref.generation): _patched_stream_object(
            data, private_obj, raw_private
        ),
        (content_obj.ref.object_number, content_obj.ref.generation): _patched_stream_object(
            data, content_obj, raw_content
        ),
    }
    date_text = _pdf_date(modification_time)
    date_bytes = date_text.encode("ascii")
    metadata_updates, metadata_labels = _metadata_updates(
        data, objects, _xmp_date(date_text)
    )
    for key, patched in metadata_updates.items():
        if key in updated:
            raise ModernWriteError("metadata object overlaps another modified object")
        updated[key] = patched
    for obj in objects.values():
        if not isinstance(obj.value, dict) or "LastModified" not in obj.value:
            continue
        patched = _patched_date_object(data, obj, date_bytes)
        key = (obj.ref.object_number, obj.ref.generation)
        if key in updated:
            raise ModernWriteError("timestamp object overlaps a modified stream object")
        updated[key] = patched

    output_data = _incremental_pdf(data, objects, updated)
    output_path.write_bytes(output_data)
    reread = read_modern_ai(output_path)
    display = extract_pdf_display(output_path)
    reparsed_paths = (
        _all_paths(reread.semantic.document)
        if reread.semantic is not None and reread.semantic.document is not None
        else []
    )
    reparsed = [path for path in reparsed_paths if path.id == path_id]
    private_reparsed = len(reparsed) == 1
    private_value_matches = False
    if private_reparsed:
        reparsed_path = reparsed[0]
        if isinstance(color, Color):
            spans = reparsed_path.unknown.get("modern_style_spans")
            fill_evidence = spans.get(paint) if isinstance(spans, dict) else None
            private_value_matches = (
                isinstance(fill_evidence, dict)
                and fill_evidence.get("alternate_rgb")
                == [color.red, color.green, color.blue]
            )
        else:
            reparsed_color = reparsed_path.fill if paint == "fill" else reparsed_path.stroke
            private_value_matches = reparsed_color == color
    pdf_reparsed = display.valid
    freshness_synced = display.private_data_freshness == "timestamps_match"
    metadata_synced = _metadata_dates_match(output_data, _xmp_date(date_text))
    validation = {
        "private_data_reparsed": private_reparsed,
        "private_data_value_matches": private_value_matches,
        "pdf_display_reparsed": pdf_reparsed,
        "pdf_and_private_timestamps_match": freshness_synced,
        "xmp_modify_dates_match": metadata_synced,
        "unknown_source_prefix_preserved": output_data.startswith(data),
        "source_not_overwritten": source_path.read_bytes() == data,
    }
    if not all(validation.values()):
        output_path.unlink(missing_ok=True)
        raise ModernWriteError(f"modern write validation failed: {validation}")
    return ModernWriteResult(
        input=str(source_path),
        output=str(output_path),
        source_sha256=digest,
        output_sha256=hashlib.sha256(output_data).hexdigest(),
        selector={"type": "path", "id": path_id},
        operation=f"set_{paint}",
        private_data_object=private_obj.ref.label(),
        pdf_content_object=content_obj.ref.label(),
        metadata_objects=metadata_labels,
        modification_date=date_text,
        before=asdict(before_color),
        after=asdict(color),
        validation=validation,
    )


def patch_modern_path_fill(
    source: str | Path,
    output: str | Path,
    *,
    path_id: str,
    color: ProcessColor,
    source_sha256: str | None = None,
    modification_time: datetime | None = None,
) -> ModernWriteResult:
    """Synchronize one proven rectangular path fill across both AI representations."""

    return _patch_modern_path_paint(
        source,
        output,
        path_id=path_id,
        color=color,
        paint="fill",
        source_sha256=source_sha256,
        modification_time=modification_time,
    )


def patch_modern_path_stroke(
    source: str | Path,
    output: str | Path,
    *,
    path_id: str,
    color: ProcessColor,
    source_sha256: str | None = None,
    modification_time: datetime | None = None,
) -> ModernWriteResult:
    """Synchronize one proven path stroke across both AI representations."""

    return _patch_modern_path_paint(
        source,
        output,
        path_id=path_id,
        color=color,
        paint="stroke",
        source_sha256=source_sha256,
        modification_time=modification_time,
    )


def _translate_private_path_geometry(
    decoded: bytes,
    *,
    segment_key: str,
    operator_spans: list[object],
    dx: float,
    dy: float,
) -> bytes:
    expected_starts = {
        item.get("start")
        for item in operator_spans
        if isinstance(item, dict) and isinstance(item.get("start"), int)
    }
    cst = parse_modern_private_data(decoded, segment_key=segment_key)
    replacements: list[tuple[int, int, bytes]] = []
    geometry_operators = {"m", "L", "l", "C", "c", "v", "y"}
    for statement in cst.statements:
        if (
            statement.operator.start not in expected_starts
            or statement.operator_name not in geometry_operators
        ):
            continue
        operands = [operand for operand in statement.operands if operand.kind == "number"]
        if len(operands) != len(statement.operands) or len(operands) % 2:
            raise ModernWriteError("PrivateData path geometry operands are not numeric pairs")
        for index, operand in enumerate(operands):
            value = float(operand.value) + (dx if index % 2 == 0 else dy)
            replacements.append(
                (operand.start, operand.end, _number(value).encode("ascii"))
            )
    if not replacements:
        raise ModernWriteError("PrivateData path has no writable geometry operands")
    output = decoded
    for start, end, replacement in sorted(replacements, reverse=True):
        output = output[:start] + replacement + output[end:]
    return output


def patch_modern_path_translate(
    source: str | Path,
    output: str | Path,
    *,
    path_id: str,
    dx: float,
    dy: float,
    source_sha256: str | None = None,
    modification_time: datetime | None = None,
) -> ModernWriteResult:
    """Translate one proven rectangle in PrivateData and PDF display atomically."""

    if not all(value == value and abs(value) != float("inf") for value in (dx, dy)):
        raise ModernWriteError("translation offsets must be finite")
    source_path = Path(source)
    output_path = Path(output)
    if output_path.exists():
        raise FileExistsError(f"refusing to overwrite existing output: {output_path}")
    data = source_path.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    if source_sha256 is not None and source_sha256 != digest:
        raise ModernWriteError("source_sha256 precondition does not match the input")
    result = read_modern_ai(data)
    if result.private_data_status != "extracted" or result.semantic is None:
        raise ModernWriteError("modern AI must have decoded semantic PrivateData")
    if result.semantic.document is None:
        raise ModernWriteError("modern AI has no semantic Document projection")
    matches = [path for path in _all_paths(result.semantic.document) if path.id == path_id]
    if len(matches) != 1:
        raise ModernWriteError(f"path selector id={path_id!r} matched {len(matches)} paths")
    target = matches[0]
    bounds = _rectangle_bounds(target)
    if bounds is None:
        raise ModernWriteError("modern translation currently requires a proven rectangle path")
    source_info = target.unknown.get("modern_source")
    if not isinstance(source_info, dict):
        raise ModernWriteError("path has no modern source provenance")
    segment_key = source_info.get("segment")
    operator_spans = source_info.get("operator_spans")
    if not isinstance(segment_key, str) or not isinstance(operator_spans, list):
        raise ModernWriteError("path has no exact PrivateData geometry spans")
    segments = [segment for segment in result.segments if segment.key == segment_key]
    if len(segments) != 1 or segments[0].decoded_bytes is None:
        raise ModernWriteError("path PrivateData segment is not uniquely decoded")
    segment = segments[0]
    patched_private = _translate_private_path_geometry(
        segment.decoded_bytes,
        segment_key=segment_key,
        operator_spans=operator_spans,
        dx=dx,
        dy=dy,
    )
    diagnostics: list[Any] = []
    objects = _parse_objects(data, ModernReadLimits(), diagnostics)
    private_obj = objects.get((segment.object_ref.object_number, segment.object_ref.generation))
    if private_obj is None:
        raise ModernWriteError("PrivateData stream object disappeared during write planning")
    display_candidates = _display_rectangle_geometry_candidates(data, objects, bounds)
    if len(display_candidates) != 1:
        raise ModernWriteError(
            f"PDF display rectangle matched {len(display_candidates)} streams; one is required"
        )
    content_obj, content_decoded, (pdf_start, pdf_end), content_filters = (
        display_candidates[0]
    )
    x, y, width, height = bounds
    pdf_geometry = (
        f"{_number(x + dx)} {_number(y + dy)} {_number(width)} {_number(height)} re"
    ).encode("ascii")
    patched_content = content_decoded[:pdf_start] + pdf_geometry + content_decoded[pdf_end:]
    updated: dict[tuple[int, int], bytes] = {
        (private_obj.ref.object_number, private_obj.ref.generation): _patched_stream_object(
            data, private_obj, _encode_private_segment(segment, patched_private)
        ),
        (content_obj.ref.object_number, content_obj.ref.generation): _patched_stream_object(
            data, content_obj, _encode_pdf_stream(patched_content, content_filters)
        ),
    }
    date_text = _pdf_date(modification_time)
    metadata_updates, metadata_labels = _metadata_updates(
        data, objects, _xmp_date(date_text)
    )
    updated.update(metadata_updates)
    date_bytes = date_text.encode("ascii")
    for obj in objects.values():
        if isinstance(obj.value, dict) and "LastModified" in obj.value:
            key = (obj.ref.object_number, obj.ref.generation)
            if key in updated:
                raise ModernWriteError("timestamp object overlaps another modified object")
            updated[key] = _patched_date_object(data, obj, date_bytes)
    output_data = _incremental_pdf(data, objects, updated)
    output_path.write_bytes(output_data)
    reread = read_modern_ai(output_path)
    reparsed_paths = (
        _all_paths(reread.semantic.document)
        if reread.semantic is not None and reread.semantic.document is not None
        else []
    )
    reparsed = [path for path in reparsed_paths if path.id == path_id]
    geometry_matches = len(reparsed) == 1 and all(
        _close(after.x, before.x + dx) and _close(after.y, before.y + dy)
        for before, after in zip(target.points, reparsed[0].points, strict=True)
    )
    display = extract_pdf_display(output_path)
    validation = {
        "private_data_reparsed": len(reparsed) == 1,
        "private_data_value_matches": geometry_matches,
        "pdf_display_reparsed": display.valid,
        "pdf_and_private_timestamps_match": (
            display.private_data_freshness == "timestamps_match"
        ),
        "xmp_modify_dates_match": _metadata_dates_match(
            output_data, _xmp_date(date_text)
        ),
        "unknown_source_prefix_preserved": output_data.startswith(data),
        "source_not_overwritten": source_path.read_bytes() == data,
    }
    if not all(validation.values()):
        output_path.unlink(missing_ok=True)
        raise ModernWriteError(f"modern translate validation failed: {validation}")
    return ModernWriteResult(
        input=str(source_path),
        output=str(output_path),
        source_sha256=digest,
        output_sha256=hashlib.sha256(output_data).hexdigest(),
        selector={"type": "path", "id": path_id},
        operation="translate",
        private_data_object=private_obj.ref.label(),
        pdf_content_object=content_obj.ref.label(),
        metadata_objects=metadata_labels,
        modification_date=date_text,
        before={"points": [{"x": point.x, "y": point.y} for point in target.points]},
        after={
            "points": [
                {"x": point.x + dx, "y": point.y + dy} for point in target.points
            ]
        },
        validation=validation,
    )


def _escape_pdf_literal(data: bytes) -> bytes:
    return data.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)")


def _private_text_document(
    data: bytes,
) -> tuple[int, int, bytes, bytes]:
    marker = b"/AI11TextDocument : /ASCII85Decode ,"
    start = data.find(marker)
    if start < 0:
        raise ModernWriteError("PrivateData has no AI11TextDocument")
    cr = data.find(b"\r", start)
    lf = data.find(b"\n", start)
    line_ends = [value for value in (cr, lf) if value >= 0]
    if not line_ends:
        raise ModernWriteError("AI11TextDocument header has no line ending")
    line_end = min(line_ends)
    payload_start = line_end + 1
    if data[line_end : line_end + 2] == b"\r\n":
        payload_start += 1
    position = payload_start
    payload_end = payload_start
    encoded = bytearray()
    while position < len(data):
        cr = data.find(b"\r", position)
        lf = data.find(b"\n", position)
        candidates = [value for value in (cr, lf) if value >= 0]
        next_end = min(candidates) if candidates else len(data)
        line = data[position:next_end]
        if not line.startswith(b"%"):
            break
        encoded.extend(line[1:])
        payload_end = next_end
        if b"~>" in line:
            break
        position = next_end + 1
        if data[next_end : next_end + 2] == b"\r\n":
            position += 1
    terminator = encoded.find(b"~>")
    if terminator < 0:
        raise ModernWriteError("AI11TextDocument ASCII85 payload is unterminated")
    try:
        decoded = base64.a85decode(bytes(encoded[:terminator]))
    except ValueError as error:
        raise ModernWriteError("AI11TextDocument ASCII85 payload is invalid") from error
    return start, payload_end, data[start:payload_start], decoded


def _encode_private_text_document(header: bytes, decoded: bytes) -> bytes:
    encoded = base64.a85encode(decoded) + b"~>"
    lines = [b"%" + encoded[start : start + 100] for start in range(0, len(encoded), 100)]
    newline = b"\r\n" if header.endswith(b"\r\n") else b"\r"
    return header + newline.join(lines)


def _text_display_candidates(
    data: bytes,
    objects: dict[tuple[int, int], Any],
    old_text: str,
) -> list[tuple[Any, bytes, tuple[int, int], tuple[str, ...]]]:
    try:
        encoded_text = old_text.encode("latin-1")
    except UnicodeEncodeError as error:
        raise ModernWriteError(
            "PDF text synchronization currently requires Latin-1 source text"
        ) from error
    needle = b"(" + _escape_pdf_literal(encoded_text) + b")"
    candidates: list[tuple[Any, bytes, tuple[int, int], tuple[str, ...]]] = []
    for obj in objects.values():
        if not isinstance(obj.value, dict) or obj.stream_start is None or obj.stream_end is None:
            continue
        filters = _filter_names(obj.value.get("Filter"))
        if filters is None or obj.value.get("DecodeParms") is not None:
            continue
        try:
            decoded = _decode_pdf_stream(data[obj.stream_start : obj.stream_end], filters)
        except (NotImplementedError, ValueError, zlib.error):
            continue
        starts: list[int] = []
        position = 0
        while True:
            found = decoded.find(needle, position)
            if found < 0:
                break
            starts.append(found)
            position = found + len(needle)
        if len(starts) == 1:
            candidates.append((obj, decoded, (starts[0], starts[0] + len(needle)), filters))
        elif len(starts) > 1:
            candidates.extend(
                (obj, decoded, (found, found + len(needle)), filters) for found in starts
            )
    return candidates


def _initial_text_bounds(
    decoded: bytes,
    text_start: int,
    text: str,
) -> tuple[float, float, float, float] | None:
    matrices = list(_PDF_TEXT_MATRIX_RE.finditer(decoded, 0, text_start))
    if not matrices:
        return None
    matrix = matrices[-1]
    intervening = decoded[matrix.end() : text_start]
    if re.search(rb"(?:Tj|TJ|Td|TD|Tm|T\*)\b", intervening):
        return None
    values = [float(matrix.group(name)) for name in ("a", "b", "c", "d", "e", "f")]
    if not _close(values[1], 0) or not _close(values[2], 0):
        return None
    font_size = max(abs(values[0]), abs(values[3]))
    if font_size <= 0:
        return None
    width = max(1, len(text)) * font_size * 0.75
    return values[4], values[5], width, font_size


def inspect_modern_text_targets(source: str | Path) -> dict[str, object]:
    """List uniquely encoded AI11/PDF text nodes with provable initial placement."""

    source_path = Path(source)
    data = source_path.read_bytes()
    result = read_modern_ai(data)
    selectors: list[dict[str, object]] = []
    if result.semantic is None:
        return {
            "profile": "modern-ai-synchronized-patch-v1",
            "source_sha256": hashlib.sha256(data).hexdigest(),
            "selectors": selectors,
        }
    diagnostics: list[Any] = []
    objects = _parse_objects(data, ModernReadLimits(), diagnostics)
    segments = {segment.key: segment for segment in result.segments}
    parent_paths = (
        _container_ancestor_paths(result.semantic.document)
        if result.semantic.document is not None
        else {}
    )
    for item in result.semantic.partial_nodes:
        if item.kind != "text":
            continue
        reasons: list[str] = []
        old_text = item.known_fields.get("text")
        segment = segments.get(item.segment_key)
        if not isinstance(old_text, str) or not old_text:
            reasons.append("text has no proven source content")
        if segment is None or segment.decoded_bytes is None:
            reasons.append("PrivateData text segment is not decoded")
        story_count = 0
        if isinstance(old_text, str) and segment is not None and segment.decoded_bytes is not None:
            try:
                document_decoded = _private_text_document(segment.decoded_bytes)[3]
                story = (
                    b"("
                    + _escape_pdf_literal(
                        b"\xfe\xff" + (old_text + "\r").encode("utf-16-be")
                    )
                    + b")"
                )
                story_count = document_decoded.count(story)
            except ModernWriteError:
                story_count = 0
        if story_count != 1:
            reasons.append(f"AI11 story value occurs {story_count} times instead of one")
        display_candidates = (
            _text_display_candidates(data, objects, old_text)
            if isinstance(old_text, str) and old_text
            else []
        )
        if len(display_candidates) != 1:
            reasons.append(
                f"PDF display text occurs {len(display_candidates)} times instead of one"
            )
        representations_consistent = (
            True if story_count == 1 and len(display_candidates) == 1 else None
        )
        impact_bounds = None
        if len(display_candidates) == 1 and isinstance(old_text, str):
            _obj, decoded, (text_start, _text_end), _filters = display_candidates[0]
            impact_bounds = _initial_text_bounds(decoded, text_start, old_text)
            if impact_bounds is None:
                reasons.append("PDF text placement is not source-local and provable")
        selector_bounds = (
            [
                impact_bounds[0],
                impact_bounds[1],
                impact_bounds[0] + impact_bounds[2],
                impact_bounds[1] + impact_bounds[3],
            ]
            if impact_bounds is not None
            else None
        )
        selectors.append(
            {
                "type": "text",
                "id": item.id,
                "name": item.name,
                "before": old_text,
                "selector": {"type": "text", "id": item.id},
                "ancestors": list(
                    parent_paths.get((str(item.parent_kind), str(item.parent_id)), ())
                ),
                "operations": ["replace_text"] if not reasons else [],
                "bounds": selector_bounds,
                "pdf_impact_bounds": list(impact_bounds) if impact_bounds else None,
                "representations_consistent": representations_consistent,
                "writable": not reasons,
                "stop_reasons": reasons,
            }
        )
    return {
        "profile": "modern-ai-synchronized-patch-v1",
        "source_sha256": hashlib.sha256(data).hexdigest(),
        "selectors": selectors,
    }


def inspect_modern_representation_consistency(source: str | Path) -> dict[str, object]:
    """Compare PrivateData and PDF values wherever source-local proof is available."""

    reports = {
        "set_fill": inspect_modern_fill_targets(source),
        "set_stroke": inspect_modern_stroke_targets(source),
        "translate": inspect_modern_translate_targets(source),
        "replace_text": inspect_modern_text_targets(source),
    }
    evidence: list[dict[str, object]] = []
    for operation, report in reports.items():
        for selector in report["selectors"]:  # type: ignore[union-attr]
            if not isinstance(selector, dict):
                continue
            consistent = selector.get("representations_consistent")
            if not isinstance(consistent, bool):
                continue
            evidence.append(
                {
                    "operation": operation,
                    "type": selector.get("type"),
                    "id": selector.get("id"),
                    "consistent": consistent,
                }
            )
    mismatches = [item for item in evidence if not item["consistent"]]
    return {
        "profile": "modern-ai-cross-representation-consistency-v1",
        "status": (
            "inconsistent"
            if mismatches
            else "consistent_for_proven_targets"
            if evidence
            else "not_proven"
        ),
        "checked_count": len(evidence),
        "mismatch_count": len(mismatches),
        "mismatches": mismatches,
        "evidence": evidence,
    }


def patch_modern_text(
    source: str | Path,
    output: str | Path,
    *,
    text_id: str,
    text: str,
    source_sha256: str | None = None,
    modification_time: datetime | None = None,
) -> ModernWriteResult:
    """Synchronize one uniquely encoded AI11/PDF text value without changing its style."""

    if not text or any(ord(character) < 32 or ord(character) > 126 for character in text):
        raise ModernWriteError("modern text replacement currently requires printable ASCII")
    source_path = Path(source)
    output_path = Path(output)
    if output_path.exists():
        raise FileExistsError(f"refusing to overwrite existing output: {output_path}")
    data = source_path.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    if source_sha256 is not None and source_sha256 != digest:
        raise ModernWriteError("source_sha256 precondition does not match the input")
    result = read_modern_ai(data)
    if result.private_data_status != "extracted" or result.semantic is None:
        raise ModernWriteError("modern AI must have decoded semantic PrivateData")
    matches = [
        item
        for item in result.semantic.partial_nodes
        if item.kind == "text" and item.id == text_id
    ]
    if len(matches) != 1:
        raise ModernWriteError(f"text selector id={text_id!r} matched {len(matches)} text nodes")
    target = matches[0]
    old_text = target.known_fields.get("text")
    if not isinstance(old_text, str) or not old_text:
        raise ModernWriteError("text node has no proven source content")
    segment_matches = [
        segment for segment in result.segments if segment.key == target.segment_key
    ]
    if len(segment_matches) != 1 or segment_matches[0].decoded_bytes is None:
        raise ModernWriteError("text PrivateData segment is not uniquely decoded")
    segment = segment_matches[0]
    private_data = segment.decoded_bytes
    document_start, document_end, document_header, document_decoded = _private_text_document(
        private_data
    )
    old_story = (
        b"("
        + _escape_pdf_literal(b"\xfe\xff" + (old_text + "\r").encode("utf-16-be"))
        + b")"
    )
    new_story = (
        b"("
        + _escape_pdf_literal(b"\xfe\xff" + (text + "\r").encode("utf-16-be"))
        + b")"
    )
    if document_decoded.count(old_story) != 1:
        raise ModernWriteError(
            f"AI11TextDocument contains {document_decoded.count(old_story)} exact story values; "
            "one is required"
        )
    patched_document = document_decoded.replace(old_story, new_story, 1)
    encoded_document = _encode_private_text_document(document_header, patched_document)
    patched_private = (
        private_data[:document_start] + encoded_document + private_data[document_end:]
    )

    diagnostics: list[Any] = []
    objects = _parse_objects(data, ModernReadLimits(), diagnostics)
    private_obj = objects.get((segment.object_ref.object_number, segment.object_ref.generation))
    if private_obj is None:
        raise ModernWriteError("PrivateData stream object disappeared during write planning")
    display_candidates = _text_display_candidates(data, objects, old_text)
    if len(display_candidates) != 1:
        raise ModernWriteError(
            f"PDF display text {old_text!r} matched {len(display_candidates)} values; "
            "exactly one is required"
        )
    content_obj, content_decoded, (pdf_start, pdf_end), content_filters = (
        display_candidates[0]
    )
    pdf_text = b"(" + _escape_pdf_literal(text.encode("ascii")) + b")"
    patched_content = content_decoded[:pdf_start] + pdf_text + content_decoded[pdf_end:]
    raw_private = _encode_private_segment(segment, patched_private)
    raw_content = _encode_pdf_stream(patched_content, content_filters)
    updated: dict[tuple[int, int], bytes] = {
        (private_obj.ref.object_number, private_obj.ref.generation): _patched_stream_object(
            data, private_obj, raw_private
        ),
        (content_obj.ref.object_number, content_obj.ref.generation): _patched_stream_object(
            data, content_obj, raw_content
        ),
    }
    date_text = _pdf_date(modification_time)
    metadata_updates, metadata_labels = _metadata_updates(
        data, objects, _xmp_date(date_text)
    )
    updated.update(metadata_updates)
    date_bytes = date_text.encode("ascii")
    for obj in objects.values():
        if isinstance(obj.value, dict) and "LastModified" in obj.value:
            key = (obj.ref.object_number, obj.ref.generation)
            if key in updated:
                raise ModernWriteError("timestamp object overlaps another modified object")
            updated[key] = _patched_date_object(data, obj, date_bytes)
    output_data = _incremental_pdf(data, objects, updated)
    output_path.write_bytes(output_data)
    reread = read_modern_ai(output_path)
    reread_matches = (
        [
            item
            for item in reread.semantic.partial_nodes
            if item.kind == "text" and item.id == text_id
        ]
        if reread.semantic is not None
        else []
    )
    display = extract_pdf_display(output_path)
    validation = {
        "private_data_reparsed": len(reread_matches) == 1,
        "private_data_value_matches": (
            len(reread_matches) == 1 and reread_matches[0].known_fields.get("text") == text
        ),
        "pdf_display_reparsed": display.valid,
        "pdf_and_private_timestamps_match": (
            display.private_data_freshness == "timestamps_match"
        ),
        "xmp_modify_dates_match": _metadata_dates_match(
            output_data, _xmp_date(date_text)
        ),
        "unknown_source_prefix_preserved": output_data.startswith(data),
        "source_not_overwritten": source_path.read_bytes() == data,
    }
    if not all(validation.values()):
        output_path.unlink(missing_ok=True)
        raise ModernWriteError(f"modern text write validation failed: {validation}")
    return ModernWriteResult(
        input=str(source_path),
        output=str(output_path),
        source_sha256=digest,
        output_sha256=hashlib.sha256(output_data).hexdigest(),
        selector={"type": "text", "id": text_id},
        operation="replace_text",
        private_data_object=private_obj.ref.label(),
        pdf_content_object=content_obj.ref.label(),
        metadata_objects=metadata_labels,
        modification_date=date_text,
        before=old_text,
        after=text,
        validation=validation,
    )
