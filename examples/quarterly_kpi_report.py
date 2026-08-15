"""Build an editable quarterly KPI report with native Illustrator stroke styles."""

from dataclasses import dataclass
from pathlib import Path

from py_ai_illustrator import (
    Color,
    Document,
    LayerBuilder,
    RenderedComponent,
    TextBlock,
    TextStyle,
    ellipse_path,
    polyline_path,
    rectangle_path,
)
from py_ai_illustrator.legacy import dump_ai7

INK = Color(0.06, 0.1, 0.18)
BLUE = Color(0.1, 0.38, 0.78)
CORAL = Color(0.92, 0.3, 0.22)
MUTED = Color(0.4, 0.44, 0.52)
GRID = Color(0.78, 0.8, 0.84)
PAPER = Color(0.965, 0.96, 0.94)


@dataclass(frozen=True, slots=True)
class Metric:
    label: str
    value: str
    change: str
    positive: bool = True


@dataclass(frozen=True, slots=True)
class MetricCard:
    id: str
    metric: Metric
    width: float = 164
    height: float = 70

    def render(self, *, x: float, top: float) -> RenderedComponent:
        builder = LayerBuilder(id=f"{self.id}.content", name=self.metric.label)
        builder.add_path(
            rectangle_path(
                f"{self.id}.background",
                x=x,
                top=top,
                width=self.width,
                height=self.height,
                fill=Color(1, 1, 1),
                stroke=Color(0.82, 0.82, 0.8),
                stroke_width=0.7,
                name=f"Metric card: {self.metric.label}",
            )
        )
        builder.add(
            TextBlock(
                id=f"{self.id}.label",
                name="Metric label",
                text=self.metric.label.upper(),
                width=self.width - 24,
                wrap=False,
                style=TextStyle(font_size=8, font_name="Helvetica-Bold", fill=MUTED),
            ).render(x=x + 12, top=top - 12)
        )
        builder.add(
            TextBlock(
                id=f"{self.id}.value",
                name="Metric value",
                text=self.metric.value,
                width=self.width - 24,
                alignment="right",
                wrap=False,
                style=TextStyle(font_size=22, font_name="Helvetica-Bold", fill=INK),
            ).render(x=x + 12, top=top - 31)
        )
        builder.add(
            TextBlock(
                id=f"{self.id}.change",
                name="Metric change",
                text=self.metric.change,
                width=60,
                alignment="right",
                wrap=False,
                style=TextStyle(
                    font_size=8,
                    font_name="Helvetica-Bold",
                    fill=BLUE if self.metric.positive else CORAL,
                ),
            ).render(x=x + self.width - 72, top=top - 59)
        )
        layer = builder.build()
        return RenderedComponent(
            width=self.width,
            height=self.height,
            paths=layer.paths,
            text_frames=layer.text_frames,
            item_order=layer.item_order,
        )


@dataclass(frozen=True, slots=True)
class LineChart:
    id: str
    labels: tuple[str, ...]
    values: tuple[float, ...]
    target: float
    width: float = 450
    height: float = 118
    minimum: float = 60
    maximum: float = 120

    def __post_init__(self) -> None:
        if len(self.labels) != len(self.values) or len(self.values) < 2:
            raise ValueError("LineChart requires matching labels and at least two values")
        if not self.minimum < self.maximum:
            raise ValueError("LineChart maximum must be greater than minimum")
        if any(value < self.minimum or value > self.maximum for value in self.values):
            raise ValueError("LineChart value falls outside the configured scale")

    def _y(self, value: float, *, top: float) -> float:
        ratio = (value - self.minimum) / (self.maximum - self.minimum)
        return top - self.height + ratio * self.height

    def render(self, *, x: float, top: float) -> RenderedComponent:
        builder = LayerBuilder(id=f"{self.id}.content", name="Monthly performance")
        ticks = (60, 80, 100, 120)
        for tick in ticks:
            y = self._y(tick, top=top)
            builder.add_path(
                polyline_path(
                    f"{self.id}.grid-{tick}",
                    points=[(x, y), (x + self.width, y)],
                    stroke=GRID,
                    stroke_width=0.7,
                    dash_pattern=(2, 4),
                    line_cap="round",
                    name=f"Grid line {tick}",
                )
            )
            builder.add(
                TextBlock(
                    id=f"{self.id}.tick-{tick}",
                    name="Y axis value",
                    text=str(tick),
                    width=30,
                    alignment="right",
                    wrap=False,
                    style=TextStyle(font_size=7, fill=MUTED),
                ).render(x=x - 38, top=y + 3)
            )

        target_y = self._y(self.target, top=top)
        builder.add_path(
            polyline_path(
                f"{self.id}.target",
                points=[(x, target_y), (x + self.width, target_y)],
                stroke=CORAL,
                stroke_width=1.6,
                dash_pattern=(10, 6),
                dash_offset=2,
                line_cap="round",
                name="Target line",
            )
        )

        step = self.width / (len(self.values) - 1)
        points = [
            (x + index * step, self._y(value, top=top))
            for index, value in enumerate(self.values)
        ]
        builder.add_path(
            polyline_path(
                f"{self.id}.actual",
                points=points,
                stroke=BLUE,
                stroke_width=3,
                line_cap="round",
                line_join="round",
                name="Actual performance",
            )
        )
        for index, ((point_x, point_y), label, value) in enumerate(
            zip(points, self.labels, self.values, strict=True)
        ):
            builder.add_path(
                ellipse_path(
                    f"{self.id}.point-{index}",
                    center_x=point_x,
                    center_y=point_y,
                    radius_x=4,
                    radius_y=4,
                    fill=Color(1, 1, 1),
                    stroke=BLUE,
                    stroke_width=2,
                    name=f"{label}: {value:g}",
                )
            )
            builder.add(
                TextBlock(
                    id=f"{self.id}.label-{index}",
                    name="Month label",
                    text=label,
                    width=50,
                    alignment="center",
                    wrap=False,
                    style=TextStyle(font_size=8, fill=MUTED),
                ).render(x=point_x - 25, top=top - self.height - 12)
            )
        layer = builder.build()
        return RenderedComponent(
            width=self.width,
            height=self.height + 24,
            paths=layer.paths,
            text_frames=layer.text_frames,
            item_order=layer.item_order,
        )


def build_document() -> Document:
    builder = LayerBuilder(id="quarterly-report", name="Quarterly KPI report")
    builder.add_path(
        rectangle_path(
            "report.background",
            x=0,
            top=420,
            width=612,
            height=420,
            fill=PAPER,
            name="Report background",
        )
    )
    builder.add(
        TextBlock(
            id="report.eyebrow",
            name="Report period",
            text="NORTH REGION / Q2 2026",
            width=536,
            alignment="right",
            wrap=False,
            style=TextStyle(font_size=9, font_name="Helvetica-Bold", fill=BLUE),
        ).render(x=38, top=394)
    )
    builder.add(
        TextBlock(
            id="report.title",
            name="Report title",
            text="Quarterly performance",
            width=536,
            wrap=False,
            style=TextStyle(font_size=24, font_name="Helvetica-Bold", fill=INK),
        ).render(x=38, top=377)
    )

    metrics = (
        Metric("Revenue", "$1.42M", "+18.4%"),
        Metric("Gross margin", "42.8%", "+3.1 pt"),
        Metric("Retention", "76.2%", "-1.4 pt", positive=False),
    )
    for index, (metric, x) in enumerate(zip(metrics, (38, 224, 410), strict=True), start=1):
        card = MetricCard(id=f"metric-{index}", metric=metric)
        builder.add_grouped(
            card.render(x=x, top=332),
            group_id=f"metric-{index}.group",
            group_name=f"Metric: {metric.label}",
        )

    builder.add_path(
        rectangle_path(
            "chart.panel",
            x=38,
            top=244,
            width=536,
            height=194,
            fill=Color(1, 1, 1),
            stroke=Color(0.82, 0.82, 0.8),
            stroke_width=0.7,
            name="Chart panel",
        )
    )
    builder.add(
        TextBlock(
            id="chart.title",
            name="Chart title",
            text="Monthly operating index",
            width=250,
            wrap=False,
            style=TextStyle(font_size=11, font_name="Helvetica-Bold", fill=INK),
        ).render(x=58, top=224)
    )
    builder.add(
        TextBlock(
            id="chart.legend",
            name="Target legend",
            text="TARGET 100",
            width=120,
            alignment="right",
            wrap=False,
            style=TextStyle(font_size=8, font_name="Helvetica-Bold", fill=CORAL),
        ).render(x=434, top=223)
    )
    chart = LineChart(
        id="operating-index",
        labels=("APR", "MAY", "JUN", "JUL", "AUG", "SEP"),
        values=(72, 84, 79, 96, 108, 112),
        target=100,
    )
    builder.add_grouped(
        chart.render(x=94, top=195),
        group_id="operating-index.group",
        group_name="Operating index chart",
    )
    builder.add(
        TextBlock(
            id="report.source",
            name="Data source",
            text="Source: internal sales and customer operations data / refreshed 2026-09-30",
            width=536,
            alignment="right",
            wrap=False,
            style=TextStyle(font_size=7, fill=MUTED),
        ).render(x=38, top=30)
    )
    return Document(
        width=612,
        height=420,
        title="Semantic quarterly KPI report",
        metadata={
            "source": "examples/quarterly_kpi_report.py",
            "component": "LineChart",
            "business_case": "quarterly-kpi-report",
        },
        layers=[builder.build()],
    )


if __name__ == "__main__":
    dump_ai7(build_document(), Path(__file__).with_name("quarterly-kpi-report.ai"))
