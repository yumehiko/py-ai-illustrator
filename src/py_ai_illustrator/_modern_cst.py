"""Lossless lexer and exact-span CST for decoded Illustrator PrivateData.

This module owns lexical tokenization and the flat statement CST. It does not
project artwork into the public Document model.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass

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


__all__ = [
    "ModernCSTStatement",
    "ModernLexeme",
    "ModernPrivateDataCST",
    "ModernSemanticLimitExceeded",
    "lex_modern_private_data",
    "parse_modern_private_data",
]
