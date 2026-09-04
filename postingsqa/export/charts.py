"""openpyxl chart helpers with one consistent, colorblind-validated palette.

Palette: the dataviz reference instance (light surface). Categorical slots are assigned in fixed
order by entity, never cycled; single-series bars use one hue; no 3-D, no dual axes.
"""

from __future__ import annotations

from openpyxl.chart import BarChart, LineChart, PieChart, Reference
from openpyxl.chart.label import DataLabelList
from openpyxl.chart.series import DataPoint
from openpyxl.worksheet.worksheet import Worksheet

CATEGORICAL = ["2a78d6", "eb6834", "1baf7a", "eda100", "e87ba4", "008300", "4a3aa7", "e34948"]
SOURCE_COLOR = {
    "remotive": CATEGORICAL[0], "greenhouse": CATEGORICAL[1], "lever": CATEGORICAL[2], "usajobs": CATEGORICAL[3],
    "adzuna": CATEGORICAL[4], "linkedin": CATEGORICAL[5], "indeed": CATEGORICAL[6], "glassdoor": CATEGORICAL[7],
}
PRIMARY = CATEGORICAL[0]
NEUTRAL = "9a9891"
STATUS_GOOD = "008300"
STATUS_BAD = "e34948"
GRID = "e6e5e0"


def _base_axes(chart, x_title: str | None, y_title: str | None) -> None:
    chart.x_axis.title = x_title
    chart.y_axis.title = y_title
    # openpyxl >= 3.1 hides axes unless delete is explicitly False
    chart.x_axis.delete = False
    chart.y_axis.delete = False
    chart.y_axis.majorGridlines = None
    chart.y_axis.number_format = "0"
    chart.style = 2
    chart.height = 7.5
    chart.width = 14


def _color_series(series, hex_color: str) -> None:
    series.graphicalProperties.solidFill = hex_color
    series.graphicalProperties.line.solidFill = hex_color


def bar_chart(
    ws: Worksheet,
    title: str,
    min_row: int,
    max_row: int,
    cat_col: int,
    val_col: int,
    horizontal: bool = False,
    color: str = PRIMARY,
    point_colors: list[str] | None = None,
    x_title: str | None = None,
    y_title: str | None = "Jobs",
) -> BarChart:
    """Single-series bar chart. Rows min_row..max_row hold categories (cat_col) and values (val_col);
    row min_row is the header."""
    chart = BarChart()
    chart.type = "bar" if horizontal else "col"
    chart.title = title
    chart.legend = None  # one series: the title names it
    chart.gapWidth = 60
    data = Reference(ws, min_col=val_col, min_row=min_row, max_row=max_row)
    cats = Reference(ws, min_col=cat_col, min_row=min_row + 1, max_row=max_row)
    chart.add_data(data, titles_from_data=True)
    chart.set_categories(cats)
    series = chart.series[0]
    _color_series(series, color)
    if point_colors:
        for idx, hex_color in enumerate(point_colors):
            pt = DataPoint(idx=idx)
            pt.graphicalProperties.solidFill = hex_color
            pt.graphicalProperties.line.solidFill = hex_color
            series.dPt.append(pt)
    _base_axes(chart, x_title, y_title)
    if horizontal:
        chart.x_axis.title, chart.y_axis.title = y_title, x_title
        chart.x_axis.scaling.orientation = "maxMin"  # largest bar on top
    return chart


def pie_chart(ws: Worksheet, title: str, min_row: int, max_row: int, cat_col: int, val_col: int, colors: list[str]) -> PieChart:
    chart = PieChart()
    chart.title = title
    data = Reference(ws, min_col=val_col, min_row=min_row, max_row=max_row)
    cats = Reference(ws, min_col=cat_col, min_row=min_row + 1, max_row=max_row)
    chart.add_data(data, titles_from_data=True)
    chart.set_categories(cats)
    series = chart.series[0]
    for idx, hex_color in enumerate(colors[: max_row - min_row]):
        pt = DataPoint(idx=idx)
        pt.graphicalProperties.solidFill = hex_color
        pt.graphicalProperties.line.solidFill = "ffffff"
        series.dPt.append(pt)
    chart.dataLabels = DataLabelList()
    chart.dataLabels.showPercent = True
    chart.dataLabels.showVal = False
    chart.dataLabels.showCatName = False
    chart.dataLabels.showSerName = False
    chart.dataLabels.showLeaderLines = False
    chart.style = 2
    chart.height = 7.5
    chart.width = 9
    return chart


def line_chart(ws: Worksheet, title: str, min_row: int, max_row: int, cat_col: int, val_col: int, color: str = PRIMARY, x_title: str = "Date", y_title: str = "Jobs") -> LineChart:
    chart = LineChart()
    chart.title = title
    chart.legend = None
    data = Reference(ws, min_col=val_col, min_row=min_row, max_row=max_row)
    cats = Reference(ws, min_col=cat_col, min_row=min_row + 1, max_row=max_row)
    chart.add_data(data, titles_from_data=True)
    chart.set_categories(cats)
    series = chart.series[0]
    series.smooth = False
    series.graphicalProperties.line.solidFill = color
    series.graphicalProperties.line.width = 25000  # EMU ~ 2px
    series.marker.symbol = "circle"
    series.marker.size = 6
    series.marker.graphicalProperties.solidFill = color
    series.marker.graphicalProperties.line.solidFill = color
    _base_axes(chart, x_title, y_title)
    chart.x_axis.number_format = "mmm d"
    chart.x_axis.tickLblSkip = 3
    chart.y_axis.scaling.min = 0
    return chart
