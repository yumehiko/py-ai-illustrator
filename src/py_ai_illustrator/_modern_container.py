"""PDF/container parsing and bounded PrivateData stream decoding.

This module deliberately implements only the PDF subset needed to locate
``PieceInfo / Illustrator / Private`` and its ``AIPrivateData*`` streams.  It
is not a general-purpose PDF parser and never rewrites the input.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import io
import re
import zlib
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING, TypeAlias

if TYPE_CHECKING:
    from ._modern_projection import ModernSemanticResult

DEFAULT_MAX_PDF_BYTES = 64 * 1024 * 1024
DEFAULT_MAX_OBJECTS = 100_000
DEFAULT_MAX_OBJECT_BYTES = 16 * 1024 * 1024
DEFAULT_MAX_REFERENCE_DEPTH = 64
DEFAULT_MAX_TEXT_DOCUMENT_NESTING = 64
DEFAULT_MAX_SEMANTIC_NESTING = 64
DEFAULT_MAX_SEGMENT_RAW_BYTES = 64 * 1024 * 1024
DEFAULT_MAX_SEGMENT_DECODED_BYTES = 128 * 1024 * 1024
DEFAULT_MAX_TOTAL_DECODED_BYTES = 256 * 1024 * 1024
DEFAULT_MAX_TOKENS = 2_000_000
DEFAULT_MAX_TOKEN_BYTES = 8 * 1024 * 1024
DEFAULT_MAX_ZSTD_WINDOW_BYTES = 128 * 1024 * 1024

_OBJECT_RE = re.compile(rb"(?m)(?:^|[\r\n])([0-9]+)[ \t]+([0-9]+)[ \t]+obj\b")
_PRIVATE_KEY_RE = re.compile(r"^AIPrivateData(?P<index>[0-9]+)?$")
_SECTION_RE = re.compile(
    rb"^(?:%AI[0-9]+_|%%)(?P<direction>Begin|End)_?(?P<name>[A-Za-z0-9_.-]+)"
)
_ZSTD_MARKER_RE = re.compile(rb"^%AI(?P<version>[0-9]+)_ZStandard_Data")
_PDF_WHITESPACE = b"\x00\x09\x0a\x0c\x0d\x20"
_PDF_DELIMITERS = b"()<>[]{}/%"


@dataclass(frozen=True, slots=True)
class ModernReadLimits:
    """Resource limits applied before and during PDF/PrivateData decoding."""

    max_pdf_bytes: int = DEFAULT_MAX_PDF_BYTES
    max_objects: int = DEFAULT_MAX_OBJECTS
    max_object_bytes: int = DEFAULT_MAX_OBJECT_BYTES
    max_reference_depth: int = DEFAULT_MAX_REFERENCE_DEPTH
    max_text_document_nesting: int = DEFAULT_MAX_TEXT_DOCUMENT_NESTING
    max_semantic_nesting: int = DEFAULT_MAX_SEMANTIC_NESTING
    max_segment_raw_bytes: int = DEFAULT_MAX_SEGMENT_RAW_BYTES
    max_segment_decoded_bytes: int = DEFAULT_MAX_SEGMENT_DECODED_BYTES
    max_total_decoded_bytes: int = DEFAULT_MAX_TOTAL_DECODED_BYTES
    max_tokens: int = DEFAULT_MAX_TOKENS
    max_token_bytes: int = DEFAULT_MAX_TOKEN_BYTES
    max_zstd_window_bytes: int = DEFAULT_MAX_ZSTD_WINDOW_BYTES

    def __post_init__(self) -> None:
        if any(value <= 0 for value in asdict(self).values()):
            raise ValueError("modern AI resource limits must be positive")


@dataclass(frozen=True, slots=True)
class PdfRef:
    object_number: int
    generation: int = 0

    def label(self) -> str:
        return f"{self.object_number} {self.generation} R"


@dataclass(frozen=True, slots=True)
class PdfName:
    value: str


PdfScalar: TypeAlias = None | bool | int | float | bytes | PdfName | PdfRef
PdfValue: TypeAlias = PdfScalar | tuple["PdfValue", ...] | dict[str, "PdfValue"]


@dataclass(frozen=True, slots=True)
class ModernDiagnostic:
    """A stable machine-readable diagnostic emitted by the bounded reader."""

    severity: str
    code: str
    message: str
    object_ref: str | None = None
    segment: str | None = None
    source_start: int | None = None
    source_end: int | None = None
    decoded_start: int | None = None
    decoded_end: int | None = None

    def to_dict(self) -> dict[str, object]:
        return {key: value for key, value in asdict(self).items() if value is not None}


@dataclass(frozen=True, slots=True)
class PrivateDataToken:
    """A lossless decoded-byte token; tokens cover the segment without gaps."""

    index: int
    kind: str
    start: int
    content_end: int
    end: int
    section_name: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {key: value for key, value in asdict(self).items() if value is not None}


@dataclass(frozen=True, slots=True)
class PrivateDataSection:
    """A begin/end section index over decoded bytes."""

    name: str
    start: int
    end: int
    begin_token: int
    end_token: int | None
    depth: int
    closed: bool

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class PrivateDataSegment:
    """One ordered ``AIPrivateData*`` stream and its lossless read evidence."""

    index: int
    key: str
    object_ref: PdfRef
    object_start: int
    object_end: int
    raw_start: int
    raw_end: int
    filters: tuple[str, ...]
    raw_bytes: bytes
    raw_sha256: str
    decoded_bytes: bytes | None
    decoded_sha256: str | None
    decode_status: str
    tokens: tuple[PrivateDataToken, ...] = ()
    sections: tuple[PrivateDataSection, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "index": self.index,
            "key": self.key,
            "object_ref": self.object_ref.label(),
            "object_span": {"start": self.object_start, "end": self.object_end},
            "raw_source_span": {"start": self.raw_start, "end": self.raw_end},
            "filters": list(self.filters),
            "raw_size": len(self.raw_bytes),
            "raw_sha256": self.raw_sha256,
            "decoded_size": len(self.decoded_bytes) if self.decoded_bytes is not None else None,
            "decoded_sha256": self.decoded_sha256,
            "decode_status": self.decode_status,
            "token_count": len(self.tokens),
            "section_count": len(self.sections),
            "sections": [section.to_dict() for section in self.sections],
        }


@dataclass(frozen=True, slots=True)
class ModernAIReadResult:
    """Read-only modern AI result with explicit container/extraction/semantic states."""

    path: str | None
    source_bytes: bytes | None
    source_sha256: str | None
    pdf_version: str | None
    object_count: int
    container_status: str
    private_data_status: str
    semantic_status: str
    piece_info_paths: tuple[str, ...]
    segments: tuple[PrivateDataSegment, ...]
    diagnostics: tuple[ModernDiagnostic, ...]
    semantic: ModernSemanticResult | None = None

    @property
    def valid(self) -> bool:
        return self.container_status == "parsed" and not any(
            diagnostic.severity == "error" for diagnostic in self.diagnostics
        )

    @property
    def is_pdf_compatible_ai(self) -> bool:
        return bool(self.piece_info_paths)

    def to_dict(self) -> dict[str, object]:
        return {
            "reader_profile": "modern-ai-read-only-v2",
            "read_only": True,
            "safe_to_reserialize": False,
            "path": self.path,
            "source_sha256": self.source_sha256,
            "pdf_version": self.pdf_version,
            "object_count": self.object_count,
            "container": {
                "status": self.container_status,
                "valid": self.container_status == "parsed",
            },
            "private_data": {
                "status": self.private_data_status,
                "piece_info_paths": list(self.piece_info_paths),
                "segment_count": len(self.segments),
                "segments": [segment.to_dict() for segment in self.segments],
            },
            "semantic": (
                self.semantic.to_dict()
                if self.semantic is not None
                else {
                    "profile": "modern-ai-semantic-read-only-v2",
                    "status": self.semantic_status,
                    "supported": False,
                    "read_only": True,
                    "message": "No decoded PrivateData was available for semantic projection.",
                }
            ),
            "diagnostics": [diagnostic.to_dict() for diagnostic in self.diagnostics],
        }


@dataclass(slots=True)
class _PdfObject:
    ref: PdfRef
    start: int
    end: int
    value: PdfValue
    stream_start: int | None = None
    stream_end: int | None = None


class _PdfSyntaxError(ValueError):
    pass


class _PdfSyntaxParser:
    def __init__(self, data: bytes, start: int, end: int, *, max_depth: int) -> None:
        self.data = data
        self.pos = start
        self.end = end
        self.max_depth = max_depth

    def _skip_space(self) -> None:
        while self.pos < self.end:
            byte = self.data[self.pos]
            if byte in _PDF_WHITESPACE:
                self.pos += 1
                continue
            if byte == ord("%"):
                line_end = self.pos + 1
                while line_end < self.end and self.data[line_end] not in b"\r\n":
                    line_end += 1
                self.pos = line_end
                continue
            break

    def _keyword(self, keyword: bytes) -> bool:
        self._skip_space()
        if not self.data.startswith(keyword, self.pos):
            return False
        after = self.pos + len(keyword)
        if after < self.end and self.data[after] not in _PDF_WHITESPACE + _PDF_DELIMITERS:
            return False
        self.pos = after
        return True

    def parse(self, depth: int = 0) -> PdfValue:
        if depth > self.max_depth:
            raise _PdfSyntaxError("PDF direct-object nesting limit exceeded")
        self._skip_space()
        if self.pos >= self.end:
            raise _PdfSyntaxError("unexpected end of PDF object")
        if self.data.startswith(b"<<", self.pos):
            return self._parse_dict(depth)
        byte = self.data[self.pos]
        if byte == ord("["):
            return self._parse_array(depth)
        if byte == ord("/"):
            return self._parse_name()
        if byte == ord("("):
            return self._parse_literal_string()
        if byte == ord("<"):
            return self._parse_hex_string()
        for keyword, value in ((b"true", True), (b"false", False), (b"null", None)):
            if self._keyword(keyword):
                return value
        return self._parse_number_or_keyword()

    def _parse_dict(self, depth: int) -> dict[str, PdfValue]:
        self.pos += 2
        result: dict[str, PdfValue] = {}
        while True:
            self._skip_space()
            if self.data.startswith(b">>", self.pos):
                self.pos += 2
                return result
            if self.pos >= self.end or self.data[self.pos] != ord("/"):
                raise _PdfSyntaxError("PDF dictionary key is not a name")
            key = self._parse_name().value
            result[key] = self.parse(depth + 1)

    def _parse_array(self, depth: int) -> tuple[PdfValue, ...]:
        self.pos += 1
        result: list[PdfValue] = []
        while True:
            self._skip_space()
            if self.pos >= self.end:
                raise _PdfSyntaxError("unterminated PDF array")
            if self.data[self.pos] == ord("]"):
                self.pos += 1
                return tuple(result)
            result.append(self.parse(depth + 1))

    def _parse_name(self) -> PdfName:
        self.pos += 1
        start = self.pos
        while self.pos < self.end and self.data[self.pos] not in _PDF_WHITESPACE + _PDF_DELIMITERS:
            self.pos += 1
        raw = self.data[start : self.pos]

        def replace(match: re.Match[bytes]) -> bytes:
            return bytes([int(match.group(1), 16)])

        raw = re.sub(rb"#([0-9A-Fa-f]{2})", replace, raw)
        return PdfName(raw.decode("latin-1"))

    def _parse_literal_string(self) -> bytes:
        self.pos += 1
        depth = 1
        output = bytearray()
        while self.pos < self.end:
            byte = self.data[self.pos]
            self.pos += 1
            if byte == ord("\\"):
                if self.pos >= self.end:
                    break
                escaped = self.data[self.pos]
                self.pos += 1
                simple = {ord("n"): 10, ord("r"): 13, ord("t"): 9, ord("b"): 8, ord("f"): 12}
                if escaped in simple:
                    output.append(simple[escaped])
                elif escaped in b"\r\n":
                    if escaped == 13 and self.pos < self.end and self.data[self.pos] == 10:
                        self.pos += 1
                elif ord("0") <= escaped <= ord("7"):
                    digits = bytearray([escaped])
                    while (
                        len(digits) < 3
                        and self.pos < self.end
                        and self.data[self.pos] in b"01234567"
                    ):
                        digits.append(self.data[self.pos])
                        self.pos += 1
                    output.append(int(digits, 8) & 0xFF)
                else:
                    output.append(escaped)
            elif byte == ord("("):
                depth += 1
                output.append(byte)
            elif byte == ord(")"):
                depth -= 1
                if depth == 0:
                    return bytes(output)
                output.append(byte)
            else:
                output.append(byte)
        raise _PdfSyntaxError("unterminated PDF literal string")

    def _parse_hex_string(self) -> bytes:
        self.pos += 1
        start = self.pos
        close = self.data.find(b">", start, self.end)
        if close < 0:
            raise _PdfSyntaxError("unterminated PDF hex string")
        raw = b"".join(self.data[start:close].split())
        if len(raw) % 2:
            raw += b"0"
        self.pos = close + 1
        try:
            return bytes.fromhex(raw.decode("ascii"))
        except (UnicodeDecodeError, ValueError) as error:
            raise _PdfSyntaxError("invalid PDF hex string") from error

    def _parse_number_or_keyword(self) -> PdfValue:
        start = self.pos
        while self.pos < self.end and self.data[self.pos] not in _PDF_WHITESPACE + _PDF_DELIMITERS:
            self.pos += 1
        token = self.data[start : self.pos]
        try:
            first: int | float = float(token) if b"." in token else int(token)
        except ValueError as error:
            raise _PdfSyntaxError(f"unsupported PDF token {token[:40]!r}") from error
        if not isinstance(first, int):
            return first
        saved = self.pos
        self._skip_space()
        second_start = self.pos
        while self.pos < self.end and self.data[self.pos] in b"0123456789":
            self.pos += 1
        if self.pos > second_start:
            second = int(self.data[second_start : self.pos])
            if self._keyword(b"R"):
                return PdfRef(first, second)
        self.pos = saved
        return first


def _diagnostic(
    diagnostics: list[ModernDiagnostic],
    severity: str,
    code: str,
    message: str,
    *,
    obj: _PdfObject | None = None,
    segment: str | None = None,
) -> None:
    diagnostics.append(
        ModernDiagnostic(
            severity=severity,
            code=code,
            message=message,
            object_ref=obj.ref.label() if obj else None,
            segment=segment,
            source_start=obj.start if obj else None,
            source_end=obj.end if obj else None,
        )
    )


def _parse_objects(
    data: bytes, limits: ModernReadLimits, diagnostics: list[ModernDiagnostic]
) -> dict[tuple[int, int], _PdfObject]:
    objects: dict[tuple[int, int], _PdfObject] = {}
    position = 0
    while True:
        match = _OBJECT_RE.search(data, position)
        if match is None:
            break
        if len(objects) >= limits.max_objects:
            _diagnostic(
                diagnostics,
                "error",
                "pdf_object_limit_exceeded",
                f"PDF has more than {limits.max_objects} indirect objects.",
            )
            break
        object_start = match.start(1)
        value_start = match.end()
        endobj = data.find(b"endobj", value_start)
        if endobj < 0:
            _diagnostic(
                diagnostics,
                "error",
                "malformed_pdf_object",
                f"Object {match.group(1).decode()} {match.group(2).decode()} has no endobj.",
            )
            break
        if endobj + len(b"endobj") - object_start > limits.max_object_bytes:
            _diagnostic(
                diagnostics,
                "error",
                "pdf_object_size_limit_exceeded",
                f"PDF object at byte {object_start} exceeds {limits.max_object_bytes} bytes.",
            )
            position = endobj + len(b"endobj")
            continue
        parser = _PdfSyntaxParser(
            data,
            value_start,
            endobj,
            max_depth=limits.max_reference_depth,
        )
        ref = PdfRef(int(match.group(1)), int(match.group(2)))
        try:
            value = parser.parse()
        except _PdfSyntaxError as error:
            obj = _PdfObject(ref, object_start, endobj + len(b"endobj"), None)
            _diagnostic(diagnostics, "error", "malformed_pdf_object", str(error), obj=obj)
            position = endobj + len(b"endobj")
            continue

        stream_start: int | None = None
        stream_end: int | None = None
        parser._skip_space()
        if data.startswith(b"stream", parser.pos):
            cursor = parser.pos + len(b"stream")
            if data.startswith(b"\r\n", cursor):
                cursor += 2
            elif cursor < len(data) and data[cursor] in b"\r\n":
                cursor += 1
            else:
                obj = _PdfObject(ref, object_start, endobj + len(b"endobj"), value)
                _diagnostic(
                    diagnostics,
                    "error",
                    "malformed_pdf_stream",
                    "stream keyword is not followed by an end-of-line marker.",
                    obj=obj,
                )
            stream_start = cursor
            declared_length = value.get("Length") if isinstance(value, dict) else None
            if isinstance(declared_length, int) and declared_length >= 0:
                candidate = cursor + declared_length
                if candidate <= endobj:
                    stream_end = candidate
            if stream_end is None:
                marker = data.find(b"endstream", cursor, endobj)
                if marker >= 0:
                    stream_end = marker
                    if data[max(cursor, stream_end - 2) : stream_end] == b"\r\n":
                        stream_end -= 2
                    elif stream_end > cursor and data[stream_end - 1] in b"\r\n":
                        stream_end -= 1
                else:
                    obj = _PdfObject(ref, object_start, endobj + len(b"endobj"), value)
                    _diagnostic(
                        diagnostics,
                        "error",
                        "malformed_pdf_stream",
                        "stream has no endstream marker and no usable direct Length.",
                        obj=obj,
                    )

        obj = _PdfObject(
            ref=ref,
            start=object_start,
            end=endobj + len(b"endobj"),
            value=value,
            stream_start=stream_start,
            stream_end=stream_end,
        )
        key = (ref.object_number, ref.generation)
        if key in objects:
            _diagnostic(
                diagnostics,
                "warning",
                "duplicate_pdf_object",
                f"Duplicate object {ref.label()}; the later definition is used.",
                obj=obj,
            )
        objects[key] = obj
        if isinstance(value, dict) and isinstance(value.get("Type"), PdfName):
            object_type = value["Type"]
            assert isinstance(object_type, PdfName)
            if object_type.value in {"ObjStm", "XRef"}:
                _diagnostic(
                    diagnostics,
                    "warning",
                    "unsupported_pdf_object_container",
                    f"/{object_type.value} is outside the bounded PDF reader profile.",
                    obj=obj,
                )
        position = endobj + len(b"endobj")
    return objects


class _Resolver:
    def __init__(
        self,
        objects: dict[tuple[int, int], _PdfObject],
        limits: ModernReadLimits,
        diagnostics: list[ModernDiagnostic],
    ) -> None:
        self.objects = objects
        self.limits = limits
        self.diagnostics = diagnostics

    def object_for(
        self, ref: PdfRef, *, path: str, seen: tuple[PdfRef, ...] = ()
    ) -> _PdfObject | None:
        if len(seen) >= self.limits.max_reference_depth:
            _diagnostic(
                self.diagnostics,
                "error",
                "pdf_reference_depth_limit_exceeded",
                f"Reference path {path} exceeds depth {self.limits.max_reference_depth}.",
            )
            return None
        if ref in seen:
            chain = " -> ".join(item.label() for item in (*seen, ref))
            _diagnostic(
                self.diagnostics,
                "error",
                "pdf_reference_cycle",
                f"Reference cycle while resolving {path}: {chain}.",
            )
            return None
        obj = self.objects.get((ref.object_number, ref.generation))
        if obj is None:
            _diagnostic(
                self.diagnostics,
                "error",
                "missing_pdf_reference",
                f"Missing object {ref.label()} while resolving {path}.",
            )
        return obj

    def value(self, value: PdfValue, *, path: str, seen: tuple[PdfRef, ...] = ()) -> PdfValue:
        resolved, _seen = self.value_with_seen(value, path=path, seen=seen)
        return resolved

    def value_with_seen(
        self, value: PdfValue, *, path: str, seen: tuple[PdfRef, ...] = ()
    ) -> tuple[PdfValue, tuple[PdfRef, ...]]:
        while isinstance(value, PdfRef):
            obj = self.object_for(value, path=path, seen=seen)
            if obj is None:
                return None, seen
            seen = (*seen, value)
            value = obj.value
        return value, seen


def _filter_names(value: PdfValue) -> tuple[str, ...] | None:
    if value is None:
        return ()
    if isinstance(value, PdfName):
        return (value.value,)
    if isinstance(value, tuple) and all(isinstance(item, PdfName) for item in value):
        return tuple(item.value for item in value if isinstance(item, PdfName))
    return None


class _DecodeLimitExceeded(ValueError):
    pass


def _append_bounded(output: bytearray, chunk: bytes, maximum: int) -> None:
    if len(output) + len(chunk) > maximum:
        raise _DecodeLimitExceeded(f"decoded stream exceeds {maximum} bytes")
    output.extend(chunk)


def _decode_flate(data: bytes, maximum: int) -> bytes:
    decoder = zlib.decompressobj()
    output = bytearray()
    for start in range(0, len(data), 64 * 1024):
        pending = data[start : start + 64 * 1024]
        while pending:
            remaining = maximum - len(output)
            chunk = decoder.decompress(pending, remaining + 1)
            _append_bounded(output, chunk, maximum)
            pending = decoder.unconsumed_tail
            if pending and remaining == 0:
                raise _DecodeLimitExceeded(f"decoded stream exceeds {maximum} bytes")
    _append_bounded(output, decoder.flush(maximum - len(output) + 1), maximum)
    if not decoder.eof:
        raise ValueError("truncated Flate stream")
    return bytes(output)


def _decode_zstd(data: bytes, limits: ModernReadLimits) -> bytes:
    try:
        import zstandard  # type: ignore[import-not-found]
    except ImportError as error:
        raise RuntimeError("zstandard package is required for AI ZStandard data") from error

    decompressor = zstandard.ZstdDecompressor(max_window_size=limits.max_zstd_window_bytes)
    output = bytearray()
    with decompressor.stream_reader(io.BytesIO(data), read_across_frames=False) as reader:
        while True:
            chunk = reader.read(64 * 1024)
            if not chunk:
                break
            _append_bounded(output, chunk, limits.max_segment_decoded_bytes)
    return bytes(output)


def _decode_filter(data: bytes, name: str, maximum: int) -> bytes:
    aliases = {
        "Fl": "FlateDecode",
        "AHx": "ASCIIHexDecode",
        "A85": "ASCII85Decode",
    }
    name = aliases.get(name, name)
    if name == "FlateDecode":
        return _decode_flate(data, maximum)
    if name == "ASCIIHexDecode":
        payload = data.split(b">", 1)[0]
        payload = b"".join(payload.split())
        if len(payload) % 2:
            payload += b"0"
        try:
            decoded = bytes.fromhex(payload.decode("ascii"))
        except (UnicodeDecodeError, ValueError) as error:
            raise ValueError("invalid ASCIIHexDecode stream") from error
        if len(decoded) > maximum:
            raise _DecodeLimitExceeded(f"decoded stream exceeds {maximum} bytes")
        return decoded
    if name == "ASCII85Decode":
        payload = data.strip()
        if payload.startswith(b"<~"):
            payload = payload[2:]
        if payload.endswith(b"~>"):
            payload = payload[:-2]
        try:
            decoded = base64.a85decode(payload, adobe=False, ignorechars=_PDF_WHITESPACE)
        except (ValueError, binascii.Error) as error:
            raise ValueError("invalid ASCII85Decode stream") from error
        if len(decoded) > maximum:
            raise _DecodeLimitExceeded(f"decoded stream exceeds {maximum} bytes")
        return decoded
    raise NotImplementedError(name)


def tokenize_private_data(
    data: bytes,
    *,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    max_token_bytes: int = DEFAULT_MAX_TOKEN_BYTES,
) -> tuple[tuple[PrivateDataToken, ...], tuple[PrivateDataSection, ...]]:
    """Index all decoded bytes as physical-line tokens and nested sections."""

    if max_tokens <= 0 or max_token_bytes <= 0:
        raise ValueError("PrivateData token limits must be positive")
    tokens: list[PrivateDataToken] = []
    section_stack: list[tuple[str, int, int, int]] = []
    sections: list[PrivateDataSection] = []
    position = 0
    while position < len(data):
        if len(tokens) >= max_tokens:
            raise _DecodeLimitExceeded(f"PrivateData exceeds {max_tokens} tokens")
        newline = position
        while newline < len(data) and data[newline] not in b"\r\n":
            newline += 1
        end = newline
        if end < len(data):
            end += 1
            if data[newline] == 13 and end < len(data) and data[end] == 10:
                end += 1
        if end - position > max_token_bytes:
            raise _DecodeLimitExceeded(
                f"PrivateData token at byte {position} exceeds {max_token_bytes} bytes"
            )
        content = data[position:newline]
        match = _SECTION_RE.match(content)
        section_name: str | None = None
        if match:
            section_name = match.group("name").decode("ascii").removesuffix("--")
            if section_name.startswith("Content_if_version_"):
                section_name = "Versioned_Content"
            direction = match.group("direction")
            kind = "section_begin" if direction == b"Begin" else "section_end"
        elif content.startswith(b"%!"):
            kind = "header"
        elif content.startswith(b"%"):
            kind = "comment"
        elif all(byte in b"\t" or 32 <= byte <= 126 for byte in content):
            kind = "statement" if content.strip() else "blank"
        else:
            kind = "opaque"
        token = PrivateDataToken(
            index=len(tokens),
            kind=kind,
            start=position,
            content_end=newline,
            end=end,
            section_name=section_name,
        )
        tokens.append(token)
        if kind == "section_begin" and section_name is not None:
            section_stack.append((section_name, token.index, position, len(section_stack)))
        elif kind == "section_end" and section_name is not None:
            matching = next(
                (
                    stack_index
                    for stack_index in range(len(section_stack) - 1, -1, -1)
                    if section_stack[stack_index][0] == section_name
                ),
                None,
            )
            if matching is not None:
                name, begin_token, start, depth = section_stack.pop(matching)
                sections.append(
                    PrivateDataSection(
                        name=name,
                        start=start,
                        end=end,
                        begin_token=begin_token,
                        end_token=token.index,
                        depth=depth,
                        closed=True,
                    )
                )
        position = end
    for name, begin_token, start, depth in section_stack:
        sections.append(
            PrivateDataSection(
                name=name,
                start=start,
                end=len(data),
                begin_token=begin_token,
                end_token=None,
                depth=depth,
                closed=False,
            )
        )
    sections.sort(key=lambda section: (section.start, -section.end, section.depth))
    return tuple(tokens), tuple(sections)


def _decode_segment(
    raw: bytes,
    pdf_filters: tuple[str, ...],
    limits: ModernReadLimits,
) -> tuple[bytes, tuple[str, ...]]:
    decoded = raw
    filters = list(pdf_filters)
    for filter_name in pdf_filters:
        decoded = _decode_filter(decoded, filter_name, limits.max_segment_decoded_bytes)
    marker = _ZSTD_MARKER_RE.match(decoded)
    if marker:
        marker_name = f"AI{marker.group('version').decode()}_ZStandard"
        filters.append(marker_name)
        decoded = _decode_zstd(decoded[marker.end() :], limits)
    if len(decoded) > limits.max_segment_decoded_bytes:
        raise _DecodeLimitExceeded(
            f"decoded PrivateData exceeds {limits.max_segment_decoded_bytes} bytes"
        )
    return decoded, tuple(filters)


def _empty_result(
    *,
    path: str | None,
    status: str,
    diagnostic: ModernDiagnostic,
) -> ModernAIReadResult:
    return ModernAIReadResult(
        path=path,
        source_bytes=None,
        source_sha256=None,
        pdf_version=None,
        object_count=0,
        container_status=status,
        private_data_status="failed",
        semantic_status="unsupported",
        piece_info_paths=(),
        segments=(),
        diagnostics=(diagnostic,),
    )


def read_modern_ai(
    source: str | Path | bytes,
    *,
    limits: ModernReadLimits | None = None,
) -> ModernAIReadResult:
    """Safely extract and index modern Illustrator PrivateData without writing.

    Ordinary PDFs are valid inputs and return ``private_data_status='absent'``.
    The caller can therefore distinguish a readable PDF container from a
    PDF-compatible Illustrator file and from semantic support.
    """

    active_limits = limits or ModernReadLimits()
    path: str | None = None
    if isinstance(source, bytes):
        data = source
    else:
        source_path = Path(source)
        path = str(source_path)
        size = source_path.stat().st_size
        if size > active_limits.max_pdf_bytes:
            return _empty_result(
                path=path,
                status="limit_exceeded",
                diagnostic=ModernDiagnostic(
                    "error",
                    "pdf_size_limit_exceeded",
                    f"PDF is {size} bytes; limit is {active_limits.max_pdf_bytes} bytes.",
                ),
            )
        data = source_path.read_bytes()
    if len(data) > active_limits.max_pdf_bytes:
        return _empty_result(
            path=path,
            status="limit_exceeded",
            diagnostic=ModernDiagnostic(
                "error",
                "pdf_size_limit_exceeded",
                f"PDF is {len(data)} bytes; limit is {active_limits.max_pdf_bytes} bytes.",
            ),
        )

    diagnostics: list[ModernDiagnostic] = []
    header_match = re.match(rb"%PDF-([0-9]+\.[0-9]+)", data)
    if header_match is None:
        return ModernAIReadResult(
            path=path,
            source_bytes=data,
            source_sha256=hashlib.sha256(data).hexdigest(),
            pdf_version=None,
            object_count=0,
            container_status="invalid",
            private_data_status="failed",
            semantic_status="unsupported",
            piece_info_paths=(),
            segments=(),
            diagnostics=(
                ModernDiagnostic(
                    "error", "invalid_pdf_header", "Input does not start with a PDF header."
                ),
            ),
        )
    pdf_version = header_match.group(1).decode("ascii")
    if b"%%EOF" not in data[-1024:]:
        diagnostics.append(
            ModernDiagnostic(
                "error",
                "missing_pdf_eof",
                "PDF does not have an %%EOF marker in its final 1024 bytes.",
            )
        )
    if re.search(rb"/Encrypt[ \t\r\n]+[0-9]+[ \t\r\n]+[0-9]+[ \t\r\n]+R\b", data[-4096:]):
        diagnostics.append(
            ModernDiagnostic(
                "error",
                "encrypted_pdf_unsupported",
                "Encrypted PDF containers are outside the modern AI read-only profile.",
            )
        )

    objects = _parse_objects(data, active_limits, diagnostics)
    if not objects:
        diagnostics.append(
            ModernDiagnostic("error", "no_pdf_objects", "No readable indirect PDF objects found.")
        )
    resolver = _Resolver(objects, active_limits, diagnostics)
    paths: list[str] = []
    private_dicts: list[tuple[str, dict[str, PdfValue]]] = []

    for obj in objects.values():
        if not isinstance(obj.value, dict) or "PieceInfo" not in obj.value:
            continue
        piece_path = f"{obj.ref.label()} /PieceInfo/Illustrator/Private"
        piece, seen = resolver.value_with_seen(
            obj.value["PieceInfo"], path=f"{obj.ref.label()} /PieceInfo"
        )
        if not isinstance(piece, dict) or "Illustrator" not in piece:
            continue
        paths.append(piece_path)
        illustrator, seen = resolver.value_with_seen(
            piece["Illustrator"],
            path=f"{obj.ref.label()} /PieceInfo/Illustrator",
            seen=seen,
        )
        if not isinstance(illustrator, dict) or "Private" not in illustrator:
            _diagnostic(
                diagnostics,
                "error",
                "missing_illustrator_private",
                "Illustrator PieceInfo does not contain /Private.",
                obj=obj,
            )
            continue
        private, _seen = resolver.value_with_seen(
            illustrator["Private"],
            path=f"{obj.ref.label()} /PieceInfo/Illustrator/Private",
            seen=seen,
        )
        if isinstance(private, dict):
            private_dicts.append((piece_path, private))
        else:
            _diagnostic(
                diagnostics,
                "error",
                "invalid_illustrator_private",
                "Illustrator /Private did not resolve to a dictionary.",
                obj=obj,
            )

    segment_entries: list[tuple[int, int, str, PdfValue]] = []
    for _path, private in private_dicts:
        for insertion_order, (key, value) in enumerate(private.items()):
            match = _PRIVATE_KEY_RE.match(key)
            if match:
                number = int(match.group("index") or 0)
                segment_entries.append((number, insertion_order, key, value))
    segment_entries.sort(key=lambda item: (item[0], item[1], item[2]))

    segments: list[PrivateDataSegment] = []
    seen_streams: set[tuple[int, int, str]] = set()
    total_decoded = 0
    for _number, _insertion_order, key, value in segment_entries:
        if not isinstance(value, PdfRef):
            diagnostics.append(
                ModernDiagnostic(
                    "error",
                    "invalid_private_data_reference",
                    f"/{key} is not an indirect stream reference.",
                    segment=key,
                )
            )
            continue
        dedupe_key = (value.object_number, value.generation, key)
        if dedupe_key in seen_streams:
            continue
        seen_streams.add(dedupe_key)
        obj = resolver.object_for(value, path=f"/{key}")
        if obj is None:
            continue
        if not isinstance(obj.value, dict) or obj.stream_start is None or obj.stream_end is None:
            _diagnostic(
                diagnostics,
                "error",
                "private_data_not_stream",
                f"/{key} does not resolve to a readable PDF stream.",
                obj=obj,
                segment=key,
            )
            continue
        raw = data[obj.stream_start : obj.stream_end]
        if len(raw) > active_limits.max_segment_raw_bytes:
            _diagnostic(
                diagnostics,
                "error",
                "private_data_raw_limit_exceeded",
                f"/{key} raw stream is {len(raw)} bytes; limit is "
                f"{active_limits.max_segment_raw_bytes} bytes.",
                obj=obj,
                segment=key,
            )
            continue
        filters = _filter_names(obj.value.get("Filter"))
        decoded: bytes | None = None
        decoded_hash: str | None = None
        tokens: tuple[PrivateDataToken, ...] = ()
        sections: tuple[PrivateDataSection, ...] = ()
        decode_status = "failed"
        effective_filters = filters or ()
        direct_zstd_marker = _ZSTD_MARKER_RE.match(raw) if filters == () else None
        if direct_zstd_marker:
            effective_filters = (
                f"AI{direct_zstd_marker.group('version').decode()}_ZStandard",
            )
        if filters is None:
            _diagnostic(
                diagnostics,
                "error",
                "invalid_stream_filter",
                f"/{key} has a malformed /Filter value.",
                obj=obj,
                segment=key,
            )
        elif obj.value.get("DecodeParms") not in (None,):
            _diagnostic(
                diagnostics,
                "error",
                "unsupported_stream_decode_params",
                f"/{key} uses unsupported /DecodeParms.",
                obj=obj,
                segment=key,
            )
        else:
            try:
                decoded, effective_filters = _decode_segment(raw, filters, active_limits)
                total_decoded += len(decoded)
                if total_decoded > active_limits.max_total_decoded_bytes:
                    raise _DecodeLimitExceeded(
                        "total decoded PrivateData exceeds "
                        f"{active_limits.max_total_decoded_bytes} bytes"
                    )
                tokens, sections = tokenize_private_data(
                    decoded,
                    max_tokens=active_limits.max_tokens,
                    max_token_bytes=active_limits.max_token_bytes,
                )
                decoded_hash = hashlib.sha256(decoded).hexdigest()
                decode_status = "decoded"
                for section in sections:
                    if not section.closed:
                        diagnostics.append(
                            ModernDiagnostic(
                                "warning",
                                "unclosed_private_data_section",
                                f"Section {section.name!r} has no matching end marker.",
                                object_ref=obj.ref.label(),
                                segment=key,
                            )
                        )
            except NotImplementedError as error:
                _diagnostic(
                    diagnostics,
                    "error",
                    "unsupported_stream_filter",
                    f"/{key} uses unsupported filter /{error.args[0]}.",
                    obj=obj,
                    segment=key,
                )
            except _DecodeLimitExceeded as error:
                _diagnostic(
                    diagnostics,
                    "error",
                    "private_data_decode_limit_exceeded",
                    f"/{key}: {error}.",
                    obj=obj,
                    segment=key,
                )
                decoded = None
            except (RuntimeError, ValueError, zlib.error) as error:
                _diagnostic(
                    diagnostics,
                    "error",
                    "private_data_decode_failed",
                    f"/{key}: {error}.",
                    obj=obj,
                    segment=key,
                )
                decoded = None
        segments.append(
            PrivateDataSegment(
                index=len(segments),
                key=key,
                object_ref=obj.ref,
                object_start=obj.start,
                object_end=obj.end,
                raw_start=obj.stream_start,
                raw_end=obj.stream_end,
                filters=effective_filters,
                raw_bytes=raw,
                raw_sha256=hashlib.sha256(raw).hexdigest(),
                decoded_bytes=decoded,
                decoded_sha256=decoded_hash,
                decode_status=decode_status,
                tokens=tokens,
                sections=sections,
            )
        )

    unique_paths = tuple(dict.fromkeys(paths))
    error_codes = {item.code for item in diagnostics if item.severity == "error"}
    if not unique_paths:
        private_status = "absent"
    elif segments and all(segment.decode_status == "decoded" for segment in segments):
        private_status = "extracted"
    elif segments:
        private_status = "partial"
    else:
        private_status = "failed"
    structural_errors = {
        "missing_pdf_eof",
        "no_pdf_objects",
        "pdf_object_limit_exceeded",
        "pdf_object_size_limit_exceeded",
        "malformed_pdf_object",
        "malformed_pdf_stream",
        "encrypted_pdf_unsupported",
    }
    container_status = "invalid" if error_codes & structural_errors else "parsed"
    if not unique_paths and container_status == "parsed":
        diagnostics.append(
            ModernDiagnostic(
                "info",
                "ordinary_pdf",
                "No /PieceInfo/Illustrator/Private path was found; this is an ordinary PDF.",
            )
        )

    semantic = None
    semantic_status = "unsupported"
    if segments and any(segment.decoded_bytes is not None for segment in segments):
        from ._modern_projection import project_modern_semantics

        semantic = project_modern_semantics(
            tuple(segments),
            max_lexemes=active_limits.max_tokens,
            max_lexeme_bytes=active_limits.max_token_bytes,
            max_text_document_nesting=active_limits.max_text_document_nesting,
            max_semantic_nesting=active_limits.max_semantic_nesting,
        )
        semantic_status = semantic.status
        diagnostics.extend(semantic.diagnostics)

    return ModernAIReadResult(
        path=path,
        source_bytes=data,
        source_sha256=hashlib.sha256(data).hexdigest(),
        pdf_version=pdf_version,
        object_count=len(objects),
        container_status=container_status,
        private_data_status=private_status,
        semantic_status=semantic_status,
        piece_info_paths=unique_paths,
        segments=tuple(segments),
        diagnostics=tuple(diagnostics),
        semantic=semantic,
    )
