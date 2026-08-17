from __future__ import annotations

import base64
import json
import runpy
import zlib
from pathlib import Path

from py_ai_illustrator.cli import main
from py_ai_illustrator.modern import ModernReadLimits, read_modern_ai
from py_ai_illustrator.modern_semantic import parse_modern_private_data

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/fixtures/generated/modern-private-data.ai"
MANIFEST = ROOT / "tests/fixtures/manifests/modern-private-data.json"


def _pdf(*objects: bytes, eof: bool = True) -> bytes:
    output = bytearray(b"%PDF-1.7\n")
    for number, value in enumerate(objects, start=1):
        output.extend(f"{number} 0 obj\n".encode())
        output.extend(value)
        output.extend(b"\nendobj\n")
    if eof:
        output.extend(b"%%EOF\n")
    return bytes(output)


def _stream(payload: bytes, *, filter_name: bytes | None = None) -> bytes:
    filter_entry = b"" if filter_name is None else b" /Filter /" + filter_name
    return (
        b"<< /Length "
        + str(len(payload)).encode()
        + filter_entry
        + b" >>\nstream\n"
        + payload
        + b"\nendstream"
    )


def _single_segment_pdf(payload: bytes, *, filter_name: bytes | None = None) -> bytes:
    return _pdf(
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Page /PieceInfo << /Illustrator 3 0 R >> >>",
        b"<< /Private 4 0 R >>",
        b"<< /AIPrivateData1 5 0 R >>",
        _stream(payload, filter_name=filter_name),
    )


def test_generated_fixture_is_deterministic_and_matches_manifest() -> None:
    generator = runpy.run_path(str(ROOT / "tools/generate_modern_ai_fixture.py"))
    assert generator["build_fixture"]() == FIXTURE.read_bytes()
    manifest = json.loads(MANIFEST.read_text())

    result = read_modern_ai(FIXTURE)

    assert result.container_status == "parsed"
    assert result.private_data_status == "extracted"
    assert result.semantic_status == "partial"
    assert result.source_sha256 == manifest["source_sha256"]
    assert list(result.piece_info_paths) == manifest["piece_info_paths"]
    assert [segment.key for segment in result.segments] == [
        "AIPrivateData1",
        "AIPrivateData2",
    ]
    for segment, expected in zip(result.segments, manifest["segments"], strict=True):
        assert segment.index == expected["index"]
        assert segment.object_ref.label() == expected["object_ref"]
        assert list(segment.filters) == expected["filters"]
        assert len(segment.raw_bytes) == expected["raw_size"]
        assert segment.raw_sha256 == expected["raw_sha256"]
        assert len(segment.decoded_bytes or b"") == expected["decoded_size"]
        assert segment.decoded_sha256 == expected["decoded_sha256"]
        assert [section.name for section in segment.sections] == expected["section_names"]


def test_raw_spans_and_token_spans_are_lossless_and_keep_unknown_bytes() -> None:
    result = read_modern_ai(FIXTURE)
    assert result.source_bytes is not None
    first, second = result.segments

    for segment in result.segments:
        assert result.source_bytes[segment.raw_start : segment.raw_end] == segment.raw_bytes
        assert segment.decoded_bytes is not None
        rebuilt = b"".join(
            segment.decoded_bytes[token.start : token.end] for token in segment.tokens
        )
        assert rebuilt == segment.decoded_bytes
        assert all(
            left.end == right.start
            for left, right in zip(segment.tokens, segment.tokens[1:], strict=False)
        )
    assert b"opaque-operator-kept 99 ZZ" in first.decoded_bytes
    assert b"opaque\x00binary\xffpayload" in second.decoded_bytes
    assert any(token.kind == "opaque" for token in second.tokens)

    assert result.semantic is not None
    zz = next(item for item in result.semantic.unknown_operators if item.name == "ZZ")
    assert first.decoded_bytes[zz.first_start : zz.first_end] == b"ZZ"
    unknown = next(
        item
        for item in result.semantic.unknown_spans
        if item.segment_key == first.key and item.reason == "unknown operator 'ZZ'"
    )
    assert first.decoded_bytes[unknown.start : unknown.end] == b"99 ZZ"


def test_modern_lexer_and_cst_keep_exact_operator_and_operand_spans() -> None:
    data = b"0 J 0 j 1 w 10 M []0 d\r(Exact operand) Ln\ropaque 99 ZZ\r"
    cst = parse_modern_private_data(data, segment_key="fixture")

    assert b"".join(data[item.start : item.end] for item in cst.lexemes) == data
    assert all(
        left.end == right.start
        for left, right in zip(cst.lexemes, cst.lexemes[1:], strict=False)
    )
    width = next(statement for statement in cst.statements if statement.operator_name == "w")
    assert data[width.operator.start : width.operator.end] == b"w"
    assert [data[item.start : item.end] for item in width.operands] == [b"1"]
    layer_name = next(statement for statement in cst.statements if statement.operator_name == "Ln")
    assert data[layer_name.operands[0].start : layer_name.operands[0].end] == b"(Exact operand)"
    assert layer_name.operands[0].value == "Exact operand"
    unknown = next(statement for statement in cst.statements if statement.operator_name == "ZZ")
    assert unknown.supported is False
    assert data[unknown.start : unknown.end] == b"99 ZZ"


def test_real_illustrator_generated_zstd_fixture_matches_profile() -> None:
    manifest = json.loads(MANIFEST.read_text())["real_generated_profile"]
    result = read_modern_ai(ROOT / manifest["fixture"])

    assert result.source_sha256 == manifest["source_sha256"]
    assert result.private_data_status == "extracted"
    assert len(result.segments) == manifest["segment_count"]
    segment = result.segments[0]
    assert segment.filters == (manifest["filter"],)
    assert segment.raw_sha256 == manifest["raw_sha256"]
    assert segment.decoded_sha256 == manifest["decoded_sha256"]
    assert segment.decoded_bytes is not None
    assert segment.decoded_bytes.startswith(b"%!PS-Adobe-3.0")
    assert result.semantic_status == "partial"
    assert result.semantic is not None
    semantic = result.semantic
    assert semantic.document is not None
    assert semantic.coverage.projected_layer_count == 1
    assert semantic.coverage.projected_path_count == 16
    assert semantic.coverage.partial_text_count == 20
    layer = semantic.document.layers[0]
    assert (layer.id, layer.name) == ("Subscription_table", "Subscription table")
    header = layer.paths[0]
    assert header.id == "subscription-comparison.background.header"
    assert [(point.x, point.y) for point in header.points] == [
        (48.0, 262.0),
        (564.0, 262.0),
        (564.0, 300.0),
        (48.0, 300.0),
        (48.0, 262.0),
    ]
    assert header.fill is not None
    assert (header.fill.red, header.fill.green, header.fill.blue) == (
        0.058823529411765,
        0.12156862745098,
        0.231372549019608,
    )
    assert layer.paths[5].stroke_width == 0.9
    first_text = semantic.partial_nodes[0]
    assert first_text.kind == "text"
    assert first_text.id == "subscription-comparison.header.plan"
    assert first_text.known_fields == {"story_index": 19, "text": "Plan"}
    assert first_text.missing_fields == ("x", "y")
    assert {diagnostic.code for diagnostic in result.diagnostics} >= {
        "unknown_modern_operators",
        "partial_modern_text",
    }


def test_ai11_text_document_nesting_limit_becomes_partial_diagnostic() -> None:
    nested = b"/1 " + (b"[" * 80) + (b"]" * 80)
    payload = (
        b"/AI11TextDocument : /ASCII85Decode ,\n%"
        + base64.a85encode(nested)
        + b"~>\n%AI11_EndTextDocument\n"
    )

    result = read_modern_ai(_single_segment_pdf(payload))

    assert result.semantic is not None
    assert result.semantic.status == "partial"
    diagnostic = next(
        item for item in result.semantic.diagnostics if item.code == "modern_text_document_partial"
    )
    assert diagnostic.severity == "warning"
    assert diagnostic.segment == "AIPrivateData1"
    assert diagnostic.decoded_start == 0
    assert diagnostic.decoded_end is not None
    assert "nesting limit exceeded" in diagnostic.message


def test_unpainted_path_is_partial_when_layer_ends() -> None:
    payload = b"%AI5_BeginLayer\n0 0 m\n10 0 L\n%AI5_EndLayer\n"

    result = read_modern_ai(_single_segment_pdf(payload))

    assert result.semantic is not None
    partial = next(item for item in result.semantic.partial_nodes if item.kind == "path")
    assert partial.known_fields == {"point_count": 2}
    assert partial.missing_fields == ("paint_operator",)
    assert partial.end == payload.index(b"%AI5_EndLayer")
    assert "layer ended" in partial.reason.lower()


def test_unpainted_path_is_partial_at_segment_eof() -> None:
    payload = b"%AI5_BeginLayer\n0 0 m\n10 0 L\n"

    result = read_modern_ai(_single_segment_pdf(payload))

    assert result.semantic is not None
    partial = next(item for item in result.semantic.partial_nodes if item.kind == "path")
    assert partial.known_fields == {"point_count": 2}
    assert partial.end == len(payload)
    assert "segment ended" in partial.reason.lower()


def test_each_moveto_abandoned_path_is_retained_with_unique_id() -> None:
    payload = (
        b"%AI5_BeginLayer\n"
        b"0 0 m\n10 0 L\n"
        b"20 20 m\n30 20 L\n"
        b"%AI5_EndLayer\n"
    )

    result = read_modern_ai(_single_segment_pdf(payload))

    assert result.semantic is not None
    partials = [item for item in result.semantic.partial_nodes if item.kind == "path"]
    assert len(partials) == 2
    assert len({item.id for item in partials}) == 2
    assert partials[0].end == payload.index(b"20 20 m")
    assert "new moveto" in partials[0].reason.lower()
    assert "layer ended" in partials[1].reason.lower()


def test_duplicate_projected_node_ids_are_disambiguated_and_order_refs_follow() -> None:
    note_value = base64.b64encode(json.dumps({"id": "duplicate"}).encode())
    note = (
        b"%_(py-ai:"
        + note_value
        + b") /UnicodeString (AdobeNoteAttribute)\n"
    )
    payload = (
        b"%AI5_BeginLayer\n"
        b"0 0 m\n10 0 L\nS\n"
        + note
        + b"20 0 m\n30 0 L\nS\n"
        + note
        + b"%AI5_EndLayer\n"
    )

    result = read_modern_ai(_single_segment_pdf(payload))

    assert result.semantic is not None
    assert result.semantic.status == "partial"
    assert result.semantic.document is not None
    layer = result.semantic.document.layers[0]
    assert [path.id for path in layer.paths] == ["duplicate", "duplicate~2"]
    assert [reference.id for reference in layer.item_order] == ["duplicate", "duplicate~2"]
    all_ids = [layer.id, *(path.id for path in layer.paths)]
    all_ids.extend(item.id for item in result.semantic.partial_nodes)
    assert len(all_ids) == len(set(all_ids))
    assert "modern_duplicate_node_id" in {
        diagnostic.code for diagnostic in result.semantic.diagnostics
    }


def test_ordinary_pdf_is_distinct_from_modern_ai() -> None:
    result = read_modern_ai(_pdf(b"<< /Type /Catalog >>"))

    assert result.container_status == "parsed"
    assert result.private_data_status == "absent"
    assert result.is_pdf_compatible_ai is False
    assert {diagnostic.code for diagnostic in result.diagnostics} == {"ordinary_pdf"}


def test_missing_eof_is_a_malformed_container_diagnostic() -> None:
    result = read_modern_ai(_pdf(b"<< /Type /Catalog >>", eof=False))

    assert result.container_status == "invalid"
    assert result.valid is False
    assert "missing_pdf_eof" in {diagnostic.code for diagnostic in result.diagnostics}


def test_missing_private_data_reference_is_diagnosed() -> None:
    result = read_modern_ai(
        _pdf(
            b"<< /PieceInfo << /Illustrator 2 0 R >> >>",
            b"<< /Private 3 0 R >>",
            b"<< /AIPrivateData1 99 0 R >>",
        )
    )

    assert result.private_data_status == "failed"
    assert "missing_pdf_reference" in {diagnostic.code for diagnostic in result.diagnostics}


def test_circular_piece_info_reference_is_diagnosed() -> None:
    result = read_modern_ai(
        _pdf(
            b"<< /PieceInfo 2 0 R >>",
            b"<< /Illustrator 2 0 R >>",
        )
    )

    assert result.private_data_status == "failed"
    assert "pdf_reference_cycle" in {diagnostic.code for diagnostic in result.diagnostics}


def test_unsupported_filter_is_explicit_and_raw_bytes_remain_available() -> None:
    payload = b"still-preserved"
    result = read_modern_ai(_single_segment_pdf(payload, filter_name=b"LZWDecode"))

    assert result.private_data_status == "partial"
    segment = result.segments[0]
    assert segment.raw_bytes == payload
    assert segment.raw_sha256
    assert segment.decoded_bytes is None
    assert segment.decode_status == "failed"
    assert "unsupported_stream_filter" in {
        diagnostic.code for diagnostic in result.diagnostics
    }


def test_corrupt_flate_stream_is_diagnosed_without_losing_raw_bytes() -> None:
    result = read_modern_ai(_single_segment_pdf(b"not-deflate", filter_name=b"FlateDecode"))

    assert result.private_data_status == "partial"
    assert result.segments[0].raw_bytes == b"not-deflate"
    assert "private_data_decode_failed" in {
        diagnostic.code for diagnostic in result.diagnostics
    }


def test_decode_expansion_and_source_size_limits_are_enforced() -> None:
    compressed = zlib.compress(b"A" * 10_000)
    expanded = read_modern_ai(
        _single_segment_pdf(compressed, filter_name=b"FlateDecode"),
        limits=ModernReadLimits(max_segment_decoded_bytes=512),
    )
    assert "private_data_decode_limit_exceeded" in {
        diagnostic.code for diagnostic in expanded.diagnostics
    }

    oversized = read_modern_ai(
        FIXTURE,
        limits=ModernReadLimits(max_pdf_bytes=100),
    )
    assert oversized.container_status == "limit_exceeded"
    assert oversized.source_bytes is None
    assert "pdf_size_limit_exceeded" in {
        diagnostic.code for diagnostic in oversized.diagnostics
    }


def test_cli_inspect_and_validate_report_three_distinct_support_states(
    capsys,
    tmp_path: Path,
) -> None:
    assert main(["inspect", str(FIXTURE), "--json"]) == 0
    inspected = json.loads(capsys.readouterr().out)
    modern = inspected["modern_ai"]
    assert modern["container"]["status"] == "parsed"
    assert modern["private_data"]["status"] == "extracted"
    assert modern["semantic"]["status"] == "partial"
    assert modern["semantic"]["profile"] == "modern-ai-semantic-read-only-v1"
    assert modern["semantic"]["coverage"]["projected_layer_count"] == 1

    assert main(["validate", str(FIXTURE)]) == 0
    validated = json.loads(capsys.readouterr().out)
    assert validated["valid"] is True
    assert validated["safe_to_reserialize"] is False
    assert validated["classification"] == "read_only_semantic_partial"
    assert validated["modern_ai"]["private_data"]["segment_count"] == 2

    ordinary_pdf = tmp_path / "ordinary.pdf"
    ordinary_pdf.write_bytes(_pdf(b"<< /Type /Catalog >>"))
    assert main(["validate", str(ordinary_pdf)]) == 0
    ordinary = json.loads(capsys.readouterr().out)
    assert ordinary["classification"] == "ordinary_pdf"
    assert ordinary["modern_ai"]["container"]["status"] == "parsed"
    assert ordinary["modern_ai"]["private_data"]["status"] == "absent"
    assert ordinary["modern_ai"]["semantic"]["status"] == "unsupported"
