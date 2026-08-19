"""Public facade for legacy Illustrator reading, writing, and lossless patching.

The implementation is split into codec, reader, writer, and patch modules.  This
module keeps the pre-refactor imports stable for callers and the CLI.
"""

from __future__ import annotations

from pathlib import Path as FilePath
from typing import Literal

from ._legacy_codec import UnsupportedLegacyFeature, linked_image_placeholder_note  # noqa: F401
from ._legacy_patch import (
    ContainerType,
    LegacyContainer,
    LegacyPatchOperation,
    LegacyPatchPlan,
    ReplaceLinkedImageSource,
    ReplaceText,
    SetPathFill,
    SetPathStroke,
    TranslateContainer,
    TranslatePath,
    TranslationMember,
    apply_legacy_patch,
    patch_container_translate,
    patch_legacy,
    patch_linked_image_source,
    patch_path_fill,
    patch_path_stroke,
    patch_path_translate,
    patch_text,
    plan_legacy_patch,
)  # noqa: F401
from ._legacy_reader import loads_ai7, reads_ai7  # noqa: F401
from ._legacy_writer import dump_ai7, dumps_ai7  # noqa: F401
from .compatibility import LegacyReadResult
from .model import (
    Artboard,
    ClippingGroup,
    CmykColor,
    Color,
    CompoundPath,
    ControlPoint,
    Document,
    Group,
    Layer,
    LayerItemRef,
    LinkedImage,
    Path,
    Point,
    ProcessColor,
    TextFrame,
)  # noqa: F401

__all__ = [
    "Artboard",
    "ClippingGroup",
    "CmykColor",
    "Color",
    "CompoundPath",
    "ContainerType",
    "ControlPoint",
    "Document",
    "Group",
    "Layer",
    "LayerItemRef",
    "LegacyContainer",
    "LegacyPatchOperation",
    "LegacyPatchPlan",
    "LinkedImage",
    "Path",
    "Point",
    "ProcessColor",
    "ReplaceLinkedImageSource",
    "ReplaceText",
    "SetPathFill",
    "SetPathStroke",
    "TextFrame",
    "TranslateContainer",
    "TranslatePath",
    "TranslationMember",
    "UnsupportedLegacyFeature",
    "apply_legacy_patch",
    "dump_ai7",
    "dumps_ai7",
    "linked_image_placeholder_note",
    "load_ai7",
    "loads_ai7",
    "patch_container_translate",
    "patch_legacy",
    "patch_linked_image_source",
    "patch_path_fill",
    "patch_path_stroke",
    "patch_path_translate",
    "patch_text",
    "plan_legacy_patch",
    "read_ai7",
    "reads_ai7",
    "reserialize_ai7",
]


def reserialize_ai7(
    result: LegacyReadResult,
    *,
    loss_policy: Literal["reject", "discard"] = "reject",
) -> bytes:
    """Serialize parsed IR, rejecting unsupported source features by default."""

    if loss_policy not in {"reject", "discard"}:
        raise ValueError("loss_policy must be 'reject' or 'discard'")
    if loss_policy == "reject" and not result.safe_to_reserialize:
        unsupported = sorted(
            {
                diagnostic.feature_name
                for diagnostic in result.diagnostics
                if diagnostic.code.startswith("unsupported-")
            }
        )
        detail = ", ".join(repr(name) for name in unsupported[:5])
        if len(unsupported) > 5:
            detail += f", and {len(unsupported) - 5} more"
        raise UnsupportedLegacyFeature(
            "Refusing to reserialize parsed legacy IR because unsupported source features "
            f"would be discarded: {detail}. Use loss_policy='discard' explicitly to allow loss."
        )
    return dumps_ai7(result.document)


def read_ai7(source: str | FilePath) -> LegacyReadResult:
    """Read a legacy file with exact source and compatibility evidence."""

    return reads_ai7(FilePath(source).read_bytes())


def load_ai7(source: str | FilePath) -> Document:
    """Read only the modeled IR from a legacy file."""

    return loads_ai7(FilePath(source).read_bytes())
