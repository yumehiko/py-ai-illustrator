"""Versioned operation-manifest schema boundary.

The compatibility implementation remains in the editing backend for now; this
module gives readers and planners a dependency-light import boundary without
making them depend on patch execution internals.
"""

from __future__ import annotations

from typing import Any

_SCHEMA_NAMES = (
    "AncestorSelector",
    "OperationManifest",
    "OperationRequest",
    "OperationRequestError",
    "Selector",
)


def __getattr__(name: str) -> Any:
    if name in _SCHEMA_NAMES:
        from . import _operation_orchestration as _implementation

        return getattr(_implementation, name)
    raise AttributeError(name)


__all__ = list(_SCHEMA_NAMES)
