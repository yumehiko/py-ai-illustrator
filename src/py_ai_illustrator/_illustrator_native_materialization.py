"""Legacy-to-native AI materialization adapter."""

from __future__ import annotations

import platform
import shutil
import subprocess
import tempfile
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ._illustrator_bridge import execute_javascript
from ._illustrator_dom import (
    document_linked_images,
    document_text_frames,
    document_text_frames_dom_order,
)
from ._illustrator_scripts import (
    build_native_materialization_javascript,
    native_fill_spec,
    text_identity_note,
)
from .assets import package_linked_images
from .format import FileFormat, inspect_file
from .legacy import linked_image_placeholder_note, load_ai7


def materialize_native_ai(
    source: str | Path,
    destination: str | Path,
    *,
    timeout: float = 90.0,
    application_name: str = "Adobe Illustrator",
    executor: Callable[..., subprocess.CompletedProcess[str]] = execute_javascript,
) -> dict[str, Any]:
    """Convert a legacy AI copy to a modern AI with native editable text."""

    source_path = Path(source).resolve()
    destination_path = Path(destination).resolve()
    if platform.system() != "Darwin":
        return {
            "status": "environment-unavailable",
            "error": "Native AI materialization is currently supported on macOS only.",
        }
    if not source_path.is_file():
        return {"status": "invalid-input", "error": f"File does not exist: {source_path}"}
    if destination_path.exists():
        return {
            "status": "invalid-input",
            "error": f"Refusing to overwrite existing output: {destination_path}",
        }
    if timeout <= 0:
        return {"status": "invalid-input", "error": "timeout must be positive"}
    if inspect_file(source_path).format is not FileFormat.LEGACY_AI:
        return {
            "status": "invalid-input",
            "error": "Native materialization currently accepts legacy AI input only.",
        }
    source_document = load_ai7(source_path)
    try:
        source_document, packaged_links = package_linked_images(
            source_document, destination_path.parent, source_base=source_path.parent
        )
    except ValueError as error:
        return {"status": "invalid-input", "error": str(error)}
    expected_justifications = Counter(
        f"Justification.{text.alignment.upper()}" for text in document_text_frames(source_document)
    )
    dom_ordered_text = document_text_frames_dom_order(source_document)
    text_notes = tuple(text_identity_note(text) for text in dom_ordered_text)
    text_contents = tuple(text.text for text in dom_ordered_text)
    desired_font_names = tuple(
        text.native_font_name or ("" if "RKSJ-" in text.font_name else text.font_name)
        for text in dom_ordered_text
    )
    desired_font_sizes = tuple(text.font_size for text in dom_ordered_text)
    desired_fills = tuple(native_fill_spec(text.fill) for text in dom_ordered_text)
    desired_trackings = tuple(text.tracking for text in dom_ordered_text)
    desired_rotations = tuple(text.rotation for text in dom_ordered_text)
    desired_alignments = tuple(text.alignment for text in dom_ordered_text)
    desired_area_widths = tuple(text.area_width for text in dom_ordered_text)
    desired_area_heights = tuple(text.area_height for text in dom_ordered_text)
    desired_leadings = tuple(text.leading for text in dom_ordered_text)
    expected_area_text_count = sum(text.is_area_text for text in dom_ordered_text)
    desired_artboards = tuple(
        {
            "name": artboard.name,
            "left": artboard.left,
            "top": artboard.top,
            "width": artboard.width,
            "height": artboard.height,
        }
        for artboard in source_document.artboards
    )
    desired_images = tuple(
        {
            "id": image.id,
            "name": image.name or image.id,
            "path": (destination_path.parent / image.source).resolve(),
            "placeholder_note": linked_image_placeholder_note(image.id),
            "width": image.width,
            "height": image.height,
            "rotation": image.rotation,
        }
        for image in document_linked_images(source_document)
    )
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="py-ai-illustrator-native-") as temp_directory:
        temp_path = Path(temp_directory)
        input_copy = temp_path / "python-generated.ai"
        shutil.copy2(source_path, input_copy)
        try:
            completed = executor(
                build_native_materialization_javascript(
                    input_copy,
                    destination_path,
                    text_notes=text_notes,
                    text_contents=text_contents,
                    desired_font_names=desired_font_names,
                    desired_font_sizes=desired_font_sizes,
                    desired_fills=desired_fills,
                    desired_trackings=desired_trackings,
                    desired_rotations=desired_rotations,
                    desired_alignments=desired_alignments,
                    desired_area_widths=desired_area_widths,
                    desired_area_heights=desired_area_heights,
                    desired_leadings=desired_leadings,
                    desired_artboards=desired_artboards,
                    desired_images=desired_images,
                    source_document_height=source_document.height,
                ),
                temp_path,
                timeout=timeout,
                application_name=application_name,
            )
        except subprocess.TimeoutExpired:
            return {
                "status": "environment-unavailable",
                "error": f"Illustrator did not answer within {timeout:g} seconds.",
            }
    if completed.returncode != 0:
        return {
            "status": "environment-unavailable",
            "error": completed.stderr.strip() or "Illustrator AppleScript failed.",
        }
    response = completed.stdout.strip()
    if not response.startswith("ok:"):
        return {"status": "failed", "illustrator_response": response}
    if not destination_path.is_file():
        return {
            "status": "failed",
            "error": "Illustrator reported success but did not create the native AI file.",
        }
    parts = response.split(":", 23)
    if len(parts) != 24:
        return {"status": "failed", "illustrator_response": response}
    (
        _,
        version,
        legacy_count,
        native_count,
        converted,
        justifications,
        assigned_notes,
        native_notes,
        identity_content_matches,
        requested_fonts,
        assigned_fonts,
        matching_fonts,
        matching_font_sizes,
        matching_fills,
        missing_fonts,
        matching_trackings,
        matching_rotations,
        recreated_area_texts,
        matching_area_texts,
        matching_leadings,
        matching_artboards,
        found_image_placeholders,
        placed_images,
        matching_linked_images,
    ) = parts
    legacy_text_count = int(legacy_count)
    native_text_count = int(native_count)
    native_justifications = justifications.split(",") if justifications else []
    assigned_note_count = int(assigned_notes)
    native_note_count = int(native_notes)
    identity_content_match_count = int(identity_content_matches)
    requested_font_count = int(requested_fonts)
    assigned_font_count = int(assigned_fonts)
    matching_font_count = int(matching_fonts)
    matching_font_size_count = int(matching_font_sizes)
    matching_fill_count = int(matching_fills)
    missing_font_names = missing_fonts.split(",") if missing_fonts else []
    matching_tracking_count = int(matching_trackings)
    matching_rotation_count = int(matching_rotations)
    recreated_area_text_count = int(recreated_area_texts)
    matching_area_text_count = int(matching_area_texts)
    matching_leading_count = int(matching_leadings)
    matching_artboard_count = int(matching_artboards)
    found_image_placeholder_count = int(found_image_placeholders)
    placed_image_count = int(placed_images)
    matching_linked_image_count = int(matching_linked_images)
    checks = {
        "legacy_conversion_succeeded": converted == "true",
        "text_frame_count": native_text_count == legacy_text_count,
        "paragraph_justifications": Counter(native_justifications) == expected_justifications,
        "text_identity_notes": assigned_note_count == legacy_text_count
        and native_note_count == legacy_text_count,
        "text_identity_mapping": identity_content_match_count == legacy_text_count,
        "requested_fonts_available": assigned_font_count == requested_font_count,
        "native_font_names": matching_font_count == requested_font_count,
        "native_font_sizes": matching_font_size_count == legacy_text_count,
        "native_text_fills": matching_fill_count == legacy_text_count,
        "native_tracking": matching_tracking_count == legacy_text_count,
        "native_rotation": matching_rotation_count == legacy_text_count,
        "native_area_text": recreated_area_text_count == expected_area_text_count
        and matching_area_text_count == expected_area_text_count,
        "native_leading": matching_leading_count == legacy_text_count,
        "native_artboards": matching_artboard_count == len(desired_artboards),
        "linked_image_placeholders": found_image_placeholder_count == len(desired_images),
        "linked_images_created": placed_image_count == len(desired_images),
        "linked_image_attributes": matching_linked_image_count == len(desired_images),
    }
    return {
        "status": "passed" if all(checks.values()) else "mismatch",
        "input": str(source_path),
        "output": str(destination_path),
        "illustrator_version": version,
        "legacy_text_count": legacy_text_count,
        "native_text_count": native_text_count,
        "native_text_identity_note_count": native_note_count,
        "text_identity_content_match_count": identity_content_match_count,
        "requested_font_count": requested_font_count,
        "assigned_font_count": assigned_font_count,
        "matching_font_count": matching_font_count,
        "matching_font_size_count": matching_font_size_count,
        "matching_fill_count": matching_fill_count,
        "missing_fonts": missing_font_names,
        "matching_tracking_count": matching_tracking_count,
        "matching_rotation_count": matching_rotation_count,
        "expected_area_text_count": expected_area_text_count,
        "recreated_area_text_count": recreated_area_text_count,
        "matching_area_text_count": matching_area_text_count,
        "matching_leading_count": matching_leading_count,
        "expected_artboard_count": len(desired_artboards),
        "matching_artboard_count": matching_artboard_count,
        "expected_linked_image_count": len(desired_images),
        "found_image_placeholder_count": found_image_placeholder_count,
        "placed_image_count": placed_image_count,
        "matching_linked_image_count": matching_linked_image_count,
        "packaged_links": [link.to_dict() for link in packaged_links],
        "native_justifications": native_justifications,
        "checks": checks,
        "format": inspect_file(destination_path).to_dict(),
    }
