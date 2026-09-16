"""Monthly view page UI - company status tracking."""

import logging
import datetime
from typing import Optional

from app.utils.ui_imports import *
from app.monthly_view.monthly_view_logic import MonthlyViewLogic

logger = logging.getLogger(__name__)

COMPANY_STAGE_OPTIONS = ["Brak", "Komplet dokumentów", "Sprawdzone", "Zaksieggowane", "Wysłane wyniki"]
COMPANY_STATUS_FILTER = ["Aktywne", "Zawieszone", "Archiwalne", "Wszystkie"]


class MonthlyViewPage(QWidget):
    """Monthly accounting status view for all companies."""
    
    def __init__(self, pracownik_id):
        super().__init__()
        self.pracownik_id = pracownik_id
        self.logic = MonthlyViewLogic(pracownik_id)

        self.month_selector = None
        self.month_employee_filter = None
        self.status_filter = None
        self.table_ksiegowosc = None
        self.table_kadry = None
        self.month_tabs = None

        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(MARGIN_NORMAL, MARGIN_NORMAL, MARGIN_NORMAL, MARGIN_NORMAL)
        layout.setSpacing(SPACING_NORMAL)

        title = create_section_header("📅 Widok miesięczny")
        layout.addWidget(title)

        header_row = QWidget()
        header_layout = QHBoxLayout(header_row)
        header_layout.setSpacing(SPACING_NORMAL)

        lbl_month = QLabel("Miesiąc rozliczeniowy:")
        set_font(lbl_month, 11, bold=True)
        header_layout.addWidget(lbl_month)

        today = datetime.date.today()

        self.month_selector = QComboBox()
        months = []

        for i in range(0, 24):
            d = (today.replace(day=1) - datetime.timedelta(days=30 * i))
            months.append(d.strftime("%Y-%m"))

        months = list(dict.fromkeys(months))
        months.sort(reverse=True)

        self.month_selector.addItems(months)
        self.month_selector.setCurrentText(today.strftime("%Y-%m"))
        self.month_selector.currentTextChanged.connect(self._change_month)
        header_layout.addWidget(self.month_selector)

        lbl_emp = QLabel("Filtr pracownika:")
        set_font(lbl_emp, 11, bold=True)
        header_layout.addWidget(lbl_emp)

        self.month_employee_filter = QComboBox()
        self.month_employee_filter.addItem("Wszyscy", None)
        self.month_employee_filter.currentIndexChanged.connect(
            lambda _: self.refresh_tables()
        )
        header_layout.addWidget(self.month_employee_filter)

        lbl_status = QLabel("Status firmy:")
        set_font(lbl_status, 11, bold=True)
        header_layout.addWidget(lbl_status)

        self.status_filter = QComboBox()
        self.status_filter.addItems(COMPANY_STATUS_FILTER)
        self.status_filter.setCurrentText("Aktywne")
        self.status_filter.currentIndexChanged.connect(
            lambda _: self.refresh_tables()
        )
        header_layout.addWidget(self.status_filter)

        btn_refresh = create_primary_button("🔃 Odśwież")
        btn_refresh.clicked.connect(self.refresh_tables)
        header_layout.addWidget(btn_refresh)

        header_layout.addStretch()
        layout.addWidget(header_row)

        self.month_tabs = QTabWidget()

        tab_ks = QWidget()
        ks_layout = QVBoxLayout(tab_ks)
        ks_layout.setContentsMargins(0, 0, 0, 0)
        ks_layout.setSpacing(SPACING_NORMAL)

        lbl_ks_header = QLabel("Księgowość – miesięczny status firm")
        set_font(lbl_ks_header, 12, bold=True)
        ks_layout.addWidget(lbl_ks_header)

        self.table_ksiegowosc = QTableWidget(0, 6)
        self.table_ksiegowosc.setHorizontalHeaderLabels([
            "ID", "Firma", "Etap", "Liczba dok.", "Czas (h:mm)", "Wartość"
        ])
        self.table_ksiegowosc.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table_ksiegowosc.cellDoubleClicked.connect(self.on_table_cell_click)
        ks_layout.addWidget(self.table_ksiegowosc)
        self.month_tabs.addTab(tab_ks, "📋 Księgowość")

        tab_kadry = QWidget()
        kadry_layout = QVBoxLayout(tab_kadry)
        kadry_layout.setContentsMargins(0, 0, 0, 0)
        kadry_layout.setSpacing(SPACING_NORMAL)

        lbl_kadry_header = QLabel("Kadry – miesięczny status firm")
        set_font(lbl_kadry_header, 12, bold=True)
        kadry_layout.addWidget(lbl_kadry_header)

        self.table_kadry = QTableWidget(0, 6)
        self.table_kadry.setHorizontalHeaderLabels([
            "ID", "Firma", "Etap", "Liczba dok.", "Czas (h:mm)", "Wartość"
        ])
        self.table_kadry.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table_kadry.cellDoubleClicked.connect(self.on_table_cell_click)
        kadry_layout.addWidget(self.table_kadry)
        self.month_tabs.addTab(tab_kadry, "💼 Kadry")

        layout.addWidget(self.month_tabs)
        self.refresh_tables()

    def _change_month(self):
        logger.debug(f"Month changed to {self.month_selector.currentText()}")
        self.refresh_tables()

    def refresh_tables(self):
        try:
            month = self.month_selector.currentText()
            status = self.status_filter.currentText()
            emp_filter = self.month_employee_filter.currentData()

            self._refresh_ksiegowosc(month, status, emp_filter)
            self._refresh_kadry(month, status, emp_filter)

            logger.info(f"Tables refreshed for {month}")
        except Exception as e:
            logger.error(f"Failed to refresh tables: {e}")
            QMessageBox.critical(self, "Błąd", f"Nie udało się odświeżyć tabel: {str(e)}")

    def _refresh_ksiegowosc(self, month: str, status: str, emp_id: Optional[int]):
        try:
            rows = self.logic.get_monthly_status(month, "ksiegowosc", status, emp_id)
            self.table_ksiegowosc.setRowCount(0)

            for row_idx, row_data in enumerate(rows):
                self.table_ksiegowosc.insertRow(row_idx)
                for col_idx, value in enumerate(row_data):
                    item = QTableWidgetItem(str(value))
                    self.table_ksiegowosc.setItem(row_idx, col_idx, item)

            logger.debug(f"Loaded {len(rows)} accounting rows")
        except Exception as e:
            logger.error(f"Failed to refresh accounting table: {e}")

    def _refresh_kadry(self, month: str, status: str, emp_id: Optional[int]):
        try:
            rows = self.logic.get_monthly_status(month, "kadry", status, emp_id)
            self.table_kadry.setRowCount(0)

            for row_idx, row_data in enumerate(rows):
                self.table_kadry.insertRow(row_idx)
                for col_idx, value in enumerate(row_data):
                    item = QTableWidgetItem(str(value))
                    self.table_kadry.setItem(row_idx, col_idx, item)

            logger.debug(f"Loaded {len(rows)} HR rows")
        except Exception as e:
            logger.error(f"Failed to refresh HR table: {e}")

    def on_table_cell_click(self, row: int, col: int):
        logger.debug(f"Table cell clicked: row={row}, col={col}")
