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
from py_ai_illustrator._modern_projection import ModernSemanticResult as InternalSemanticResult
from py_ai_illustrator._operation_plan import plan_edit as InternalPlanEdit
from py_ai_illustrator._operation_schema import OperationManifest as InternalManifest
from py_ai_illustrator._operation_schema import Selector as SchemaSelector


def test_public_facades_preserve_the_existing_types_and_operations() -> None:
    assert ModernAIReadResult is not None
    assert ModernCSTStatement is InternalCSTStatement
    assert ModernSemanticResult is InternalSemanticResult
    assert ModernWriteResult.__name__ == "ModernWriteResult"
    assert OperationManifest.__module__ == "py_ai_illustrator._operation_schema"
    assert Selector.__module__ == "py_ai_illustrator._operation_schema"
    assert InternalManifest is OperationManifest
    assert SchemaSelector is Selector
    assert plan_edit is InternalPlanEdit
    assert plan_edit.__module__ == "py_ai_illustrator._operation_plan"
    assert callable(read_modern_ai)
    assert callable(parse_modern_private_data)
    assert callable(plan_edit)


def test_target_discovery_has_a_separate_import_boundary() -> None:
    assert inspect_modern_fill_targets.__module__ == (
        "py_ai_illustrator._modern_discovery"
    )


def test_internal_backends_do_not_import_public_compatibility_facades() -> None:
    package = Path(__file__).parents[1] / "src" / "py_ai_illustrator"
    internal_modules = (
        "_modern_container.py",
        "_modern_cst.py",
        "_modern_projection.py",
        "_modern_discovery.py",
        "_modern_patch.py",
        "_modern_write_contract.py",
        "_operation_schema.py",
        "_operation_plan.py",
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


def test_internal_responsibilities_and_dependency_direction_are_explicit() -> None:
    package = Path(__file__).parents[1] / "src" / "py_ai_illustrator"

    def imports_for(name: str) -> set[str]:
        tree = ast.parse((package / name).read_text(encoding="utf-8"))
        return {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module is not None
        }

    def defined_names(name: str) -> set[str]:
        tree = ast.parse((package / name).read_text(encoding="utf-8"))
        return {
            node.name
            for node in ast.walk(tree)
            if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
        }

    assert {
        "ModernCSTStatement",
        "lex_modern_private_data",
        "parse_modern_private_data",
    }.issubset(defined_names("_modern_cst.py"))
    assert {"ModernSemanticResult", "project_modern_semantics"}.issubset(
        defined_names("_modern_projection.py")
    )
    assert "project_modern_semantics" not in defined_names("_modern_cst.py")
    assert "inspect_modern_fill_targets" in defined_names("_modern_discovery.py")
    assert "patch_modern_path_fill" in defined_names("_modern_patch.py")
    assert "inspect_modern_fill_targets" not in defined_names("_modern_patch.py")

    assert "_modern_patch" not in imports_for("_modern_discovery.py")
    assert "_modern_discovery" in imports_for("_modern_patch.py")
    assert "_operation_orchestration" not in imports_for("_operation_schema.py")
    assert "_operation_orchestration" not in imports_for("_operation_plan.py")
    assert "_operation_plan" in imports_for("_operation_orchestration.py")
    assert "_operation_schema" in imports_for("_operation_plan.py")
    assert "apply_legacy_patch" not in (
        package / "_operation_plan.py"
    ).read_text(encoding="utf-8")
    assert "plan_edit" in defined_names("_operation_plan.py")
    assert "apply_edit" in defined_names("_operation_orchestration.py")
    assert "plan_edit" not in defined_names("_operation_orchestration.py")
