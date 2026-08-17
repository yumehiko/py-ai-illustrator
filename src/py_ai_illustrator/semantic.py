"""Deterministic semantic comparison for the JSON-compatible graphics IR."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from .model import Document

DifferenceKind = Literal["added", "removed", "changed", "reordered"]
_MISSING = object()


@dataclass(frozen=True, slots=True)
class SemanticDifference:
    """One source-independent difference between two IR documents."""

    kind: DifferenceKind
    path: str
    before: Any = None
    after: Any = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "path": self.path,
            "before": self.before,
            "after": self.after,
        }


@dataclass(frozen=True, slots=True)
class SemanticDiff:
    """Ordered semantic differences between two documents."""

    differences: tuple[SemanticDifference, ...]

    @property
    def equal(self) -> bool:
        return not self.differences

    def to_dict(self) -> dict[str, Any]:
        return {
            "equal": self.equal,
            "difference_count": len(self.differences),
            "differences": [difference.to_dict() for difference in self.differences],
        }


def _path_field(path: str, field: str) -> str:
    return f"{path}.{field}" if path else field


def _list_identity(value: Any) -> tuple[str, str] | None:
    if not isinstance(value, dict):
        return None
    node_id = value.get("id")
    if not isinstance(node_id, str):
        return None
    kind = value.get("kind")
    return (kind if isinstance(kind, str) else "id", node_id)


def _identified_list(
    values: list[Any],
) -> tuple[list[tuple[str, str]], dict[tuple[str, str], Any]] | None:
    identities = [_list_identity(value) for value in values]
    if any(identity is None for identity in identities):
        return None
    concrete = [identity for identity in identities if identity is not None]
    if len(set(concrete)) != len(concrete):
        return None
    return concrete, dict(zip(concrete, values, strict=True))


def _identity_path(path: str, identity: tuple[str, str]) -> str:
    kind, node_id = identity
    label = "id" if kind == "id" else f"{kind}.id"
    return f"{path}[{label}={node_id!r}]"


def _compare(before: Any, after: Any, path: str, output: list[SemanticDifference]) -> None:
    if before == after:
        return
    if isinstance(before, dict) and isinstance(after, dict):
        for key in sorted(before.keys() | after.keys()):
            child_path = _path_field(path, key)
            before_value = before.get(key, _MISSING)
            after_value = after.get(key, _MISSING)
            if before_value is _MISSING:
                output.append(SemanticDifference("added", child_path, after=after_value))
            elif after_value is _MISSING:
                output.append(SemanticDifference("removed", child_path, before=before_value))
            else:
                _compare(before_value, after_value, child_path, output)
        return
    if isinstance(before, list) and isinstance(after, list):
        before_identified = _identified_list(before)
        after_identified = _identified_list(after)
        if before_identified is not None and after_identified is not None:
            before_order, before_by_id = before_identified
            after_order, after_by_id = after_identified
            common_identities = before_by_id.keys() & after_by_id.keys()
            before_common_order = [item for item in before_order if item in common_identities]
            after_common_order = [item for item in after_order if item in common_identities]
            if before_common_order != after_common_order:
                output.append(
                    SemanticDifference(
                        "reordered",
                        _path_field(path, "@order"),
                        before=before_common_order,
                        after=after_common_order,
                    )
                )
            for identity in sorted(before_by_id.keys() | after_by_id.keys()):
                child_path = _identity_path(path, identity)
                before_value = before_by_id.get(identity, _MISSING)
                after_value = after_by_id.get(identity, _MISSING)
                if before_value is _MISSING:
                    output.append(SemanticDifference("added", child_path, after=after_value))
                elif after_value is _MISSING:
                    output.append(SemanticDifference("removed", child_path, before=before_value))
                else:
                    _compare(before_value, after_value, child_path, output)
            return
        common_length = min(len(before), len(after))
        for index in range(common_length):
            _compare(before[index], after[index], f"{path}[{index}]", output)
        for index in range(common_length, len(before)):
            output.append(SemanticDifference("removed", f"{path}[{index}]", before=before[index]))
        for index in range(common_length, len(after)):
            output.append(SemanticDifference("added", f"{path}[{index}]", after=after[index]))
        return
    output.append(SemanticDifference("changed", path, before=before, after=after))


def semantic_diff(before: Document, after: Document) -> SemanticDiff:
    """Compare two documents without consulting or normalizing their source bytes."""

    differences: list[SemanticDifference] = []
    _compare(before.to_dict(), after.to_dict(), "", differences)
    return SemanticDiff(tuple(differences))
