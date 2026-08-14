"""Byte-preserving source model for legacy Illustrator documents."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

DEFAULT_MAX_SOURCE_BYTES = 64 * 1024 * 1024
DEFAULT_MAX_LINE_BYTES = 8 * 1024 * 1024
DEFAULT_MAX_LINES = 2_000_000

LineKind = Literal["blank", "comment", "statement"]
_PHYSICAL_LINE_RE = re.compile(rb"[^\r\n]*(?:\r\n|\r|\n|$)")


class SourceLimitExceeded(ValueError):
    """Raised when tokenizing input would exceed a configured resource limit."""


@dataclass(frozen=True, slots=True)
class LegacyLineToken:
    """One physical line and its exact half-open byte spans."""

    line_number: int
    start: int
    content_end: int
    end: int
    kind: LineKind

    def __post_init__(self) -> None:
        if self.line_number < 1:
            raise ValueError("line_number must be positive")
        if not 0 <= self.start <= self.content_end <= self.end:
            raise ValueError("line token spans must be ordered and non-negative")


@dataclass(frozen=True, slots=True)
class LegacySource:
    """Original bytes plus a non-destructive index of physical lines."""

    data: bytes
    lines: tuple[LegacyLineToken, ...]

    def to_bytes(self) -> bytes:
        return self.data

    def raw_line(self, token: LegacyLineToken) -> bytes:
        return self.data[token.start : token.end]

    def line_content(self, token: LegacyLineToken) -> bytes:
        return self.data[token.start : token.content_end]

    def line_ending(self, token: LegacyLineToken) -> bytes:
        return self.data[token.content_end : token.end]


def tokenize_legacy(
    data: bytes,
    *,
    max_source_bytes: int = DEFAULT_MAX_SOURCE_BYTES,
    max_line_bytes: int = DEFAULT_MAX_LINE_BYTES,
    max_lines: int = DEFAULT_MAX_LINES,
) -> LegacySource:
    """Index legacy source without decoding, normalizing, or dropping bytes."""

    if max_source_bytes <= 0 or max_line_bytes <= 0 or max_lines <= 0:
        raise ValueError("source limits must be positive")
    if len(data) > max_source_bytes:
        raise SourceLimitExceeded(
            f"Legacy source is {len(data)} bytes; limit is {max_source_bytes} bytes"
        )

    lines: list[LegacyLineToken] = []
    for line_number, match in enumerate(_PHYSICAL_LINE_RE.finditer(data), start=1):
        start, end = match.span()
        if start == end:
            continue
        if data[start:end].endswith(b"\r\n"):
            content_end = end - 2
        elif data[start:end].endswith((b"\r", b"\n")):
            content_end = end - 1
        else:
            content_end = end
        if end - start > max_line_bytes:
            raise SourceLimitExceeded(
                f"Legacy source line {line_number} is {end - start} bytes; "
                f"limit is {max_line_bytes} bytes"
            )
        if line_number > max_lines:
            raise SourceLimitExceeded(f"Legacy source exceeds {max_lines} lines")

        content = data[start:content_end].lstrip()
        kind: LineKind
        if not content:
            kind = "blank"
        elif content.startswith(b"%"):
            kind = "comment"
        else:
            kind = "statement"
        lines.append(
            LegacyLineToken(
                line_number=line_number,
                start=start,
                content_end=content_end,
                end=end,
                kind=kind,
            )
        )

    return LegacySource(data=data, lines=tuple(lines))
