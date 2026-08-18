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
    manifest = json.loads(MANIFEST.read_text())["real_generated_profiles"]["styled_table"]
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
    assert (
        header.fill.cyan,
        header.fill.magenta,
        header.fill.yellow,
        header.fill.black,
    ) == (
        0.988311588764191,
        0.941435873508453,
        0.599298059940338,
        0.422369718551636,
    )
    assert layer.paths[5].stroke_width == 0.9
    fill_evidence = header.unknown["modern_style_spans"]["fill"]
    assert fill_evidence["alternate_rgb"] == [
        0.058823529411765,
        0.12156862745098,
        0.231372549019608,
    ]
    assert segment.decoded_bytes[fill_evidence["start"] : fill_evidence["end"]].endswith(
        b"Xa"
    )
    first_text = semantic.partial_nodes[0]
    assert first_text.kind == "text"
    assert first_text.id == "subscription-comparison.header.plan"
    assert first_text.known_fields == {"story_index": 19, "text": "Plan"}
    assert first_text.missing_fields == (
        "coordinate_space",
        "x",
        "y",
        "font_size",
        "font_name",
        "fill",
    )
    assert (first_text.parent_kind, first_text.parent_id, first_text.item_index) == (
        "layer",
        "Subscription_table",
        16,
    )
    assert {diagnostic.code for diagnostic in result.diagnostics} >= {
        "unknown_modern_operators",
        "partial_modern_text",
    }


def test_all_real_v2_fixtures_match_hash_and_semantic_manifest() -> None:
    profiles = json.loads(MANIFEST.read_text())["real_generated_profiles"]
    for profile in profiles.values():
        result = read_modern_ai(ROOT / profile["fixture"])
        assert result.source_sha256 == profile["source_sha256"]
        assert len(result.source_bytes or b"") == profile["source_size"]
        assert len(result.segments) == profile["segment_count"]
        segment = result.segments[0]
        assert segment.filters == (profile["filter"],)
        assert len(segment.raw_bytes) == profile["raw_size"]
        assert segment.raw_sha256 == profile["raw_sha256"]
        assert len(segment.decoded_bytes or b"") == profile["decoded_size"]
        assert segment.decoded_sha256 == profile["decoded_sha256"]
        assert result.semantic is not None
        coverage = result.semantic.coverage
        expected = profile["semantic"]
        assert coverage.projected_layer_count == expected["layers"]
        assert coverage.projected_path_count == expected["paths"]
        assert coverage.projected_group_count == expected["groups"]
        assert coverage.projected_compound_path_count == expected["compound_paths"]
        assert coverage.projected_clipping_group_count == expected["clipping_groups"]
        assert coverage.projected_text_count == expected["text_frames"]
        assert coverage.partial_text_count == expected["partial_text"]


def test_real_cmyk_curve_has_exact_color_and_bezier_source_evidence() -> None:
    profile = json.loads(MANIFEST.read_text())["real_generated_profiles"]["cmyk_curve"]
    result = read_modern_ai(ROOT / profile["fixture"])
    assert result.semantic is not None and result.semantic.document is not None
    segment = result.segments[0]
    assert segment.decoded_bytes is not None
    path = result.semantic.document.layers[0].paths[0]

    assert path.id == "cmyk-curve"
    assert path.closed is False
    assert (path.points[0].x, path.points[0].y) == (20.0, 20.0)
    assert path.points[0].out_handle is not None
    assert (path.points[0].out_handle.x, path.points[0].out_handle.y) == (20.0, 150.0)
    assert path.points[1].in_handle is not None
    assert (path.points[1].in_handle.x, path.points[1].in_handle.y) == (180.0, 50.0)
    assert path.stroke is not None
    assert (
        path.stroke.cyan,
        path.stroke.magenta,
        path.stroke.yellow,
        path.stroke.black,
    ) == (1.0, 0.25, 0.0, 0.1)
    stroke_span = path.unknown["modern_style_spans"]["stroke"]
    assert segment.decoded_bytes[stroke_span["start"] : stroke_span["end"]] == b"1 0.25 0 0.1 K"


def test_real_compound_and_clipping_structure_preserves_order_and_exact_spans() -> None:
    profile = json.loads(MANIFEST.read_text())["real_generated_profiles"]["mixed_stack"]
    first = read_modern_ai(ROOT / profile["fixture"])
    second = read_modern_ai(ROOT / profile["fixture"])
    assert first.to_dict() == second.to_dict()
    assert first.semantic is not None and first.semantic.document is not None
    segment = first.segments[0]
    assert segment.decoded_bytes is not None
    layer = first.semantic.document.layers[0]

    assert [reference.kind for reference in layer.item_order] == [
        "clipping_group",
        "path",
        "compound_path",
    ]
    compound = layer.compound_paths[0]
    assert [(path.id, path.polarity) for path in compound.paths] == [
        ("frame-outer", "positive"),
        ("frame-inner", "negative"),
    ]
    clipping = layer.clipping_groups[0]
    assert clipping.clipping_path.id == "clip-mask"
    assert clipping.clipping_path.closed is True
    assert [path.id for path in clipping.paths] == ["clip-content"]
    clip_span = clipping.unknown["modern_source"]["span"]
    assert segment.decoded_bytes[clip_span["start"] : clip_span["end"]].startswith(b"q")
    assert segment.decoded_bytes[clip_span["start"] : clip_span["end"]].endswith(b"Q")
    mask_span = clipping.clipping_path.unknown["modern_source"]["span"]
    mask_source = segment.decoded_bytes[mask_span["start"] : mask_span["end"]]
    assert b"h\rW\rn" in mask_source
    compound_span = compound.unknown["modern_source"]["span"]
    compound_source = segment.decoded_bytes[compound_span["start"] : compound_span["end"]]
    assert compound_source.startswith(b"*u")
    assert compound_source.endswith(b"*U")


def test_real_banner_groups_and_partial_text_are_deterministically_located() -> None:
    profile = json.loads(MANIFEST.read_text())["real_generated_profiles"]["campaign_banner"]
    result = read_modern_ai(ROOT / profile["fixture"])
    assert result.semantic is not None and result.semantic.document is not None
    layer = result.semantic.document.layers[0]

    assert [reference.kind for reference in layer.item_order] == ["group", "group", "group"]
    assert [[reference.kind for reference in group.item_order] for group in layer.groups] == [
        ["path", "path", "path"],
        ["path", "path", "path"],
        ["path", "path", "path"],
    ]
    first_text = result.semantic.partial_nodes[0]
    assert first_text.known_fields["text"] == "DESIGN SYSTEMS / WORKSHOP"
    assert (first_text.parent_kind, first_text.parent_id, first_text.item_index) == (
        "group",
        layer.groups[0].id,
        2,
    )
    assert "identity_note_span" in first_text.evidence
    assert "text_document_span" in first_text.evidence
    segment = result.segments[0]
    assert segment.decoded_bytes is not None
    group_span = layer.groups[0].unknown["modern_source"]["span"]
    assert segment.decoded_bytes[group_span["start"] : group_span["end"]].startswith(b"u")
    assert segment.decoded_bytes[group_span["start"] : group_span["end"]].endswith(b"U")


def test_real_nested_groups_are_counted_recursively() -> None:
    profile = json.loads(MANIFEST.read_text())["real_generated_profiles"][
        "nested_packaging_groups"
    ]
    result = read_modern_ai(ROOT / profile["fixture"])
    assert result.semantic is not None and result.semantic.document is not None
    layer = result.semantic.document.layers[0]

    assert len(layer.groups) == 3
    assert [len(group.groups) for group in layer.groups] == [1, 0, 1]
    assert result.semantic.coverage.projected_group_count == 5
    assert result.semantic.coverage.projected_path_count == 14
    assert layer.groups[0].groups[0].paths[0].id == "label-1.badge.background"


def test_partial_parent_id_follows_a_source_renamed_group() -> None:
    note_value = base64.b64encode(json.dumps({"id": "renamed-group"}).encode())
    payload = (
        b"%AI5_BeginLayer\n"
        b"u\n"
        b"0 0 m\n10 0 L\n"
        b"U\n"
        b"%_(py-ai:"
        + note_value
        + b") /UnicodeString (AdobeNoteAttribute)\n"
        b"u\n"
        b"20 0 m\n30 0 L\n"
        b"U\n"
        b"%_(py-ai:"
        + note_value
        + b") /UnicodeString (AdobeNoteAttribute)\n"
        b"%AI5_EndLayer\n"
    )

    result = read_modern_ai(_single_segment_pdf(payload))

    assert result.semantic is not None and result.semantic.document is not None
    groups = result.semantic.document.layers[0].groups
    partials = [item for item in result.semantic.partial_nodes if item.kind == "path"]
    assert [group.id for group in groups] == ["renamed-group", "renamed-group~2"]
    assert [partial.parent_id for partial in partials] == [group.id for group in groups]


def test_q_and_Q_restore_paint_state_after_a_clipping_group() -> None:
    payload = (
        b"%AI5_BeginLayer\n"
        b"1 0 0 Xa\n"
        b"q\n"
        b"0 1 0 Xa\n"
        b"0 0 m\n10 0 L\n10 10 L\nf\n"
        b"0 0 m\n10 0 L\n10 10 L\nh\nW\nn\n"
        b"Q\n"
        b"20 20 m\n30 20 L\n30 30 L\nf\n"
        b"%AI5_EndLayer\n"
    )

    result = read_modern_ai(_single_segment_pdf(payload))

    assert result.semantic is not None and result.semantic.document is not None
    layer = result.semantic.document.layers[0]
    clipped = layer.clipping_groups[0].paths[0]
    outside = layer.paths[0]
    assert clipped.fill is not None
    assert (clipped.fill.red, clipped.fill.green, clipped.fill.blue) == (0.0, 1.0, 0.0)
    assert outside.fill is not None
    assert (outside.fill.red, outside.fill.green, outside.fill.blue) == (1.0, 0.0, 0.0)


def test_text_frame_requires_complete_source_local_placement_and_style_evidence() -> None:
    text_document = b"/1 << /1 [ << /0 << /0 (Hello\\r) >> >> ] >>"
    metadata = json.dumps(
        {
            "id": "proven-text",
            "name": "Proven text",
            "coordinate_space": "document",
            "x": 12,
            "y": 34,
            "font_size": 10,
            "font_name": "Helvetica",
            "fill": {"cyan": 0, "magenta": 0, "yellow": 0, "black": 1},
        },
        separators=(",", ":"),
    ).encode()
    payload = (
        b"/AI11TextDocument : /ASCII85Decode ,\n%"
        + base64.a85encode(text_document)
        + b"~>\n%AI11_EndTextDocument\n%AI5_BeginLayer\n"
        + b"/AI11Text :\n0 /FreeUndo ,\n0 /FrameIndex ,\n0 /StoryIndex ,\n;\n"
        + b"%_(py-ai-text:"
        + metadata
        + b") /UnicodeString (AdobeNoteAttribute) ,\n%AI5_EndLayer\n"
    )

    result = read_modern_ai(_single_segment_pdf(payload))

    assert result.semantic is not None and result.semantic.document is not None
    assert result.semantic.coverage.projected_text_count == 1
    assert result.semantic.coverage.partial_text_count == 0
    text = result.semantic.document.layers[0].text_frames[0]
    assert (text.id, text.text, text.x, text.y, text.font_size) == (
        "proven-text",
        "Hello",
        12.0,
        34.0,
        10.0,
    )
    assert text.fill.black == 1.0
    assert text.unknown["modern_source"]["identity_and_placement_note_span"]


def test_incomplete_clipping_structure_is_retained_as_an_exact_partial() -> None:
    payload = (
        b"%AI5_BeginLayer\nq\n1 0 0 Xa\n"
        b"0 0 m\n10 0 L\n10 10 L\nf\nQ\n%AI5_EndLayer\n"
    )

    result = read_modern_ai(_single_segment_pdf(payload))

    assert result.semantic is not None
    partial = next(
        item for item in result.semantic.partial_nodes if item.kind == "clipping_group"
    )
    assert partial.missing_fields == ("clipping_path",)
    assert len(partial.known_fields["content_path_ids"]) == 1
    assert payload[partial.start : partial.end].startswith(b"q")
    assert payload[partial.start : partial.end].endswith(b"Q")
    assert "partial_modern_structure" in {
        diagnostic.code for diagnostic in result.semantic.diagnostics
    }


def test_semantic_hierarchy_nesting_limit_preserves_source_and_reports_error() -> None:
    payload = b"%AI5_BeginLayer\nu\nu\nU\nU\n%AI5_EndLayer\n"

    result = read_modern_ai(
        _single_segment_pdf(payload),
        limits=ModernReadLimits(max_semantic_nesting=2),
    )

    assert result.segments[0].decoded_bytes == payload
    diagnostic = next(
        item for item in result.diagnostics if item.code == "modern_semantic_limit_exceeded"
    )
    assert diagnostic.severity == "error"
    assert diagnostic.segment == "AIPrivateData1"
    assert "hierarchy nesting exceeds 2" in diagnostic.message


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
        b"0 0 0 XA\n"
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
    assert modern["reader_profile"] == "modern-ai-read-only-v2"
    assert modern["read_only"] is True
    assert modern["safe_to_reserialize"] is False
    assert modern["semantic"]["profile"] == "modern-ai-semantic-read-only-v2"
    assert modern["semantic"]["read_only"] is True
    assert modern["semantic"]["coverage"]["projected_layer_count"] == 1

    assert main(["inspect", str(FIXTURE)]) == 0
    plain = capsys.readouterr().out
    assert "semantic-profile: modern-ai-semantic-read-only-v2" in plain
    assert "artwork: layers=1 paths=0 groups=0" in plain
    assert "partial-nodes:" in plain

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
