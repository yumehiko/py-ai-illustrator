"""Use inkai as a comparison oracle for this project's modern AI reader.

Run this script only in the isolated evaluation environment documented by the
Decision Gate L ADR. It imports ``inkai`` when present, but inkai is not a
project or development dependency and its graph is not a production backend.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import platform
import time
import warnings
from collections import Counter
from pathlib import Path
from typing import Any

from py_ai_illustrator.legacy import read_ai7
from py_ai_illustrator.modern import read_modern_ai

MARKERS = {
    "unknown_operator": b"opaque-operator-kept 99 ZZ",
    "opaque_binary": b"opaque\x00binary\xffpayload",
}
MAX_GRAPH_OBJECTS = 200_000
MAX_GRAPH_DEPTH = 64


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _exception(error: BaseException) -> dict[str, str]:
    return {"type": type(error).__name__, "message": str(error)}


def _package_version(name: str) -> str | None:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None


def _children(value: Any) -> list[Any]:
    if isinstance(value, dict):
        return list(value.keys()) + list(value.values())
    if isinstance(value, (list, tuple, set, frozenset)):
        return list(value)
    namespace = getattr(value, "__dict__", None)
    return list(namespace.values()) if isinstance(namespace, dict) else []


def _summarize_inkai_graph(root: Any) -> dict[str, Any]:
    from inkai.parser.operators import Operator, UnknownOperator

    classes: Counter[str] = Counter()
    commands: Counter[str] = Counter()
    unknown_commands: Counter[str] = Counter()
    seen: set[int] = set()
    stack: list[tuple[Any, int]] = [(root, 0)]
    truncated = False

    while stack:
        value, depth = stack.pop()
        if value is None or isinstance(value, (str, bytes, int, float, bool)):
            continue
        identity = id(value)
        if identity in seen:
            continue
        seen.add(identity)
        classes[type(value).__name__] += 1
        if isinstance(value, Operator):
            commands[value.command] += 1
        if isinstance(value, UnknownOperator):
            unknown_commands[value.command] += 1
        if len(seen) >= MAX_GRAPH_OBJECTS or depth >= MAX_GRAPH_DEPTH:
            truncated = True
            continue
        stack.extend((child, depth + 1) for child in _children(value))

    return {
        "object_count": len(seen),
        "truncated": truncated,
        "classes": dict(sorted(classes.items())),
        "operator_commands": dict(sorted(commands.items())),
        "unknown_operator_commands": dict(sorted(unknown_commands.items())),
    }


def _evaluate_current_modern_reader(path: Path) -> dict[str, Any]:
    started = time.monotonic()
    result = read_modern_ai(path)
    source = result.source_bytes
    decoded = b"".join(
        segment.decoded_bytes or b"" for segment in result.segments
    )
    segments = []
    for segment in result.segments:
        raw_matches_source = (
            source is not None
            and source[segment.raw_start : segment.raw_end] == segment.raw_bytes
        )
        decoded_from_tokens = None
        if segment.decoded_bytes is not None:
            decoded_from_tokens = b"".join(
                segment.decoded_bytes[token.start : token.end]
                for token in segment.tokens
            )
        segments.append(
            {
                "key": segment.key,
                "filters": list(segment.filters),
                "decode_status": segment.decode_status,
                "raw_size": len(segment.raw_bytes),
                "decoded_size": (
                    len(segment.decoded_bytes)
                    if segment.decoded_bytes is not None
                    else None
                ),
                "raw_sha256": segment.raw_sha256,
                "decoded_sha256": segment.decoded_sha256,
                "raw_span_matches_source": raw_matches_source,
                "tokens_reconstruct_decoded": (
                    decoded_from_tokens == segment.decoded_bytes
                    if decoded_from_tokens is not None
                    else None
                ),
            }
        )
    return {
        "format": "modern_ai",
        "elapsed_seconds": round(time.monotonic() - started, 6),
        "container_status": result.container_status,
        "private_data_status": result.private_data_status,
        "semantic_status": result.semantic_status,
        "source_sha256": result.source_sha256,
        "diagnostic_codes": [item.code for item in result.diagnostics],
        "markers_in_decoded_data": {
            name: marker in decoded for name, marker in MARKERS.items()
        },
        "segments": segments,
    }


def _evaluate_current_legacy_reader(path: Path) -> dict[str, Any]:
    started = time.monotonic()
    result = read_ai7(path)
    document = result.document
    counts: Counter[str] = Counter(
        {
            "artboards": len(document.artboards),
            "layers": len(document.layers),
        }
    )

    def count_container(container: Any) -> None:
        counts["paths"] += len(container.paths)
        counts["text_frames"] += len(container.text_frames)
        counts["linked_images"] += len(container.linked_images)
        counts["compound_paths"] += len(container.compound_paths)
        counts["clipping_groups"] += len(container.clipping_groups)
        counts["groups"] += len(container.groups)
        for compound in container.compound_paths:
            counts["compound_component_paths"] += len(compound.paths)
        for clipping in container.clipping_groups:
            counts["clipping_paths"] += 1 + len(clipping.paths)
        for child in container.groups:
            count_container(child)

    for layer in document.layers:
        count_container(layer)

    source = result.source.to_bytes()
    return {
        "format": "legacy_ai",
        "elapsed_seconds": round(time.monotonic() - started, 6),
        "classification": result.classification,
        "safe_to_reserialize": result.safe_to_reserialize,
        "source_reconstructs_input": source == path.read_bytes(),
        "coverage": {
            "statement_count": result.coverage.statement_count,
            "recognized_statement_count": result.coverage.recognized_statement_count,
            "unsupported_statement_count": result.coverage.unsupported_statement_count,
            "unsupported_resource_count": result.coverage.unsupported_resource_count,
        },
        "diagnostic_codes": [item.code for item in result.diagnostics],
        "ir_counts": dict(sorted(counts.items())),
        "origin_count": len(result.origins),
        "origin_spans_inside_source": all(
            0 <= origin.start < origin.end <= len(source) for origin in result.origins
        ),
    }


def _evaluate_current_reader(path: Path) -> dict[str, Any]:
    if path.read_bytes().startswith(b"%PDF-"):
        return _evaluate_current_modern_reader(path)
    return _evaluate_current_legacy_reader(path)


def _evaluate_inkai(path: Path) -> dict[str, Any]:
    import inkai

    evaluation: dict[str, Any] = {}
    captured: list[str] = []
    extracted: bytes | None = None
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        started = time.monotonic()
        try:
            extracted = inkai.extract(str(path))
            evaluation["extract"] = {
                "status": "success",
                "elapsed_seconds": round(time.monotonic() - started, 6),
                "size": len(extracted),
                "sha256": _sha256(extracted),
                "markers": {
                    name: marker in extracted for name, marker in MARKERS.items()
                },
            }
        except BaseException as error:  # evaluation must report assertions too
            evaluation["extract"] = {
                "status": "error",
                "elapsed_seconds": round(time.monotonic() - started, 6),
                "error": _exception(error),
            }
        captured.extend(str(item.message) for item in caught)

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        started = time.monotonic()
        try:
            document = inkai.parse(str(path))
            evaluation["parse"] = {
                "status": "success",
                "elapsed_seconds": round(time.monotonic() - started, 6),
                "graph": _summarize_inkai_graph(document),
            }
        except BaseException as error:  # evaluation must report assertions too
            evaluation["parse"] = {
                "status": "error",
                "elapsed_seconds": round(time.monotonic() - started, 6),
                "error": _exception(error),
            }
        captured.extend(str(item.message) for item in caught)

    evaluation["warnings"] = captured
    return evaluation


def evaluate(path: Path) -> dict[str, Any]:
    source = path.read_bytes()
    return {
        "fixture": str(path),
        "input_size": len(source),
        "input_sha256": _sha256(source),
        "current_reader": _evaluate_current_reader(path),
        "inkai": _evaluate_inkai(path),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("fixtures", nargs="+", type=Path)
    parser.add_argument("--inkai-revision", required=True)
    args = parser.parse_args()
    report = {
        "schema_version": 1,
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "inkai_revision": args.inkai_revision,
            "packages": {
                name: _package_version(name)
                for name in ("pypdf", "zstandard", "pyparsing", "Pillow", "lxml")
            },
        },
        "fixtures": [evaluate(path) for path in args.fixtures],
    }
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
