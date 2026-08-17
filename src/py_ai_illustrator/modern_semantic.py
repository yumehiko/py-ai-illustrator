"""Source-preserving lexer, CST, and limited modern Illustrator reducer.

The semantic reader consumes only decoded ``PrivateDataSegment`` bytes.  It
does not reopen the PDF, and it keeps unsupported syntax addressable by exact
decoded-byte spans instead of guessing at its meaning.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import math
import re
from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, Any

from .model import Color, ControlPoint, Document, Layer, LayerItemRef, Path, Point

if TYPE_CHECKING:
    from .modern import ModernDiagnostic, PrivateDataSegment


_SPACE = b"\x00\x09\x0a\x0c\x0d\x20"
_DELIMITERS = b"()<>[]{}/%{}"
_NUMBER_RE = re.compile(rb"[+-]?(?:\d+\.\d*|\.\d+|\d+)(?:[Ee][+-]?\d+)?")
_PAGE_ORIGIN_RE = re.compile(rb"^%%PageOrigin:\s*([+-]?[\d.]+)\s+([+-]?[\d.]+)")
_BOUNDING_BOX_RE = re.compile(
    rb"^%%BoundingBox:\s*([+-]?[\d.]+)\s+([+-]?[\d.]+)\s+"
    rb"([+-]?[\d.]+)\s+([+-]?[\d.]+)"
)
_SECTION_RE = re.compile(rb"^%AI\d+_(?:Begin|End)_?(?P<name>[A-Za-z0-9_.-]+)")
_TEXT_OBJECT_RE = re.compile(
    rb"/AI11Text\s*:\s*(?P<body>.*?)/StoryIndex\s*,(?P<tail>.*?)[\r\n]\s*;",
    re.DOTALL,
)
_STORY_INDEX_RE = re.compile(rb"(?P<index>\d+)\s+/StoryIndex\s*,")
_NOTE_RE = re.compile(rb"^%_\((?P<value>.*?)\)\s+/UnicodeString\s+\(AdobeNoteAttribute\)")
_XML_UID_RE = re.compile(rb"^%_/XMLUID\s+:\s+\((?P<value>.*?)\)")
DEFAULT_MAX_LEXEMES = 2_000_000
DEFAULT_MAX_LEXEME_BYTES = 8 * 1024 * 1024
DEFAULT_MAX_TEXT_DOCUMENT_NESTING = 64


SUPPORTED_OPERATORS = frozenset(
    {
        # Layer and object metadata.
        "Lb",
        "Ln",
        "LB",
        ":",
        ",",
        ";",
        # Path construction and paint.
        "m",
        "L",
        "l",
        "C",
        "c",
        "f",
        "F",
        "f*",
        "S",
        "s",
        "B",
        "b",
        "n",
        "Xa",
        "XA",
        "w",
        # State accepted but not currently projected into a Path field.
        "A",
        "AE",
        "As",
        "D",
        "J",
        "j",
        "M",
        "O",
        "R",
        "XR",
        "Xw",
        "Xd",
        "XW",
        "Xy",
    }
)


@dataclass(frozen=True, slots=True)
class ModernLexeme:
    """One exact lexical span in decoded PrivateData bytes."""

    kind: str
    start: int
    end: int
    value: str | float | int | bool | None = None

    def to_dict(self) -> dict[str, object]:
        return {key: value for key, value in asdict(self).items() if value is not None}


class ModernSemanticLimitExceeded(ValueError):
    """Semantic lexing stopped before allocating beyond its configured bounds."""


@dataclass(frozen=True, slots=True)
class ModernCSTStatement:
    """An operator and the exact lexemes consumed as its operands."""

    index: int
    start: int
    end: int
    operator: ModernLexeme
    operands: tuple[ModernLexeme, ...]
    supported: bool

    @property
    def operator_name(self) -> str:
        return str(self.operator.value)

    def to_dict(self) -> dict[str, object]:
        return {
            "index": self.index,
            "span": {"start": self.start, "end": self.end},
            "operator": self.operator.to_dict(),
            "operands": [operand.to_dict() for operand in self.operands],
            "supported": self.supported,
        }


@dataclass(frozen=True, slots=True)
class ModernPrivateDataCST:
    """Lossless lexical index and flat PostScript statement CST for one segment."""

    segment_index: int
    segment_key: str
    decoded_size: int
    lexemes: tuple[ModernLexeme, ...]
    statements: tuple[ModernCSTStatement, ...]

    def to_dict(self, *, include_tokens: bool = False) -> dict[str, object]:
        result: dict[str, object] = {
            "segment_index": self.segment_index,
            "segment_key": self.segment_key,
            "decoded_size": self.decoded_size,
            "lexeme_count": len(self.lexemes),
            "statement_count": len(self.statements),
        }
        if include_tokens:
            result["lexemes"] = [lexeme.to_dict() for lexeme in self.lexemes]
            result["statements"] = [statement.to_dict() for statement in self.statements]
        return result


@dataclass(frozen=True, slots=True)
class ModernUnknownOperator:
    name: str
    count: int
    segment_key: str
    first_start: int
    first_end: int

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ModernUnknownSpan:
    """An unsupported statement span; bytes remain in its owning segment."""

    segment_key: str
    start: int
    end: int
    sha256: str
    reason: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(slots=True)
class ModernPartialNode:
    """Recognized artwork that cannot be safely constructed as a complete IR node."""

    kind: str
    id: str
    name: str | None
    segment_key: str
    start: int
    end: int
    known_fields: dict[str, object]
    missing_fields: tuple[str, ...]
    reason: str

    def to_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "id": self.id,
            "name": self.name,
            "source_span": {"segment": self.segment_key, "start": self.start, "end": self.end},
            "known_fields": self.known_fields,
            "missing_fields": list(self.missing_fields),
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class ModernSemanticCoverage:
    decoded_bytes: int
    operator_count: int
    supported_operator_count: int
    unknown_operator_count: int
    projected_layer_count: int
    projected_path_count: int
    partial_text_count: int
    unknown_span_bytes: int

    @property
    def operator_ratio(self) -> float:
        if not self.operator_count:
            return 0.0
        return self.supported_operator_count / self.operator_count

    def to_dict(self) -> dict[str, object]:
        return {**asdict(self), "operator_ratio": self.operator_ratio}


@dataclass(frozen=True, slots=True)
class ModernSemanticResult:
    """Project-owned semantic result layered on authoritative decoded bytes."""

    status: str
    document: Document | None
    csts: tuple[ModernPrivateDataCST, ...]
    coverage: ModernSemanticCoverage
    unknown_operators: tuple[ModernUnknownOperator, ...]
    unknown_spans: tuple[ModernUnknownSpan, ...]
    partial_nodes: tuple[ModernPartialNode, ...]
    diagnostics: tuple[ModernDiagnostic, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "profile": "modern-ai-semantic-read-only-v1",
            "status": self.status,
            "supported": self.status == "supported",
            "document": self.document.to_dict() if self.document is not None else None,
            "cst": [cst.to_dict() for cst in self.csts],
            "coverage": self.coverage.to_dict(),
            "unknown_operators": [item.to_dict() for item in self.unknown_operators],
            "unknown_spans": [item.to_dict() for item in self.unknown_spans],
            "partial_nodes": [item.to_dict() for item in self.partial_nodes],
            "diagnostics": [item.to_dict() for item in self.diagnostics],
            "message": (
                "Supported artwork was projected to read-only IR; unsupported content remains "
                "available through decoded bytes and exact spans."
            ),
        }


def _decode_string(raw: bytes) -> str:
    output = bytearray()
    index = 1
    while index < len(raw) - 1:
        byte = raw[index]
        index += 1
        if byte != ord("\\"):
            output.append(byte)
            continue
        if index >= len(raw) - 1:
            break
        escaped = raw[index]
        index += 1
        simple = {ord("n"): 10, ord("r"): 13, ord("t"): 9, ord("b"): 8, ord("f"): 12}
        if escaped in simple:
            output.append(simple[escaped])
        elif escaped in b"\r\n":
            if escaped == 13 and index < len(raw) - 1 and raw[index] == 10:
                index += 1
        elif escaped in b"01234567":
            digits = bytearray([escaped])
            while len(digits) < 3 and index < len(raw) - 1 and raw[index] in b"01234567":
                digits.append(raw[index])
                index += 1
            output.append(int(digits, 8) & 0xFF)
        else:
            output.append(escaped)
    return output.decode("utf-8", errors="replace")


def lex_modern_private_data(
    data: bytes,
    *,
    max_lexemes: int = DEFAULT_MAX_LEXEMES,
    max_lexeme_bytes: int = DEFAULT_MAX_LEXEME_BYTES,
) -> tuple[ModernLexeme, ...]:
    """Tokenize every byte without normalization; adjacent spans cover ``data``."""

    tokens: list[ModernLexeme] = []

    def add(token: ModernLexeme) -> None:
        if len(tokens) >= max_lexemes:
            raise ModernSemanticLimitExceeded(
                f"modern semantic lexeme count exceeds {max_lexemes}"
            )
        if token.end - token.start > max_lexeme_bytes:
            raise ModernSemanticLimitExceeded(
                f"modern semantic lexeme exceeds {max_lexeme_bytes} bytes"
            )
        tokens.append(token)

    position = 0
    while position < len(data):
        start = position
        byte = data[position]
        if byte in _SPACE:
            position += 1
            while position < len(data) and data[position] in _SPACE:
                position += 1
            add(ModernLexeme("whitespace", start, position))
            continue
        if byte == ord("%"):
            position += 1
            while position < len(data) and data[position] not in b"\r\n":
                position += 1
            raw = data[start:position]
            match = _SECTION_RE.match(raw)
            kind = "section_marker" if match else "comment"
            value = match.group("name").decode("ascii") if match else None
            add(ModernLexeme(kind, start, position, value))
            continue
        if byte == ord("("):
            depth = 1
            position += 1
            while position < len(data) and depth:
                current = data[position]
                position += 1
                if current == ord("\\") and position < len(data):
                    if (
                        data[position] == 13
                        and position + 1 < len(data)
                        and data[position + 1] == 10
                    ):
                        position += 2
                    else:
                        position += 1
                elif current == ord("("):
                    depth += 1
                elif current == ord(")"):
                    depth -= 1
            raw = data[start:position]
            add(ModernLexeme("string", start, position, _decode_string(raw)))
            continue
        if byte == ord("/"):
            position += 1
            while position < len(data) and data[position] not in _SPACE + _DELIMITERS:
                position += 1
            add(
                ModernLexeme("name", start, position, data[start + 1 : position].decode("latin-1"))
            )
            continue
        if byte == ord("<") and data[position : position + 2] != b"<<":
            position += 1
            while position < len(data) and data[position] != ord(">"):
                position += 1
            if position < len(data):
                position += 1
            add(
                ModernLexeme(
                    "hex_string",
                    start,
                    position,
                    data[start + 1 : max(start + 1, position - 1)].decode(
                        "ascii", errors="replace"
                    ),
                )
            )
            continue
        number = _NUMBER_RE.match(data, position)
        if number is not None:
            position = number.end()
            raw_number = number.group()
            value: int | float
            value = (
                float(raw_number)
                if any(char in raw_number for char in b".Ee")
                else int(raw_number)
            )
            add(ModernLexeme("number", start, position, value))
            continue
        pair = data[position : position + 2]
        if pair in {b"<<", b">>"}:
            position += 2
            add(ModernLexeme("delimiter", start, position, pair.decode("ascii")))
            continue
        if byte in b"[]{}<>":
            position += 1
            add(ModernLexeme("delimiter", start, position, chr(byte)))
            continue
        position += 1
        while position < len(data) and data[position] not in _SPACE + _DELIMITERS:
            position += 1
        raw = data[start:position]
        if not raw:
            position = start + 1
            add(ModernLexeme("opaque", start, position))
            continue
        text = raw.decode("latin-1")
        if text in {"true", "false"}:
            add(ModernLexeme("boolean", start, position, text == "true"))
        elif text in {"null", "nil"}:
            add(ModernLexeme("null", start, position, None))
        else:
            add(ModernLexeme("operator", start, position, text))
    return tuple(tokens)


def parse_modern_private_data(
    data: bytes,
    *,
    segment_index: int = 0,
    segment_key: str = "AIPrivateData",
    max_lexemes: int = DEFAULT_MAX_LEXEMES,
    max_lexeme_bytes: int = DEFAULT_MAX_LEXEME_BYTES,
) -> ModernPrivateDataCST:
    """Build a flat CST whose operand/operator spans point into ``data``."""

    lexemes = lex_modern_private_data(
        data, max_lexemes=max_lexemes, max_lexeme_bytes=max_lexeme_bytes
    )
    operands: list[ModernLexeme] = []
    statements: list[ModernCSTStatement] = []
    for lexeme in lexemes:
        if lexeme.kind in {"whitespace", "comment", "section_marker"}:
            continue
        if lexeme.kind != "operator":
            operands.append(lexeme)
            continue
        start = operands[0].start if operands else lexeme.start
        statements.append(
            ModernCSTStatement(
                index=len(statements),
                start=start,
                end=lexeme.end,
                operator=lexeme,
                operands=tuple(operands),
                supported=str(lexeme.value) in SUPPORTED_OPERATORS,
            )
        )
        operands.clear()
    return ModernPrivateDataCST(
        segment_index=segment_index,
        segment_key=segment_key,
        decoded_size=len(data),
        lexemes=lexemes,
        statements=tuple(statements),
    )


def _numbers(statement: ModernCSTStatement) -> list[float]:
    return [float(item.value) for item in statement.operands if item.kind == "number"]


def _source_dict(
    segment: str, start: int, end: int, operators: list[tuple[int, int]]
) -> dict[str, Any]:
    return {
        "segment": segment,
        "span": {"start": start, "end": end},
        "operator_spans": [{"start": left, "end": right} for left, right in operators],
    }


@dataclass(slots=True)
class _PathBuilder:
    start: int
    points: list[Point]
    operator_spans: list[tuple[int, int]]


@dataclass(slots=True)
class _Reduction:
    layers: list[Layer] = field(default_factory=list)
    partials: list[ModernPartialNode] = field(default_factory=list)
    diagnostics: list[ModernDiagnostic] = field(default_factory=list)
    page_origin: tuple[float, float] = (0.0, 0.0)
    bounding_box: tuple[float, float, float, float] | None = None
    path_count: int = 0
    partial_path_count: int = 0


def _story_texts(
    data: bytes, *, max_nesting: int
) -> tuple[dict[int, str], tuple[int, int] | None, str | None]:
    from .modern import _PdfSyntaxParser

    marker = b"/AI11TextDocument : /ASCII85Decode ,"
    start = data.find(marker)
    if start < 0:
        return {}, None, None
    cr = data.find(b"\r", start)
    lf = data.find(b"\n", start)
    line_end = min(item for item in (cr, lf) if item >= 0) if cr >= 0 or lf >= 0 else -1
    if line_end < 0:
        return {}, None, "AI11TextDocument header has no payload line."
    encoded = bytearray()
    position = line_end + 1
    if data[line_end : line_end + 2] == b"\r\n":
        position += 1
    payload_end = position
    while position < len(data):
        cr = data.find(b"\r", position)
        lf = data.find(b"\n", position)
        candidates = [item for item in (cr, lf) if item >= 0]
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
        return {}, (start, payload_end), "AI11TextDocument ASCII85 payload is unterminated."
    try:
        decoded = base64.a85decode(bytes(encoded[:terminator]))
        wrapped = b"<<" + decoded + b">>"
        root = _PdfSyntaxParser(wrapped, 0, len(wrapped), max_depth=max_nesting).parse()
        if not isinstance(root, dict):
            raise ValueError("decoded AI11TextDocument root is not a dictionary")
        document = root.get("1")
        stories = document.get("1") if isinstance(document, dict) else None
        if not isinstance(stories, tuple):
            raise ValueError("decoded AI11TextDocument has no story array")
        result: dict[int, str] = {}
        for index, story in enumerate(stories):
            raw: object = None
            if isinstance(story, dict):
                content = story.get("0")
                if isinstance(content, dict):
                    raw = content.get("0")
            if isinstance(raw, bytes):
                text = raw.decode("utf-16") if raw.startswith(b"\xfe\xff") else raw.decode("utf-8")
                result[index] = text.removesuffix("\r")
        return result, (start, payload_end), None
    except Exception as error:
        # AI11TextDocument is untrusted decoded input.  Keep parser failures,
        # including RecursionError, inside the semantic diagnostic boundary.
        return {}, (start, payload_end), f"{type(error).__name__}: {error}"


def _note_value(raw: bytes) -> dict[str, object] | None:
    if raw.startswith(b"py-ai-text:"):
        try:
            value = json.loads(raw[len(b"py-ai-text:") :].decode("utf-8"))
            return value if isinstance(value, dict) else None
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None
    if raw.startswith(b"py-ai:"):
        try:
            decoded = base64.b64decode(raw[len(b"py-ai:") :], validate=True)
            value = json.loads(decoded)
            return value if isinstance(value, dict) else None
        except (ValueError, binascii.Error, UnicodeDecodeError, json.JSONDecodeError):
            return None
    return None


def _reduce_segment(
    segment: PrivateDataSegment,
    cst: ModernPrivateDataCST,
    *,
    max_text_document_nesting: int,
) -> _Reduction:
    from .modern import ModernDiagnostic

    data = segment.decoded_bytes
    assert data is not None
    reduction = _Reduction()
    stories, text_document_span, text_error = _story_texts(
        data, max_nesting=max_text_document_nesting
    )
    if text_error:
        reduction.diagnostics.append(
            ModernDiagnostic(
                "warning",
                "modern_text_document_partial",
                text_error,
                segment=segment.key,
                decoded_start=text_document_span[0] if text_document_span else None,
                decoded_end=text_document_span[1] if text_document_span else None,
            )
        )

    events: list[tuple[int, str, object]] = [
        (statement.start, "statement", statement) for statement in cst.statements
    ]
    events.extend(
        (lexeme.start, "comment", lexeme)
        for lexeme in cst.lexemes
        if lexeme.kind in {"comment", "section_marker"}
    )
    text_matches = list(_TEXT_OBJECT_RE.finditer(data))
    events.extend((match.start(), "text", match) for match in text_matches)
    events.sort(key=lambda item: (item[0], {"text": 0, "comment": 1, "statement": 2}[item[1]]))

    current_layer: Layer | None = None
    current_path: _PathBuilder | None = None
    fill: Color | None = None
    stroke: Color | None = None
    stroke_width = 1.0
    last_object: Path | ModernPartialNode | Layer | None = None

    def transform(x: float, y: float) -> tuple[float, float]:
        return x - reduction.page_origin[0], y - reduction.page_origin[1]

    def abandon_path(end: int, reason: str) -> None:
        nonlocal current_path, last_object
        if current_path is None:
            return
        partial = ModernPartialNode(
            kind="path",
            id=f"modern-{segment.index}-partial-path-{reduction.partial_path_count}",
            name=None,
            segment_key=segment.key,
            start=current_path.start,
            end=max(current_path.start, end),
            known_fields={"point_count": len(current_path.points)},
            missing_fields=("paint_operator",),
            reason=reason,
        )
        reduction.partial_path_count += 1
        reduction.partials.append(partial)
        last_object = partial
        current_path = None

    def finish_path(statement: ModernCSTStatement) -> None:
        nonlocal current_path, last_object
        if current_path is None:
            return
        current_path.operator_spans.append((statement.operator.start, statement.operator.end))
        numbers = current_path.points
        paint = statement.operator_name
        if len(numbers) < 2 or current_layer is None:
            partial_id = reduction.partial_path_count
            reduction.partial_path_count += 1
            reduction.partials.append(
                ModernPartialNode(
                    kind="path",
                    id=f"modern-{segment.index}-partial-path-{partial_id}",
                    name=None,
                    segment_key=segment.key,
                    start=current_path.start,
                    end=statement.end,
                    known_fields={"point_count": len(numbers), "paint_operator": paint},
                    missing_fields=("geometry",) if len(numbers) < 2 else ("layer",),
                    reason="Path could not be safely constructed as common IR.",
                )
            )
            last_object = reduction.partials[-1]
            current_path = None
            return
        use_fill = fill if paint in {"f", "F", "f*", "B", "b"} else None
        use_stroke = stroke if paint in {"S", "s", "B", "b"} else None
        path = Path(
            id=f"modern-{segment.index}-path-{reduction.path_count}",
            points=list(numbers),
            closed=paint in {"f", "F", "f*", "s", "B", "b"},
            fill=use_fill,
            stroke=use_stroke,
            stroke_width=stroke_width,
            unknown={
                "modern_source": _source_dict(
                    segment.key,
                    current_path.start,
                    statement.end,
                    current_path.operator_spans,
                )
            },
        )
        current_layer.paths.append(path)
        current_layer.item_order.append(LayerItemRef("path", path.id))
        reduction.path_count += 1
        last_object = path
        current_path = None

    consumed_text_starts = {match.start() for match in text_matches}
    for _offset, kind, payload in events:
        if kind == "text":
            match = payload
            assert isinstance(match, re.Match)
            index_match = _STORY_INDEX_RE.search(match.group())
            story_index = int(index_match.group("index")) if index_match else None
            text = stories.get(story_index) if story_index is not None else None
            known: dict[str, object] = {"story_index": story_index}
            if text is not None:
                known["text"] = text
            missing = ["x", "y"]
            if text is None:
                missing.append("text")
            identity = story_index if story_index is not None else len(reduction.partials)
            partial = ModernPartialNode(
                kind="text",
                id=f"modern-{segment.index}-text-{identity}",
                name=None,
                segment_key=segment.key,
                start=match.start(),
                end=match.end(),
                known_fields=known,
                missing_fields=tuple(missing),
                reason=(
                    "AI11 story content is readable, but absolute placement is not proven by the "
                    "supported decoded-byte fields."
                ),
            )
            reduction.partials.append(partial)
            last_object = partial
            continue

        if kind == "comment":
            lexeme = payload
            assert isinstance(lexeme, ModernLexeme)
            raw = data[lexeme.start : lexeme.end]
            page = _PAGE_ORIGIN_RE.match(raw)
            if page:
                reduction.page_origin = (float(page.group(1)), float(page.group(2)))
            bounds = _BOUNDING_BOX_RE.match(raw)
            if bounds:
                reduction.bounding_box = tuple(float(bounds.group(i)) for i in range(1, 5))  # type: ignore[assignment]
            if raw.startswith(b"%AI5_BeginLayer"):
                abandon_path(
                    lexeme.start,
                    "A layer began before the prior path received a paint operator.",
                )
                current_layer = Layer(
                    id=f"modern-{segment.index}-layer-{len(reduction.layers)}",
                    name=f"Layer {len(reduction.layers) + 1}",
                )
                reduction.layers.append(current_layer)
                last_object = current_layer
                continue
            if raw.startswith(b"%AI5_EndLayer"):
                abandon_path(
                    lexeme.start,
                    "The layer ended before the path received a paint operator.",
                )
                current_layer = None
                last_object = None
                continue
            uid = _XML_UID_RE.match(raw)
            if uid and isinstance(last_object, Layer):
                last_object.id = uid.group("value").decode("utf-8", errors="replace")
                continue
            note = _NOTE_RE.match(raw)
            if note:
                value = _note_value(note.group("value"))
                if value is not None and last_object is not None:
                    if isinstance(value.get("id"), str) and value["id"]:
                        last_object.id = str(value["id"])
                    if isinstance(value.get("name"), str):
                        last_object.name = str(value["name"])
                continue
            continue

        statement = payload
        assert isinstance(statement, ModernCSTStatement)
        if statement.start in consumed_text_starts:
            continue
        op = statement.operator_name
        values = _numbers(statement)
        if op == "Ln" and current_layer is not None:
            string = next(
                (item.value for item in statement.operands if item.kind == "string"), None
            )
            if isinstance(string, str):
                current_layer.name = string
                last_object = current_layer
        elif op == "Xa" and len(values) >= 3:
            try:
                fill = Color(*values[-3:])
            except ValueError:
                fill = None
        elif op == "XA" and len(values) >= 3:
            try:
                stroke = Color(*values[-3:])
            except ValueError:
                stroke = None
        elif op == "w" and values and math.isfinite(values[-1]) and values[-1] >= 0:
            stroke_width = values[-1]
        elif op == "m" and len(values) >= 2:
            abandon_path(
                statement.start,
                "A new moveto began before the prior path received a paint operator.",
            )
            x, y = transform(values[-2], values[-1])
            current_path = _PathBuilder(
                statement.start,
                [Point(x, y)],
                [(statement.operator.start, statement.operator.end)],
            )
        elif op in {"L", "l"} and current_path is not None and len(values) >= 2:
            x, y = transform(values[-2], values[-1])
            current_path.points.append(Point(x, y))
            current_path.operator_spans.append((statement.operator.start, statement.operator.end))
        elif op in {"C", "c"} and current_path is not None and len(values) >= 6:
            x1, y1 = transform(values[-6], values[-5])
            x2, y2 = transform(values[-4], values[-3])
            x3, y3 = transform(values[-2], values[-1])
            current_path.points[-1] = current_path.points[-1].with_out_handle(ControlPoint(x1, y1))
            current_path.points.append(Point(x3, y3, in_handle=ControlPoint(x2, y2)))
            current_path.operator_spans.append((statement.operator.start, statement.operator.end))
        elif op in {"f", "F", "f*", "S", "s", "B", "b", "n"}:
            finish_path(statement)
    abandon_path(len(data), "The segment ended before the path received a paint operator.")
    return reduction


def _deduplicate_node_ids(
    layers: list[Layer],
    partials: list[ModernPartialNode],
) -> list[tuple[str, str]]:
    """Make projected and partial node IDs globally unique and report renames."""

    used: set[str] = set()
    collisions: list[tuple[str, str]] = []

    def unique_id(candidate: str) -> str:
        if candidate not in used:
            used.add(candidate)
            return candidate
        suffix = 2
        while f"{candidate}~{suffix}" in used:
            suffix += 1
        result = f"{candidate}~{suffix}"
        used.add(result)
        collisions.append((candidate, result))
        return result

    for layer in layers:
        layer.id = unique_id(layer.id)
        for path in layer.paths:
            path.id = unique_id(path.id)
        # Source notes can rename paths after their item refs were created.
        layer.item_order = [LayerItemRef("path", path.id) for path in layer.paths]
    for partial in partials:
        partial.id = unique_id(partial.id)
    return collisions


def project_modern_semantics(
    segments: tuple[PrivateDataSegment, ...],
    *,
    max_lexemes: int = DEFAULT_MAX_LEXEMES,
    max_lexeme_bytes: int = DEFAULT_MAX_LEXEME_BYTES,
    max_text_document_nesting: int = DEFAULT_MAX_TEXT_DOCUMENT_NESTING,
) -> ModernSemanticResult:
    """Lex decoded segments and project the supported artwork subset."""

    from .modern import ModernDiagnostic

    csts: list[ModernPrivateDataCST] = []
    layers: list[Layer] = []
    partials: list[ModernPartialNode] = []
    diagnostics: list[ModernDiagnostic] = []
    unknown: list[ModernUnknownOperator] = []
    unknown_spans: list[ModernUnknownSpan] = []
    decoded_size = 0
    operator_count = 0
    supported_count = 0
    path_count = 0
    bounds: list[tuple[float, float, float, float]] = []

    for segment in segments:
        if segment.decoded_bytes is None:
            continue
        data = segment.decoded_bytes
        decoded_size += len(data)
        try:
            cst = parse_modern_private_data(
                data,
                segment_index=segment.index,
                segment_key=segment.key,
                max_lexemes=max_lexemes,
                max_lexeme_bytes=max_lexeme_bytes,
            )
        except ModernSemanticLimitExceeded as error:
            diagnostics.append(
                ModernDiagnostic(
                    "error",
                    "modern_semantic_limit_exceeded",
                    str(error),
                    segment=segment.key,
                )
            )
            continue
        csts.append(cst)
        operator_count += len(cst.statements)
        supported_count += sum(statement.supported for statement in cst.statements)
        by_name: dict[str, list[ModernCSTStatement]] = {}
        for statement in cst.statements:
            if statement.supported:
                continue
            by_name.setdefault(statement.operator_name, []).append(statement)
            raw = data[statement.start : statement.end]
            unknown_spans.append(
                ModernUnknownSpan(
                    segment.key,
                    statement.start,
                    statement.end,
                    hashlib.sha256(raw).hexdigest(),
                    f"unknown operator {statement.operator_name!r}",
                )
            )
        for name, statements in sorted(by_name.items()):
            first = statements[0]
            unknown.append(
                ModernUnknownOperator(
                    name,
                    len(statements),
                    segment.key,
                    first.operator.start,
                    first.operator.end,
                )
            )
        if by_name:
            diagnostics.append(
                ModernDiagnostic(
                    "warning",
                    "unknown_modern_operators",
                    f"/{segment.key} contains {len(by_name)} unknown operator names; "
                    "exact spans retained.",
                    segment=segment.key,
                )
            )
        reduction = _reduce_segment(
            segment,
            cst,
            max_text_document_nesting=max_text_document_nesting,
        )
        layers.extend(reduction.layers)
        partials.extend(reduction.partials)
        diagnostics.extend(reduction.diagnostics)
        path_count += reduction.path_count
        if reduction.bounding_box:
            bounds.append(reduction.bounding_box)

    id_collisions = _deduplicate_node_ids(layers, partials)
    for original, replacement in id_collisions:
        diagnostics.append(
            ModernDiagnostic(
                "warning",
                "modern_duplicate_node_id",
                f"Duplicate semantic node id {original!r} was disambiguated as {replacement!r}.",
            )
        )

    document: Document | None = None
    if layers:
        max_x = max(
            [point.x for layer in layers for path in layer.paths for point in path.points] or [1.0]
        )
        max_y = max(
            [point.y for layer in layers for path in layer.paths for point in path.points] or [1.0]
        )
        if bounds:
            max_x = max(max_x, *(item[2] for item in bounds))
            max_y = max(max_y, *(item[3] - item[1] for item in bounds))
        document = Document(
            width=max(1.0, max_x),
            height=max(1.0, max_y),
            layers=layers,
            title="Modern AI read-only projection",
            metadata={
                "source": "modern_private_data",
                "read_only": True,
                "dimensions": (
                    "content-derived; document canvas size is not in the supported profile"
                ),
            },
        )
    text_count = sum(item.kind == "text" for item in partials)
    if text_count:
        diagnostics.append(
            ModernDiagnostic(
                "warning",
                "partial_modern_text",
                f"Recognized {text_count} AI11 text objects; content is retained but "
                "placement is partial.",
            )
        )
    has_text_document_partial = any(
        item.code == "modern_text_document_partial" for item in diagnostics
    )
    if document is None and not partials and not has_text_document_partial:
        status = "unsupported"
    elif (
        unknown
        or partials
        or id_collisions
        or has_text_document_partial
    ):
        status = "partial"
    else:
        status = "supported"
    coverage = ModernSemanticCoverage(
        decoded_bytes=decoded_size,
        operator_count=operator_count,
        supported_operator_count=supported_count,
        unknown_operator_count=operator_count - supported_count,
        projected_layer_count=len(layers),
        projected_path_count=path_count,
        partial_text_count=text_count,
        unknown_span_bytes=sum(item.end - item.start for item in unknown_spans),
    )
    return ModernSemanticResult(
        status=status,
        document=document,
        csts=tuple(csts),
        coverage=coverage,
        unknown_operators=tuple(unknown),
        unknown_spans=tuple(unknown_spans),
        partial_nodes=tuple(partials),
        diagnostics=tuple(diagnostics),
    )


__all__ = [
    "ModernCSTStatement",
    "ModernLexeme",
    "ModernPartialNode",
    "ModernPrivateDataCST",
    "ModernSemanticCoverage",
    "ModernSemanticLimitExceeded",
    "ModernSemanticResult",
    "ModernUnknownOperator",
    "ModernUnknownSpan",
    "SUPPORTED_OPERATORS",
    "lex_modern_private_data",
    "parse_modern_private_data",
    "project_modern_semantics",
]
