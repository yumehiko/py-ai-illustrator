"""Editable-target discovery boundary for modern synchronized editing.

Discovery is exposed separately from operation execution.  The implementation
adapter is lazy to keep importing inventory APIs free of write-time work and
to preserve the existing target evidence and stop reasons byte-for-byte.
"""

from __future__ import annotations

from typing import Any

_DISCOVERY_NAMES = (
    "inspect_modern_container_translate_targets",
    "inspect_modern_fill_targets",
    "inspect_modern_representation_consistency",
    "inspect_modern_stroke_targets",
    "inspect_modern_text_targets",
    "inspect_modern_translate_targets",
)


def __getattr__(name: str) -> Any:
    if name in _DISCOVERY_NAMES:
        from . import _modern_patch as _implementation

        return getattr(_implementation, name)
    raise AttributeError(name)


__all__ = list(_DISCOVERY_NAMES)
