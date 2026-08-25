# app/faktury/faktury_ui.py
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QComboBox, QLineEdit,
    QPushButton, QTableWidget, QTableWidgetItem, QHeaderView, QFileDialog,
    QMessageBox
)
from PyQt6.QtCore import Qt

from app.faktury.faktury_logic import FakturyLogic
from app.db.dao_conn import get_conn
from app.db.dao_faktury import get_invoice_for_company_month
from app.utils.saldeo_exporter import export_saldeo_csv


class FakturyPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)

        self.logic = FakturyLogic()

        layout = QVBoxLayout(self)

        # ============================================================
        # FILTRY
        # ============================================================
        filter_row = QHBoxLayout()

        # Firma (autouzupełnianie)
        self.firma_combo = QComboBox()
        self.firma_combo.setEditable(True)

        conn = get_conn()
        cur = conn.cursor()
        cur.execute("SELECT id, nazwa FROM firmy ORDER BY nazwa")
        for r in cur.fetchall():
            self.firma_combo.addItem(r["nazwa"], r["id"])
        conn.close()

        filter_row.addWidget(QLabel("Firma:"))
        filter_row.addWidget(self.firma_combo)

        # Miesiąc
        self.miesiac_edit = QLineEdit()
        self.miesiac_edit.setPlaceholderText("YYYY-MM")
        filter_row.addWidget(QLabel("Miesiąc:"))
        filter_row.addWidget(self.miesiac_edit)

        # Odśwież
        btn_refresh = QPushButton("Odśwież")
        btn_refresh.clicked.connect(self.refresh_tables)
        filter_row.addWidget(btn_refresh)

        # Generuj fakturę
        btn_generate = QPushButton("Generuj fakturę")
        btn_generate.clicked.connect(self.generate_invoice)
        filter_row.addWidget(btn_generate)

        # Eksport do Saldeo
        btn_export = QPushButton("Eksport do Saldeo")
        btn_export.clicked.connect(self.export_saldeo)
        filter_row.addWidget(btn_export)

        filter_row.addStretch(1)
        layout.addLayout(filter_row)

        # ============================================================
        # TABELA POZYCJI FAKTURY
        # ============================================================
        self.positions_table = QTableWidget(0, 5)
        self.positions_table.setHorizontalHeaderLabels([
            "Nazwa pozycji", "Ilość", "Jednostka", "Netto", "VAT"
        ])
        self.positions_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)

        layout.addWidget(QLabel("Pozycje faktury"))
        layout.addWidget(self.positions_table)

        # ============================================================
        # TABELA FAKTUR (zapisane)
        # ============================================================
        self.invoices_table = QTableWidget(0, 4)
        self.invoices_table.setHorizontalHeaderLabels([
            "ID", "Miesiąc", "Netto", "Brutto"
        ])
        self.invoices_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)

        layout.addWidget(QLabel("Zapisane faktury"))
        layout.addWidget(self.invoices_table)

    # ============================================================
    # Odświeżanie tabel
    # ============================================================
    def refresh_tables(self):
        firma_id = self.firma_combo.currentData()
        miesiac = self.miesiac_edit.text().strip()

        if not firma_id or not miesiac:
            return

        # Pozycje faktury (generowane)
        pozycje = self.logic.generate_invoice_positions(firma_id, miesiac)

        self.positions_table.setRowCount(0)
        for p in pozycje:
            row = self.positions_table.rowCount()
            self.positions_table.insertRow(row)

            self.positions_table.setItem(row, 0, QTableWidgetItem(p["nazwa"]))
            self.positions_table.setItem(row, 1, QTableWidgetItem(str(p["ilosc"])))
            self.positions_table.setItem(row, 2, QTableWidgetItem(p["jednostka"]))
            self.positions_table.setItem(row, 3, QTableWidgetItem(f"{p['netto']:.2f}"))
            self.positions_table.setItem(row, 4, QTableWidgetItem(f"{p['vat']}%"))

        # Zapisana faktura
        invoice = get_invoice_for_company_month(firma_id, miesiac)

        self.invoices_table.setRowCount(0)
        if invoice:
            row = self.invoices_table.rowCount()
            self.invoices_table.insertRow(row)

            self.invoices_table.setItem(row, 0, QTableWidgetItem(str(invoice["id"])))
            self.invoices_table.setItem(row, 1, QTableWidgetItem(invoice["miesiac"]))
            self.invoices_table.setItem(row, 2, QTableWidgetItem(f"{invoice['kwota_netto']:.2f}"))
            self.invoices_table.setItem(row, 3, QTableWidgetItem(f"{invoice['kwota_brutto']:.2f}"))

    # ============================================================
    # Generowanie faktury
    # ============================================================
    def generate_invoice(self):
        firma_id = self.firma_combo.currentData()
        miesiac = self.miesiac_edit.text().strip()

        if not firma_id or not miesiac:
            QMessageBox.warning(self, "Błąd", "Wybierz firmę i miesiąc.")
            return

        invoice = self.logic.generate_and_save_invoice(firma_id, miesiac)

        QMessageBox.information(self, "OK", f"Faktura wygenerowana (ID: {invoice['id']}).")
        self.refresh_tables()

    # ============================================================
    # Eksport do Saldeo
    # ============================================================
    def export_saldeo(self):
        miesiac = self.miesiac_edit.text().strip()
        if not miesiac:
            QMessageBox.warning(self, "Błąd", "Podaj miesiąc.")
            return

        path, _ = QFileDialog.getSaveFileName(self, "Zapisz CSV", "", "CSV (*.csv)")
        if not path:
            return

        try:
            export_saldeo_csv(None, path, miesiac)
            QMessageBox.information(self, "OK", "Eksport zakończony.")
        except Exception as e:
            QMessageBox.critical(self, "Błąd", str(e))
