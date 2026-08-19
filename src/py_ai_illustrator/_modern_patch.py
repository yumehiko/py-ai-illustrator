"""Modern synchronized patch implementation.

This module owns source-preserving PDF/PrivateData mutation and post-apply
validation. Target discovery and representation evidence live in
:mod:`._modern_discovery`.
"""

from __future__ import annotations

import hashlib
import re
import zlib
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ._modern_container import (
    _ZSTD_MARKER_RE,
    ModernReadLimits,
    PdfName,
    _filter_names,
    _parse_objects,
    read_modern_ai,
)
from ._modern_cst import parse_modern_private_data
from ._modern_discovery import (
    _all_paths,
    _close,
    _decode_pdf_stream,
    _display_fill_candidates,
    _display_rectangle_geometry_candidates,
    _display_stroke_candidates,
    _encode_private_text_document,
    _escape_pdf_literal,
    _private_text_document,
    _rectangle_bounds,
    _rgb,
    _text_display_candidates,
)
from ._modern_write_contract import ModernWriteError
from .model import CmykColor, Color, ProcessColor
from .verification import extract_pdf_display

_LENGTH_RE = re.compile(rb"/Length[ \t\r\n]+(?P<length>[0-9]+)\b")
_PDF_DATE_RE = re.compile(rb"D:[0-9]{14}(?:Z|[+-][0-9]{2}'[0-9]{2}')?")
_STARTXREF_RE = re.compile(rb"startxref[ \t\r\n]+(?P<offset>[0-9]+)")
_XMP_MODIFY_RE = re.compile(
    rb"(<xmp:(?:ModifyDate|MetadataDate)>)[^<]*(</xmp:(?:ModifyDate|MetadataDate)>)"
)

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
