"""Public facade for synchronized modern AI patch operations.

The implementation lives in :mod:`._modern_patch`.  This module intentionally
contains no PDF or PrivateData mutation logic so the historical API remains a
stable entry point while the internal write boundary is explicit.
"""

from ._modern_patch import (
    ModernWriteError,
    ModernWriteResult,
    _pdf_paint_matches,  # noqa: F401
    inspect_modern_container_translate_targets,
    inspect_modern_fill_targets,
    inspect_modern_representation_consistency,
    inspect_modern_stroke_targets,
    inspect_modern_text_targets,
    inspect_modern_translate_targets,
    patch_modern_path_fill,
    patch_modern_path_stroke,
    patch_modern_path_translate,
    patch_modern_text,
)

__all__ = [
    "ModernWriteError",
    "ModernWriteResult",
    "inspect_modern_container_translate_targets",
    "inspect_modern_fill_targets",
    "inspect_modern_representation_consistency",
    "inspect_modern_stroke_targets",
    "inspect_modern_text_targets",
    "inspect_modern_translate_targets",
    "patch_modern_path_fill",
    "patch_modern_path_stroke",
    "patch_modern_path_translate",
    "patch_modern_text",
]
