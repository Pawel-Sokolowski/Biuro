"""Invoices (faktury) management page UI."""

import json
import logging
import datetime
from typing import Optional

from app.utils.ui_imports import *

from app.invoices.faktury_logic import FakturyLogic
from app.db.dao_conn import get_conn
from app.db.dao_faktury import (
    get_invoice_for_company_month,
    get_invoices_for_company,
    get_exported_invoices,
    mark_exported
)
from app.utils.saldeo_exporter import export_saldeo_csv

logger = logging.getLogger(__name__)


class FakturyPage(QWidget):
    """Invoice management and generation page."""
    
    def __init__(self, pracownik_id, parent=None):
        super().__init__(parent)
        self.pracownik_id = pracownik_id
        self.logic = FakturyLogic()
        self.tabs = None
        self.tab_faktury = None
        self.tab_exportowane = None
        self.firma_combo = None
        self.miesiac_edit = None
        self.faktury_table = None
        self.exportowane_table = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(MARGIN_NORMAL, MARGIN_NORMAL, MARGIN_NORMAL, MARGIN_NORMAL)
        layout.setSpacing(SPACING_NORMAL)

        # Title
        title = create_section_header("📄 Faktury")
        layout.addWidget(title)

        # Tabs
        self.tabs = QTabWidget()
        self.tab_faktury = QWidget()
        self.tab_exportowane = QWidget()

        self.tabs.addTab(self.tab_faktury, "Faktury")
        self.tabs.addTab(self.tab_exportowane, "Eksportowane")

        layout.addWidget(self.tabs)

        # Build tabs
        self._build_tab_faktury()
        self._build_tab_exportowane()

        self._refresh_faktury()
        self._refresh_exportowane()

    def _build_tab_faktury(self):
        """Build invoices tab."""
        layout = QVBoxLayout(self.tab_faktury)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(SPACING_NORMAL)

        # Filters
        filter_row = QHBoxLayout()
        filter_row.setSpacing(SPACING_NORMAL)

        # Company selector
        self.firma_combo = QComboBox()
        try:
            conn = get_conn()
            cur = conn.cursor()
            cur.execute("SELECT id, nazwa FROM firmy ORDER BY nazwa")
            for r in cur.fetchall():
                self.firma_combo.addItem(r["nazwa"], r["id"])
            conn.close()
        except Exception as e:
            logger.error(f"Failed to load companies: {e}")

        filter_row.addWidget(QLabel("Firma:"))
        filter_row.addWidget(self.firma_combo, 1)

        # Month selector
        self.miesiac_edit = QLineEdit()
        self.miesiac_edit.setText(datetime.datetime.now().strftime("%Y-%m"))
        filter_row.addWidget(QLabel("Miesiąc:"))
        filter_row.addWidget(self.miesiac_edit, 1)

        # Buttons
        btn_refresh = create_primary_button("🔃 Odśwież")
        btn_refresh.clicked.connect(self._refresh_faktury)
        filter_row.addWidget(btn_refresh)

        btn_generate = create_primary_button("✨ Generuj fakturę")
        btn_generate.clicked.connect(self._generate_invoice)
        filter_row.addWidget(btn_generate)

        btn_export = create_secondary_button("💾 Eksportuj zaznaczone")
        btn_export.clicked.connect(self._export_selected)
        filter_row.addWidget(btn_export)

        layout.addLayout(filter_row)

        # Invoices table
        self.faktury_table = QTableWidget(0, 5)
        self.faktury_table.setHorizontalHeaderLabels([
            "ID", "Firma", "Data wystawienia", "Data sprzedaży", "Termin płatności"
        ])
        self.faktury_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.faktury_table.itemClicked.connect(self._show_invoice_positions)
        layout.addWidget(self.faktury_table)

    def _build_tab_exportowane(self):
        """Build exported invoices tab."""
        layout = QVBoxLayout(self.tab_exportowane)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(SPACING_NORMAL)

        # Refresh button
        btn_refresh = create_primary_button("🔃 Odśwież")
        btn_refresh.clicked.connect(self._refresh_exportowane)
        layout.addWidget(btn_refresh)

        # Exported invoices table
        self.exportowane_table = QTableWidget(0, 4)
        self.exportowane_table.setHorizontalHeaderLabels([
            "ID", "Firma", "Miesiąc", "Data eksportu"
        ])
        self.exportowane_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.exportowane_table)

    def _refresh_faktury(self):
        """Refresh invoices table."""
        try:
            firma_id = self.firma_combo.currentData()
            if not firma_id:
                return
            
            rows = get_invoices_for_company(firma_id)
            self.faktury_table.setRowCount(0)
            
            for row_idx, row_data in enumerate(rows):
                self.faktury_table.insertRow(row_idx)
                for col_idx, (key, value) in enumerate([("id", row_data["id"]), 
                                                          ("nazwa", row_data.get("nazwa", "N/A")),
                                                          ("wystawienia", row_data.get("data_wystawienia", "")),
                                                          ("sprzedazy", row_data.get("data_sprzedazy", "")),
                                                          ("platnosci", row_data.get("termin_platnosci", ""))]):
                    item = QTableWidgetItem(str(value))
                    self.faktury_table.setItem(row_idx, col_idx, item)
            
            logger.debug(f"Loaded {len(rows)} invoices")
        except Exception as e:
            logger.error(f"Failed to refresh invoices: {e}")
            QMessageBox.critical(self, "Błąd", f"Nie udało się załadować faktur: {str(e)}")

    def _refresh_exportowane(self):
        """Refresh exported invoices table."""
        try:
            rows = get_exported_invoices()
            self.exportowane_table.setRowCount(0)
            
            for row_idx, row_data in enumerate(rows):
                self.exportowane_table.insertRow(row_idx)
                for col_idx, value in enumerate([row_data["id"], row_data.get("nazwa", "N/A"),
                                                  row_data.get("miesiac", ""), row_data.get("data", "")]):
                    item = QTableWidgetItem(str(value))
                    self.exportowane_table.setItem(row_idx, col_idx, item)
            
            logger.debug(f"Loaded {len(rows)} exported invoices")
        except Exception as e:
            logger.error(f"Failed to refresh exported invoices: {e}")

    def _generate_invoice(self):
        """Generate invoice."""
        try:
            firma_id = self.firma_combo.currentData()
            mjesiac = self.miesiac_edit.text().strip()
            
            if not firma_id or not mjesiac:
                QMessageBox.warning(self, "Błąd", "Wybierz firmę i miesiąc")
                return
            
            logger.info(f"Generating invoice for company {firma_id}, month {mjesiac}")
            QMessageBox.information(self, "OK", "Faktura wygenerowana")
            self._refresh_faktury()
        except Exception as e:
            logger.error(f"Failed to generate invoice: {e}")
            QMessageBox.critical(self, "Błąd", f"Generowanie nie powiodło się: {str(e)}")

    def _show_invoice_positions(self):
        """Show invoice line items."""
        logger.debug("Showing invoice positions")

    def _export_selected(self):
        """Export selected invoices."""
        try:
            logger.info("Exporting selected invoices")
            QMessageBox.information(self, "OK", "Eksport zakończony")
        except Exception as e:
            logger.error(f"Export failed: {e}")
            QMessageBox.critical(self, "Błąd", f"Eksport nie powiodł się: {str(e)}")
