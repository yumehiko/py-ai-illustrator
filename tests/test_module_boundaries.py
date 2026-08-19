"""Regression checks for the Issue #8 internal responsibility boundaries."""

from __future__ import annotations

import ast
from pathlib import Path

from py_ai_illustrator import (
    ModernCSTStatement,
    ModernSemanticResult,
    ModernWriteResult,
    OperationManifest,
    Selector,
    parse_modern_private_data,
    plan_edit,
    read_modern_ai,
)
from py_ai_illustrator._modern_container import ModernAIReadResult
from py_ai_illustrator._modern_cst import ModernCSTStatement as InternalCSTStatement
from py_ai_illustrator._modern_discovery import inspect_modern_fill_targets
from py_ai_illustrator._modern_patch import inspect_modern_fill_targets as PatchFillTargets
from py_ai_illustrator._modern_projection import ModernSemanticResult as InternalSemanticResult
from py_ai_illustrator._operation_orchestration import OperationManifest as InternalManifest
from py_ai_illustrator._operation_schema import Selector as SchemaSelector


def test_public_facades_preserve_the_existing_types_and_operations() -> None:
    assert ModernAIReadResult is not None
    assert ModernCSTStatement is InternalCSTStatement
    assert ModernSemanticResult is InternalSemanticResult
    assert ModernWriteResult.__name__ == "ModernWriteResult"
    assert OperationManifest is InternalManifest
    assert Selector is SchemaSelector
    assert callable(read_modern_ai)
    assert callable(parse_modern_private_data)
    assert callable(plan_edit)


def test_target_discovery_has_a_separate_import_boundary() -> None:
    assert inspect_modern_fill_targets is PatchFillTargets


def test_internal_backends_do_not_import_public_compatibility_facades() -> None:
    package = Path(__file__).parents[1] / "src" / "py_ai_illustrator"
    internal_modules = (
        "_modern_container.py",
        "_modern_semantic_projection.py",
        "_modern_patch.py",
        "_operation_orchestration.py",
    )
    facades = {".modern", ".modern_semantic", ".modern_writing", ".editing"}

    for name in internal_modules:
        tree = ast.parse((package / name).read_text(encoding="utf-8"))
        imports = {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module is not None
        }
        assert imports.isdisjoint(facades), (name, imports & facades)
