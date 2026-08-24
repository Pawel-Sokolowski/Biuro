# app/reports/reports_ui.py
from app.utils.ui_imports import *
from PyQt6.QtCharts import (
    QChart, QChartView,
    QBarSeries, QBarSet,
    QLineSeries,
    QValueAxis, QBarCategoryAxis
)
from PyQt6.QtGui import QPainter
import datetime

from app.reports.reports_logic import (
    get_all_companies,
    get_months_range,
    build_report,
    build_yearly_report,
    compare_companies,
    build_profit_report
)


def format_hours(seconds: float) -> str:
    """Konwersja sekund → H:MM"""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    return f"{h}:{m:02d}"


class ReportsPage(QWidget):
    def __init__(self, pracownik_id):
        super().__init__()
        self.pracownik_id = pracownik_id
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)

        title = QLabel("<b>Raporty</b>")
        title.setFont(QFont("Arial", 18))
        layout.addWidget(title)

        mode_box = QHBoxLayout()
        self.mode_combo = QComboBox()
        self.mode_combo.addItems([
            "Raport miesięczny",
            "Raport roczny",
            "Porównanie firm",
            "Zarobek na godzinę"
        ])
        mode_box.addWidget(QLabel("Tryb:"))
        mode_box.addWidget(self.mode_combo)
        layout.addLayout(mode_box)

        self.firm_list = QListWidget()
        self.firm_list.setSelectionMode(QListWidget.SelectionMode.MultiSelection)
        for f in get_all_companies():
            item = QListWidgetItem(f"{f['nazwa']} (ID {f['id']})")
            item.setData(Qt.ItemDataRole.UserRole, f["id"])
            self.firm_list.addItem(item)
        layout.addWidget(self.firm_list)

        self.month_start = QComboBox()
        self.month_end = QComboBox()
        self.year_combo = QComboBox()

        now = datetime.datetime.now()
        months = [f"{y}-{m:02d}" for y in range(2024, now.year + 1) for m in range(1, 13)]

        self.month_start.addItems(months)
        self.month_end.addItems(months)
        self.year_combo.addItems([str(y) for y in range(2024, now.year + 1)])

        self.month_box_container = QWidget()
        self.month_box = QHBoxLayout(self.month_box_container)
        self.month_box.addWidget(QLabel("Od:"))
        self.month_box.addWidget(self.month_start)
        self.month_box.addWidget(QLabel("Do:"))
        self.month_box.addWidget(self.month_end)
        layout.addWidget(self.month_box_container)

        self.year_box_container = QWidget()
        self.year_box = QHBoxLayout(self.year_box_container)
        self.year_box.addWidget(QLabel("Rok:"))
        self.year_box.addWidget(self.year_combo)
        layout.addWidget(self.year_box_container)

        self.mode_combo.currentTextChanged.connect(self._update_mode_ui)
        self._update_mode_ui(self.mode_combo.currentText())

        btn = QPushButton("Generuj raport")
        btn.setProperty("class", "primary")
        btn.clicked.connect(self._generate_report)
        layout.addWidget(btn)

        btn_export = QPushButton("Eksport do Excel")
        btn_export.clicked.connect(self._export_to_excel)
        layout.addWidget(btn_export)

        self.chart_top = QChartView()
        self.chart_top.setRenderHint(QPainter.RenderHint.Antialiasing)

        self.chart_bottom = QChartView()
        self.chart_bottom.setRenderHint(QPainter.RenderHint.Antialiasing)

        layout.addWidget(self.chart_top)
        layout.addWidget(self.chart_bottom)

    def _update_mode_ui(self, mode):
        self.month_box_container.setVisible(mode in ["Raport miesięczny", "Porównanie firm", "Zarobek na godzinę"])
        self.year_box_container.setVisible(mode == "Raport roczny")

    def _generate_report(self):
        selected = [
            self.firm_list.item(i).data(Qt.ItemDataRole.UserRole)
            for i in range(self.firm_list.count())
            if self.firm_list.item(i).isSelected()
        ]

        if not selected:
            QMessageBox.warning(self, "Brak wyboru", "Wybierz co najmniej jedną firmę.")
            return

        mode = self.mode_combo.currentText()

        if mode == "Raport miesięczny":
            start = self.month_start.currentText()
            end = self.month_end.currentText()
            months = get_months_range(start, end)
            data = build_report(selected, months)
            self._render_mixed(data, selected, months)

        elif mode == "Raport roczny":
            year = int(self.year_combo.currentText())
            if len(selected) != 1:
                QMessageBox.warning(self, "Błąd", "Raport roczny wymaga jednej firmy.")
                return
            firma_id = selected[0]
            months = [f"{year}-{m:02d}" for m in range(1, 13)]
            data = build_yearly_report(firma_id, year)
            self._render_mixed({firma_id: data}, [firma_id], months)

        elif mode == "Porównanie firm":
            miesiac = self.month_start.currentText()
            data = compare_companies(selected, miesiac)
            self._render_compare(data, selected, miesiac)

        elif mode == "Zarobek na godzinę":
            start = self.month_start.currentText()
            end = self.month_end.currentText()
            months = get_months_range(start, end)
            data = build_profit_report(selected, months)
            self._render_profit(data, selected, months)

    # ============================================================
    # WYKRES MIESZANY — SŁUPKI (KWOTA) + LINIA (CZAS)
    # ============================================================

    def _render_mixed(self, data, selected, months):
        categories = months

        # --- Kadry ---
        chart_kadry = QChart()
        chart_kadry.setTitle("Kadry — kwota vs czas")

        bar_series = QBarSeries()
        line_series = QLineSeries()
        line_series.setName("Czas (godziny)")

        # słupki
        for fid in selected:
            bar = QBarSet(f"Firma {fid}")
            for m in months:
                bar.append(data[fid][m]["koszt_kadry"])
            bar_series.append(bar)

        # linia — poprawione indeksowanie
        for fid in selected:
            for idx, m in enumerate(months):
                seconds = data[fid][m]["godziny_kadry"]
                hours_float = seconds / 3600
                line_series.append(idx, hours_float)
                line_series.setPointLabelsVisible(True)
                line_series.setPointLabelsFormat(format_hours(seconds))

        chart_kadry.addSeries(bar_series)
        chart_kadry.addSeries(line_series)

        axis_x = QBarCategoryAxis()
        axis_x.append(categories)
        chart_kadry.addAxis(axis_x, Qt.AlignmentFlag.AlignBottom)
        bar_series.attachAxis(axis_x)
        line_series.attachAxis(axis_x)

        axis_y = QValueAxis()
        axis_y.setTitleText("Kwota (zł)")
        chart_kadry.addAxis(axis_y, Qt.AlignmentFlag.AlignLeft)
        bar_series.attachAxis(axis_y)

        axis_y2 = QValueAxis()
        axis_y2.setTitleText("Czas (godziny)")
        chart_kadry.addAxis(axis_y2, Qt.AlignmentFlag.AlignRight)
        line_series.attachAxis(axis_y2)

        self.chart_top.setChart(chart_kadry)

        # --- Księgowość ---
        chart_ks = QChart()
        chart_ks.setTitle("Księgowość — kwota vs czas")

        bar_series2 = QBarSeries()
        line_series2 = QLineSeries()
        line_series2.setName("Czas (godziny)")

        for fid in selected:
            bar = QBarSet(f"Firma {fid}")
            for m in months:
                bar.append(data[fid][m]["koszt_ksiegowosc"])
            bar_series2.append(bar)

        for fid in selected:
            for idx, m in enumerate(months):
                seconds = data[fid][m]["godziny_ksiegowosc"]
                hours_float = seconds / 3600
                line_series2.append(idx, hours_float)
                line_series2.setPointLabelsVisible(True)
                line_series2.setPointLabelsFormat(format_hours(seconds))

        chart_ks.addSeries(bar_series2)
        chart_ks.addSeries(line_series2)

        axis_x2 = QBarCategoryAxis()
        axis_x2.append(categories)
        chart_ks.addAxis(axis_x2, Qt.AlignmentFlag.AlignBottom)
        bar_series2.attachAxis(axis_x2)
        line_series2.attachAxis(axis_x2)

        axis_y3 = QValueAxis()
        axis_y3.setTitleText("Kwota (zł)")
        chart_ks.addAxis(axis_y3, Qt.AlignmentFlag.AlignLeft)
        bar_series2.attachAxis(axis_y3)

        axis_y4 = QValueAxis()
        axis_y4.setTitleText("Czas (godziny)")
        chart_ks.addAxis(axis_y4, Qt.AlignmentFlag.AlignRight)
        line_series2.attachAxis(axis_y4)

        self.chart_bottom.setChart(chart_ks)

    # ============================================================
    # ZAROBEK NA GODZINĘ — SŁUPKI
    # ============================================================

    def _render_profit(self, data, selected, months):
        categories = months

        chart_kadry = QChart()
        chart_kadry.setTitle("Zarobek na godzinę — Kadry")

        series_kadry = QBarSeries()

        for fid in selected:
            bar = QBarSet(f"Firma {fid}")
            for m in months:
                bar.append(data[fid][m]["profit_kadry"])
            series_kadry.append(bar)

        chart_kadry.addSeries(series_kadry)

        axis_x = QBarCategoryAxis()
        axis_x.append(categories)
        chart_kadry.addAxis(axis_x, Qt.AlignmentFlag.AlignBottom)
        series_kadry.attachAxis(axis_x)

        axis_y = QValueAxis()
        axis_y.setTitleText("Zł / godz")
        chart_kadry.addAxis(axis_y, Qt.AlignmentFlag.AlignLeft)
        series_kadry.attachAxis(axis_y)

        self.chart_top.setChart(chart_kadry)

        chart_ks = QChart()
        chart_ks.setTitle("Zarobek na godzinę — Księgowość")

        series_ks = QBarSeries()

        for fid in selected:
            bar = QBarSet(f"Firma {fid}")
            for m in months:
                bar.append(data[fid][m]["profit_ksiegowosc"])
            series_ks.append(bar)

        chart_ks.addSeries(series_ks)

        axis_x2 = QBarCategoryAxis()
        axis_x2.append(categories)
        chart_ks.addAxis(axis_x2, Qt.AlignmentFlag.AlignBottom)
        series_ks.attachAxis(axis_x2)

        axis_y2 = QValueAxis()
        axis_y2.setTitleText("Zł / godz")
        chart_ks.addAxis(axis_y2, Qt.AlignmentFlag.AlignLeft)
        series_ks.attachAxis(axis_y2)

        self.chart_bottom.setChart(chart_ks)

    # ============================================================
    # PORÓWNANIE FIRM — LINIOWY
    # ============================================================

    def _render_compare(self, data, selected, miesiac):
        chart_top = QChart()
        chart_top.setTitle(f"Kadry — porównanie firm ({miesiac})")

        chart_bottom = QChart()
        chart_bottom.setTitle(f"Księgowość — porównanie firm ({miesiac})")

        s_kadry = QLineSeries()
        s_ksieg = QLineSeries()

        x = 1
        for firma_id in selected:
            s_kadry.append(x, data[firma_id]["koszt_na_godzine_kadry"])
            s_ksieg.append(x, data[firma_id]["koszt_na_godzine_ksiegowosc"])
            x += 1

        chart_top.addSeries(s_kadry)
        chart_bottom.addSeries(s_ksieg)

        chart_top.createDefaultAxes()
        chart_bottom.createDefaultAxes()

        self.chart_top.setChart(chart_top)
        self.chart_bottom.setChart(chart_bottom)

    # ============================================================
    # EKSPORT DO EXCELA
    # ============================================================

    def _export_to_excel(self):
        import openpyxl
        from openpyxl.styles import Font
        from PyQt6.QtWidgets import QFileDialog

        mode = self.mode_combo.currentText()

        selected = [
            self.firm_list.item(i).data(Qt.ItemDataRole.UserRole)
            for i in range(self.firm_list.count())
            if self.firm_list.item(i).isSelected()
        ]

        if not selected:
            QMessageBox.warning(self, "Błąd", "Wybierz firmę przed eksportem.")
            return

        if mode == "Raport roczny":
            year = int(self.year_combo.currentText())
            months = [f"{year}-{m:02d}" for m in range(1, 13)]
            data = build_yearly_report(selected[0], year)
            data = {selected[0]: data}
        else:
            start = self.month_start.currentText()
            end = self.month_end.currentText()
            months = get_months_range(start, end)

            if mode == "Raport miesięczny":
                data = build_report(selected, months)
            elif mode == "Zarobek na godzinę":
                data = build_profit_report(selected, months)
            elif mode == "Porównanie firm":
                data = compare_companies(selected, self.month_start.currentText())
                months = [self.month_start.currentText()]

        filepath, _ = QFileDialog.getSaveFileName(
            self,
            "Zapisz raport Excel",
            "",
            "Pliki Excel (*.xlsx)"
        )
        if not filepath:
            return

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Raport"

        headers = [
            "Firma ID",
            "Miesiąc",
            "Kwota Kadry",
            "Kwota Księgowość",
            "Czas Kadry (H:MM)",
            "Czas Księgowość (H:MM)",
            "Zarobek/h Kadry",
            "Zarobek/h Księgowość"
        ]
        ws.append(headers)

        for col in range(1, len(headers) + 1):
            ws.cell(row=1, column=col).font = Font(bold=True)

        for fid in selected:
            for m in months:
                row = data[fid][m]

                godz_kadry = format_hours(row.get("godziny_kadry", 0))
                godz_ks = format_hours(row.get("godziny_ksiegowosc", 0))

                ws.append([
                    fid,
                    m,
                    row.get("koszt_kadry", row.get("profit_kadry", 0)),
                    row.get("koszt_ksiegowosc", row.get("profit_ksiegowosc", 0)),
                    godz_kadry,
                    godz_ks,
                    round(row.get("profit_kadry", row.get("koszt_na_godzine_kadry", 0)), 2),
                    round(row.get("profit_ksiegowosc", row.get("koszt_na_godzine_ksiegowosc", 0)), 2)
                ])

        try:
            wb.save(filepath)
            QMessageBox.information(self, "Sukces", f"Raport zapisany:\n{filepath}")
        except Exception as e:
            QMessageBox.critical(self, "Błąd", f"Nie udało się zapisać pliku:\n{e}")
