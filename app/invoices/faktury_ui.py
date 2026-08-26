# app/faktury/faktury_ui.py
import json
from datetime import datetime

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QComboBox, QLineEdit,
    QPushButton, QTableWidget, QTableWidgetItem, QHeaderView, QFileDialog,
    QMessageBox, QTabWidget
)
from PyQt6.QtCore import Qt

from app.invoices.faktury_logic import FakturyLogic
from app.db.dao_conn import get_conn
from app.db.dao_faktury import (
    get_invoice_for_company_month,
    get_invoices_for_company,
    get_exported_invoices,
    mark_exported
)
from app.utils.saldeo_exporter import export_saldeo_csv


class FakturyPage(QWidget):
    def __init__(self, pracownik_id, parent=None):
        super().__init__(parent)
        self.pracownik_id = pracownik_id
        self.logic = FakturyLogic()

        layout = QVBoxLayout(self)

        # ============================================================
        #  ZAKŁADKI
        # ============================================================
        self.tabs = QTabWidget()
        self.tab_faktury = QWidget()
        self.tab_exportowane = QWidget()

        self.tabs.addTab(self.tab_faktury, "Faktury")
        self.tabs.addTab(self.tab_exportowane, "Eksportowane")

        layout.addWidget(self.tabs)

        # ============================================================
        #  BUDOWANIE ZAKŁADEK
        # ============================================================
        self._build_tab_faktury()
        self._build_tab_exportowane()

        self._refresh_faktury()
        self._refresh_exportowane()

    # ============================================================
    #  ZAKŁADKA FAKTURY
    # ============================================================
    def _build_tab_faktury(self):
        layout = QVBoxLayout(self.tab_faktury)

        # FILTRY
        filter_row = QHBoxLayout()

        # Firma
        self.firma_combo = QComboBox()
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("SELECT id, nazwa FROM firmy ORDER BY nazwa")
        for r in cur.fetchall():
            self.firma_combo.addItem(r["nazwa"], r["id"])
        conn.close()

        filter_row.addWidget(QLabel("Firma:"))
        filter_row.addWidget(self.firma_combo)

        # Miesiąc (domyślnie bieżący)
        self.miesiac_edit = QLineEdit()
        self.miesiac_edit.setText(datetime.now().strftime("%Y-%m"))
        filter_row.addWidget(QLabel("Miesiąc:"))
        filter_row.addWidget(self.miesiac_edit)

        btn_refresh = QPushButton("Odśwież")
        btn_refresh.clicked.connect(self._refresh_faktury)
        filter_row.addWidget(btn_refresh)

        btn_generate = QPushButton("Generuj fakturę")
        btn_generate.clicked.connect(self._generate_invoice)
        filter_row.addWidget(btn_generate)

        btn_export_selected = QPushButton("Eksportuj zaznaczone")
        btn_export_selected.clicked.connect(self._export_selected)
        filter_row.addWidget(btn_export_selected)

        layout.addLayout(filter_row)

        # Tabela faktur
        self.faktury_table = QTableWidget(0, 5)
        self.faktury_table.setHorizontalHeaderLabels([
            "ID", "Firma", "Data wystawienia", "Data sprzedaży", "Termin płatności"
        ])
        self.faktury_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.faktury_table.itemClicked.connect(self._show_invoice_positions)

        layout.addWidget(self.faktury_table)

        # Pozycje faktury (pod fakturą)
        self.positions_table = QTableWidget(0, 6)
        self.positions_table.setHorizontalHeaderLabels([
            "Nazwa", "Ilość", "Jednostka", "Netto", "VAT", "Kategoria"
        ])
        self.positions_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)

        layout.addWidget(QLabel("Pozycje faktury"))
        layout.addWidget(self.positions_table)

    # ============================================================
    #  ZAKŁADKA EKSPORTOWANE
    # ============================================================
    def _build_tab_exportowane(self):
        layout = QVBoxLayout(self.tab_exportowane)

        self.exported_table = QTableWidget(0, 5)
        self.exported_table.setHorizontalHeaderLabels([
            "ID", "Firma", "Data wystawienia", "Data sprzedaży", "Termin płatności"
        ])
        self.exported_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)

        layout.addWidget(self.exported_table)

    # ============================================================
    #  ODSWIEŻANIE FAKTUR
    # ============================================================
    def _refresh_faktury(self):
        firma_id = self.firma_combo.currentData()
        miesiac = self.miesiac_edit.text().strip()

        if not firma_id or not miesiac:
            return

        invoices = get_invoices_for_company(firma_id)

        self.faktury_table.setRowCount(0)

        for inv in invoices:
            row = self.faktury_table.rowCount()
            self.faktury_table.insertRow(row)

            self.faktury_table.setItem(row, 0, QTableWidgetItem(str(inv["id"])))
            self.faktury_table.setItem(row, 1, QTableWidgetItem(inv["firma_nazwa"]))
            self.faktury_table.setItem(row, 2, QTableWidgetItem(inv["data_wystawienia"]))
            self.faktury_table.setItem(row, 3, QTableWidgetItem(inv["data_sprzedazy"]))
            self.faktury_table.setItem(row, 4, QTableWidgetItem(inv["termin_platnosci"]))

    # ============================================================
    #  ODSWIEŻANIE EKSPORTOWANYCH
    # ============================================================
    def _refresh_exportowane(self):
        invoices = get_exported_invoices()

        self.exported_table.setRowCount(0)

        for inv in invoices:
            row = self.exported_table.rowCount()
            self.exported_table.insertRow(row)

            self.exported_table.setItem(row, 0, QTableWidgetItem(str(inv["id"])))
            self.exported_table.setItem(row, 1, QTableWidgetItem(inv["firma_nazwa"]))
            self.exported_table.setItem(row, 2, QTableWidgetItem(inv["data_wystawienia"]))
            self.exported_table.setItem(row, 3, QTableWidgetItem(inv["data_sprzedazy"]))
            self.exported_table.setItem(row, 4, QTableWidgetItem(inv["termin_platnosci"]))

    # ============================================================
    #  GENEROWANIE FAKTURY
    # ============================================================
    def _generate_invoice(self):
        firma_id = self.firma_combo.currentData()
        miesiac = self.miesiac_edit.text().strip()

        invoice = self.logic.generate_and_save_invoice(firma_id, miesiac)
        QMessageBox.information(self, "OK", f"Faktura wygenerowana (ID: {invoice['id']}).")

        self._refresh_faktury()

    # ============================================================
    #  WYŚWIETLANIE POZYCJI POD FAKTURĄ
    # ============================================================
    def _show_invoice_positions(self, item):
        row = item.row()
        inv_id = int(self.faktury_table.item(row, 0).text())

        conn = get_conn()
        cur = conn.cursor()
        cur.execute("SELECT pozycje_json FROM faktury WHERE id = ?", (inv_id,))
        row = cur.fetchone()
        conn.close()

        if not row:
            return

        pozycje = json.loads(row["pozycje_json"])

        self.positions_table.setRowCount(0)

        for p in pozycje:
            r = self.positions_table.rowCount()
            self.positions_table.insertRow(r)

            self.positions_table.setItem(r, 0, QTableWidgetItem(p.get("nazwa", "")))
            self.positions_table.setItem(r, 1, QTableWidgetItem(str(p.get("ilosc", ""))))
            self.positions_table.setItem(r, 2, QTableWidgetItem(p.get("jednostka", "")))
            self.positions_table.setItem(r, 3, QTableWidgetItem(f"{p.get('netto', 0):.2f}"))
            self.positions_table.setItem(r, 4, QTableWidgetItem(str(p.get("vat", ""))))
            self.positions_table.setItem(r, 5, QTableWidgetItem(p.get("kategoria", "")))

    # ============================================================
    #  EKSPORT ZAZNACZONYCH
    # ============================================================
    def _export_selected(self):
        firma_id = self.firma_combo.currentData()
        miesiac = self.miesiac_edit.text().strip()

        invoices = get_invoices_for_company(firma_id)
        ids = [inv["id"] for inv in invoices]

        path, _ = QFileDialog.getSaveFileName(self, "Zapisz CSV", "", "CSV (*.csv)")
        if not path:
            return

        export_saldeo_csv(None, path, miesiac)
        mark_exported(ids)

        QMessageBox.information(self, "OK", "Wybrane faktury wyeksportowano.")

        self._refresh_faktury()
        self._refresh_exportowane()
