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

from .model import (
    ClippingGroup,
    CmykColor,
    Color,
    CompoundPath,
    ControlPoint,
    Document,
    Group,
    Layer,
    LayerItemRef,
    Path,
    Point,
    ProcessColor,
    TextFrame,
)

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
DEFAULT_MAX_SEMANTIC_NESTING = 64


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
        "v",
        "y",
        "h",
        "H",
        "f",
        "F",
        "f*",
        "S",
        "s",
        "B",
        "b",
        "n",
        # Process colors.  Xa/XA may carry CMYK followed by an RGB alternate.
        "Xa",
        "XA",
        "k",
        "K",
        # Artwork hierarchy.
        "u",
        "U",
        "*u",
        "*U",
        "q",
        "Q",
        "W",
        "W*",
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
    parent_kind: str | None = None
    parent_id: str | None = None
    item_index: int | None = None
    evidence: dict[str, object] = field(default_factory=dict)
    _parent_ref: object | None = field(default=None, repr=False, compare=False)

    def to_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "id": self.id,
            "name": self.name,
            "source_span": {"segment": self.segment_key, "start": self.start, "end": self.end},
            "known_fields": self.known_fields,
            "missing_fields": list(self.missing_fields),
            "reason": self.reason,
            "parent": (
                {"kind": self.parent_kind, "id": self.parent_id, "item_index": self.item_index}
                if self.parent_kind is not None and self.parent_id is not None
                else None
            ),
            "evidence": self.evidence,
        }


@dataclass(frozen=True, slots=True)
class ModernSemanticCoverage:
    decoded_bytes: int
    operator_count: int
    supported_operator_count: int
    unknown_operator_count: int
    projected_layer_count: int
    projected_path_count: int
    projected_group_count: int
    projected_compound_path_count: int
    projected_clipping_group_count: int
    projected_text_count: int
    partial_node_count: int
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
            "profile": "modern-ai-semantic-read-only-v2",
            "status": self.status,
            "supported": self.status == "supported",
            "read_only": True,
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
    explicit_closed: bool | None = None
    clipping_candidate: bool = False


@dataclass(frozen=True, slots=True)
class _StyleValue:
    value: object
    start: int
    end: int
    alternate_rgb: tuple[float, float, float] | None = None


@dataclass(slots=True)
class _ContainerBuilder:
    kind: str
    id: str
    start: int
    node: Layer | Group | None = None
    paths: list[Path] = field(default_factory=list)
    clipping_path: Path | None = None
    operator_spans: list[tuple[int, int]] = field(default_factory=list)
    item_count: int = 0
    graphics_state: tuple[
        _StyleValue | None,
        _StyleValue | None,
        _StyleValue,
        _StyleValue,
    ] | None = None


@dataclass(slots=True)
class _Reduction:
    layers: list[Layer] = field(default_factory=list)
    partials: list[ModernPartialNode] = field(default_factory=list)
    diagnostics: list[ModernDiagnostic] = field(default_factory=list)
    page_origin: tuple[float, float] = (0.0, 0.0)
    bounding_box: tuple[float, float, float, float] | None = None
    path_count: int = 0
    partial_path_count: int = 0
    group_count: int = 0
    compound_path_count: int = 0
    clipping_group_count: int = 0
    text_count: int = 0


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
    max_semantic_nesting: int,
) -> _Reduction:
    from .modern import ModernDiagnostic

    data = segment.decoded_bytes
    assert data is not None
    reduction = _Reduction()
    stories, text_document_span, text_error = _story_texts(
        data, max_nesting=max_text_document_nesting
    )
    if text_error and (
        _TEXT_OBJECT_RE.search(data) or "has no story array" not in text_error
    ):
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
    containers: list[_ContainerBuilder] = []
    current_path: _PathBuilder | None = None
    fill: _StyleValue | None = None
    stroke: _StyleValue | None = None
    stroke_width = _StyleValue(1.0, 0, 0)
    polarity = _StyleValue("positive", 0, 0)
    last_object: Path | TextFrame | ModernPartialNode | Layer | Group | None = None
    pending_text_containers: dict[int, _ContainerBuilder | None] = {}

    def transform(x: float, y: float) -> tuple[float, float]:
        return x - reduction.page_origin[0], y - reduction.page_origin[1]

    def append_partial(
        partial: ModernPartialNode,
        container: _ContainerBuilder | None = None,
    ) -> None:
        active = container if container is not None else (containers[-1] if containers else None)
        if active is not None:
            partial.parent_kind = active.kind
            partial.parent_id = active.id
            partial.item_index = active.item_count
            partial._parent_ref = active.node if active.node is not None else active
            active.item_count += 1
        reduction.partials.append(partial)

    def rebind_partial_parent(old: object, new: object) -> None:
        for partial in reduction.partials:
            if partial._parent_ref is old:
                partial._parent_ref = new

    def restore_graphics_state(builder: _ContainerBuilder) -> None:
        nonlocal fill, stroke, stroke_width, polarity
        if builder.graphics_state is not None:
            fill, stroke, stroke_width, polarity = builder.graphics_state

    def append_complete(
        kind: str,
        item: Path | TextFrame | CompoundPath | ClippingGroup | Group,
        *,
        count_item: bool = True,
    ) -> bool:
        if not containers:
            return False
        active = containers[-1]
        source = item.unknown.setdefault("modern_source", {})
        if isinstance(source, dict):
            source["parent"] = {"kind": active.kind, "id": active.id}
            source["item_index"] = (
                active.item_count if count_item else max(0, active.item_count - 1)
            )
        target = active.node
        if isinstance(target, (Layer, Group)):
            if kind == "path":
                target.paths.append(item)  # type: ignore[arg-type]
            elif kind == "text":
                target.text_frames.append(item)  # type: ignore[arg-type]
            elif kind == "compound_path":
                target.compound_paths.append(item)  # type: ignore[arg-type]
            elif kind == "clipping_group":
                target.clipping_groups.append(item)  # type: ignore[arg-type]
            elif kind == "group":
                target.groups.append(item)  # type: ignore[arg-type]
            else:
                return False
            target.item_order.append(LayerItemRef(kind, item.id))
        elif active.kind == "compound_path" and kind == "path":
            active.paths.append(item)  # type: ignore[arg-type]
        elif active.kind == "clipping_group" and kind == "path":
            path = item
            assert isinstance(path, Path)
            if current_path is not None and current_path.clipping_candidate:
                if active.clipping_path is not None:
                    return False
                active.clipping_path = path
            else:
                active.paths.append(path)
        else:
            return False
        if count_item:
            active.item_count += 1
        return True

    def structural_partial(builder: _ContainerBuilder, end: int, reason: str) -> None:
        child_ids = [path.id for path in builder.paths]
        if builder.clipping_path is not None:
            child_ids.append(builder.clipping_path.id)
        if isinstance(builder.node, Group):
            child_ids = [reference.id for reference in builder.node.item_order]
        parent = containers[-1] if containers else None
        partial = ModernPartialNode(
            kind=builder.kind,
            id=builder.id,
            name=builder.node.name if isinstance(builder.node, Group) else None,
            segment_key=segment.key,
            start=builder.start,
            end=max(builder.start, end),
            known_fields={"child_ids": child_ids},
            missing_fields=("closing_operator",),
            reason=reason,
            evidence={
                "operator_spans": [
                    {"start": start, "end": stop} for start, stop in builder.operator_spans
                ]
            },
        )
        rebind_partial_parent(builder.node if builder.node is not None else builder, partial)
        append_partial(partial, parent)

    def finish_layer(end: int, *, closed: bool, reason: str | None = None) -> None:
        if current_layer is None or not containers:
            return
        start = containers[0].start
        current_layer.unknown["modern_source"] = {
            "segment": segment.key,
            "span": {"start": start, "end": end},
            "closed": closed,
        }
        if not closed and reason is not None:
            reduction.diagnostics.append(
                ModernDiagnostic(
                    "warning",
                    "modern_layer_unclosed",
                    reason,
                    segment=segment.key,
                    decoded_start=start,
                    decoded_end=end,
                )
            )

    def close_container(
        expected: str, statement: ModernCSTStatement
    ) -> _ContainerBuilder | None:
        nonlocal last_object
        if len(containers) <= 1 or containers[-1].kind != expected:
            partial = ModernPartialNode(
                kind=expected,
                id=f"modern-{segment.index}-unmatched-{expected}-{statement.index}",
                name=None,
                segment_key=segment.key,
                start=statement.start,
                end=statement.end,
                known_fields={"closing_operator": statement.operator_name},
                missing_fields=("opening_operator",),
                reason=f"Unmatched {statement.operator_name!r} hierarchy operator.",
            )
            append_partial(partial)
            last_object = partial
            return None
        builder = containers.pop()
        builder.operator_spans.append((statement.operator.start, statement.operator.end))
        source = {
            "segment": segment.key,
            "span": {"start": builder.start, "end": statement.end},
            "operator_spans": [
                {"start": start, "end": stop} for start, stop in builder.operator_spans
            ],
        }
        if expected == "group":
            assert isinstance(builder.node, Group)
            builder.node.unknown["modern_source"] = source
            if append_complete("group", builder.node):
                last_object = builder.node
            else:
                structural_partial(
                    builder,
                    statement.end,
                    "A group appeared inside a container that common IR cannot represent.",
                )
                last_object = reduction.partials[-1]
        elif expected == "compound_path":
            if len(builder.paths) >= 2:
                compound = CompoundPath(
                    id=builder.id,
                    paths=builder.paths,
                    unknown={"modern_source": source},
                )
                rebind_partial_parent(builder, compound)
                if append_complete("compound_path", compound):
                    last_object = compound  # type: ignore[assignment]
                else:
                    structural_partial(
                        builder,
                        statement.end,
                        "A compound path appeared inside an unsupported parent container.",
                    )
                    last_object = reduction.partials[-1]
            else:
                partial = ModernPartialNode(
                    kind="compound_path",
                    id=builder.id,
                    name=None,
                    segment_key=segment.key,
                    start=builder.start,
                    end=statement.end,
                    known_fields={"child_ids": [path.id for path in builder.paths]},
                    missing_fields=("at_least_two_paths",),
                    reason="Compound path closed without two provable component paths.",
                    evidence=source,
                )
                rebind_partial_parent(builder, partial)
                append_partial(partial)
                last_object = partial
        else:
            if builder.clipping_path is not None and builder.paths:
                clipping = ClippingGroup(
                    id=builder.id,
                    clipping_path=builder.clipping_path,
                    paths=builder.paths,
                    unknown={"modern_source": source},
                )
                rebind_partial_parent(builder, clipping)
                if append_complete("clipping_group", clipping):
                    last_object = clipping  # type: ignore[assignment]
                else:
                    structural_partial(
                        builder,
                        statement.end,
                        "A clipping group appeared inside an unsupported parent container.",
                    )
                    last_object = reduction.partials[-1]
            else:
                missing: list[str] = []
                if builder.clipping_path is None:
                    missing.append("clipping_path")
                if not builder.paths:
                    missing.append("content_paths")
                partial = ModernPartialNode(
                    kind="clipping_group",
                    id=builder.id,
                    name=None,
                    segment_key=segment.key,
                    start=builder.start,
                    end=statement.end,
                    known_fields={
                        "clipping_path_id": (
                            builder.clipping_path.id if builder.clipping_path is not None else None
                        ),
                        "content_path_ids": [path.id for path in builder.paths],
                    },
                    missing_fields=tuple(missing),
                    reason="Clipping group closed without a provable mask and content path.",
                    evidence=source,
                )
                rebind_partial_parent(builder, partial)
                append_partial(partial)
                last_object = partial
        return builder

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
        append_partial(partial)
        last_object = partial
        current_path = None

    def finish_path(statement: ModernCSTStatement) -> None:
        nonlocal current_path, last_object
        if current_path is None:
            return
        current_path.operator_spans.append((statement.operator.start, statement.operator.end))
        numbers = current_path.points
        paint = statement.operator_name
        if len(numbers) < 2 or current_layer is None or not containers:
            partial_id = reduction.partial_path_count
            reduction.partial_path_count += 1
            append_partial(
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
        if current_path.clipping_candidate:
            use_fill = None
            use_stroke = None
        missing_paint: list[str] = []
        if paint in {"f", "F", "f*", "B", "b"} and use_fill is None:
            missing_paint.append("fill_color")
        if paint in {"S", "s", "B", "b"} and use_stroke is None:
            missing_paint.append("stroke_color")
        if missing_paint:
            partial = ModernPartialNode(
                kind="path",
                id=f"modern-{segment.index}-partial-path-{reduction.partial_path_count}",
                name=None,
                segment_key=segment.key,
                start=current_path.start,
                end=statement.end,
                known_fields={
                    "points": [asdict(point) for point in current_path.points],
                    "closed": (
                        current_path.explicit_closed
                        if current_path.explicit_closed is not None
                        else paint in {"f", "F", "f*", "s", "B", "b"}
                    ),
                    "paint_operator": paint,
                },
                missing_fields=tuple(missing_paint),
                reason="Paint operator is known, but its required process color is not proven.",
            )
            reduction.partial_path_count += 1
            append_partial(partial)
            last_object = partial
            current_path = None
            return
        style_spans: dict[str, object] = {}
        for key, style in (
            ("fill", use_fill),
            ("stroke", use_stroke),
            ("stroke_width", stroke_width if use_stroke is not None else None),
            ("polarity", polarity),
        ):
            if style is not None and style.end > style.start:
                entry: dict[str, object] = {"start": style.start, "end": style.end}
                if style.alternate_rgb is not None:
                    entry["alternate_rgb"] = list(style.alternate_rgb)
                style_spans[key] = entry
        path = Path(
            id=f"modern-{segment.index}-path-{reduction.path_count}",
            points=list(numbers),
            closed=(
                current_path.explicit_closed
                if current_path.explicit_closed is not None
                else paint in {"f", "F", "f*", "s", "B", "b"}
            ),
            fill=use_fill.value if use_fill is not None else None,  # type: ignore[arg-type]
            stroke=use_stroke.value if use_stroke is not None else None,  # type: ignore[arg-type]
            stroke_width=float(stroke_width.value),
            polarity=str(polarity.value),
            unknown={
                "modern_source": _source_dict(
                    segment.key,
                    current_path.start,
                    statement.end,
                    current_path.operator_spans,
                ),
                "modern_style_spans": style_spans,
            },
        )
        if not append_complete("path", path):
            partial = ModernPartialNode(
                kind="path",
                id=path.id,
                name=path.name,
                segment_key=segment.key,
                start=current_path.start,
                end=statement.end,
                known_fields={"point_count": len(path.points), "paint_operator": paint},
                missing_fields=("representable_parent",),
                reason="Path parent cannot be represented by the common IR.",
            )
            append_partial(partial)
            last_object = partial
            current_path = None
            return
        reduction.path_count += 1
        last_object = path
        current_path = None

    def process_color(values: list[float], statement: ModernCSTStatement) -> _StyleValue | None:
        try:
            if statement.operator_name in {"k", "K"} and len(values) >= 4:
                color: ProcessColor = CmykColor(*values[-4:])
                alternate = None
            elif len(values) >= 7:
                color = CmykColor(*values[-7:-3])
                alternate = tuple(values[-3:])
                Color(*alternate)
            elif len(values) >= 3:
                color = Color(*values[-3:])
                alternate = None
            else:
                return None
        except ValueError:
            return None
        return _StyleValue(color, statement.start, statement.end, alternate)

    def text_fill(value: object) -> ProcessColor | None:
        if not isinstance(value, dict):
            return None
        try:
            if {"cyan", "magenta", "yellow", "black"}.issubset(value):
                return CmykColor(
                    float(value["cyan"]),
                    float(value["magenta"]),
                    float(value["yellow"]),
                    float(value["black"]),
                )
            if {"red", "green", "blue"}.issubset(value):
                return Color(float(value["red"]), float(value["green"]), float(value["blue"]))
        except (TypeError, ValueError):
            return None
        return None

    def apply_text_note(
        partial: ModernPartialNode,
        value: dict[str, object],
        note_span: tuple[int, int],
    ) -> TextFrame | None:
        if isinstance(value.get("id"), str) and value["id"]:
            partial.id = str(value["id"])
        if isinstance(value.get("name"), str):
            partial.name = str(value["name"])
        partial.end = note_span[1]
        partial.evidence["identity_note_span"] = {
            "segment": segment.key,
            "start": note_span[0],
            "end": note_span[1],
        }
        provable = {
            "coordinate_space": value.get("coordinate_space"),
            "x": value.get("x"),
            "y": value.get("y"),
            "font_size": value.get("font_size"),
            "font_name": value.get("font_name"),
            "fill": value.get("fill"),
        }
        for key, item in provable.items():
            if item is not None:
                partial.known_fields[key] = item
        required = ("coordinate_space", "x", "y", "font_size", "font_name", "fill")
        missing = [key for key in required if provable[key] is None]
        if (
            provable["coordinate_space"] != "document"
            and "coordinate_space" not in missing
        ):
            missing.append("coordinate_space=document")
        fill_value = text_fill(provable["fill"])
        if provable["fill"] is not None and fill_value is None:
            missing.append("valid_fill")
        numeric_text_fields: dict[str, float] = {}
        for key in ("x", "y", "font_size"):
            if provable[key] is None:
                continue
            try:
                numeric_text_fields[key] = float(provable[key])  # type: ignore[arg-type]
            except (TypeError, ValueError):
                missing.append(f"valid_{key}")
        if any(not math.isfinite(item) for item in numeric_text_fields.values()):
            missing.append("finite_text_coordinates_and_size")
        if (
            provable["font_name"] is not None
            and (
                not isinstance(provable["font_name"], str)
                or not provable["font_name"]
            )
        ):
            missing.append("valid_font_name")
        text = partial.known_fields.get("text")
        if not isinstance(text, str):
            missing.append("text")
        partial.missing_fields = tuple(dict.fromkeys(missing))
        if missing or fill_value is None or not isinstance(text, str):
            partial.reason = (
                "Text content or identity is readable, but complete placement/style evidence "
                "is not present in source-local supported fields. AI11 internal placement "
                "matrices are retained but their coordinate/index mapping is not guessed."
            )
            return None
        try:
            frame = TextFrame(
                id=partial.id,
                name=partial.name,
                text=text,
                x=numeric_text_fields["x"],
                y=numeric_text_fields["y"],
                font_size=numeric_text_fields["font_size"],
                font_name=provable["font_name"],
                tracking=float(value.get("tracking", 0.0)),
                rotation=float(value.get("rotation", 0.0)),
                area_width=(
                    float(value["area_width"])
                    if value.get("area_width") is not None
                    else None
                ),
                area_height=(
                    float(value["area_height"])
                    if value.get("area_height") is not None
                    else None
                ),
                leading=(float(value["leading"]) if value.get("leading") is not None else None),
                fill=fill_value,
                alignment=str(value.get("alignment", "left")),
                unknown={
                    "modern_source": {
                        "segment": segment.key,
                        "object_span": {"start": partial.start, "end": note_span[1]},
                        "identity_and_placement_note_span": {
                            "start": note_span[0],
                            "end": note_span[1],
                        },
                        "text_document_span": (
                            {"start": text_document_span[0], "end": text_document_span[1]}
                            if text_document_span is not None
                            else None
                        ),
                    }
                },
            )
        except (TypeError, ValueError) as error:
            partial.missing_fields = ("valid_text_frame_fields",)
            partial.reason = (
                f"Source-local text metadata is invalid: {type(error).__name__}: {error}"
            )
            return None
        return frame

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
            missing = ["coordinate_space", "x", "y"]
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
                missing_fields=tuple([*missing, "font_size", "font_name", "fill"]),
                reason=(
                    "AI11 story content is readable, but complete source-local placement/style "
                    "evidence is not proven by the supported decoded-byte fields."
                ),
                evidence=(
                    {
                        "text_document_span": {
                            "segment": segment.key,
                            "start": text_document_span[0],
                            "end": text_document_span[1],
                        }
                    }
                    if text_document_span is not None
                    else {}
                ),
            )
            active = containers[-1] if containers else None
            append_partial(partial, active)
            pending_text_containers[id(partial)] = active
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
                while len(containers) > 1:
                    builder = containers.pop()
                    structural_partial(
                        builder,
                        lexeme.start,
                        f"A new layer began before the {builder.kind} was closed.",
                    )
                    restore_graphics_state(builder)
                finish_layer(
                    lexeme.start,
                    closed=False,
                    reason="A new layer began before the previous layer ended.",
                )
                current_layer = Layer(
                    id=f"modern-{segment.index}-layer-{len(reduction.layers)}",
                    name=f"Layer {len(reduction.layers) + 1}",
                )
                reduction.layers.append(current_layer)
                containers = [
                    _ContainerBuilder(
                        "layer",
                        current_layer.id,
                        lexeme.start,
                        node=current_layer,
                    )
                ]
                last_object = current_layer
                continue
            if raw.startswith(b"%AI5_EndLayer"):
                abandon_path(
                    lexeme.start,
                    "The layer ended before the path received a paint operator.",
                )
                while len(containers) > 1:
                    builder = containers.pop()
                    structural_partial(
                        builder,
                        lexeme.start,
                        f"The layer ended before the {builder.kind} received its closing operator.",
                    )
                    restore_graphics_state(builder)
                finish_layer(lexeme.end, closed=True)
                current_layer = None
                containers = []
                last_object = None
                continue
            uid = _XML_UID_RE.match(raw)
            if uid and isinstance(last_object, Layer):
                last_object.id = uid.group("value").decode("utf-8", errors="replace")
                if containers and containers[0].node is last_object:
                    containers[0].id = last_object.id
                continue
            note = _NOTE_RE.match(raw)
            if note:
                value = _note_value(note.group("value"))
                if value is not None and last_object is not None:
                    if isinstance(last_object, ModernPartialNode) and last_object.kind == "text":
                        frame = apply_text_note(
                            last_object,
                            value,
                            (lexeme.start, lexeme.end),
                        )
                        if frame is not None:
                            parent = pending_text_containers.pop(id(last_object), None)
                            reduction.partials.remove(last_object)
                            if parent is not None:
                                saved = containers
                                try:
                                    if parent in containers:
                                        while containers[-1] is not parent:
                                            containers.pop()
                                    else:
                                        containers = [parent]
                                    append_complete("text", frame, count_item=False)
                                finally:
                                    containers = saved
                            reduction.text_count += 1
                            last_object = frame
                    else:
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
        elif op in {"Xa", "k"}:
            fill = process_color(values, statement)
        elif op in {"XA", "K"}:
            stroke = process_color(values, statement)
        elif op == "w" and values and math.isfinite(values[-1]) and values[-1] >= 0:
            stroke_width = _StyleValue(values[-1], statement.start, statement.end)
        elif op == "D" and values:
            polarity = _StyleValue(
                "positive" if values[-1] != 0 else "negative",
                statement.start,
                statement.end,
            )
        elif op in {"u", "*u", "q"}:
            abandon_path(statement.start, "A hierarchy operator began before the path was painted.")
            if len(containers) >= max_semantic_nesting:
                raise ModernSemanticLimitExceeded(
                    f"modern semantic hierarchy nesting exceeds {max_semantic_nesting}"
                )
            if op == "u":
                group_id = f"modern-{segment.index}-group-{reduction.group_count}"
                reduction.group_count += 1
                group = Group(id=group_id)
                containers.append(
                    _ContainerBuilder(
                        "group",
                        group_id,
                        statement.start,
                        node=group,
                        operator_spans=[(statement.operator.start, statement.operator.end)],
                    )
                )
            elif op == "*u":
                compound_id = (
                    f"modern-{segment.index}-compound-{reduction.compound_path_count}"
                )
                reduction.compound_path_count += 1
                containers.append(
                    _ContainerBuilder(
                        "compound_path",
                        compound_id,
                        statement.start,
                        operator_spans=[(statement.operator.start, statement.operator.end)],
                    )
                )
            else:
                clipping_id = (
                    f"modern-{segment.index}-clipping-{reduction.clipping_group_count}"
                )
                reduction.clipping_group_count += 1
                containers.append(
                    _ContainerBuilder(
                        "clipping_group",
                        clipping_id,
                        statement.start,
                        operator_spans=[(statement.operator.start, statement.operator.end)],
                        graphics_state=(fill, stroke, stroke_width, polarity),
                    )
                )
        elif op == "U":
            abandon_path(statement.start, "A group ended before the path was painted.")
            close_container("group", statement)
        elif op == "*U":
            abandon_path(statement.start, "A compound path ended before the path was painted.")
            close_container("compound_path", statement)
        elif op == "Q":
            abandon_path(statement.start, "A clipping group ended before the path was painted.")
            builder = close_container("clipping_group", statement)
            if builder is not None:
                restore_graphics_state(builder)
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
        elif op == "v" and current_path is not None and len(values) >= 4:
            x2, y2 = transform(values[-4], values[-3])
            x3, y3 = transform(values[-2], values[-1])
            anchor = current_path.points[-1]
            current_path.points[-1] = anchor.with_out_handle(ControlPoint(anchor.x, anchor.y))
            current_path.points.append(Point(x3, y3, in_handle=ControlPoint(x2, y2)))
            current_path.operator_spans.append((statement.operator.start, statement.operator.end))
        elif op == "y" and current_path is not None and len(values) >= 4:
            x1, y1 = transform(values[-4], values[-3])
            x3, y3 = transform(values[-2], values[-1])
            current_path.points[-1] = current_path.points[-1].with_out_handle(ControlPoint(x1, y1))
            current_path.points.append(Point(x3, y3, in_handle=ControlPoint(x3, y3)))
            current_path.operator_spans.append((statement.operator.start, statement.operator.end))
        elif op in {"h", "H"} and current_path is not None:
            current_path.explicit_closed = op == "h"
            current_path.operator_spans.append((statement.operator.start, statement.operator.end))
        elif op in {"W", "W*"} and current_path is not None:
            current_path.clipping_candidate = True
            current_path.operator_spans.append((statement.operator.start, statement.operator.end))
        elif op in {"f", "F", "f*", "S", "s", "B", "b", "n"}:
            finish_path(statement)
    abandon_path(len(data), "The segment ended before the path received a paint operator.")
    while len(containers) > 1:
        builder = containers.pop()
        structural_partial(
            builder,
            len(data),
            f"The segment ended before the {builder.kind} received its closing operator.",
        )
        restore_graphics_state(builder)
    finish_layer(
        len(data),
        closed=False,
        reason="The segment ended before the layer received its end marker.",
    )
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

    parent_renames: dict[str, list[str]] = {}

    def typed_items(
        container: Layer | Group,
    ) -> dict[str, list[Path | TextFrame | CompoundPath | ClippingGroup | Group]]:
        return {
            "path": list(container.paths),
            "text": list(container.text_frames),
            "compound_path": list(container.compound_paths),
            "clipping_group": list(container.clipping_groups),
            "group": list(container.groups),
        }

    def stable_order(container: Layer | Group) -> list[tuple[str, object]]:
        by_kind = typed_items(container)
        indexes = {kind: 0 for kind in by_kind}
        result: list[tuple[str, object]] = []
        for reference in container.item_order:
            items = by_kind.get(reference.kind, [])
            index = indexes.get(reference.kind, 0)
            if index < len(items):
                result.append((reference.kind, items[index]))
                indexes[reference.kind] = index + 1
        for kind, items in by_kind.items():
            result.extend((kind, item) for item in items[indexes[kind] :])
        return result

    def visit_path(path: Path) -> None:
        path.id = unique_id(path.id)

    def sync_source_parent(item: object, kind: str, parent_id: str) -> None:
        unknown = getattr(item, "unknown", None)
        if not isinstance(unknown, dict):
            return
        source = unknown.get("modern_source")
        if isinstance(source, dict):
            source["parent"] = {"kind": kind, "id": parent_id}

    def visit_container(container: Layer | Group) -> None:
        original = container.id
        container.id = unique_id(container.id)
        parent_renames.setdefault(original, []).append(container.id)
        ordered = stable_order(container)
        for kind, item in ordered:
            parent_kind = "layer" if isinstance(container, Layer) else "group"
            sync_source_parent(item, parent_kind, container.id)
            if kind == "path":
                visit_path(item)  # type: ignore[arg-type]
            elif kind == "text":
                item.id = unique_id(item.id)  # type: ignore[union-attr]
            elif kind == "compound_path":
                compound = item
                assert isinstance(compound, CompoundPath)
                compound.id = unique_id(compound.id)
                for path in compound.paths:
                    sync_source_parent(path, "compound_path", compound.id)
                    visit_path(path)
            elif kind == "clipping_group":
                clipping = item
                assert isinstance(clipping, ClippingGroup)
                clipping.id = unique_id(clipping.id)
                sync_source_parent(clipping.clipping_path, "clipping_group", clipping.id)
                visit_path(clipping.clipping_path)
                for path in clipping.paths:
                    sync_source_parent(path, "clipping_group", clipping.id)
                    visit_path(path)
            elif kind == "group":
                visit_container(item)  # type: ignore[arg-type]
        container.item_order = [LayerItemRef(kind, item.id) for kind, item in ordered]

    for layer in layers:
        visit_container(layer)
    for partial in partials:
        partial.id = unique_id(partial.id)
    for partial in partials:
        parent = partial._parent_ref
        if parent is not None and isinstance(getattr(parent, "id", None), str):
            partial.parent_id = parent.id
        elif partial.parent_id in parent_renames and len(parent_renames[partial.parent_id]) == 1:
            partial.parent_id = parent_renames[partial.parent_id][0]
    return collisions


def _walk_container_items(
    container: Layer | Group,
) -> tuple[list[Path], list[Group], list[CompoundPath], list[ClippingGroup], list[TextFrame]]:
    paths = list(container.paths)
    groups = list(container.groups)
    compounds = list(container.compound_paths)
    clippings = list(container.clipping_groups)
    texts = list(container.text_frames)
    for compound in compounds:
        paths.extend(compound.paths)
    for clipping in clippings:
        paths.append(clipping.clipping_path)
        paths.extend(clipping.paths)
    for group in container.groups:
        child_paths, child_groups, child_compounds, child_clippings, child_texts = (
            _walk_container_items(group)
        )
        paths.extend(child_paths)
        groups.extend(child_groups)
        compounds.extend(child_compounds)
        clippings.extend(child_clippings)
        texts.extend(child_texts)
    return paths, groups, compounds, clippings, texts


def project_modern_semantics(
    segments: tuple[PrivateDataSegment, ...],
    *,
    max_lexemes: int = DEFAULT_MAX_LEXEMES,
    max_lexeme_bytes: int = DEFAULT_MAX_LEXEME_BYTES,
    max_text_document_nesting: int = DEFAULT_MAX_TEXT_DOCUMENT_NESTING,
    max_semantic_nesting: int = DEFAULT_MAX_SEMANTIC_NESTING,
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
        try:
            reduction = _reduce_segment(
                segment,
                cst,
                max_text_document_nesting=max_text_document_nesting,
                max_semantic_nesting=max_semantic_nesting,
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
        layers.extend(reduction.layers)
        partials.extend(reduction.partials)
        diagnostics.extend(reduction.diagnostics)
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
    all_paths: list[Path] = []
    all_groups: list[Group] = []
    all_compounds: list[CompoundPath] = []
    all_clippings: list[ClippingGroup] = []
    all_texts: list[TextFrame] = []
    for layer in layers:
        paths_in_layer, groups_in_layer, compounds_in_layer, clippings_in_layer, texts_in_layer = (
            _walk_container_items(layer)
        )
        all_paths.extend(paths_in_layer)
        all_groups.extend(groups_in_layer)
        all_compounds.extend(compounds_in_layer)
        all_clippings.extend(clippings_in_layer)
        all_texts.extend(texts_in_layer)
    path_count = len(all_paths)
    if layers:
        max_x = max([point.x for path in all_paths for point in path.points] or [1.0])
        max_y = max([point.y for path in all_paths for point in path.points] or [1.0])
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
                f"Projected {len(all_texts)} text frames from complete source-local evidence; "
                f"retained {text_count} text objects as partial because placement/style "
                "evidence was incomplete.",
            )
        )
    elif all_texts:
        diagnostics.append(
            ModernDiagnostic(
                "info",
                "modern_text_projected",
                f"Projected {len(all_texts)} text frames from complete source-local evidence.",
            )
        )
    structural_partial_count = sum(
        item.kind in {"group", "compound_path", "clipping_group"} for item in partials
    )
    if structural_partial_count:
        diagnostics.append(
            ModernDiagnostic(
                "warning",
                "partial_modern_structure",
                f"Retained {structural_partial_count} incomplete hierarchy nodes with exact spans.",
            )
        )
    other_partial_counts: dict[str, int] = {}
    for item in partials:
        if item.kind == "text":
            continue
        other_partial_counts[item.kind] = other_partial_counts.get(item.kind, 0) + 1
    if other_partial_counts:
        summary = ", ".join(
            f"{kind}={count}" for kind, count in sorted(other_partial_counts.items())
        )
        diagnostics.append(
            ModernDiagnostic(
                "warning",
                "partial_modern_nodes",
                f"Retained non-text partial nodes with exact spans: {summary}.",
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
        projected_group_count=len(all_groups),
        projected_compound_path_count=len(all_compounds),
        projected_clipping_group_count=len(all_clippings),
        projected_text_count=len(all_texts),
        partial_node_count=len(partials),
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
