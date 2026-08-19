"""Compatibility facade for optional Adobe Illustrator integration.

The implementation is intentionally split by responsibility:

* ``_illustrator_bridge`` owns AppleScript/subprocess execution.
* ``_illustrator_dom`` owns IR traversal and pure comparisons.
* ``_illustrator_scripts`` owns ExtendScript generation.
* the remaining ``_illustrator_*`` modules own the font, inspection, fixture,
  legacy, modern, and native adapters respectively.

This module keeps the historical imports stable for callers and the CLI.
"""

from __future__ import annotations

import json  # noqa: F401 - historical module attribute used by integrations/tests
import platform  # noqa: F401 - historical module attribute used by integrations/tests
import subprocess  # noqa: F401 - historical module attribute used by integrations/tests
from pathlib import Path
from typing import Any

from ._illustrator_bridge import execute_javascript
from ._illustrator_dom import (
    angle_close,
    color_close,
    compare_roundtrip_semantics,
    compare_structure,
    document_clipping_groups,
    document_compound_paths,
    document_groups,
    document_linked_images,
    document_paths,
    document_text_frames,
    document_text_frames_dom_order,
    expected_structure,
    group_descendants,
    group_paths,
    group_signature,
    path_geometry_close,
)
from ._illustrator_fixtures import run_illustrator_export_test as _run_export
from ._illustrator_fonts import list_illustrator_fonts as _list_fonts
from ._illustrator_inspection import run_illustrator_test as _run_test
from ._illustrator_legacy import run_illustrator_roundtrip_test as _run_legacy_roundtrip
from ._illustrator_modern import run_illustrator_modern_roundtrip_test as _run_modern_roundtrip
from ._illustrator_native_materialization import materialize_native_ai as _materialize_native
from ._illustrator_scripts import (
    build_export_javascript,
    build_font_catalog_javascript,
    build_javascript,
    build_modern_roundtrip_javascript,
    build_native_materialization_javascript,
    build_roundtrip_javascript,
    character_code_expression,
    native_fill_spec,
    text_identity_note,
)
from .legacy import load_ai7  # noqa: F401 - historical facade export

# Private names were historically importable and are retained as aliases for
# compatibility with downstream tests and integrations.
_execute_javascript = execute_javascript
_character_code_expression = character_code_expression
_text_identity_note = text_identity_note
_native_fill_spec = native_fill_spec
_group_paths = group_paths
_document_paths = document_paths
_document_text_frames = document_text_frames
_document_linked_images = document_linked_images
_document_text_frames_dom_order = document_text_frames_dom_order
_group_descendants = group_descendants
_document_groups = document_groups
_document_compound_paths = document_compound_paths
_document_clipping_groups = document_clipping_groups
_group_signature = group_signature
_expected_structure = expected_structure
_build_javascript = build_javascript
_build_export_javascript = build_export_javascript
_build_roundtrip_javascript = build_roundtrip_javascript
_build_modern_roundtrip_javascript = build_modern_roundtrip_javascript
_build_native_materialization_javascript = build_native_materialization_javascript
_build_font_catalog_javascript = build_font_catalog_javascript
_compare_structure = compare_structure
_color_close = color_close
_path_geometry_close = path_geometry_close
_angle_close = angle_close
_compare_roundtrip_semantics = compare_roundtrip_semantics


def list_illustrator_fonts(
    *,
    query: str | None = None,
    required: tuple[str, ...] = (),
    timeout: float = 30.0,
    application_name: str = "Adobe Illustrator",
) -> dict[str, Any]:
    return _list_fonts(
        query=query,
        required=required,
        timeout=timeout,
        application_name=application_name,
        executor=_execute_javascript,
    )


def run_illustrator_test(
    source: str | Path,
    *,
    timeout: float = 90.0,
    application_name: str = "Adobe Illustrator",
) -> dict[str, Any]:
    return _run_test(
        source, timeout=timeout, application_name=application_name, executor=_execute_javascript
    )


def run_illustrator_modern_roundtrip_test(
    source: str | Path,
    *,
    output: str | Path | None = None,
    timeout: float = 90.0,
    application_name: str = "Adobe Illustrator",
) -> dict[str, Any]:
    return _run_modern_roundtrip(
        source,
        output=output,
        timeout=timeout,
        application_name=application_name,
        executor=_execute_javascript,
    )


def materialize_native_ai(
    source: str | Path,
    destination: str | Path,
    *,
    timeout: float = 90.0,
    application_name: str = "Adobe Illustrator",
) -> dict[str, Any]:
    return _materialize_native(
        source,
        destination,
        timeout=timeout,
        application_name=application_name,
        executor=_execute_javascript,
    )


def run_illustrator_export_test(
    *,
    fixture: str = "rgb-rectangle",
    output: str | Path | None = None,
    timeout: float = 90.0,
    application_name: str = "Adobe Illustrator",
) -> dict[str, Any]:
    return _run_export(
        fixture=fixture,
        output=output,
        timeout=timeout,
        application_name=application_name,
        executor=_execute_javascript,
    )


def run_illustrator_roundtrip_test(
    source: str | Path,
    *,
    output: str | Path | None = None,
    timeout: float = 90.0,
    application_name: str = "Adobe Illustrator",
) -> dict[str, Any]:
    return _run_legacy_roundtrip(
        source,
        output=output,
        timeout=timeout,
        application_name=application_name,
        executor=_execute_javascript,
    )
