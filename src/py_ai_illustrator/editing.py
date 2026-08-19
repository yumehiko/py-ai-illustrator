"""Public facade for versioned safe-edit planning and apply orchestration.

Schema parsing, selector resolution, planning, and apply verification are
implemented in dedicated internal boundaries. Keeping this module as the
compatibility entry point preserves the existing Python and CLI contracts.
"""

from ._operation_orchestration import apply_edit, apply_edit_plan
from ._operation_plan import (
    AllowedImpact,
    LegacyEditPlan,
    ModernEditPlan,
    ResolvedNode,
    ResolvedOperation,
    SelectorResolver,
    inspect_editable_legacy,
    inspect_editable_modern,
    plan_edit,
    unexpected_semantic_differences,
)
from ._operation_schema import (
    AncestorSelector,
    OperationManifest,
    OperationRequest,
    OperationRequestError,
    Selector,
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
