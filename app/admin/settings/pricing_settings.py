# app/admin/settings/pricing_settings.py

from app.utils.parsing import to_float
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QLabel, QTableWidget, QTableWidgetItem,
    QPushButton, QHeaderView, QFileDialog, QMessageBox, QHBoxLayout,
    QDialog, QFormLayout, QLineEdit, QComboBox, QDialogButtonBox
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont

from app.admin.global_settings_logic import GlobalSettingsLogic


class PricingSettings(QWidget):
    """Cennik księgowości: podgląd, edycja, import."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.logic = GlobalSettingsLogic()

        layout = QVBoxLayout(self)

        title = QLabel("Cennik globalny – Księgowość")
        title.setFont(QFont("Arial", 12, QFont.Weight.Bold))
        layout.addWidget(title)

        self.table = QTableWidget(0, 8)
        self.table.setHorizontalHeaderLabels([
            "Klucz", "Cena netto", "Dok. ponad limit", "Zdrowotna",
            "Netto", "Brutto", "VAT", "GTU"
        ])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.table)

        btn_row = QHBoxLayout()

        btn_refresh = QPushButton("Odśwież")
        btn_refresh.clicked.connect(self._refresh)
        btn_row.addWidget(btn_refresh)

        btn_add = QPushButton("Dodaj pozycję")
        btn_add.clicked.connect(self._add_item)
        btn_row.addWidget(btn_add)

        btn_edit = QPushButton("Edytuj wybrany")
        btn_edit.clicked.connect(self._edit_selected)
        btn_row.addWidget(btn_edit)

        btn_import = QPushButton("Importuj CSV/XLSX")
        btn_import.clicked.connect(self._import)
        btn_row.addWidget(btn_import)

        btn_row.addStretch()
        layout.addLayout(btn_row)

        self._refresh()

    def _get_field(self, obj, key, default=None):
        """Defensywny getter: działa z Row i dict."""
        if obj is None:
            return default
        if isinstance(obj, dict):
            return obj.get(key, default)
        try:
            return obj[key]
        except Exception:
            try:
                # Row może wspierać .keys()
                return obj.get(key, default)
            except Exception:
                return default

    _to_float = staticmethod(to_float)

    def _refresh(self):
        rows = self.logic.get_global_pricing()
        self.table.setRowCount(0)

        vat_default = self.logic.get_setting("ksiegowosc_domyslny_vat") or "23%"
        gtu_default = self.logic.get_setting("ksiegowosc_domyslne_gtu") or "GTU_12"

        for r in rows:
            row = self.table.rowCount()
            self.table.insertRow(row)

            klucz = str(self._get_field(r, "klucz", "") or "")
            cena_netto = self._to_float(self._get_field(r, "cena_netto_ksiegowosc", 0.0))
            ponad = self._to_float(self._get_field(r, "cena_za_dokument_ponad_limit", 0.0))
            zdrowotna = self._to_float(self._get_field(r, "zdrowotna", 0.0))
            netto = self._to_float(self._get_field(r, "netto", cena_netto + zdrowotna))
            brutto = self._to_float(self._get_field(r, "brutto", round(netto * 1.23, 2)))

            self.table.setItem(row, 0, QTableWidgetItem(str(klucz)))
            self.table.setItem(row, 1, QTableWidgetItem(f"{cena_netto:.2f}"))
            self.table.setItem(row, 2, QTableWidgetItem(f"{ponad:.2f}"))
            self.table.setItem(row, 3, QTableWidgetItem(f"{zdrowotna:.2f}"))
            self.table.setItem(row, 4, QTableWidgetItem(f"{netto:.2f}"))
            self.table.setItem(row, 5, QTableWidgetItem(f"{brutto:.2f}"))
            self.table.setItem(row, 6, QTableWidgetItem(vat_default))
            self.table.setItem(row, 7, QTableWidgetItem(gtu_default))

    def _add_item(self):
        dlg = QDialog(self)
        dlg.setWindowTitle("Nowa pozycja cennika")
        form = QFormLayout(dlg)
        e_klucz = QLineEdit()
        e_netto = QLineEdit("0")
        e_ponad = QLineEdit("0")
        e_zdrow = QLineEdit("0")
        form.addRow("Klucz:", e_klucz)
        form.addRow("Cena netto:", e_netto)
        form.addRow("Dok. ponad limit:", e_ponad)
        form.addRow("Zdrowotna:", e_zdrow)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        form.addRow(buttons)

        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        klucz = e_klucz.text().strip()
        if not klucz:
            QMessageBox.warning(self, "Cennik", "Podaj klucz.")
            return
        try:
            netto = float(e_netto.text().replace(",", ".") or 0)
            ponad = float(e_ponad.text().replace(",", ".") or 0)
            zdrow = float(e_zdrow.text().replace(",", ".") or 0)
            vat_txt = (self.logic.get_setting("ksiegowosc_domyslny_vat") or "23").replace("%", "")
            try:
                mult = 1 + float(vat_txt) / 100
            except ValueError:
                mult = 1.23
            suma = round(netto + zdrow, 2)
            self.logic.add_global_pricing_item(
                klucz=klucz,
                cena_netto_ksiegowosc=netto,
                cena_za_dokument_ponad_limit=ponad,
                zdrowotna=zdrow,
                netto=suma,
                brutto=round(suma * mult, 2),
            )
            self._refresh()
        except Exception as e:
            QMessageBox.critical(self, "Błąd", f"Nie udało się dodać (czy klucz już istnieje?): {e}")

    def _edit_selected(self):
        row = self.table.currentRow()
        if row < 0:
            QMessageBox.information(self, "Cennik", "Wybierz pozycję.")
            return

        klucz_item = self.table.item(row, 0)
        if not klucz_item:
            QMessageBox.critical(self, "Błąd", "Nieprawidłowy wiersz.")
            return
        klucz = klucz_item.text()

        cena_netto = self.table.item(row, 1).text() if self.table.item(row, 1) else "0"
        ponad = self.table.item(row, 2).text() if self.table.item(row, 2) else "0"
        zdrowotna = self.table.item(row, 3).text() if self.table.item(row, 3) else "0"
        netto = self.table.item(row, 4).text() if self.table.item(row, 4) else "0"
        brutto = self.table.item(row, 5).text() if self.table.item(row, 5) else "0"
        vat = self.table.item(row, 6).text() if self.table.item(row, 6) else (self.logic.get_setting("ksiegowosc_domyslny_vat") or "23%")
        gtu = self.table.item(row, 7).text() if self.table.item(row, 7) else (self.logic.get_setting("ksiegowosc_domyslne_gtu") or "GTU_12")

        dlg = QDialog(self)
        dlg.setWindowTitle(f"Edytuj cennik: {klucz}")
        form = QFormLayout(dlg)

        e_netto = QLineEdit(cena_netto)
        e_ponad = QLineEdit(ponad)
        e_zdrow = QLineEdit(zdrowotna)
        e_nettosum = QLineEdit(netto)
        e_brutto = QLineEdit(brutto)

        cb_vat = QComboBox()
        cb_vat.addItems(["23%", "8%", "5%", "0%", "zw", "np", "np UE oo", "0 WDT", "0 EX"])
        cb_vat.setCurrentText(vat)

        cb_gtu = QComboBox()
        cb_gtu.addItems([
            "GTU_01", "GTU_02", "GTU_03", "GTU_04", "GTU_05",
            "GTU_06", "GTU_07", "GTU_08", "GTU_09", "GTU_10",
            "GTU_11", "GTU_12", "GTU_13"
        ])
        cb_gtu.setCurrentText(gtu)

        form.addRow("Cena netto:", e_netto)
        form.addRow("Dok. ponad limit:", e_ponad)
        form.addRow("Zdrowotna:", e_zdrow)
        form.addRow("Netto suma:", e_nettosum)
        form.addRow("Brutto:", e_brutto)
        form.addRow("VAT:", cb_vat)
        form.addRow("GTU:", cb_gtu)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        form.addRow(buttons)

        if dlg.exec() == QDialog.DialogCode.Accepted:
            try:
                netto_f = float(e_netto.text().replace(",", "."))
                ponad_f = float(e_ponad.text().replace(",", "."))
                zdrow_f = float(e_zdrow.text().replace(",", "."))
                nettosum_f = float(e_nettosum.text().replace(",", "."))
                brutto_f = float(e_brutto.text().replace(",", "."))

                self.logic.update_global_pricing_item(
                    klucz=klucz,
                    cena_netto_ksiegowosc=netto_f,
                    cena_za_dokument_ponad_limit=ponad_f,
                    zdrowotna=zdrow_f,
                    netto=nettosum_f,
                    brutto=brutto_f,
                )

                self.logic.set_setting("ksiegowosc_domyslny_vat", cb_vat.currentText())
                self.logic.set_setting("ksiegowosc_domyslne_gtu", cb_gtu.currentText())

                self._refresh()
                QMessageBox.information(self, "OK", "Zapisano zmiany.")

            except Exception as e:
                QMessageBox.critical(self, "Błąd", f"Nie udało się zapisać: {e}")

    def _import(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Importuj cennik księgowości",
            "",
            "Pliki cennika (*.csv *.xlsx *.xls);;CSV (*.csv);;Excel (*.xlsx *.xls)"
        )
        if not path:
            return

        try:
            self.logic.import_global_pricing_from_csv(path)
            self._refresh()
            QMessageBox.information(self, "OK", "Zaimportowano cennik księgowości.")
        except Exception as e:
            QMessageBox.critical(self, "Błąd", f"Import nieudany: {e}")
