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

        self.tabs = QTabWidget()
        self.tab_faktury = QWidget()
        self.tab_exportowane = QWidget()

        self.tabs.addTab(self.tab_faktury, "Faktury")
        self.tabs.addTab(self.tab_exportowane, "Eksportowane")

        layout.addWidget(self.tabs)

        self._build_tab_faktury()
        self._build_tab_exportowane()

        self._refresh_faktury()
        self._refresh_exportowane()

    def _build_tab_faktury(self):
        layout = QVBoxLayout(self.tab_faktury)

        filter_row = QHBoxLayout()

        # Firma (tylko podgląd)
        self.firma_combo = QComboBox()
        self.firma_combo.addItem("Wszystkie firmy", None)
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("SELECT id, nazwa FROM firmy ORDER BY nazwa")
        for r in cur.fetchall():
            self.firma_combo.addItem(r["nazwa"], r["id"])
        conn.close()
        self.firma_combo.currentIndexChanged.connect(self._refresh_faktury)

        filter_row.addWidget(QLabel("Firma (tylko podgląd):"))
        filter_row.addWidget(self.firma_combo)

        # Miesiąc
        self.miesiac_edit = QLineEdit()
        self.miesiac_edit.setText(datetime.now().strftime("%Y-%m"))
        filter_row.addWidget(QLabel("Miesiąc:"))
        filter_row.addWidget(self.miesiac_edit)

        btn_refresh = QPushButton("Odśwież")
        btn_refresh.clicked.connect(self._refresh_faktury)
        filter_row.addWidget(btn_refresh)

        # Jeden przycisk generuj (dla wszystkich firm)
        btn_generate_all = QPushButton("Generuj faktury")
        btn_generate_all.clicked.connect(self._generate_invoices_for_month)
        filter_row.addWidget(btn_generate_all)

        btn_export_selected = QPushButton("Eksportuj zaznaczone")
        btn_export_selected.clicked.connect(self._export_selected)
        filter_row.addWidget(btn_export_selected)

        layout.addLayout(filter_row)

        # tabela faktur
        self.faktury_table = QTableWidget(0, 6)
        self.faktury_table.setHorizontalHeaderLabels([
            "ID", "Firma", "Miesiąc", "Data wystawienia", "Data sprzedaży", "Termin płatności"
        ])
        self.faktury_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.faktury_table.itemClicked.connect(self._show_invoice_positions)
        layout.addWidget(self.faktury_table)

        # pozycje
        self.positions_table = QTableWidget(0, 6)
        self.positions_table.setHorizontalHeaderLabels([
            "Nazwa", "Ilość", "Jednostka", "Netto", "VAT", "Kategoria"
        ])
        self.positions_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)

        layout.addWidget(QLabel("Pozycje faktury"))
        layout.addWidget(self.positions_table)

    def _build_tab_exportowane(self):
        layout = QVBoxLayout(self.tab_exportowane)
        self.exported_table = QTableWidget(0, 5)
        self.exported_table.setHorizontalHeaderLabels([
            "ID", "Firma", "Data wystawienia", "Data sprzedaży", "Termin płatności"
        ])
        self.exported_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.exported_table)

    def _refresh_faktury(self):
        firma_id = self.firma_combo.currentData()
        miesiac = self.miesiac_edit.text().strip()
        if not miesiac:
            return

        self.faktury_table.setRowCount(0)

        if firma_id:
            invoices = get_invoices_for_company(firma_id)
        else:
            conn = get_conn()
            cur = conn.cursor()
            cur.execute("""
                SELECT f.*, c.nazwa AS firma_nazwa
                FROM faktury f
                JOIN firmy c ON f.firma_id = c.id
                WHERE f.miesiac = ?
                ORDER BY f.firma_id, f.miesiac DESC
            """, (miesiac,))
            rows = cur.fetchall()
            conn.close()
            invoices = [dict(r) for r in rows]

        for inv in invoices:
            row = self.faktury_table.rowCount()
            self.faktury_table.insertRow(row)
            self.faktury_table.setItem(row, 0, QTableWidgetItem(str(inv.get("id", ""))))
            self.faktury_table.setItem(row, 1, QTableWidgetItem(inv.get("firma_nazwa", "")))
            self.faktury_table.setItem(row, 2, QTableWidgetItem(inv.get("miesiac", "")))
            self.faktury_table.setItem(row, 3, QTableWidgetItem(inv.get("data_wystawienia", "")))
            self.faktury_table.setItem(row, 4, QTableWidgetItem(inv.get("data_sprzedazy", "")))
            self.faktury_table.setItem(row, 5, QTableWidgetItem(inv.get("termin_platnosci", "")))

    def _refresh_exportowane(self):
        invoices = get_exported_invoices()
        self.exported_table.setRowCount(0)
        for inv in invoices:
            row = self.exported_table.rowCount()
            self.exported_table.insertRow(row)
            self.exported_table.setItem(row, 0, QTableWidgetItem(str(inv.get("id", ""))))
            self.exported_table.setItem(row, 1, QTableWidgetItem(inv.get("firma_nazwa", "")))
            self.exported_table.setItem(row, 2, QTableWidgetItem(inv.get("data_wystawienia", "")))
            self.exported_table.setItem(row, 3, QTableWidgetItem(inv.get("data_sprzedazy", "")))
            self.exported_table.setItem(row, 4, QTableWidgetItem(inv.get("termin_platnosci", "")))

    def _generate_invoices_for_month(self):
        miesiac = self.miesiac_edit.text().strip()
        if not miesiac:
            QMessageBox.information(self, "Generowanie", "Podaj miesiąc w formacie RRRR-MM.")
            return

        if QMessageBox.question(self, "Generowanie faktur",
                                f"Czy na pewno chcesz wygenerować faktury dla wszystkich firm za {miesiac}?",
                                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
                                ) != QMessageBox.StandardButton.Yes:
            return

        result = self.logic.generate_and_save_invoices_for_month(miesiac, only_active=True)

        msg = f"Przetworzono: {result.get('processed', 0)} firm.\nUtworzono: {result.get('created', 0)} faktur.\nZaktualizowano: {result.get('updated', 0)} faktur."
        if result.get("errors"):
            msg += f"\nBłędy: {len(result['errors'])}. Sprawdź logi."
        QMessageBox.information(self, "Generowanie zbiorcze", msg)

        self._refresh_faktury()
        self._refresh_exportowane()

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
        pozycje = json.loads(row["pozycje_json"] or "[]")
        self.positions_table.setRowCount(0)
        for p in pozycje:
            r = self.positions_table.rowCount()
            self.positions_table.insertRow(r)
            self.positions_table.setItem(r, 0, QTableWidgetItem(p.get("nazwa", "")))
            self.positions_table.setItem(r, 1, QTableWidgetItem(str(p.get("ilosc", ""))))
            self.positions_table.setItem(r, 2, QTableWidgetItem(p.get("jednostka", "")))
            self.positions_table.setItem(r, 3, QTableWidgetItem(f"{float(p.get('netto', 0) or 0):.2f}"))
            self.positions_table.setItem(r, 4, QTableWidgetItem(str(p.get("vat", ""))))
            self.positions_table.setItem(r, 5, QTableWidgetItem(p.get("kategoria", "")))

    def _export_selected(self):
        firma_id = self.firma_combo.currentData()
        miesiac = self.miesiac_edit.text().strip()
        if not miesiac:
            QMessageBox.information(self, "Eksport", "Podaj miesiąc w formacie RRRR-MM.")
            return

        if firma_id:
            invoices = get_invoices_for_company(firma_id)
        else:
            conn = get_conn()
            cur = conn.cursor()
            cur.execute("SELECT f.id FROM faktury f WHERE f.miesiac = ?", (miesiac,))
            rows = cur.fetchall()
            conn.close()
            invoices = [{"id": r["id"]} for r in rows]

        if not invoices:
            QMessageBox.information(self, "Eksport", "Brak faktur do eksportu.")
            return

        ids = [inv["id"] for inv in invoices]
        path, _ = QFileDialog.getSaveFileName(self, "Zapisz CSV", "", "CSV (*.csv)")
        if not path:
            return

        try:
            export_saldeo_csv(None, path, miesiac)
            mark_exported(ids)
            QMessageBox.information(self, "OK", "Faktury wyeksportowano.")
        except Exception as e:
            QMessageBox.critical(self, "Błąd", f"Nie udało się wyeksportować faktur: {e}")

        self._refresh_faktury()
        self._refresh_exportowane()
