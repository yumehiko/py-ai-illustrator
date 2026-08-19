"""Public facade for versioned safe-edit planning and apply orchestration.

Schema parsing, selector resolution, planning, and apply verification are
implemented in :mod:`._operation_orchestration`.  Keeping this module as the
compatibility entry point preserves the existing Python and CLI contracts.
"""

from ._operation_orchestration import (
    AllowedImpact,
    AncestorSelector,
    LegacyEditPlan,
    ModernEditPlan,
    OperationManifest,
    OperationRequest,
    OperationRequestError,
    ResolvedNode,
    ResolvedOperation,
    Selector,
    SelectorResolver,
    apply_edit,
    apply_edit_plan,
    inspect_editable_legacy,
    inspect_editable_modern,
    plan_edit,
    unexpected_semantic_differences,
)

__all__ = [
    "AllowedImpact",
    "AncestorSelector",
    "LegacyEditPlan",
    "ModernEditPlan",
    "OperationManifest",
    "OperationRequest",
    "OperationRequestError",
    "ResolvedNode",
    "ResolvedOperation",
    "Selector",
    "SelectorResolver",
    "apply_edit",
    "apply_edit_plan",
    "inspect_editable_legacy",
    "inspect_editable_modern",
    "plan_edit",
    "unexpected_semantic_differences",
]
