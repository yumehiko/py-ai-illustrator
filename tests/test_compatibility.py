from pathlib import Path

import pytest

from py_ai_illustrator.cli import main
from py_ai_illustrator.legacy import (
    UnsupportedLegacyFeature,
    dumps_ai7,
    reads_ai7,
    reserialize_ai7,
)
from py_ai_illustrator.model import Color, Document, Layer, Point
from py_ai_illustrator.model import Path as AIPath


def supported_document() -> Document:
    return Document(
        width=100,
        height=80,
        layers=[
            Layer(
                id="artwork",
                name="Artwork",
                paths=[
                    AIPath(
                        id="shape",
                        points=[Point(10, 10), Point(90, 10), Point(90, 70), Point(10, 70)],
                        fill=Color(1, 0, 0),
                    )
                ],
            )
        ],
    )


def test_reader_returns_source_coverage_and_recognized_inventory() -> None:
    data = dumps_ai7(supported_document())
    result = reads_ai7(data)

    assert result.document.to_dict() == supported_document().to_dict()
    assert result.source.to_bytes() == data
    assert result.coverage.complete is True
    assert result.safe_to_reserialize is True
    assert result.classification == "convertible"
    assert {entry.name for entry in result.coverage.operators} >= {"m", "L", "f"}
    assert {entry.name for entry in result.coverage.resources} >= {
        "%AI5_FileFormat",
        "%%BoundingBox",
    }


def test_unknown_operator_and_resource_are_source_located_and_make_result_partial() -> None:
    data = dumps_ai7(supported_document()).replace(
        b"%%EndSetup\n",
        b"%%EndSetup\n%%AIFutureResource: opaque\n12 34 FutureOperator\n",
    )
    result = reads_ai7(data)

    assert result.source.to_bytes() == data
    assert result.coverage.unsupported_statement_count == 1
    assert result.coverage.unsupported_resource_count == 1
    assert result.safe_to_reserialize is False
    assert result.classification == "partially_parsed"
    assert [(item.code, item.feature_name) for item in result.diagnostics] == [
        ("unsupported-resource", "%%AIFutureResource"),
        ("unsupported-operator", "FutureOperator"),
    ]
    assert all(item.start < item.end for item in result.diagnostics)
    report = result.compatibility_report()
    assert report["safe_to_reserialize"] is False
    assert report["coverage"]["unsupported_statement_count"] == 1


def test_reserialize_rejects_unknown_features_unless_loss_is_explicit() -> None:
    data = dumps_ai7(supported_document()).replace(
        b"%%EndSetup\n",
        b"%%EndSetup\n12 34 FutureOperator\n",
    )
    result = reads_ai7(data)

    with pytest.raises(UnsupportedLegacyFeature, match="Refusing to reserialize"):
        reserialize_ai7(result)

    discarded = reserialize_ai7(result, loss_policy="discard")
    assert b"FutureOperator" not in discarded
    assert reads_ai7(discarded).safe_to_reserialize is True


def test_json_export_is_strict_by_default_and_validate_reports_partial(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source = tmp_path / "future.ai"
    destination = tmp_path / "future.json"
    source.write_bytes(
        dumps_ai7(supported_document()).replace(
            b"%%EndSetup\n",
            b"%%EndSetup\n12 34 FutureOperator\n",
        )
    )

    with pytest.raises(SystemExit):
        main(["export", str(source), "--to", "json", "-o", str(destination)])
    assert not destination.exists()

    assert main(["validate", str(source)]) == 1
    output = capsys.readouterr().out
    assert '"classification": "partially_parsed"' in output
    assert '"safe_to_reserialize": false' in output

    assert (
        main(
            [
                "export",
                str(source),
                "--to",
                "json",
                "-o",
                str(destination),
                "--allow-partial",
            ]
        )
        == 0
    )
    assert destination.exists()
