"""Encoding and metadata codecs shared by the legacy reader, writer, and patcher."""

from __future__ import annotations

import base64
import hashlib
import json
import math
import re

from .model import Artboard, CmykColor, LinkedImage, Path, ProcessColor

_POSTSCRIPT_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
_PATH_NOTE_PREFIX = "py-ai:"


class UnsupportedLegacyFeature(ValueError):
    """Raised when data falls outside the Phase 0 legacy subset."""


def _number(value: float) -> str:
    if abs(value) < 0.0000005:
        return "0"
    return f"{value:.6f}".rstrip("0").rstrip(".") or "0"


def _escape_postscript_string(value: str) -> str:
    return value.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _unescape_postscript_string(value: str) -> str:
    return value.replace("\\)", ")").replace("\\(", "(").replace("\\\\", "\\")


def _text_encoding(font_name: str) -> str:
    return "cp932" if "RKSJ-" in font_name else "ascii"


def _escape_postscript_text(value: str, *, font_name: str) -> str:
    normalized = value.replace("\r\n", "\r").replace("\n", "\r")
    encoding = _text_encoding(font_name)
    try:
        encoded = normalized.encode(encoding)
    except UnicodeEncodeError as error:
        raise UnsupportedLegacyFeature(
            f"Text cannot be encoded for AI7 font {font_name!r}; "
            "use an RKSJ-H/RKSJ-V font for Japanese text"
        ) from error

    output: list[str] = []
    for byte in encoded:
        if byte in {ord("("), ord(")"), ord("\\")}:
            output.append("\\" + chr(byte))
        elif 32 <= byte <= 126:
            output.append(chr(byte))
        else:
            output.append(f"\\{byte:03o}")
    return "".join(output)


def _unescape_postscript_bytes(value: str) -> bytes:
    output = bytearray()
    index = 0
    simple_escapes = {
        "n": b"\n",
        "r": b"\r",
        "t": b"\t",
        "b": b"\b",
        "f": b"\f",
        "(": b"(",
        ")": b")",
        "\\": b"\\",
    }
    while index < len(value):
        character = value[index]
        if character != "\\":
            output.extend(character.encode("latin-1"))
            index += 1
            continue
        index += 1
        if index >= len(value):
            break
        escaped = value[index]
        if escaped in simple_escapes:
            output.extend(simple_escapes[escaped])
            index += 1
            continue
        if escaped in "01234567":
            octal = escaped
            index += 1
            while index < len(value) and len(octal) < 3 and value[index] in "01234567":
                octal += value[index]
                index += 1
            output.append(int(octal, 8))
            continue
        output.extend(escaped.encode("latin-1"))
        index += 1
    return bytes(output)


def _unescape_postscript_text(value: str, *, font_name: str) -> str:
    raw = _unescape_postscript_bytes(value)
    encoding = "cp932" if _text_encoding(font_name) == "cp932" else "latin-1"
    try:
        decoded = raw.decode(encoding)
    except UnicodeDecodeError:
        decoded = raw.decode("latin-1")
    return decoded.replace("\r\n", "\n").replace("\r", "\n")


def _decode_base64_json_object(encoded: str) -> dict[str, object] | None:
    try:
        value = json.loads(base64.b64decode(encoded, validate=True).decode("utf-8"))
    except (ValueError, UnicodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _structured_resource_supported(line: str) -> bool | None:
    """Return whether a semantics-bearing recognized resource is modeled."""

    string_prefixes = (
        "%%py-ai-layer-id: ",
        "%%py-ai-path-name: ",
        "%%py-ai-compound-id: ",
        "%%py-ai-compound-name: ",
        "%%py-ai-clipping-id: ",
        "%%py-ai-clipping-name: ",
        "%%py-ai-group-id: ",
        "%%py-ai-group-name: ",
        "%%py-ai-text-id: ",
        "%%py-ai-text-name: ",
    )
    utf8_prefixes = (
        "%%py-ai-path-id-utf8: ",
        "%%py-ai-path-name-utf8: ",
        "%%py-ai-group-id-utf8: ",
        "%%py-ai-group-name-utf8: ",
        "%%py-ai-text-id-utf8: ",
        "%%py-ai-text-name-utf8: ",
    )
    if line.startswith("%%Title:"):
        return line.startswith("%%Title: (") and line.endswith(")")
    if line.startswith("%AI5_FileFormat"):
        return line == "%AI5_FileFormat 3.0"
    if line.startswith("%AI3_BeginEncoding:"):
        match = re.fullmatch(r"%AI3_BeginEncoding: (\S+) (\S+)", line)
        return (
            match is not None
            and "RKSJ-" in match.group(1)
            and match.group(1).removeprefix("_") == match.group(2)
        )
    if line.startswith("%AI3_EndEncoding"):
        return line == "%AI3_EndEncoding AdobeType"
    if line.startswith("%%py-ai-metadata: "):
        return _decode_base64_json_object(line.removeprefix("%%py-ai-metadata: ")) is not None
    if line.startswith("%%py-ai-artboard: "):
        payload = _decode_base64_json_object(line.removeprefix("%%py-ai-artboard: "))
        if payload is None:
            return False
        try:
            Artboard.from_dict(payload)
        except (KeyError, TypeError, ValueError):
            return False
        return True
    if line.startswith("%%py-ai-linked-image: "):
        payload = _decode_base64_json_object(line.removeprefix("%%py-ai-linked-image: "))
        if payload is None:
            return False
        try:
            LinkedImage.from_dict(payload)
        except (KeyError, TypeError, ValueError):
            return False
        return True
    for prefix in string_prefixes:
        if line.startswith(prefix):
            return line.startswith(prefix + "(") and line.endswith(")")
    for prefix in utf8_prefixes:
        if line.startswith(prefix):
            try:
                base64.b64decode(line.removeprefix(prefix), validate=True).decode("utf-8")
            except (ValueError, UnicodeError):
                return False
            return True
    if line.startswith("%%py-ai-text-alignment: "):
        return re.fullmatch(r"%%py-ai-text-alignment: \((left|center|right)\)", line) is not None
    if line.startswith("%%py-ai-text-native-font: "):
        match = re.fullmatch(r"%%py-ai-text-native-font: \(([^()]*)\)", line)
        return match is not None and _POSTSCRIPT_NAME_RE.fullmatch(match.group(1)) is not None
    if line.startswith(("%%py-ai-text-tracking: ", "%%py-ai-text-rotation: ")):
        try:
            value = float(line.split(": ", 1)[1])
        except ValueError:
            return False
        return math.isfinite(value)
    if line.startswith("%%py-ai-text-area: "):
        try:
            width, height = (float(value) for value in line.split(": ", 1)[1].split())
        except (TypeError, ValueError):
            return False
        return math.isfinite(width) and math.isfinite(height) and width > 0 and height > 0
    if line.startswith("%%py-ai-text-leading: "):
        try:
            leading = float(line.removeprefix("%%py-ai-text-leading: "))
        except ValueError:
            return False
        return math.isfinite(leading) and leading > 0
    if line.startswith("%AI7_Tag:"):
        return line.startswith("%AI7_Tag: (") and line.endswith(")")
    if line.startswith("%AI3_Note:"):
        note = line.removeprefix("%AI3_Note:").lstrip()
        note_id, _ = _parse_path_note(note)
        placeholder = re.fullmatch(r"py-ai-image-placeholder:[0-9a-f]{64}", note)
        return note_id is not None or placeholder is not None
    return None


def _structured_resource_node_types(line: str) -> frozenset[str] | None:
    if line.startswith("%%py-ai-artboard: "):
        return frozenset({"artboard"})
    if line.startswith("%%py-ai-layer-id: "):
        return frozenset({"layer"})
    if line.startswith(("%%py-ai-path-", "%AI7_Tag:")):
        return frozenset({"path", "linked_image"})
    if line.startswith("%AI3_Note:"):
        note = line.removeprefix("%AI3_Note:").lstrip()
        if note.startswith("py-ai-image-placeholder:"):
            return frozenset({"linked_image"})
        return frozenset({"path"})
    if line.startswith("%%py-ai-compound-"):
        return frozenset({"compound_path"})
    if line.startswith("%%py-ai-clipping-"):
        return frozenset({"clipping_group"})
    if line.startswith("%%py-ai-group-"):
        return frozenset({"group"})
    if line.startswith("%%py-ai-text-"):
        return frozenset({"text"})
    if line.startswith("%%py-ai-linked-image: "):
        return frozenset({"linked_image"})
    return None


def _path_note(path: Path) -> str | None:
    payload = {"id": path.id}
    if path.name is not None:
        payload["name"] = path.name
    encoded = base64.b64encode(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).decode("ascii")
    note = _PATH_NOTE_PREFIX + encoded
    return note if len(note) <= 254 else None


def _parse_path_note(note: str) -> tuple[str | None, str | None]:
    if not note.startswith(_PATH_NOTE_PREFIX):
        return None, None
    try:
        decoded = base64.b64decode(note.removeprefix(_PATH_NOTE_PREFIX), validate=True).decode(
            "utf-8"
        )
        payload = json.loads(decoded)
    except (ValueError, UnicodeError, json.JSONDecodeError):
        return None, None
    if not isinstance(payload, dict):
        return None, None
    path_id = payload.get("id")
    path_name = payload.get("name")
    return (
        path_id if isinstance(path_id, str) and path_id else None,
        path_name if isinstance(path_name, str) else None,
    )


def _artboard_comment(artboard: Artboard) -> str:
    payload = {
        "id": artboard.id,
        "name": artboard.name,
        "left": artboard.left,
        "top": artboard.top,
        "width": artboard.width,
        "height": artboard.height,
    }
    encoded = base64.b64encode(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
    ).decode("ascii")
    return "%%py-ai-artboard: " + encoded


def _linked_image_comment(image: LinkedImage) -> str:
    payload = {
        "id": image.id,
        "source": image.source,
        "x": image.x,
        "y": image.y,
        "width": image.width,
        "height": image.height,
        "rotation": image.rotation,
        "name": image.name,
    }
    encoded = base64.b64encode(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
    ).decode("ascii")
    return "%%py-ai-linked-image: " + encoded


def linked_image_placeholder_note(image_id: str) -> str:
    """Return the native note used to locate an image's legacy placeholder."""

    digest = hashlib.sha256(image_id.encode("utf-8")).hexdigest()
    return "py-ai-image-placeholder:" + digest


def _color_operator(color: ProcessColor, *, stroke: bool) -> str:
    if isinstance(color, CmykColor):
        operator = "K" if stroke else "k"
        values = (color.cyan, color.magenta, color.yellow, color.black)
    else:
        operator = "XA" if stroke else "Xa"
        values = (color.red, color.green, color.blue)
    return " ".join([*(_number(value) for value in values), operator])
