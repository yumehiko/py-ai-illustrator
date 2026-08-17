"""Compatibility reporting for conservative legacy Illustrator parsing."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .lossless import LegacySource
from .model import Document

FeatureKind = Literal["operator", "resource"]
FeatureSupport = Literal["modeled", "structural", "unsupported"]


@dataclass(frozen=True, slots=True)
class LegacyFieldOrigin:
    """Exact source span and precondition bytes for one modeled node field."""

    field: str
    start: int
    end: int
    expected: bytes

    def __post_init__(self) -> None:
        if not 0 <= self.start <= self.end:
            raise ValueError("field origin span must be ordered and non-negative")
        if len(self.expected) != self.end - self.start:
            raise ValueError("field origin bytes must match its source span")

    def to_dict(self) -> dict[str, object]:
        return {
            "field": self.field,
            "span": {"start": self.start, "end": self.end},
            "expected_hex": self.expected.hex(),
        }


@dataclass(frozen=True, slots=True)
class LegacyNodeOrigin:
    """Source provenance for one parsed semantic node."""

    node_type: str
    node_id: str
    start: int
    end: int
    fields: tuple[LegacyFieldOrigin, ...] = ()

    def __post_init__(self) -> None:
        if not 0 <= self.start < self.end:
            raise ValueError("node origin span must be ordered and non-empty")
        if any(field.start < self.start or field.end > self.end for field in self.fields):
            raise ValueError("field origins must be inside their node origin")

    def field(self, name: str) -> LegacyFieldOrigin | None:
        matches = [field for field in self.fields if field.field == name]
        if len(matches) > 1:
            raise ValueError(f"node origin contains duplicate {name!r} fields")
        return matches[0] if matches else None

    def fields_with_prefix(self, prefix: str) -> tuple[LegacyFieldOrigin, ...]:
        """Return fields whose names share a prefix, preserving source order."""

        return tuple(field for field in self.fields if field.field.startswith(prefix))

    def to_dict(self) -> dict[str, object]:
        return {
            "node_type": self.node_type,
            "node_id": self.node_id,
            "span": {"start": self.start, "end": self.end},
            "fields": [field.to_dict() for field in self.fields],
        }


@dataclass(frozen=True, slots=True)
class LegacyFeatureOccurrence:
    """Inventory entry for one operator or comment/resource name."""

    kind: FeatureKind
    name: str
    support: FeatureSupport
    count: int
    line_numbers: tuple[int, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "name": self.name,
            "support": self.support,
            "count": self.count,
            "line_numbers": list(self.line_numbers),
        }


@dataclass(frozen=True, slots=True)
class LegacyDiagnostic:
    """One source-located compatibility diagnostic."""

    code: str
    severity: Literal["warning", "error"]
    message: str
    line_number: int
    start: int
    end: int
    feature_kind: FeatureKind
    feature_name: str

    def to_dict(self) -> dict[str, object]:
        return {
            "code": self.code,
            "severity": self.severity,
            "message": self.message,
            "line_number": self.line_number,
            "span": {"start": self.start, "end": self.end},
            "feature_kind": self.feature_kind,
            "feature_name": self.feature_name,
        }


@dataclass(frozen=True, slots=True)
class LegacyParseCoverage:
    """Line coverage plus recognized and unsupported feature inventories."""

    line_count: int
    statement_count: int
    comment_count: int
    modeled_statement_count: int
    structural_statement_count: int
    unsupported_statement_count: int
    recognized_resource_count: int
    unsupported_resource_count: int
    operators: tuple[LegacyFeatureOccurrence, ...]
    resources: tuple[LegacyFeatureOccurrence, ...]

    @property
    def complete(self) -> bool:
        return self.unsupported_statement_count == 0 and self.unsupported_resource_count == 0

    @property
    def recognized_statement_count(self) -> int:
        return self.modeled_statement_count + self.structural_statement_count

    def to_dict(self) -> dict[str, object]:
        return {
            "complete": self.complete,
            "line_count": self.line_count,
            "statement_count": self.statement_count,
            "comment_count": self.comment_count,
            "modeled_statement_count": self.modeled_statement_count,
            "structural_statement_count": self.structural_statement_count,
            "recognized_statement_count": self.recognized_statement_count,
            "unsupported_statement_count": self.unsupported_statement_count,
            "recognized_resource_count": self.recognized_resource_count,
            "unsupported_resource_count": self.unsupported_resource_count,
            "operators": [entry.to_dict() for entry in self.operators],
            "resources": [entry.to_dict() for entry in self.resources],
        }


@dataclass(frozen=True, slots=True)
class LegacyReadResult:
    """Parsed IR together with its exact source and compatibility evidence."""

    document: Document
    source: LegacySource
    coverage: LegacyParseCoverage
    diagnostics: tuple[LegacyDiagnostic, ...]
    origins: tuple[LegacyNodeOrigin, ...] = ()

    @property
    def safe_to_reserialize(self) -> bool:
        return self.coverage.complete and not any(
            diagnostic.severity == "error" for diagnostic in self.diagnostics
        )

    @property
    def classification(self) -> Literal["convertible", "partially_parsed"]:
        return "convertible" if self.safe_to_reserialize else "partially_parsed"

    def compatibility_report(self) -> dict[str, object]:
        return {
            "classification": self.classification,
            "safe_to_reserialize": self.safe_to_reserialize,
            "coverage": self.coverage.to_dict(),
            "diagnostics": [diagnostic.to_dict() for diagnostic in self.diagnostics],
            "origins": [origin.to_dict() for origin in self.origins],
        }


_MODELED_OPERATORS = frozenset(
    {
        "Lb",
        "Ln",
        "u",
        "U",
        "*u",
        "*U",
        "q",
        "Q",
        "To",
        "Tp",
        "Tm",
        "Tf",
        "Ta",
        "Tx",
        "TO",
        "Xa",
        "XA",
        "k",
        "K",
        "J",
        "j",
        "w",
        "M",
        "d",
        "D",
        "h",
        "H",
        "W",
        "m",
        "l",
        "L",
        "c",
        "C",
        "v",
        "V",
        "y",
        "Y",
        "b",
        "f",
        "s",
        "n",
        "B",
        "F",
        "S",
        "N",
    }
)
_STRUCTURAL_OPERATORS = frozenset({"A", "LB", "TP", "Tr", "TZ"})
_RECOGNIZED_RESOURCES = frozenset(
    {
        "%!PS-Adobe-3.0",
        "%%Creator",
        "%%Title",
        "%%BoundingBox",
        "%%HiResBoundingBox",
        "%%DocumentProcessColors",
        "%%PageOrigin",
        "%%EndComments",
        "%%BeginProlog",
        "%%EndProlog",
        "%%BeginSetup",
        "%%EndSetup",
        "%%Trailer",
        "%%EOF",
        "%AI5_FileFormat",
        "%AI3_ColorUsage",
        "%AI3_TemplateBox",
        "%AI3_DocumentPreview",
        "%AI5_ArtSize",
        "%AI5_RulerUnits",
        "%AI5_ArtFlags",
        "%AI5_NumLayers",
        "%AI3_BeginEncoding",
        "%AI3_EndEncoding",
        "%AI5_BeginLayer",
        "%AI5_EndLayer",
        "%AI7_Tag",
        "%AI3_Note",
        "%%py-ai-metadata",
        "%%py-ai-artboard",
        "%%py-ai-layer-id",
        "%%py-ai-path-id-utf8",
        "%%py-ai-path-name",
        "%%py-ai-path-name-utf8",
        "%%py-ai-compound-id",
        "%%py-ai-compound-name",
        "%%py-ai-clipping-id",
        "%%py-ai-clipping-name",
        "%%py-ai-group-id",
        "%%py-ai-group-id-utf8",
        "%%py-ai-group-name",
        "%%py-ai-group-name-utf8",
        "%%py-ai-text-id",
        "%%py-ai-text-id-utf8",
        "%%py-ai-text-name",
        "%%py-ai-text-name-utf8",
        "%%py-ai-text-alignment",
        "%%py-ai-text-native-font",
        "%%py-ai-text-tracking",
        "%%py-ai-text-rotation",
        "%%py-ai-text-area",
        "%%py-ai-text-leading",
        "%%py-ai-linked-image",
    }
)


def _resource_name(content: bytes) -> str:
    end = len(content)
    for delimiter in (b":", b" ", b"\t"):
        position = content.find(delimiter)
        if position >= 0:
            end = min(end, position)
    return content[:end].decode("latin-1")


def analyze_legacy_source(
    source: LegacySource,
) -> tuple[LegacyParseCoverage, tuple[LegacyDiagnostic, ...]]:
    """Classify source lines without normalizing or discarding their bytes."""

    operator_lines: dict[tuple[str, FeatureSupport], list[int]] = {}
    resource_lines: dict[tuple[str, FeatureSupport], list[int]] = {}
    diagnostics: list[LegacyDiagnostic] = []
    modeled_statements = 0
    structural_statements = 0
    unsupported_statements = 0
    recognized_resources = 0
    unsupported_resources = 0
    statement_count = 0
    comment_count = 0

    for token in source.lines:
        if token.kind == "statement":
            statement_count += 1
            raw_operator = source.operator(token)
            name = raw_operator.decode("latin-1") if raw_operator is not None else "<missing>"
            if name in _MODELED_OPERATORS:
                support: FeatureSupport = "modeled"
                modeled_statements += 1
            elif name in _STRUCTURAL_OPERATORS:
                support = "structural"
                structural_statements += 1
            else:
                support = "unsupported"
                unsupported_statements += 1
                diagnostics.append(
                    LegacyDiagnostic(
                        code="unsupported-operator",
                        severity="warning",
                        message=(
                            f"Unsupported legacy operator {name!r} is preserved in source only."
                        ),
                        line_number=token.line_number,
                        start=token.start,
                        end=token.end,
                        feature_kind="operator",
                        feature_name=name,
                    )
                )
            operator_lines.setdefault((name, support), []).append(token.line_number)
            continue

        if token.kind != "comment":
            continue
        comment_count += 1
        content = source.line_content(token).lstrip()
        name = _resource_name(content)
        support = "structural" if name in _RECOGNIZED_RESOURCES else "unsupported"
        if support == "structural":
            recognized_resources += 1
        else:
            unsupported_resources += 1
            diagnostics.append(
                LegacyDiagnostic(
                    code="unsupported-resource",
                    severity="warning",
                    message=(
                        f"Unsupported legacy resource/comment {name!r} is preserved in source "
                        "only."
                    ),
                    line_number=token.line_number,
                    start=token.start,
                    end=token.end,
                    feature_kind="resource",
                    feature_name=name,
                )
            )
        resource_lines.setdefault((name, support), []).append(token.line_number)

    def inventory(
        kind: FeatureKind,
        entries: dict[tuple[str, FeatureSupport], list[int]],
    ) -> tuple[LegacyFeatureOccurrence, ...]:
        return tuple(
            LegacyFeatureOccurrence(
                kind=kind,
                name=name,
                support=support,
                count=len(lines),
                line_numbers=tuple(lines),
            )
            for (name, support), lines in sorted(entries.items())
        )

    coverage = LegacyParseCoverage(
        line_count=len(source.lines),
        statement_count=statement_count,
        comment_count=comment_count,
        modeled_statement_count=modeled_statements,
        structural_statement_count=structural_statements,
        unsupported_statement_count=unsupported_statements,
        recognized_resource_count=recognized_resources,
        unsupported_resource_count=unsupported_resources,
        operators=inventory("operator", operator_lines),
        resources=inventory("resource", resource_lines),
    )
    return coverage, tuple(diagnostics)
