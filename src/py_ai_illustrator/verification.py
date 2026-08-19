"""PDF display evidence, deterministic previews, and pixel visual differences."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import struct
import subprocess
import tempfile
import zlib
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .format import FileFormat, inspect_file
from .legacy import read_ai7
from .model import (
    ClippingGroup,
    CmykColor,
    Color,
    CompoundPath,
    Group,
    Layer,
    LinkedImage,
    ProcessColor,
    TextFrame,
)
from .model import Path as ArtworkPath
from .modern import (
    ModernDiagnostic,
    ModernReadLimits,
    PdfName,
    PdfRef,
    PdfValue,
    _parse_objects,
)

_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_DISPLAY_EXCLUDED_KEYS = frozenset(
    {
        "PieceInfo",
        "Metadata",
        "LastModified",
        "Thumb",
        "Parent",
        "Length",
    }
)
DEFAULT_PREVIEW_DPI = 144
DEFAULT_RENDER_TIMEOUT = 60.0
DEFAULT_MAX_RASTER_PIXELS = 100_000_000


@dataclass(frozen=True, slots=True)
class PdfDisplayPage:
    """Source evidence for one PDF page in page-tree order."""

    index: int
    object_ref: str
    media_box: tuple[float, float, float, float] | None
    crop_box: tuple[float, float, float, float] | None
    rotation: int
    page_last_modified: str | None
    illustrator_last_modified: str | None
    private_data_freshness: str
    content_refs: tuple[str, ...]
    content_sha256: str
    resources_sha256: str
    display_sha256: str

    def to_dict(self) -> dict[str, object]:
        data = asdict(self)
        data["media_box"] = list(self.media_box) if self.media_box is not None else None
        data["crop_box"] = list(self.crop_box) if self.crop_box is not None else None
        data["content_refs"] = list(self.content_refs)
        return data


@dataclass(frozen=True, slots=True)
class PdfDisplayResult:
    """Bounded evidence for the PDF-visible half of a modern AI container."""

    path: str | None
    source_sha256: str | None
    status: str
    pages: tuple[PdfDisplayPage, ...]
    display_sha256: str | None
    private_data_freshness: str
    diagnostics: tuple[ModernDiagnostic, ...]

    @property
    def valid(self) -> bool:
        return self.status == "extracted" and not any(
            diagnostic.severity == "error" for diagnostic in self.diagnostics
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "profile": "pdf-display-evidence-v1",
            "status": self.status,
            "valid": self.valid,
            "path": self.path,
            "source_sha256": self.source_sha256,
            "page_count": len(self.pages),
            "display_sha256": self.display_sha256,
            "private_data_freshness": self.private_data_freshness,
            "pages": [page.to_dict() for page in self.pages],
            "diagnostics": [diagnostic.to_dict() for diagnostic in self.diagnostics],
        }


@dataclass(frozen=True, slots=True)
class PreviewPage:
    index: int
    width: int
    height: int
    output: str
    pixel_sha256: str
    png_sha256: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class PreviewResult:
    source: str
    source_sha256: str
    renderer: str
    renderer_version: str
    dpi: int
    pages: tuple[PreviewPage, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "profile": "deterministic-raster-preview-v1",
            "source": self.source,
            "source_sha256": self.source_sha256,
            "renderer": self.renderer,
            "renderer_version": self.renderer_version,
            "dpi": self.dpi,
            "page_count": len(self.pages),
            "pages": [page.to_dict() for page in self.pages],
        }


@dataclass(frozen=True, slots=True)
class VisualDiffPage:
    index: int
    width: int
    height: int
    changed_pixels: int
    changed_ratio: float
    mean_absolute_difference: float
    maximum_channel_difference: int
    changed_bounds: tuple[int, int, int, int] | None
    output: str
    png_sha256: str

    def to_dict(self) -> dict[str, object]:
        data = asdict(self)
        data["changed_bounds"] = (
            list(self.changed_bounds) if self.changed_bounds is not None else None
        )
        return data


@dataclass(frozen=True, slots=True)
class VisualDiffResult:
    before: PreviewResult
    after: PreviewResult
    threshold: int
    pages: tuple[VisualDiffPage, ...]

    @property
    def equal(self) -> bool:
        return all(page.changed_pixels == 0 for page in self.pages)

    def to_dict(self) -> dict[str, object]:
        return {
            "profile": "pixel-visual-diff-v1",
            "equal": self.equal,
            "threshold": self.threshold,
            "page_count": len(self.pages),
            "changed_pixels": sum(page.changed_pixels for page in self.pages),
            "before": self.before.to_dict(),
            "after": self.after.to_dict(),
            "pages": [page.to_dict() for page in self.pages],
        }


def _source_bytes(
    source: str | Path | bytes, limits: ModernReadLimits
) -> tuple[str | None, bytes]:
    if isinstance(source, bytes):
        path = None
        data = source
    else:
        source_path = Path(source)
        path = str(source_path)
        if source_path.stat().st_size > limits.max_pdf_bytes:
            raise ValueError(f"PDF exceeds {limits.max_pdf_bytes} bytes")
        data = source_path.read_bytes()
    if len(data) > limits.max_pdf_bytes:
        raise ValueError(f"PDF exceeds {limits.max_pdf_bytes} bytes")
    return path, data


def _box(value: PdfValue) -> tuple[float, float, float, float] | None:
    if not isinstance(value, tuple) or len(value) != 4:
        return None
    if not all(isinstance(item, (int, float)) and not isinstance(item, bool) for item in value):
        return None
    return tuple(float(item) for item in value)  # type: ignore[return-value]


def _canonical_pdf_value(
    value: PdfValue,
    *,
    objects: dict[tuple[int, int], Any],
    source: bytes,
    max_depth: int,
    depth: int = 0,
    seen: frozenset[PdfRef] = frozenset(),
) -> object:
    if depth > max_depth:
        return {"limit": "reference-depth"}
    if isinstance(value, PdfRef):
        if value in seen:
            return {"cycle": value.label()}
        obj = objects.get((value.object_number, value.generation))
        if obj is None:
            return {"missing": value.label()}
        result: dict[str, object] = {
            "value": _canonical_pdf_value(
                obj.value,
                objects=objects,
                source=source,
                max_depth=max_depth,
                depth=depth + 1,
                seen=seen | {value},
            )
        }
        if obj.stream_start is not None and obj.stream_end is not None:
            stream = source[obj.stream_start : obj.stream_end]
            result["stream_sha256"] = hashlib.sha256(stream).hexdigest()
            result["stream_size"] = len(stream)
        return result
    if isinstance(value, PdfName):
        return {"name": value.value}
    if isinstance(value, bytes):
        return {"bytes_sha256": hashlib.sha256(value).hexdigest(), "size": len(value)}
    if isinstance(value, tuple):
        return [
            _canonical_pdf_value(
                item,
                objects=objects,
                source=source,
                max_depth=max_depth,
                depth=depth + 1,
                seen=seen,
            )
            for item in value
        ]
    if isinstance(value, dict):
        return {
            key: _canonical_pdf_value(
                item,
                objects=objects,
                source=source,
                max_depth=max_depth,
                depth=depth + 1,
                seen=seen,
            )
            for key, item in sorted(value.items())
            if key not in _DISPLAY_EXCLUDED_KEYS
        }
    return value


def _evidence_digest(value: object) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("ascii")
    return hashlib.sha256(payload).hexdigest()


def _content_refs(value: PdfValue) -> tuple[str, ...]:
    if isinstance(value, PdfRef):
        return (value.label(),)
    if isinstance(value, tuple):
        return tuple(item.label() for item in value if isinstance(item, PdfRef))
    return ()


def extract_pdf_display(
    source: str | Path | bytes,
    *,
    limits: ModernReadLimits | None = None,
) -> PdfDisplayResult:
    """Extract bounded page/content/resource evidence without interpreting artwork."""

    active_limits = limits or ModernReadLimits()
    try:
        path, data = _source_bytes(source, active_limits)
    except (OSError, ValueError) as error:
        return PdfDisplayResult(
            path=str(source) if not isinstance(source, bytes) else None,
            source_sha256=None,
            status="failed",
            pages=(),
            display_sha256=None,
            private_data_freshness="unverified",
            diagnostics=(ModernDiagnostic("error", "pdf_display_read_failed", str(error)),),
        )
    source_hash = hashlib.sha256(data).hexdigest()
    if not data.startswith(b"%PDF-"):
        return PdfDisplayResult(
            path=path,
            source_sha256=source_hash,
            status="unsupported",
            pages=(),
            display_sha256=None,
            private_data_freshness="unverified",
            diagnostics=(
                ModernDiagnostic(
                    "error",
                    "pdf_display_requires_pdf",
                    "Display extraction requires a PDF or PDF-compatible AI container.",
                ),
            ),
        )

    diagnostics: list[ModernDiagnostic] = []
    objects = _parse_objects(data, active_limits, diagnostics)
    catalogs = [
        obj
        for obj in objects.values()
        if isinstance(obj.value, dict)
        and isinstance(obj.value.get("Type"), PdfName)
        and obj.value["Type"].value == "Catalog"
    ]
    page_entries: list[tuple[Any, dict[str, PdfValue]]] = []
    visited: set[PdfRef] = set()

    def walk(value: PdfValue, inherited: dict[str, PdfValue], depth: int) -> None:
        if depth > active_limits.max_reference_depth:
            diagnostics.append(
                ModernDiagnostic(
                    "error",
                    "pdf_page_tree_depth_limit_exceeded",
                    f"PDF page tree exceeds depth {active_limits.max_reference_depth}.",
                )
            )
            return
        if not isinstance(value, PdfRef):
            return
        if value in visited:
            diagnostics.append(
                ModernDiagnostic(
                    "error",
                    "pdf_page_tree_cycle",
                    f"PDF page tree contains a cycle at {value.label()}.",
                )
            )
            return
        visited.add(value)
        obj = objects.get((value.object_number, value.generation))
        if obj is None or not isinstance(obj.value, dict):
            diagnostics.append(
                ModernDiagnostic(
                    "error",
                    "missing_pdf_page_reference",
                    f"Page tree reference {value.label()} is missing or not a dictionary.",
                )
            )
            return
        node = obj.value
        current = dict(inherited)
        for key in ("MediaBox", "CropBox", "Resources", "Rotate"):
            if key in node:
                current[key] = node[key]
        node_type = node.get("Type")
        if isinstance(node_type, PdfName) and node_type.value == "Page":
            page_entries.append((obj, current | node))
            return
        kids = node.get("Kids")
        if not isinstance(kids, tuple):
            diagnostics.append(
                ModernDiagnostic(
                    "error",
                    "invalid_pdf_page_tree",
                    f"Page tree node {value.label()} has no direct /Kids array.",
                )
            )
            return
        for kid in kids:
            walk(kid, current, depth + 1)

    for catalog in catalogs[:1]:
        assert isinstance(catalog.value, dict)
        walk(catalog.value.get("Pages"), {}, 0)

    if not page_entries:
        # A bounded fallback still reports evidence for malformed PDFs that omit a usable Catalog.
        page_entries = [
            (obj, obj.value)
            for obj in objects.values()
            if isinstance(obj.value, dict)
            and isinstance(obj.value.get("Type"), PdfName)
            and obj.value["Type"].value == "Page"
        ]
        if page_entries:
            diagnostics.append(
                ModernDiagnostic(
                    "warning",
                    "pdf_page_tree_fallback",
                    "Pages were found by object order because the Catalog page tree was unusable.",
                )
            )

    pages: list[PdfDisplayPage] = []
    consistency_states: list[str] = []

    def resolve(value: PdfValue, depth: int = 0) -> PdfValue:
        seen_refs: set[PdfRef] = set()
        while isinstance(value, PdfRef):
            if depth > active_limits.max_reference_depth or value in seen_refs:
                return None
            seen_refs.add(value)
            obj = objects.get((value.object_number, value.generation))
            if obj is None:
                return None
            value = obj.value
            depth += 1
        return value

    def timestamp(value: PdfValue) -> str | None:
        resolved = resolve(value)
        if isinstance(resolved, bytes):
            return resolved.decode("latin-1", errors="replace")
        return None

    for index, (obj, page) in enumerate(page_entries, start=1):
        contents = page.get("Contents")
        resources = page.get("Resources")
        content_evidence = _canonical_pdf_value(
            contents,
            objects=objects,
            source=data,
            max_depth=active_limits.max_reference_depth,
        )
        resource_evidence = _canonical_pdf_value(
            resources,
            objects=objects,
            source=data,
            max_depth=active_limits.max_reference_depth,
        )
        media_box = _box(page.get("MediaBox"))
        crop_box = _box(page.get("CropBox")) or media_box
        rotation_value = page.get("Rotate", 0)
        rotation = rotation_value if isinstance(rotation_value, int) else 0
        page_modified = timestamp(page.get("LastModified"))
        illustrator_modified: str | None = None
        piece_info = resolve(page.get("PieceInfo"))
        if isinstance(piece_info, dict):
            illustrator = resolve(piece_info.get("Illustrator"))
            if isinstance(illustrator, dict):
                illustrator_modified = timestamp(illustrator.get("LastModified"))
        if page_modified is not None and illustrator_modified is not None:
            freshness = (
                "timestamps_match"
                if page_modified == illustrator_modified
                else "timestamp_mismatch"
            )
        elif illustrator_modified is None:
            freshness = "no_private_data_timestamp"
        else:
            freshness = "unverified"
        consistency_states.append(freshness)
        if freshness == "timestamp_mismatch":
            diagnostics.append(
                ModernDiagnostic(
                    "warning",
                    "pdf_private_data_last_modified_mismatch",
                    f"Page {index} PDF /LastModified ({page_modified}) differs from "
                    f"Illustrator PieceInfo /LastModified ({illustrator_modified}).",
                    object_ref=obj.ref.label(),
                )
            )
        content_hash = _evidence_digest(content_evidence)
        resource_hash = _evidence_digest(resource_evidence)
        page_evidence = {
            "media_box": media_box,
            "crop_box": crop_box,
            "rotation": rotation % 360,
            "contents": content_evidence,
            "resources": resource_evidence,
        }
        pages.append(
            PdfDisplayPage(
                index=index,
                object_ref=obj.ref.label(),
                media_box=media_box,
                crop_box=crop_box,
                rotation=rotation % 360,
                page_last_modified=page_modified,
                illustrator_last_modified=illustrator_modified,
                private_data_freshness=freshness,
                content_refs=_content_refs(contents),
                content_sha256=content_hash,
                resources_sha256=resource_hash,
                display_sha256=_evidence_digest(page_evidence),
            )
        )

    if not pages:
        diagnostics.append(
            ModernDiagnostic("error", "pdf_has_no_pages", "PDF has no readable display pages.")
        )
    status = "extracted" if pages else "failed"
    if any(item.severity == "error" for item in diagnostics):
        status = "partial" if pages else "failed"
    document_hash = _evidence_digest([page.to_dict() for page in pages]) if pages else None
    if "timestamp_mismatch" in consistency_states:
        overall_freshness = "timestamp_mismatch"
    elif consistency_states and all(state == "timestamps_match" for state in consistency_states):
        overall_freshness = "timestamps_match"
    elif consistency_states and all(
        state == "no_private_data_timestamp" for state in consistency_states
    ):
        overall_freshness = "not_applicable"
    else:
        overall_freshness = "unverified"
    return PdfDisplayResult(
        path=path,
        source_sha256=source_hash,
        status=status,
        pages=tuple(pages),
        display_sha256=document_hash,
        private_data_freshness=overall_freshness,
        diagnostics=tuple(diagnostics),
    )


def _png_chunk(name: bytes, payload: bytes) -> bytes:
    return (
        struct.pack(">I", len(payload))
        + name
        + payload
        + struct.pack(">I", zlib.crc32(name + payload) & 0xFFFFFFFF)
    )


def _encode_png_rgba(width: int, height: int, pixels: bytes) -> bytes:
    if width <= 0 or height <= 0 or len(pixels) != width * height * 4:
        raise ValueError("RGBA pixels do not match positive image dimensions")
    scanlines = bytearray()
    stride = width * 4
    for row in range(height):
        scanlines.append(0)
        scanlines.extend(pixels[row * stride : (row + 1) * stride])
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    return (
        _PNG_SIGNATURE
        + _png_chunk(b"IHDR", ihdr)
        + _png_chunk(b"IDAT", zlib.compress(bytes(scanlines), level=9))
        + _png_chunk(b"IEND", b"")
    )


def _paeth(left: int, above: int, upper_left: int) -> int:
    estimate = left + above - upper_left
    left_distance = abs(estimate - left)
    above_distance = abs(estimate - above)
    diagonal_distance = abs(estimate - upper_left)
    if left_distance <= above_distance and left_distance <= diagonal_distance:
        return left
    if above_distance <= diagonal_distance:
        return above
    return upper_left


def _read_png_rgba(
    data: bytes, *, max_pixels: int = DEFAULT_MAX_RASTER_PIXELS
) -> tuple[int, int, bytes]:
    if not data.startswith(_PNG_SIGNATURE):
        raise ValueError("renderer did not produce a PNG")
    position = len(_PNG_SIGNATURE)
    width = height = color_type = bit_depth = interlace = None
    compressed = bytearray()
    while position + 12 <= len(data):
        length = struct.unpack(">I", data[position : position + 4])[0]
        name = data[position + 4 : position + 8]
        payload_start = position + 8
        payload_end = payload_start + length
        if payload_end + 4 > len(data):
            raise ValueError("truncated PNG chunk")
        payload = data[payload_start:payload_end]
        expected_crc = struct.unpack(">I", data[payload_end : payload_end + 4])[0]
        if zlib.crc32(name + payload) & 0xFFFFFFFF != expected_crc:
            raise ValueError("PNG chunk checksum mismatch")
        if name == b"IHDR":
            width, height, bit_depth, color_type, compression, filtering, interlace = (
                struct.unpack(">IIBBBBB", payload)
            )
            if compression != 0 or filtering != 0:
                raise ValueError("unsupported PNG compression or filtering method")
        elif name == b"IDAT":
            compressed.extend(payload)
        elif name == b"IEND":
            break
        position = payload_end + 4
    if width is None or height is None or color_type is None or bit_depth is None:
        raise ValueError("PNG has no usable IHDR")
    if width <= 0 or height <= 0 or width * height > max_pixels:
        raise ValueError(f"PNG exceeds {max_pixels} pixels")
    if bit_depth != 8 or interlace != 0 or color_type not in {0, 2, 4, 6}:
        raise ValueError("renderer PNG must be non-interlaced 8-bit gray, RGB, gray-alpha, or RGBA")
    channels = {0: 1, 2: 3, 4: 2, 6: 4}[color_type]
    stride = width * channels
    decoded = zlib.decompress(bytes(compressed))
    if len(decoded) != height * (stride + 1):
        raise ValueError("PNG scanline size does not match IHDR")
    rows: list[bytearray] = []
    cursor = 0
    for _row_index in range(height):
        filter_type = decoded[cursor]
        cursor += 1
        raw = decoded[cursor : cursor + stride]
        cursor += stride
        restored = bytearray(stride)
        previous = rows[-1] if rows else bytearray(stride)
        for index, byte in enumerate(raw):
            left = restored[index - channels] if index >= channels else 0
            above = previous[index]
            upper_left = previous[index - channels] if index >= channels else 0
            if filter_type == 0:
                value = byte
            elif filter_type == 1:
                value = byte + left
            elif filter_type == 2:
                value = byte + above
            elif filter_type == 3:
                value = byte + ((left + above) // 2)
            elif filter_type == 4:
                value = byte + _paeth(left, above, upper_left)
            else:
                raise ValueError(f"unsupported PNG filter {filter_type}")
            restored[index] = value & 0xFF
        rows.append(restored)
    rgba = bytearray(width * height * 4)
    output = 0
    for row in rows:
        for column in range(width):
            offset = column * channels
            if color_type == 0:
                red = green = blue = row[offset]
                alpha = 255
            elif color_type == 2:
                red, green, blue = row[offset : offset + 3]
                alpha = 255
            elif color_type == 4:
                red = green = blue = row[offset]
                alpha = row[offset + 1]
            else:
                red, green, blue, alpha = row[offset : offset + 4]
            rgba[output : output + 4] = bytes((red, green, blue, alpha))
            output += 4
    return width, height, bytes(rgba)


def _page_output(base: Path, index: int) -> Path:
    if index == 1:
        return base
    return base.with_name(f"{base.stem}-{index}{base.suffix}")


def _renderer_version(executable: str) -> str:
    process = subprocess.run(
        [executable, "-v"],
        capture_output=True,
        check=False,
        timeout=10,
        env={**os.environ, "LC_ALL": "C"},
    )
    output = (process.stderr or process.stdout).decode("utf-8", errors="replace").splitlines()
    return output[0].strip() if output else "unknown"


def render_pdf_preview(
    source: str | Path,
    output: str | Path,
    *,
    dpi: int = DEFAULT_PREVIEW_DPI,
    page: int | None = None,
    renderer: str = "pdftocairo",
    timeout: float = DEFAULT_RENDER_TIMEOUT,
    overwrite: bool = False,
) -> PreviewResult:
    """Render normalized PNG pages through an explicit Poppler backend."""

    if not 36 <= dpi <= 600:
        raise ValueError("preview DPI must be between 36 and 600")
    if timeout <= 0:
        raise ValueError("render timeout must be positive")
    source_path = Path(source)
    output_path = Path(output)
    if output_path.suffix.lower() != ".png":
        raise ValueError("preview output must use a .png suffix")
    display = extract_pdf_display(source_path)
    if not display.valid:
        messages = "; ".join(item.message for item in display.diagnostics)
        raise ValueError(f"PDF display extraction failed: {messages}")
    page_numbers = [page] if page is not None else list(range(1, len(display.pages) + 1))
    if any(number < 1 or number > len(display.pages) for number in page_numbers):
        raise ValueError(f"preview page must be between 1 and {len(display.pages)}")
    destinations = [
        output_path if page is not None else _page_output(output_path, index)
        for index in range(1, len(page_numbers) + 1)
    ]
    existing = [path for path in destinations if path.exists()]
    if existing and not overwrite:
        raise FileExistsError(f"refusing to overwrite existing preview: {existing[0]}")
    executable = shutil.which(renderer)
    if executable is None:
        raise RuntimeError(
            f"{renderer} was not found; install Poppler or select an available renderer"
        )

    rendered: list[PreviewPage] = []
    with tempfile.TemporaryDirectory(prefix="py-ai-preview-") as directory:
        temporary = Path(directory)
        for result_index, (page_number, destination) in enumerate(
            zip(page_numbers, destinations, strict=True), start=1
        ):
            prefix = temporary / f"page-{page_number}"
            process = subprocess.run(
                [
                    executable,
                    "-png",
                    "-singlefile",
                    "-r",
                    str(dpi),
                    "-f",
                    str(page_number),
                    "-l",
                    str(page_number),
                    str(source_path),
                    str(prefix),
                ],
                capture_output=True,
                check=False,
                timeout=timeout,
                env={**os.environ, "LC_ALL": "C"},
            )
            generated = prefix.with_suffix(".png")
            if process.returncode != 0 or not generated.exists():
                detail = process.stderr.decode("utf-8", errors="replace").strip()
                raise RuntimeError(f"{renderer} failed for page {page_number}: {detail}")
            width, height, pixels = _read_png_rgba(generated.read_bytes())
            normalized = _encode_png_rgba(width, height, pixels)
            destination.write_bytes(normalized)
            rendered.append(
                PreviewPage(
                    index=page_number if page is not None else result_index,
                    width=width,
                    height=height,
                    output=str(destination),
                    pixel_sha256=hashlib.sha256(pixels).hexdigest(),
                    png_sha256=hashlib.sha256(normalized).hexdigest(),
                )
            )
    return PreviewResult(
        source=str(source_path),
        source_sha256=hashlib.sha256(source_path.read_bytes()).hexdigest(),
        renderer=renderer,
        renderer_version=_renderer_version(executable),
        dpi=dpi,
        pages=tuple(rendered),
    )


def _rgba_color(color: ProcessColor | None) -> tuple[int, int, int, int]:
    if color is None:
        return (0, 0, 0, 0)
    if isinstance(color, Color):
        values = (color.red, color.green, color.blue)
    else:
        assert isinstance(color, CmykColor)
        values = (
            1 - min(1.0, color.cyan + color.black),
            1 - min(1.0, color.magenta + color.black),
            1 - min(1.0, color.yellow + color.black),
        )
    return tuple(round(value * 255) for value in values) + (255,)  # type: ignore[return-value]


def _cubic(
    start: tuple[float, float],
    first: tuple[float, float],
    second: tuple[float, float],
    end: tuple[float, float],
    t: float,
) -> tuple[float, float]:
    inverse = 1 - t
    return (
        inverse**3 * start[0]
        + 3 * inverse**2 * t * first[0]
        + 3 * inverse * t**2 * second[0]
        + t**3 * end[0],
        inverse**3 * start[1]
        + 3 * inverse**2 * t * first[1]
        + 3 * inverse * t**2 * second[1]
        + t**3 * end[1],
    )


def _flatten_path(path: ArtworkPath, scale: float, height: int) -> list[tuple[float, float]]:
    if not path.points:
        return []

    def raster(x: float, y: float) -> tuple[float, float]:
        return x * scale, height - y * scale

    output = [raster(path.points[0].x, path.points[0].y)]
    previous = path.points[0]
    for point in path.points[1:]:
        if previous.out_handle is not None or point.in_handle is not None:
            first = previous.out_handle or previous
            second = point.in_handle or point
            for step in range(1, 17):
                output.append(
                    raster(
                        *_cubic(
                            (previous.x, previous.y),
                            (first.x, first.y),
                            (second.x, second.y),
                            (point.x, point.y),
                            step / 16,
                        )
                    )
                )
        else:
            output.append(raster(point.x, point.y))
        previous = point
    return output


def _put_pixel(
    pixels: bytearray,
    width: int,
    height: int,
    x: int,
    y: int,
    color: tuple[int, int, int, int],
) -> None:
    if 0 <= x < width and 0 <= y < height:
        offset = (y * width + x) * 4
        pixels[offset : offset + 4] = bytes(color)


def _fill_polygon(
    pixels: bytearray,
    width: int,
    height: int,
    points: list[tuple[float, float]],
    color: tuple[int, int, int, int],
) -> None:
    if len(points) < 3:
        return
    minimum_y = max(0, int(min(point[1] for point in points)))
    maximum_y = min(height - 1, int(max(point[1] for point in points)) + 1)
    closed = [*points, points[0]]
    for y in range(minimum_y, maximum_y + 1):
        scan = y + 0.5
        intersections: list[float] = []
        for first, second in zip(closed, closed[1:], strict=False):
            if (first[1] <= scan < second[1]) or (second[1] <= scan < first[1]):
                ratio = (scan - first[1]) / (second[1] - first[1])
                intersections.append(first[0] + ratio * (second[0] - first[0]))
        intersections.sort()
        for left, right in zip(intersections[::2], intersections[1::2], strict=False):
            for x in range(max(0, int(left)), min(width, int(right) + 1)):
                _put_pixel(pixels, width, height, x, y, color)


def _stroke_segment(
    pixels: bytearray,
    width: int,
    height: int,
    first: tuple[float, float],
    second: tuple[float, float],
    line_width: float,
    color: tuple[int, int, int, int],
) -> None:
    radius = max(0.5, line_width / 2)
    minimum_x = max(0, int(min(first[0], second[0]) - radius - 1))
    maximum_x = min(width - 1, int(max(first[0], second[0]) + radius + 1))
    minimum_y = max(0, int(min(first[1], second[1]) - radius - 1))
    maximum_y = min(height - 1, int(max(first[1], second[1]) + radius + 1))
    dx = second[0] - first[0]
    dy = second[1] - first[1]
    length_squared = dx * dx + dy * dy
    for y in range(minimum_y, maximum_y + 1):
        for x in range(minimum_x, maximum_x + 1):
            if length_squared == 0:
                ratio = 0.0
            else:
                ratio = max(
                    0.0,
                    min(1.0, ((x - first[0]) * dx + (y - first[1]) * dy) / length_squared),
                )
            closest_x = first[0] + ratio * dx
            closest_y = first[1] + ratio * dy
            if (x - closest_x) ** 2 + (y - closest_y) ** 2 <= radius**2:
                _put_pixel(pixels, width, height, x, y, color)


def _paint_path(
    pixels: bytearray,
    width: int,
    height: int,
    path: ArtworkPath,
    scale: float,
) -> None:
    points = _flatten_path(path, scale, height)
    if path.fill is not None and path.closed:
        _fill_polygon(pixels, width, height, points, _rgba_color(path.fill))
    if path.stroke is not None and len(points) >= 2:
        segments = list(zip(points, points[1:], strict=False))
        if path.closed and points[-1] != points[0]:
            segments.append((points[-1], points[0]))
        for first, second in segments:
            _stroke_segment(
                pixels,
                width,
                height,
                first,
                second,
                path.stroke_width * scale,
                _rgba_color(path.stroke),
            )


def _paint_text(
    pixels: bytearray,
    width: int,
    height: int,
    text: TextFrame,
    scale: float,
) -> None:
    size = max(1, round(text.font_size * scale))
    cell = max(1, size // 7)
    origin_x = round(text.x * scale)
    baseline = round(height - text.y * scale)
    color = _rgba_color(text.fill)
    for character_index, character in enumerate(text.text):
        if character.isspace():
            continue
        code = ord(character)
        left = origin_x + character_index * max(1, round(size * 0.6))
        for row in range(7):
            for column in range(5):
                bit = (code >> ((row * 5 + column) % 11)) & 1
                if not bit and row not in {0, 6}:
                    continue
                for offset_y in range(cell):
                    for offset_x in range(cell):
                        _put_pixel(
                            pixels,
                            width,
                            height,
                            left + column * cell + offset_x,
                            baseline - size + row * cell + offset_y,
                            color,
                        )


def _paint_image(
    pixels: bytearray,
    width: int,
    height: int,
    image: LinkedImage,
    scale: float,
) -> None:
    digest = hashlib.sha256(image.source.encode("utf-8")).digest()
    first = (digest[0], digest[1], digest[2], 255)
    second = (240, 240, 240, 255)
    left = round(image.x * scale)
    right = round((image.x + image.width) * scale)
    top = round(height - (image.y + image.height) * scale)
    bottom = round(height - image.y * scale)
    for y in range(max(0, min(top, bottom)), min(height, max(top, bottom))):
        for x in range(max(0, min(left, right)), min(width, max(left, right))):
            color = first if ((x // 8) + (y // 8)) % 2 else second
            _put_pixel(pixels, width, height, x, y, color)


def _paint_container(
    pixels: bytearray,
    width: int,
    height: int,
    container: Layer | Group,
    scale: float,
) -> None:
    for item in container.ordered_items():
        if isinstance(item, ArtworkPath):
            _paint_path(pixels, width, height, item, scale)
        elif isinstance(item, TextFrame):
            _paint_text(pixels, width, height, item, scale)
        elif isinstance(item, LinkedImage):
            _paint_image(pixels, width, height, item, scale)
        elif isinstance(item, CompoundPath | ClippingGroup):
            for path in item.paths:
                _paint_path(pixels, width, height, path, scale)
        elif isinstance(item, Group):
            _paint_container(pixels, width, height, item, scale)


def render_legacy_preview(
    source: str | Path,
    output: str | Path,
    *,
    dpi: int = DEFAULT_PREVIEW_DPI,
    overwrite: bool = False,
) -> PreviewResult:
    """Render a deterministic reference raster from the trusted legacy IR subset."""

    if not 36 <= dpi <= 600:
        raise ValueError("preview DPI must be between 36 and 600")
    source_path = Path(source)
    output_path = Path(output)
    if output_path.suffix.lower() != ".png":
        raise ValueError("preview output must use a .png suffix")
    if output_path.exists() and not overwrite:
        raise FileExistsError(f"refusing to overwrite existing preview: {output_path}")
    result = read_ai7(source_path)
    scale = dpi / 72
    width = max(1, round(result.document.width * scale))
    height = max(1, round(result.document.height * scale))
    if width * height > DEFAULT_MAX_RASTER_PIXELS:
        raise ValueError(f"legacy preview exceeds {DEFAULT_MAX_RASTER_PIXELS} pixels")
    pixels = bytearray(b"\xff" * (width * height * 4))
    for layer in result.document.layers:
        if layer.visible:
            _paint_container(pixels, width, height, layer, scale)
    png = _encode_png_rgba(width, height, bytes(pixels))
    output_path.write_bytes(png)
    source_hash = hashlib.sha256(source_path.read_bytes()).hexdigest()
    return PreviewResult(
        source=str(source_path),
        source_sha256=source_hash,
        renderer="py-ai-legacy-ir",
        renderer_version="legacy-ir-reference-v1",
        dpi=dpi,
        pages=(
            PreviewPage(
                index=1,
                width=width,
                height=height,
                output=str(output_path),
                pixel_sha256=hashlib.sha256(bytes(pixels)).hexdigest(),
                png_sha256=hashlib.sha256(png).hexdigest(),
            ),
        ),
    )


def render_preview(
    source: str | Path,
    output: str | Path,
    *,
    dpi: int = DEFAULT_PREVIEW_DPI,
    page: int | None = None,
    renderer: str = "pdftocairo",
    timeout: float = DEFAULT_RENDER_TIMEOUT,
    overwrite: bool = False,
) -> PreviewResult:
    """Dispatch to PDF display rendering or the deterministic legacy IR backend."""

    report = inspect_file(source)
    if report.format is FileFormat.LEGACY_AI:
        if page not in (None, 1):
            raise ValueError("legacy IR preview has one document canvas page")
        return render_legacy_preview(source, output, dpi=dpi, overwrite=overwrite)
    if report.format in {FileFormat.PDF_COMPATIBLE_AI, FileFormat.PDF}:
        return render_pdf_preview(
            source,
            output,
            dpi=dpi,
            page=page,
            renderer=renderer,
            timeout=timeout,
            overwrite=overwrite,
        )
    raise ValueError(f"preview does not support {report.format.value}")


def _pixel_at(pixels: bytes, width: int, height: int, x: int, y: int) -> tuple[int, int, int, int]:
    if x >= width or y >= height:
        return (255, 255, 255, 255)
    offset = (y * width + x) * 4
    return tuple(pixels[offset : offset + 4])  # type: ignore[return-value]


def visual_diff(
    before: str | Path,
    after: str | Path,
    output: str | Path,
    *,
    dpi: int = DEFAULT_PREVIEW_DPI,
    threshold: int = 0,
    renderer: str = "pdftocairo",
    timeout: float = DEFAULT_RENDER_TIMEOUT,
    overwrite: bool = False,
) -> VisualDiffResult:
    """Rasterize two supported documents identically and write heat-map PNGs."""

    if not 0 <= threshold <= 255:
        raise ValueError("visual diff threshold must be between 0 and 255")
    output_path = Path(output)
    if output_path.suffix.lower() != ".png":
        raise ValueError("visual diff output must use a .png suffix")
    with tempfile.TemporaryDirectory(prefix="py-ai-visual-diff-") as directory:
        root = Path(directory)
        before_result = render_preview(
            before,
            root / "before.png",
            dpi=dpi,
            renderer=renderer,
            timeout=timeout,
        )
        after_result = render_preview(
            after,
            root / "after.png",
            dpi=dpi,
            renderer=renderer,
            timeout=timeout,
        )
        page_count = max(len(before_result.pages), len(after_result.pages))
        destinations = [_page_output(output_path, index) for index in range(1, page_count + 1)]
        existing = [path for path in destinations if path.exists()]
        if existing and not overwrite:
            raise FileExistsError(f"refusing to overwrite existing visual diff: {existing[0]}")
        results: list[VisualDiffPage] = []
        for index, destination in enumerate(destinations, start=1):
            if index <= len(before_result.pages):
                before_png = root / Path(before_result.pages[index - 1].output).name
                before_width, before_height, before_pixels = _read_png_rgba(
                    before_png.read_bytes()
                )
            else:
                before_width = before_height = 0
                before_pixels = b""
            if index <= len(after_result.pages):
                after_png = root / Path(after_result.pages[index - 1].output).name
                after_width, after_height, after_pixels = _read_png_rgba(after_png.read_bytes())
            else:
                after_width = after_height = 0
                after_pixels = b""
            width = max(before_width, after_width)
            height = max(before_height, after_height)
            heatmap = bytearray(width * height * 4)
            changed = 0
            total_difference = 0
            maximum = 0
            min_x = width
            min_y = height
            max_x = max_y = -1
            cursor = 0
            for y in range(height):
                for x in range(width):
                    left = _pixel_at(before_pixels, before_width, before_height, x, y)
                    right = _pixel_at(after_pixels, after_width, after_height, x, y)
                    channel_differences = [abs(a - b) for a, b in zip(left, right, strict=True)]
                    pixel_difference = max(channel_differences)
                    total_difference += sum(channel_differences)
                    maximum = max(maximum, pixel_difference)
                    if pixel_difference > threshold:
                        changed += 1
                        min_x = min(min_x, x)
                        min_y = min(min_y, y)
                        max_x = max(max_x, x)
                        max_y = max(max_y, y)
                        intensity = max(96, pixel_difference)
                        heatmap[cursor : cursor + 4] = bytes(
                            (255, 255 - intensity, 255 - intensity, 255)
                        )
                    else:
                        gray = (right[0] * 77 + right[1] * 150 + right[2] * 29) // 256
                        muted = 224 + gray // 8
                        heatmap[cursor : cursor + 4] = bytes((muted, muted, muted, 255))
                    cursor += 4
            png = _encode_png_rgba(width, height, bytes(heatmap))
            destination.write_bytes(png)
            pixel_count = width * height
            bounds = (min_x, min_y, max_x + 1, max_y + 1) if changed else None
            results.append(
                VisualDiffPage(
                    index=index,
                    width=width,
                    height=height,
                    changed_pixels=changed,
                    changed_ratio=changed / pixel_count if pixel_count else 0.0,
                    mean_absolute_difference=(
                        total_difference / (pixel_count * 4) if pixel_count else 0.0
                    ),
                    maximum_channel_difference=maximum,
                    changed_bounds=bounds,
                    output=str(destination),
                    png_sha256=hashlib.sha256(png).hexdigest(),
                )
            )
    return VisualDiffResult(
        before=before_result,
        after=after_result,
        threshold=threshold,
        pages=tuple(results),
    )
