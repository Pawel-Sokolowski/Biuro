# app/reports/reports_ui.py
from app.ui.ui_imports import *
from PyQt6.QtCharts import QChart, QChartView, QLineSeries
from PyQt6.QtGui import QPainter

from app.reports.reports_logic import (
    get_all_companies,
    get_months_range,
    build_report,
    build_yearly_report,
    compare_companies
)


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

        # --- wybór trybu ---
        mode_box = QHBoxLayout()
        self.mode_combo = QComboBox()
        self.mode_combo.addItems(["Raport miesięczny", "Raport roczny", "Porównanie firm"])
        mode_box.addWidget(QLabel("Tryb:"))
        mode_box.addWidget(self.mode_combo)
        layout.addLayout(mode_box)

        # --- wybór firm ---
        self.firm_list = QListWidget()
        self.firm_list.setSelectionMode(QListWidget.SelectionMode.MultiSelection)
        for f in get_all_companies():
            item = QListWidgetItem(f"{f['nazwa']} (ID {f['id']})")
            item.setData(Qt.ItemDataRole.UserRole, f["id"])
            self.firm_list.addItem(item)
        layout.addWidget(self.firm_list)

        # --- wybór miesięcy / roku ---
        self.month_start = QComboBox()
        self.month_end = QComboBox()
        self.year_combo = QComboBox()

        now = datetime.datetime.now()
        months = []
        for y in range(2024, now.year + 1):
            for m in range(1, 13):
                months.append(f"{y}-{m:02d}")

        self.month_start.addItems(months)
        self.month_end.addItems(months)

        self.year_combo.addItems([str(y) for y in range(2024, now.year + 1)])

        # --- dynamiczne UI ---
        self.month_box = QHBoxLayout()
        self.month_box.addWidget(QLabel("Od:"))
        self.month_box.addWidget(self.month_start)
        self.month_box.addWidget(QLabel("Do:"))
        self.month_box.addWidget(self.month_end)

        self.year_box = QHBoxLayout()
        self.year_box.addWidget(QLabel("Rok:"))
        self.year_box.addWidget(self.year_combo)

        layout.addLayout(self.month_box)
        layout.addLayout(self.year_box)

        self.mode_combo.currentTextChanged.connect(self._update_mode_ui)
        self._update_mode_ui(self.mode_combo.currentText())

        # --- przycisk ---
        btn = QPushButton("Generuj raport")
        btn.setProperty("class", "primary")
        btn.clicked.connect(self._generate_report)
        layout.addWidget(btn)

        # --- wykresy ---
        self.chart_top = QChartView()
        self.chart_top.setRenderHint(QPainter.RenderHint.Antialiasing)

        self.chart_bottom = QChartView()
        self.chart_bottom.setRenderHint(QPainter.RenderHint.Antialiasing)

        layout.addWidget(self.chart_top)
        layout.addWidget(self.chart_bottom)

    def _update_mode_ui(self, mode):
        self.month_box.setVisible(mode == "Raport miesięczny")
        self.year_box.setVisible(mode == "Raport roczny")

    def _generate_report(self):
        selected = []
        for i in range(self.firm_list.count()):
            item = self.firm_list.item(i)
            if item.isSelected():
                selected.append(item.data(Qt.ItemDataRole.UserRole))

        if not selected:
            QMessageBox.warning(self, "Brak wyboru", "Wybierz co najmniej jedną firmę.")
            return

        mode = self.mode_combo.currentText()

        if mode == "Raport miesięczny":
            start = self.month_start.currentText()
            end = self.month_end.currentText()
            months = get_months_range(start, end)
            data = build_report(selected, months)
            self._render_monthly(data, selected, months)

        elif mode == "Raport roczny":
            year = int(self.year_combo.currentText())
            if len(selected) != 1:
                QMessageBox.warning(self, "Błąd", "Raport roczny wymaga jednej firmy.")
                return
            firma_id = selected[0]
            data = build_yearly_report(firma_id, year)
            self._render_yearly(firma_id, data)

        elif mode == "Porównanie firm":
            miesiac = self.month_start.currentText()
            data = compare_companies(selected, miesiac)
            self._render_compare(data, selected, miesiac)

    # --- RENDERING ---

    def _render_monthly(self, data, selected, months):
        chart_top = QChart()
        chart_top.setTitle("Kadry — koszt na godzinę")

        chart_bottom = QChart()
        chart_bottom.setTitle("Księgowość — koszt na godzinę")

        for firma_id in selected:
            s_kadry = QLineSeries()
            s_kadry.setName(f"Firma {firma_id}")

            s_ksieg = QLineSeries()
            s_ksieg.setName(f"Firma {firma_id}")

            for m in months:
                s_kadry.append(float(m[-2:]), data[firma_id][m]["koszt_na_godzine_kadry"])
                s_ksieg.append(float(m[-2:]), data[firma_id][m]["koszt_na_godzine_ksiegowosc"])

            chart_top.addSeries(s_kadry)
            chart_bottom.addSeries(s_ksieg)

        chart_top.createDefaultAxes()
        chart_bottom.createDefaultAxes()

        self.chart_top.setChart(chart_top)
        self.chart_bottom.setChart(chart_bottom)

    def _render_yearly(self, firma_id, data):
        chart_top = QChart()
        chart_top.setTitle(f"Kadry — koszt na godzinę (rok)")

        chart_bottom = QChart()
        chart_bottom.setTitle(f"Księgowość — koszt na godzinę (rok)")

        s_kadry = QLineSeries()
        s_ksieg = QLineSeries()

        for m, v in data.items():
            s_kadry.append(float(m[-2:]), v["koszt_na_godzine_kadry"])
            s_ksieg.append(float(m[-2:]), v["koszt_na_godzine_ksiegowosc"])

        chart_top.addSeries(s_kadry)
        chart_bottom.addSeries(s_ksieg)

        chart_top.createDefaultAxes()
        chart_bottom.createDefaultAxes()

        self.chart_top.setChart(chart_top)
        self.chart_bottom.setChart(chart_bottom)

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
