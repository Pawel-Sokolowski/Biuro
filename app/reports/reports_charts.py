# app/reports/reports_charts.py
from PyQt6.QtCharts import QChart, QChartView, QLineSeries, QValueAxis, QCategoryAxis
from PyQt6.QtGui import QPen, QColor
from PyQt6.QtCore import Qt


def create_line_chart(title: str, data: dict, key: str) -> QChartView:
    """
    Tworzy wykres liniowy dla podanego klucza:
    key = 'koszt_na_godzine_kadry' lub 'koszt_na_godzine_ksiegowosc'
    data = { '2024-01': {...}, '2024-02': {...}, ... }
    """

    series = QLineSeries()
    months = list(data.keys())

    # wartości
    values = [data[m][key] for m in months]

    # dodawanie punktów
    for i, m in enumerate(months):
        series.append(i, data[m][key])

    # styl linii
    pen = QPen(QColor("#0078D4"), 3)
    series.setPen(pen)

    chart = QChart()
    chart.addSeries(series)
    chart.setTitle(title)
    chart.legend().hide()

    # oś X — miesiące
    axis_x = QCategoryAxis()
    axis_x.setLabelsPosition(QCategoryAxis.AxisLabelsPosition.AxisLabelsPositionOnValue)

    for i, m in enumerate(months):
        axis_x.append(m, i)

    axis_x.setGridLineVisible(False)
    chart.addAxis(axis_x, Qt.AlignmentFlag.AlignBottom)
    series.attachAxis(axis_x)

    # oś Y — wartości
    axis_y = QValueAxis()
    axis_y.setLabelFormat("%.2f")
    axis_y.setTitleText("PLN / godzina")

    min_val = min(values) if values else 0
    max_val = max(values) if values else 1

    axis_y.setRange(min_val * 0.9, max_val * 1.1)
    chart.addAxis(axis_y, Qt.AlignmentFlag.AlignLeft)
    series.attachAxis(axis_y)

    view = QChartView(chart)
    view.setRenderHint(view.RenderHint.Antialiasing)
    return view
