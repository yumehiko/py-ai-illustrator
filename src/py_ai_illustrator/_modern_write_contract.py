"""Shared error contract for modern synchronized write backends."""

from __future__ import annotations


class ModernWriteError(ValueError):
    """Raised before output when a synchronized modern patch is not provably safe."""


__all__ = ["ModernWriteError"]
