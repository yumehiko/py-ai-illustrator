"""Pure operation planning boundary.

Planning is intentionally read-only.  The lazy adapter keeps the established
plan report and fail-closed behavior while allowing callers to import planning
without importing the apply backend directly.
"""

from __future__ import annotations

from typing import Any


def plan_edit(*args: Any, **kwargs: Any) -> Any:
    """Resolve selectors and preconditions without modifying an AI file."""

    from ._operation_orchestration import plan_edit as _plan

    return _plan(*args, **kwargs)


__all__ = ["plan_edit"]
