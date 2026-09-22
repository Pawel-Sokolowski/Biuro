# app/invoices/faktury_ui.py
import json
from datetime import datetime

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QComboBox, QLineEdit,
    QPushButton, QTableWidget, QTableWidgetItem, QHeaderView, QFileDialog,
    QMessageBox, QTabWidget, QGridLayout, QSizePolicy, QFrame,
    QAbstractItemView, QDialog, QDialogButtonBox, QSpinBox, QFormLayout
)
from PyQt6.QtCore import Qt

from app.invoices.faktury_logic import FakturyLogic
from app.db.dao_conn import get_conn
from app.db.dao_faktury import (
    get_invoice_for_company_month,
    get_exported_invoices,
    mark_exported
)
from app.utils.saldeo_exporter import export_saldeo_xlsx


class MonthYearDialog(QDialog):
    def __init__(self, parent=None, initial_month=None, initial_year=None):
        super().__init__(parent)
        self.setWindowTitle("Wybierz miesiąc i rok")
        self.setModal(True)
        layout = QFormLayout(self)

        self.month_cb = QComboBox()
        months = [
            ("01", "Styczeń"), ("02", "Luty"), ("03", "Marzec"), ("04", "Kwiecień"),
            ("05", "Maj"), ("06", "Czerwiec"), ("07", "Lipiec"), ("08", "Sierpień"),
            ("09", "Wrzesień"), ("10", "Październik"), ("11", "Listopad"), ("12", "Grudzień")
        ]
        for code, name in months:
            self.month_cb.addItem(f"{code} - {name}", code)

        self.year_sb = QSpinBox()
        self.year_sb.setRange(2000, 2100)
        self.year_sb.setValue(initial_year or datetime.now().year)

        if initial_month:
            try:
                if "-" in initial_month:
                    parts = initial_month.split("-")
                    if len(parts[0]) == 4:
                        # YYYY-MM
                        self.year_sb.setValue(int(parts[0]))
                        self.month_cb.setCurrentIndex(int(parts[1]) - 1)
                    else:
                        # MM-YYYY
                        self.year_sb.setValue(int(parts[1]))
                        self.month_cb.setCurrentIndex(int(parts[0]) - 1)
            except Exception:
                pass

        layout.addRow("Miesiąc:", self.month_cb)
        layout.addRow("Rok:", self.year_sb)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)

    def selected(self):
        code = self.month_cb.currentData()
        year = self.year_sb.value()
        return f"{year}-{code}"


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

        # initial refresh
        self._refresh_faktury()
        self._refresh_exportowane()

    def _build_tab_faktury(self):
        layout = QVBoxLayout(self.tab_faktury)

        # top controls row
        controls = QHBoxLayout()

        # Firma filter (view only)
        self.firma_combo = QComboBox()
        self.firma_combo.addItem("Wszystkie firmy", None)
        conn = get_conn()
        conn.row_factory = None
        cur = conn.cursor()
        cur.execute("SELECT id, nazwa FROM firmy ORDER BY nazwa")
        for r in cur.fetchall():
            fid = r[0]
            name = r[1]
            self.firma_combo.addItem(name, fid)
        conn.close()
        self.firma_combo.currentIndexChanged.connect(self._refresh_faktury)
        controls.addWidget(QLabel("Firma (podgląd):"))
        controls.addWidget(self.firma_combo)

        # Month selector: button that opens MonthYearDialog
        self.month_button = QPushButton(datetime.now().strftime("%m-%Y"))
        self.month_button.setCheckable(False)
        self.month_button.clicked.connect(self._open_month_dialog)
        controls.addWidget(QLabel("Miesiąc:"))
        controls.addWidget(self.month_button)

        # Generate button
        self.btn_generate_all = QPushButton("Generuj faktury")
        self.btn_generate_all.clicked.connect(self._on_generate_clicked)
        controls.addWidget(self.btn_generate_all)

        # Refresh button
        btn_refresh = QPushButton("Odśwież")
        btn_refresh.clicked.connect(self._refresh_faktury)
        controls.addWidget(btn_refresh)

        # Export selected
        btn_export_selected = QPushButton("Eksportuj zaznaczone")
        btn_export_selected.clicked.connect(self._export_selected)
        controls.addWidget(btn_export_selected)

        controls.addStretch()
        layout.addLayout(controls)

        # table of invoices (read-only)
        self.faktury_table = QTableWidget(0, 7)
        self.faktury_table.setHorizontalHeaderLabels([
            "ID", "Firma", "Miesiąc", "Kwota netto", "Kwota brutto", "Data wystawienia", "Termin płatności"
        ])
        self.faktury_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.faktury_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.faktury_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.faktury_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.faktury_table.itemSelectionChanged.connect(self._on_invoice_selection_changed)
        layout.addWidget(self.faktury_table)

        # positions table below (read-only)
        self.positions_table = QTableWidget(0, 6)
        self.positions_table.setHorizontalHeaderLabels([
            "Nazwa", "Ilość", "Jednostka", "Netto", "VAT", "Kategoria"
        ])
        self.positions_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.positions_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.positions_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.positions_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        layout.addWidget(QLabel("Pozycje faktury"))
        layout.addWidget(self.positions_table)

    def _build_tab_exportowane(self):
        layout = QVBoxLayout(self.tab_exportowane)
        self.exported_table = QTableWidget(0, 6)
        self.exported_table.setHorizontalHeaderLabels([
            "ID", "Firma", "Miesiąc", "Kwota netto", "Kwota brutto", "Data eksportu"
        ])
        self.exported_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.exported_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        layout.addWidget(self.exported_table)

    # ---------------- month dialog handling ----------------
    def _open_month_dialog(self):
        txt = self.month_button.text().strip()
        dialog = MonthYearDialog(self, initial_month=txt, initial_year=datetime.now().year)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            sel = dialog.selected()  # "YYYY-MM"
            y, m = sel.split("-")
            self.month_button.setText(f"{m}-{y}")
            self._refresh_faktury()

    def _on_generate_clicked(self):
        # determine selected month: parse from button text (MM-YYYY)
        txt = self.month_button.text().strip()
        try:
            parts = txt.split("-")
            if len(parts) == 2:
                mm = int(parts[0])
                yyyy = int(parts[1])
                miesiac = f"{yyyy}-{mm:02d}"
            else:
                raise ValueError("Niepoprawny format")
        except Exception:
            QMessageBox.information(self, "Generowanie", "Wybierz miesiąc (kliknij przycisk i wybierz miesiąc).")
            return

        if QMessageBox.question(
            self, "Generowanie faktur",
            f"Czy na pewno chcesz wygenerować faktury dla wszystkich firm za {miesiac}?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        ) != QMessageBox.StandardButton.Yes:
            return

        try:
            result = self.logic.generate_and_save_invoices_for_month(miesiac, only_active=True)
        except Exception as e:
            import logging, traceback
            logging.getLogger("generate_invoices").exception("Unhandled exception in UI generate: %s", e)
            QMessageBox.critical(self, "Błąd",
                                 f"Wyjątek podczas generowania: {e}\nSprawdź generate_invoices_run.log i konsolę.")
            return

        msg = (
            f"Przetworzono: {result.get('processed', 0)} firm.\n"
            f"Utworzono: {result.get('created', 0)} faktur.\n"
            f"Zaktualizowano: {result.get('updated', 0)} faktur."
        )
        if result.get("errors"):
            msg += f"\nBłędy: {len(result['errors'])}. Sprawdź logi."
        QMessageBox.information(self, "Generowanie zbiorcze", msg)

        # refresh view for selected month
        self._refresh_faktury()
        self._refresh_exportowane()

    # ---------------- refresh / display ----------------
    def _refresh_faktury(self):
        firma_id = self.firma_combo.currentData()
        # determine month from button label
        txt = self.month_button.text().strip()
        miesiac = None
        try:
            parts = txt.split("-")
            if len(parts) == 2:
                mm = int(parts[0])
                yyyy = int(parts[1])
                miesiac = f"{yyyy}-{mm:02d}"
        except Exception:
            miesiac = datetime.now().strftime("%Y-%m")

        if not miesiac:
            return

        self.faktury_table.setRowCount(0)

        conn = get_conn()
        conn.row_factory = None
        cur = conn.cursor()
        if firma_id:
            cur.execute("""
                SELECT f.id, c.nazwa AS firma_nazwa, f.miesiac, f.kwota_netto, f.kwota_brutto, f.data_wystawienia, f.termin_platnosci
                FROM faktury f
                JOIN firmy c ON f.firma_id = c.id
                WHERE f.firma_id = ? AND f.miesiac = ?
                ORDER BY f.miesiac DESC, f.created_at DESC
            """, (firma_id, miesiac))
        else:
            cur.execute("""
                SELECT f.id, c.nazwa AS firma_nazwa, f.miesiac, f.kwota_netto, f.kwota_brutto, f.data_wystawienia, f.termin_platnosci
                FROM faktury f
                JOIN firmy c ON f.firma_id = c.id
                WHERE f.miesiac = ?
                ORDER BY f.firma_id, f.miesiac DESC
            """, (miesiac,))
        rows = cur.fetchall()
        conn.close()
        invoices = [dict(zip([d[0] for d in cur.description], r)) if False else None for r in rows]  # placeholder to avoid lint
        # simpler: iterate rows directly
        for inv in rows:
            row = self.faktury_table.rowCount()
            self.faktury_table.insertRow(row)
            # id
            it0 = QTableWidgetItem(str(inv[0]))
            it0.setFlags(it0.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.faktury_table.setItem(row, 0, it0)
            # firma
            it1 = QTableWidgetItem(inv[1] or "")
            it1.setFlags(it1.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.faktury_table.setItem(row, 1, it1)
            # miesiac
            it2 = QTableWidgetItem(inv[2] or "")
            it2.setFlags(it2.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.faktury_table.setItem(row, 2, it2)
            # netto
            it3 = QTableWidgetItem(str(inv[3] or ""))
            it3.setFlags(it3.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.faktury_table.setItem(row, 3, it3)
            # brutto
            it4 = QTableWidgetItem(str(inv[4] or ""))
            it4.setFlags(it4.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.faktury_table.setItem(row, 4, it4)
            # data wystawienia
            it5 = QTableWidgetItem(inv[5] or "")
            it5.setFlags(it5.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.faktury_table.setItem(row, 5, it5)
            # termin platnosci
            it6 = QTableWidgetItem(inv[6] or "")
            it6.setFlags(it6.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.faktury_table.setItem(row, 6, it6)

        # clear positions view
        self.positions_table.setRowCount(0)

    def _on_invoice_selection_changed(self):
        selected = self.faktury_table.selectedItems()
        if not selected:
            self.positions_table.setRowCount(0)
            return
        row = selected[0].row()
        id_item = self.faktury_table.item(row, 0)
        if not id_item:
            return
        try:
            inv_id = int(id_item.text())
        except Exception:
            return

        conn = get_conn()
        conn.row_factory = None
        cur = conn.cursor()
        cur.execute("SELECT pozycje_json FROM faktury WHERE id = ?", (inv_id,))
        row = cur.fetchone()
        conn.close()
        if not row:
            self.positions_table.setRowCount(0)
            return

        pozycje_json = row[0] if isinstance(row, (list, tuple)) else row.get("pozycje_json") if hasattr(row, "keys") else None
        try:
            pozycje = json.loads(pozycje_json or "[]")
        except Exception:
            pozycje = []

        self.positions_table.setRowCount(0)
        for p in pozycje:
            r = self.positions_table.rowCount()
            self.positions_table.insertRow(r)

            it0 = QTableWidgetItem(str(p.get("nazwa") or p.get("opis", "")))
            it0.setFlags(it0.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.positions_table.setItem(r, 0, it0)

            it1 = QTableWidgetItem(str(p.get("ilosc", "")))
            it1.setFlags(it1.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.positions_table.setItem(r, 1, it1)

            it2 = QTableWidgetItem(str(p.get("jednostka", "")))
            it2.setFlags(it2.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.positions_table.setItem(r, 2, it2)

            try:
                netto_val = float(p.get("netto") or p.get("wartosc_netto") or 0)
                netto_str = f"{netto_val:.2f}"
            except Exception:
                netto_str = str(p.get("netto") or p.get("wartosc_netto") or "0")
            it3 = QTableWidgetItem(netto_str)
            it3.setFlags(it3.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.positions_table.setItem(r, 3, it3)

            it4 = QTableWidgetItem(str(p.get("vat") or p.get("stawka_vat", "")))
            it4.setFlags(it4.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.positions_table.setItem(r, 4, it4)

            it5 = QTableWidgetItem(str(p.get("kategoria", "")))
            it5.setFlags(it5.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.positions_table.setItem(r, 5, it5)

    # ---------------- export ----------------
    def _export_selected(self):
        selected = self.faktury_table.selectedItems()
        if not selected:
            QMessageBox.information(self, "Eksport", "Zaznacz faktury do eksportu.")
            return
        ids = set()
        for it in selected:
            try:
                row = it.row()
                id_item = self.faktury_table.item(row, 0)
                if id_item:
                    ids.add(int(id_item.text()))
            except Exception:
                continue
        if not ids:
            QMessageBox.information(self, "Eksport", "Brak poprawnych ID.")
            return

        path, _ = QFileDialog.getSaveFileName(self, "Zapisz XLSX", "", "Excel (*.xlsx)")
        if not path:
            return

        try:
            conn = get_conn()
            conn.row_factory = None
            cur = conn.cursor()
            cur.execute("""
                SELECT f.id, c.nazwa AS firma_nazwa, f.miesiac, f.kwota_netto, f.kwota_brutto, f.pozycje_json
                FROM faktury f
                JOIN firmy c ON f.firma_id = c.id
                WHERE f.id IN ({})
            """.format(",".join("?" * len(ids))), tuple(ids))
            rows = cur.fetchall()
            conn.close()
            invoices = []
            for r in rows:
                invoices.append({
                    "id": r[0],
                    "firma_nazwa": r[1],
                    "miesiac": r[2],
                    "kwota_netto": r[3],
                    "kwota_brutto": r[4],
                    "pozycje_json": r[5]
                })
            export_saldeo_xlsx(invoices, path)
            mark_exported(list(ids))
            QMessageBox.information(self, "OK", "Faktury wyeksportowano.")
        except Exception as e:
            QMessageBox.critical(self, "Błąd", f"Nie udało się wyeksportować faktur: {e}")

        self._refresh_faktury()
        self._refresh_exportowane()

    def _refresh_exportowane(self):
        invoices = get_exported_invoices()
        self.exported_table.setRowCount(0)
        for inv in invoices:
            row = self.exported_table.rowCount()
            self.exported_table.insertRow(row)
            it0 = QTableWidgetItem(str(inv.get("numer", "")))
            it0.setFlags(it0.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.exported_table.setItem(row, 0, it0)
            it1 = QTableWidgetItem(inv.get("firma_nazwa", ""))
            it1.setFlags(it1.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.exported_table.setItem(row, 1, it1)
            it2 = QTableWidgetItem(inv.get("miesiac", ""))
            it2.setFlags(it2.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.exported_table.setItem(row, 2, it2)
            it3 = QTableWidgetItem(str(inv.get("kwota_netto", "")))
            it3.setFlags(it3.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.exported_table.setItem(row, 3, it3)
            it4 = QTableWidgetItem(str(inv.get("kwota_brutto", "")))
            it4.setFlags(it4.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.exported_table.setItem(row, 4, it4)
            it5 = QTableWidgetItem(inv.get("data_eksportu", ""))
            it5.setFlags(it5.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.exported_table.setItem(row, 5, it5)
