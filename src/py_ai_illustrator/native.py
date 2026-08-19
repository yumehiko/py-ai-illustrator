"""Compile the project-owned graphic IR directly through Illustrator's DOM."""

from __future__ import annotations

import json
import os
import platform
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from ._illustrator_bridge import execute_javascript as _execute_javascript
from .assets import PackagedLink, package_linked_images
from .format import FileFormat, inspect_file
from .model import (
    ClippingGroup,
    CmykColor,
    CompoundPath,
    Document,
    Group,
    Layer,
    LinkedImage,
    ProcessColor,
    TextFrame,
)
from .model import Path as AIPath
from .native_bridge import (
    NativeCompileRequest,
    NativeContractError,
    NativeRuntimeBridge,
    load_native_runtime_source,
    parse_native_compile_result,
)


@dataclass(frozen=True, slots=True)
class NativeCompileProfile:
    """Explicit settings needed when creating a new Illustrator document."""

    color_space: Literal["rgb", "cmyk"] = "rgb"
    pdf_compatible: bool = True
    embed_linked_files: bool = False

    def __post_init__(self) -> None:
        if self.color_space not in {"rgb", "cmyk"}:
            raise ValueError("color_space must be 'rgb' or 'cmyk'")
        if not self.pdf_compatible:
            raise ValueError("Direct native compile requires a PDF-compatible AI output")
        if self.embed_linked_files:
            raise ValueError("Direct native compile currently preserves images as external links")


def _identity_note(kind: str, item_id: str, name: str | None) -> str:
    payload = json.dumps(
        {"id": item_id, "name": name},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return f"py-ai-{kind}:{payload}"


def _color_spec(color: ProcessColor | None) -> dict[str, object] | None:
    if color is None:
        return None
    if isinstance(color, CmykColor):
        return {
            "type": "cmyk",
            "values": [color.cyan, color.magenta, color.yellow, color.black],
        }
    return {"type": "rgb", "values": [color.red, color.green, color.blue]}


def _path_spec(path: AIPath) -> dict[str, object]:
    return {
        "kind": "path",
        "id": path.id,
        "name": path.name or path.id,
        "note": _identity_note("path", path.id, path.name),
        "points": [
            {
                "anchor": [point.x, point.y],
                "left": (
                    [point.in_handle.x, point.in_handle.y]
                    if point.in_handle is not None
                    else [point.x, point.y]
                ),
                "right": (
                    [point.out_handle.x, point.out_handle.y]
                    if point.out_handle is not None
                    else [point.x, point.y]
                ),
                "smooth": point.smooth,
            }
            for point in path.points
        ],
        "closed": path.closed,
        "fill": _color_spec(path.fill),
        "stroke": _color_spec(path.stroke),
        "stroke_width": path.stroke_width,
        "dash_pattern": path.dash_pattern,
        "dash_offset": path.dash_offset,
        "line_cap": path.line_cap,
        "line_join": path.line_join,
        "miter_limit": path.miter_limit,
        "polarity": path.polarity,
    }


def _text_spec(text: TextFrame) -> dict[str, object]:
    font_name = text.native_font_name or text.font_name
    if "RKSJ-" in font_name:
        raise ValueError(
            f"Text {text.id!r} requires a native PostScript font name for direct compile"
        )
    return {
        "kind": "text",
        "id": text.id,
        "name": text.name or text.id,
        "note": _identity_note("text", text.id, text.name),
        "contents": text.text,
        "x": text.x,
        "y": text.y,
        "font_name": font_name,
        "font_size": text.font_size,
        "tracking": text.tracking,
        "rotation": text.rotation,
        "area_width": text.area_width,
        "area_height": text.area_height,
        "leading": text.leading,
        "fill": _color_spec(text.fill),
        "alignment": text.alignment,
    }


def _image_spec(image: LinkedImage, destination_directory: Path) -> dict[str, object]:
    linked_file = (destination_directory / image.source).resolve()
    return {
        "kind": "image",
        "id": image.id,
        "name": image.name or image.id,
        "note": _identity_note("image", image.id, image.name),
        "file": str(linked_file),
        "x": image.x,
        "y": image.y,
        "width": image.width,
        "height": image.height,
        "rotation": image.rotation,
    }


def _compound_spec(compound: CompoundPath) -> dict[str, object]:
    return {
        "kind": "compound_path",
        "id": compound.id,
        "name": compound.name or compound.id,
        "note": _identity_note("compound-path", compound.id, compound.name),
        "paths": [_path_spec(path) for path in compound.paths],
    }


def _clipping_spec(clipping: ClippingGroup) -> dict[str, object]:
    return {
        "kind": "clipping_group",
        "id": clipping.id,
        "name": clipping.name or clipping.id,
        "note": _identity_note("clipping-group", clipping.id, clipping.name),
        "clipping_path": _path_spec(clipping.clipping_path),
        "paths": [_path_spec(path) for path in clipping.paths],
    }


def _group_spec(group: Group, destination_directory: Path) -> dict[str, object]:
    return {
        "kind": "group",
        "id": group.id,
        "name": group.name or group.id,
        "note": _identity_note("group", group.id, group.name),
        "items": _ordered_item_specs(group, destination_directory),
    }


def _ordered_item_specs(
    container: Layer | Group,
    destination_directory: Path,
) -> list[dict[str, object]]:
    specs: list[dict[str, object]] = []
    for item in container.ordered_items():
        if isinstance(item, AIPath):
            specs.append(_path_spec(item))
        elif isinstance(item, TextFrame):
            specs.append(_text_spec(item))
        elif isinstance(item, LinkedImage):
            specs.append(_image_spec(item, destination_directory))
        elif isinstance(item, CompoundPath):
            specs.append(_compound_spec(item))
        elif isinstance(item, ClippingGroup):
            specs.append(_clipping_spec(item))
        elif isinstance(item, Group):
            specs.append(_group_spec(item, destination_directory))
        else:  # pragma: no cover - model union makes this defensive
            raise TypeError(f"Unsupported native item type: {type(item).__name__}")
    return specs


def _document_spec(
    document: Document,
    destination_directory: Path,
    profile: NativeCompileProfile,
) -> dict[str, object]:
    artboards = [
        {
            "id": artboard.id,
            "name": artboard.name,
            "rect": [
                artboard.left,
                artboard.top,
                artboard.left + artboard.width,
                artboard.top - artboard.height,
            ],
        }
        for artboard in document.artboards
    ]
    if not artboards:
        artboards = [
            {
                "id": "artboard-1",
                "name": "Artboard 1",
                "rect": [0.0, document.height, document.width, 0.0],
            }
        ]
    return {
        "title": document.title,
        "width": document.width,
        "height": document.height,
        "color_space": profile.color_space,
        "pdf_compatible": profile.pdf_compatible,
        "embed_linked_files": profile.embed_linked_files,
        "artboards": artboards,
        "layers": [
            {
                "id": layer.id,
                "name": layer.name,
                "visible": layer.visible,
                "locked": layer.locked,
                "items": _ordered_item_specs(layer, destination_directory),
            }
            for layer in document.layers
        ],
    }


def _walk_items(
    container: Layer | Group,
) -> list[AIPath | TextFrame | LinkedImage | CompoundPath | ClippingGroup | Group]:
    result: list[
        AIPath | TextFrame | LinkedImage | CompoundPath | ClippingGroup | Group
    ] = []
    for item in container.ordered_items():
        if isinstance(item, Group):
            result.append(item)
            result.extend(_walk_items(item))
        elif isinstance(item, CompoundPath):
            result.append(item)
            result.extend(item.paths)
        elif isinstance(item, ClippingGroup):
            result.append(item)
            result.append(item.clipping_path)
            result.extend(item.paths)
        else:
            result.append(item)
    return result


def _validate_document(document: Document) -> None:
    if not document.layers:
        raise ValueError("Native compile requires at least one layer")
    if not all(layer.id and layer.name for layer in document.layers):
        raise ValueError("Native compile requires non-empty layer ids and names")

    identities: dict[str, str] = {}
    for artboard in document.artboards:
        identities[artboard.id] = "Artboard"
    for layer in document.layers:
        if layer.id in identities:
            raise ValueError(f"Duplicate stable id {layer.id!r} in document containers")
        identities[layer.id] = "Layer"
        if layer.unknown:
            raise ValueError(f"Layer {layer.id!r} contains unsupported unknown data")
        for item in _walk_items(layer):
            item_id = item.id
            if not item_id:
                raise ValueError(f"{type(item).__name__} has an empty stable id")
            if item_id in identities:
                raise ValueError(
                    f"Duplicate stable id {item_id!r} in {identities[item_id]} and "
                    f"{type(item).__name__}"
                )
            identities[item_id] = type(item).__name__
            if item.unknown:
                raise ValueError(
                    f"{type(item).__name__} {item_id!r} contains unsupported unknown data"
                )
            if isinstance(item, TextFrame):
                _text_spec(item)


def _build_direct_native_javascript(
    spec: dict[str, object],
    destination: Path,
) -> str:
    """Return the independent JSX runtime.

    The arguments remain accepted for compatibility with the previous private
    helper. They are now sent through NativeCompileRequest by the bridge instead
    of being embedded in the JavaScript source.
    """

    del spec, destination
    return load_native_runtime_source()





def _load_document(source: Document | str | Path) -> tuple[Document, Path | None]:
    if isinstance(source, Document):
        return source, None
    path = Path(source).resolve()
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Document IR JSON must contain an object")
    return Document.from_dict(data), path.parent


def _result_with_links(
    result: dict[str, Any],
    packaged_links: list[PackagedLink],
) -> dict[str, Any]:
    result["packaged_links"] = [link.to_dict() for link in packaged_links]
    return result


def compile_native_ai(
    source: Document | str | Path,
    destination: str | Path,
    *,
    source_base: str | Path | None = None,
    profile: NativeCompileProfile | None = None,
    timeout: float = 120.0,
    application_name: str = "Adobe Illustrator",
) -> dict[str, Any]:
    """Compile a ``Document`` IR directly to a verified native Illustrator file."""

    destination_path = Path(destination).resolve()
    if platform.system() != "Darwin":
        return {
            "status": "environment-unavailable",
            "error": "Direct native compile is currently supported on macOS only.",
        }
    if destination_path.exists():
        return {
            "status": "invalid-input",
            "error": f"Refusing to overwrite existing output: {destination_path}",
        }
    if timeout <= 0:
        return {"status": "invalid-input", "error": "timeout must be positive"}

    compile_profile = profile or NativeCompileProfile()
    if destination_path.suffix.lower() != ".ai":
        return {"status": "invalid-input", "error": "Native output must use the .ai suffix"}
    try:
        document, inferred_source_base = _load_document(source)
        _validate_document(document)
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as error:
        return {"status": "invalid-input", "error": str(error)}
    effective_source_base = source_base if source_base is not None else inferred_source_base

    destination_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        packaged_document, packaged_links = package_linked_images(
            document,
            destination_path.parent,
            source_base=effective_source_base,
        )
    except ValueError as error:
        return {"status": "invalid-input", "error": str(error)}

    spec = _document_spec(packaged_document, destination_path.parent, compile_profile)
    with tempfile.TemporaryDirectory(
        prefix="py-ai-direct-native-",
        dir=destination_path.parent,
    ) as temp_directory:
        temp_path = Path(temp_directory)
        temporary_output = temp_path / destination_path.name
        request = NativeCompileRequest(
            document=spec,
            destination=str(temporary_output),
        )
        runtime_source = _build_direct_native_javascript(spec, temporary_output)
        try:
            completed = NativeRuntimeBridge().execute(
                request,
                temp_path,
                timeout=timeout,
                application_name=application_name,
                runtime_source=runtime_source,
                script_executor=_execute_javascript,
            )
        except subprocess.TimeoutExpired:
            return _result_with_links(
                {
                    "status": "environment-unavailable",
                    "error": f"Illustrator did not answer within {timeout:g} seconds.",
                },
                packaged_links,
            )
        except NativeContractError as error:
            return _result_with_links(
                {"status": "invalid-input", "error": str(error)},
                packaged_links,
            )
        except OSError as error:
            return _result_with_links(
                {
                    "status": "environment-unavailable",
                    "error": f"Could not prepare or execute the Illustrator runtime: {error}",
                },
                packaged_links,
            )

        if completed.returncode != 0:
            return _result_with_links(
                {
                    "status": "environment-unavailable",
                    "error": completed.stderr.strip() or "Illustrator AppleScript failed.",
                },
                packaged_links,
            )
        try:
            illustrator_result = parse_native_compile_result(completed.stdout)
        except NativeContractError as error:
            return _result_with_links(
                {
                    "status": "failed",
                    "error": str(error),
                    "illustrator_response": completed.stdout.strip(),
                },
                packaged_links,
            )
        if not illustrator_result.get("ok"):
            return _result_with_links(
                {
                    "status": (
                        "mismatch"
                        if isinstance(illustrator_result.get("checks"), dict)
                        else "failed"
                    ),
                    "illustrator": illustrator_result,
                },
                packaged_links,
            )
        if not temporary_output.is_file():
            return _result_with_links(
                {
                    "status": "failed",
                    "error": "Illustrator reported success but did not create the native AI file.",
                    "illustrator": illustrator_result,
                },
                packaged_links,
            )
        format_report = inspect_file(temporary_output)
        if format_report.format is not FileFormat.PDF_COMPATIBLE_AI:
            return _result_with_links(
                {
                    "status": "mismatch",
                    "error": "Direct compile output is not a PDF-compatible Illustrator file.",
                    "format": format_report.to_dict(),
                    "illustrator": illustrator_result,
                },
                packaged_links,
            )
        try:
            os.link(temporary_output, destination_path)
        except FileExistsError:
            return _result_with_links(
                {
                    "status": "invalid-input",
                    "error": f"Refusing to overwrite existing output: {destination_path}",
                },
                packaged_links,
            )
        temporary_output.unlink()

    return _result_with_links(
        {
            "status": "passed",
            "output": str(destination_path),
            "profile": {
                "color_space": compile_profile.color_space,
                "pdf_compatible": compile_profile.pdf_compatible,
                "embed_linked_files": compile_profile.embed_linked_files,
            },
            "illustrator": illustrator_result,
            "format": inspect_file(destination_path).to_dict(),
        },
        packaged_links,
    )
