import pytest

from py_ai_illustrator import SourceLimitExceeded, tokenize_legacy


def test_tokenizer_preserves_unknown_bytes_spans_and_mixed_line_endings() -> None:
    data = (
        b"%!PS-Adobe-3.0\r\n"
        b"%AI5_FileFormat 3.0\n"
        b"\r"
        b"12 34 futureOperator \xff\r"
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
    assert source.line_content(source.lines[3]) == b"12 34 futureOperator \xff"
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
