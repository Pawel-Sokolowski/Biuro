# app/ui/reports_page.py
import logging
import datetime

from app.utils.ui_imports import *
from app.reports.reports_logic import (
    get_all_companies,
    get_months_range,
    build_report,
    build_yearly_report,
    compare_companies,
    build_profit_report
)
from app.reports.reports_charts import create_line_chart

logger = logging.getLogger(__name__)


class ReportsPage(QWidget):

    def __init__(self, pracownik_id):
        super().__init__()
        self.pracownik_id = pracownik_id

        self.mode_combo = None
        self.firm_list = None
        self.month_start = None
        self.month_end = None
        self.year_combo = None

        self.month_box_container = None
        self.year_box_container = None

        self.result_area = None
        self.chart_container = None

        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(15)

        title = create_section_header("📊 Raporty")
        layout.addWidget(title)

        mode_box = QHBoxLayout()
        mode_box.addWidget(QLabel("Tryb:"))

        self.mode_combo = QComboBox()
        self.mode_combo.addItems([
            "Raport miesięczny",
            "Raport roczny",
            "Porównanie firm",
            "Zarobek na godzinę"
        ])
        self.mode_combo.currentTextChanged.connect(self._update_mode_ui)
        mode_box.addWidget(self.mode_combo)
        mode_box.addStretch()
        layout.addLayout(mode_box)

        layout.addWidget(QLabel("Wybierz firmy:"))
        self.firm_list = QListWidget()
        self.firm_list.setSelectionMode(QAbstractItemView.SelectionMode.MultiSelection)

        for f in get_all_companies():
            item = QListWidgetItem(f"{f['nazwa']} (ID {f['id']})")
            item.setData(Qt.ItemDataRole.UserRole, f["id"])
            self.firm_list.addItem(item)

        layout.addWidget(self.firm_list, 1)

        now = datetime.datetime.now()
        months = [f"{y}-{m:02d}" for y in range(2024, now.year + 1) for m in range(1, 13)]
        years = [str(y) for y in range(2024, now.year + 1)]

        self.month_start = QComboBox()
        self.month_start.addItems(months)
        self.month_end = QComboBox()
        self.month_end.addItems(months)
        self.year_combo = QComboBox()
        self.year_combo.addItems(years)

        self.month_box_container = QWidget()
        mb = QHBoxLayout(self.month_box_container)
        mb.addWidget(QLabel("Od:"))
        mb.addWidget(self.month_start)
        mb.addWidget(QLabel("Do:"))
        mb.addWidget(self.month_end)
        layout.addWidget(self.month_box_container)

        self.year_box_container = QWidget()
        yb = QHBoxLayout(self.year_box_container)
        yb.addWidget(QLabel("Rok:"))
        yb.addWidget(self.year_combo)
        layout.addWidget(self.year_box_container)

        self._update_mode_ui(self.mode_combo.currentText())

        btn_generate = create_primary_button("✨ Generuj raport")
        btn_generate.clicked.connect(self._generate_report)
        layout.addWidget(btn_generate)

        self.result_area = QTextEdit()
        self.result_area.setReadOnly(True)
        layout.addWidget(self.result_area, 2)

        self.chart_container = QVBoxLayout()
        layout.addLayout(self.chart_container)

    def _update_mode_ui(self, mode: str):
        mode = mode.lower()
        self.month_box_container.setVisible("miesięczny" in mode or "porównanie" in mode or "godzinę" in mode)
        self.year_box_container.setVisible("roczny" in mode)

    def _generate_report(self):
        try:
            mode = self.mode_combo.currentText()
            selected_firms = [
                self.firm_list.item(i).data(Qt.ItemDataRole.UserRole)
                for i in range(self.firm_list.count())
                if self.firm_list.item(i).isSelected()
            ]

            if not selected_firms:
                QMessageBox.warning(self, "Błąd", "Wybierz przynajmniej jedną firmę")
                return

            # czyszczenie wykresów
            while self.chart_container.count():
                item = self.chart_container.takeAt(0)
                if item.widget():
                    item.widget().deleteLater()

            if "miesięczny" in mode:
                months = get_months_range(self.month_start.currentText(), self.month_end.currentText())
                result = build_report(selected_firms, months)

                # wykresy
                for fid in selected_firms:
                    chart_kadry = create_line_chart(
                        f"Kadry — Firma {fid}",
                        result[fid],
                        "koszt_na_godzine_kadry"
                    )
                    chart_ksieg = create_line_chart(
                        f"Księgowość — Firma {fid}",
                        result[fid],
                        "koszt_na_godzine_ksiegowosc"
                    )
                    self.chart_container.addWidget(chart_kadry)
                    self.chart_container.addWidget(chart_ksieg)

            elif "roczny" in mode:
                if len(selected_firms) != 1:
                    QMessageBox.warning(self, "Błąd", "Raport roczny wymaga jednej firmy")
                    return

                fid = selected_firms[0]
                result = build_yearly_report(fid, int(self.year_combo.currentText()))

                chart_kadry = create_line_chart(f"Kadry — Firma {fid}", result, "koszt_na_godzine_kadry")
                chart_ksieg = create_line_chart(f"Księgowość — Firma {fid}", result, "koszt_na_godzine_ksiegowosc")
                self.chart_container.addWidget(chart_kadry)
                self.chart_container.addWidget(chart_ksieg)

            elif "porównanie" in mode.lower():
                month = self.month_start.currentText()
                result = compare_companies(selected_firms, month)

                # jeden punkt → wykres słupkowy byłby lepszy, ale zostawiam liniowy
                for fid in selected_firms:
                    single = {month: result[fid]}
                    chart_kadry = create_line_chart(f"Kadry — Firma {fid}", single, "koszt_na_godzine_kadry")
                    chart_ksieg = create_line_chart(f"Księgowość — Firma {fid}", single, "koszt_na_godzine_ksiegowosc")
                    self.chart_container.addWidget(chart_kadry)
                    self.chart_container.addWidget(chart_ksieg)

            elif "godzinę" in mode.lower():
                month = self.month_start.currentText()
                result = build_profit_report(selected_firms, month)

                for fid in selected_firms:
                    single = {month: result[fid]}
                    chart_kadry = create_line_chart(f"Kadry — Firma {fid}", single, "profit_kadry")
                    chart_ksieg = create_line_chart(f"Księgowość — Firma {fid}", single, "profit_ksiegowosc")
                    self.chart_container.addWidget(chart_kadry)
                    self.chart_container.addWidget(chart_ksieg)

            self.result_area.setText(str(result))

        except Exception as e:
            logger.error(f"Failed to generate report: {e}")
            QMessageBox.critical(self, "Błąd", str(e))
