"""CST boundary for decoded Illustrator PrivateData.

The reducer implementation remains private to the semantic backend, but this
module exposes the lossless lexer/CST contract independently from projection.
It is intentionally a lazy adapter so importing the CST boundary never
reopens a PDF or imports the public facade.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ._modern_semantic_projection import (
        ModernCSTStatement,
        ModernLexeme,
        ModernPrivateDataCST,
        ModernSemanticLimitExceeded,
    )


def lex_modern_private_data(*args: Any, **kwargs: Any) -> Any:
    """Lex one decoded PrivateData segment without semantic projection."""

    from ._modern_semantic_projection import lex_modern_private_data as _lex

    return _lex(*args, **kwargs)


def parse_modern_private_data(*args: Any, **kwargs: Any) -> Any:
    """Build the exact-span CST and semantic evidence for one segment."""

    from ._modern_semantic_projection import parse_modern_private_data as _parse

    return _parse(*args, **kwargs)


def __getattr__(name: str) -> Any:
    if name in {
        "ModernCSTStatement",
        "ModernLexeme",
        "ModernPrivateDataCST",
        "ModernSemanticLimitExceeded",
    }:
        from . import _modern_semantic_projection as _implementation

        return getattr(_implementation, name)
    raise AttributeError(name)


__all__ = [
    "ModernCSTStatement",
    "ModernLexeme",
    "ModernPrivateDataCST",
    "ModernSemanticLimitExceeded",
    "lex_modern_private_data",
    "parse_modern_private_data",
]
