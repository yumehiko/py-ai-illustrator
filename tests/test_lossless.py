import pytest

from py_ai_illustrator import SourceLimitExceeded, SourceReplacement, tokenize_legacy


def test_tokenizer_preserves_unknown_bytes_spans_and_mixed_line_endings() -> None:
    data = (
        b"%!PS-Adobe-3.0\r\n"
        b"%AI5_FileFormat 3.0\n"
        b"\r"
        b"12 34 \xffFutureOperator\r"
        b"%%EOF"
    )
    source = tokenize_legacy(data)

    assert source.to_bytes() == data
    assert [token.kind for token in source.lines] == [
        "comment",
        "comment",
        "blank",
        "statement",
        "comment",
    ]
    assert [source.line_ending(token) for token in source.lines] == [
        b"\r\n",
        b"\n",
        b"\r",
        b"\r",
        b"",
    ]
    assert source.line_content(source.lines[3]) == b"12 34 \xffFutureOperator"
    assert source.operator(source.lines[3]) == b"\xffFutureOperator"
    assert b"".join(source.raw_line(token) for token in source.lines) == data
    assert [token.line_number for token in source.lines] == [1, 2, 3, 4, 5]
    assert all(
        left.end == right.start
        for left, right in zip(source.lines, source.lines[1:], strict=False)
    )


def test_tokenizer_handles_empty_input() -> None:
    source = tokenize_legacy(b"")
    assert source.lines == ()
    assert source.to_bytes() == b""


@pytest.mark.parametrize(
    ("data", "limits", "message"),
    [
        (b"1234", {"max_source_bytes": 3}, "source is 4 bytes"),
        (b"1234\n", {"max_line_bytes": 4}, "line 1 is 5 bytes"),
        (b"a\nb\nc\n", {"max_lines": 2}, "exceeds 2 lines"),
    ],
)
def test_tokenizer_enforces_resource_limits(
    data: bytes, limits: dict[str, int], message: str
) -> None:
    with pytest.raises(SourceLimitExceeded, match=message):
        tokenize_legacy(data, **limits)


def test_tokenizer_rejects_non_positive_limits() -> None:
    with pytest.raises(ValueError, match="limits must be positive"):
        tokenize_legacy(b"data", max_lines=0)


def test_statement_operator_spans_skip_strings_and_inline_comments() -> None:
    source = tokenize_legacy(
        b"(Layer (nested\\) name)) Ln\r\n"
        b"10 20 m % inline comment\n"
        b"%%EOF\n"
    )
    assert [source.operator(token) for token in source.lines] == [b"Ln", b"m", None]


def test_local_patch_changes_only_the_selected_operator_span() -> None:
    data = b"%!PS-Adobe-3.0\r\n10 20 m % keep unknown \xff\r\n%%EOF"
    source = tokenize_legacy(data)
    statement = source.lines[1]
    assert statement.operator_start is not None
    assert statement.operator_end is not None

    patched = source.patched(
        [SourceReplacement(statement.operator_start, statement.operator_end, b"L")]
    )
    assert patched.to_bytes() == b"%!PS-Adobe-3.0\r\n10 20 L % keep unknown \xff\r\n%%EOF"
    assert patched.operator(patched.lines[1]) == b"L"


def test_local_patch_rejects_overlapping_or_out_of_bounds_spans() -> None:
    source = tokenize_legacy(b"abcdef")
    with pytest.raises(ValueError, match="must not overlap"):
        source.patched(
            [
                SourceReplacement(1, 4, b"x"),
                SourceReplacement(3, 5, b"y"),
            ]
        )
    with pytest.raises(ValueError, match="exceeds source length"):
        source.patched([SourceReplacement(1, 7, b"x")])
