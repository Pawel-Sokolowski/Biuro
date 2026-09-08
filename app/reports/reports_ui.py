"""Reports page UI - analytics and reporting."""

import logging
import datetime
from typing import Optional

from app.utils.ui_imports import *

from app.reports.reports_logic import (
    get_all_companies,
    get_months_range,
    build_report,
    build_yearly_report,
    compare_companies,
    build_profit_report
)

logger = logging.getLogger(__name__)


def format_hours(seconds: float) -> str:
    """Convert seconds to H:MM format."""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    return f"{h}:{m:02d}"


class ReportsPage(QWidget):
    """Analytics and reporting page."""
    
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
        
        self._build_ui()

    def _build_ui(self):
        """Build reports page UI."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(MARGIN_NORMAL, MARGIN_NORMAL, MARGIN_NORMAL, MARGIN_NORMAL)
        layout.setSpacing(SPACING_NORMAL)

        # Title
        title = create_section_header("📊 Raporty")
        layout.addWidget(title)

        # Mode selector
        mode_box = QHBoxLayout()
        mode_box.setSpacing(SPACING_NORMAL)
        
        lbl_mode = QLabel("Tryb:")
        set_font(lbl_mode, 11, bold=True)
        mode_box.addWidget(lbl_mode)
        
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

        # Company list
        layout.addWidget(QLabel("Wybierz firmy:"))
        self.firm_list = QListWidget()
        self.firm_list.setSelectionMode(QAbstractItemView.SelectionMode.MultiSelection)
        try:
            for f in get_all_companies():
                item = QListWidgetItem(f"{f['nazwa']} (ID {f['id']})")
                item.setData(Qt.ItemDataRole.UserRole, f["id"])
                self.firm_list.addItem(item)
        except Exception as e:
            logger.error(f"Failed to load companies: {e}")
        layout.addWidget(self.firm_list, 1)

        # Date selectors
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
        self.month_box = QHBoxLayout(self.month_box_container)
        self.month_box.setSpacing(SPACING_NORMAL)
        self.month_box.addWidget(QLabel("Od:"))
        self.month_box.addWidget(self.month_start, 1)
        self.month_box.addWidget(QLabel("Do:"), 0)
        self.month_box.addWidget(self.month_end, 1)
        layout.addWidget(self.month_box_container)

        self.year_box_container = QWidget()
        self.year_box = QHBoxLayout(self.year_box_container)
        self.year_box.setSpacing(SPACING_NORMAL)
        self.year_box.addWidget(QLabel("Rok:"))
        self.year_box.addWidget(self.year_combo, 1)
        self.year_box.addStretch()
        layout.addWidget(self.year_box_container)

        self._update_mode_ui(self.mode_combo.currentText())

        # Generate button
        btn_generate = create_primary_button("✨ Generuj raport")
        btn_generate.clicked.connect(self._generate_report)
        layout.addWidget(btn_generate)

        # Export button
        btn_export = create_secondary_button(💼 Eksport do Excel")
        btn_export.clicked.connect(self._export_to_excel)
        layout.addWidget(btn_export)

        # Result area
        self.result_area = QTextEdit()
        self.result_area.setReadOnly(True)
        layout.addWidget(QLabel("Wynik:"), 0)
        layout.addWidget(self.result_area, 2)

    def _update_mode_ui(self, mode: str):
        """Update UI based on selected report mode."""
        self.month_box_container.setVisible("miesięczny" in mode.lower())
        self.year_box_container.setVisible("roczny" in mode.lower())
        logger.debug(f"Report mode changed to: {mode}")

    def _generate_report(self):
        """Generate and display report."""
        try:
            mode = self.mode_combo.currentText()
            selected_firms = [self.firm_list.item(i).data(Qt.ItemDataRole.UserRole) 
                            for i in range(self.firm_list.count()) 
                            if self.firm_list.item(i).isSelected()]

            if not selected_firms:
                QMessageBox.warning(self, "Błąd", "Wybierz przynajmniej jedną firmę")
                return

            result = "Generowanie raportu..."
            
            if "miesięczny" in mode:
                result = build_report(selected_firms, self.month_start.currentText())
            elif "roczny" in mode:
                result = build_yearly_report(selected_firms, self.year_combo.currentText())
            elif "Porównanie" in mode:
                result = compare_companies(selected_firms, self.month_start.currentText())
            elif "godzinę" in mode:
                result = build_profit_report(selected_firms)

            self.result_area.setText(str(result))
            logger.info(f"Report generated: {mode}")
        except Exception as e:
            logger.error(f"Failed to generate report: {e}")
            QMessageBox.critical(self, "Błąd", f"Nie udało się wygenerować raportu: {str(e)}")

    def _export_to_excel(self):
        """Export report to Excel."""
        try:
            file_path, _ = QFileDialog.getSaveFileName(self, "Zapisz raport", "", "Excel Files (*.xlsx)")
            if file_path:
                # TODO: Implement Excel export
                QMessageBox.information(self, "Info", "Funkcja eksportu jest w przygotowaniu")
                logger.info(f"Export initiated to {file_path}")
        except Exception as e:
            logger.error(f"Export failed: {e}")
            QMessageBox.critical(self, "Błąd", f"Eksport nie powiodł się: {str(e)}")
