"""Public facade for modern PrivateData CST and semantic projection APIs.

The implementation is split between :mod:`._modern_cst` and
:mod:`._modern_projection`. The facade preserves the public import path while
internal code can depend on the container, CST, projection, discovery, and
patch layers explicitly.
"""

from ._modern_cst import (
    ModernCSTStatement,
    ModernLexeme,
    ModernPrivateDataCST,
    ModernSemanticLimitExceeded,
    lex_modern_private_data,
    parse_modern_private_data,
)
from ._modern_projection import (
    ModernPartialNode,
    ModernSemanticCoverage,
    ModernSemanticResult,
    ModernUnknownOperator,
    ModernUnknownSpan,
    project_modern_semantics,
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
    "lex_modern_private_data",
    "parse_modern_private_data",
    "project_modern_semantics",
]
