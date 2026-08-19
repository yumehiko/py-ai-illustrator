"""Read-only semantic projection boundary for modern PrivateData."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ._modern_semantic_projection import (
        ModernPartialNode,
        ModernSemanticCoverage,
        ModernSemanticResult,
        ModernUnknownOperator,
        ModernUnknownSpan,
    )


def project_modern_semantics(*args: Any, **kwargs: Any) -> Any:
    """Project a previously decoded CST/segment stream into the low-level IR."""

    from ._modern_semantic_projection import project_modern_semantics as _project

    return _project(*args, **kwargs)


def __getattr__(name: str) -> Any:
    if name in {
        "ModernPartialNode",
        "ModernSemanticCoverage",
        "ModernSemanticResult",
        "ModernUnknownOperator",
        "ModernUnknownSpan",
    }:
        from . import _modern_semantic_projection as _implementation

        return getattr(_implementation, name)
    raise AttributeError(name)


__all__ = [
    "ModernPartialNode",
    "ModernSemanticCoverage",
    "ModernSemanticResult",
    "ModernUnknownOperator",
    "ModernUnknownSpan",
    "project_modern_semantics",
]
