# app/admin/settings/pricing_kadry_settings.py

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QLabel, QTableWidget, QTableWidgetItem,
    QPushButton, QHeaderView, QFileDialog, QMessageBox, QHBoxLayout,
    QDialog, QFormLayout, QLineEdit, QComboBox, QDialogButtonBox
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont

from app.admin.global_settings_logic import GlobalSettingsLogic


class PricingKadrySettings(QWidget):
    """
    Zakładka: Cennik globalny kadry
    - podgląd
    - edycja
    - import CSV/XLSX
    - GTU + VAT
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.logic = GlobalSettingsLogic()

        layout = QVBoxLayout(self)

        title = QLabel("Cennik globalny – Kadry")
        title.setFont(QFont("Arial", 12, QFont.Weight.Bold))
        layout.addWidget(title)

        # tabela
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels([
            "Klucz", "Cena netto", "Cena brutto", "Opis", "VAT", "GTU"
        ])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.table)

        # przyciski
        btn_row = QHBoxLayout()

        btn_refresh = QPushButton("Odśwież")
        btn_refresh.clicked.connect(self._refresh)
        btn_row.addWidget(btn_refresh)

        btn_edit = QPushButton("Edytuj wybrany")
        btn_edit.clicked.connect(self._edit_selected)
        btn_row.addWidget(btn_edit)

        btn_import = QPushButton("Importuj CSV/XLSX")
        btn_import.clicked.connect(self._import)
        btn_row.addWidget(btn_import)

        btn_row.addStretch()
        layout.addLayout(btn_row)

        self._refresh()

    # ---------------------------------------------------------
    # REFRESH
    # ---------------------------------------------------------
    def _refresh(self):
        rows = self.logic.get_pricing_kadry()
        self.table.setRowCount(0)

        for r in rows:
            row = self.table.rowCount()
            self.table.insertRow(row)

            klucz = r.get("klucz", "")
            cena_netto = float(r.get("cena_netto_kadry", 0))
            cena_brutto = float(r.get("cena_brutto_kadry", cena_netto * 1.23))
            opis = r.get("opis", "")

            # VAT + GTU — pobieramy z app_settings
            vat = self.logic.get_setting("kadry_domyslny_vat") or "23%"
            gtu = self.logic.get_setting("kadry_domyslne_gtu") or "GTU_12"

            self.table.setItem(row, 0, QTableWidgetItem(str(klucz)))
            self.table.setItem(row, 1, QTableWidgetItem(f"{cena_netto:.2f}"))
            self.table.setItem(row, 2, QTableWidgetItem(f"{cena_brutto:.2f}"))
            self.table.setItem(row, 3, QTableWidgetItem(str(opis)))
            self.table.setItem(row, 4, QTableWidgetItem(vat))
            self.table.setItem(row, 5, QTableWidgetItem(gtu))

    # ---------------------------------------------------------
    # EDIT
    # ---------------------------------------------------------
    def _edit_selected(self):
        row = self.table.currentRow()
        if row < 0:
            QMessageBox.information(self, "Kadry", "Wybierz pozycję.")
            return

        klucz = self.table.item(row, 0).text()
        cena_netto = self.table.item(row, 1).text()
        cena_brutto = self.table.item(row, 2).text()
        opis = self.table.item(row, 3).text()
        vat = self.table.item(row, 4).text()
        gtu = self.table.item(row, 5).text()

        dlg = QDialog(self)
        dlg.setWindowTitle(f"Edytuj cennik kadry: {klucz}")
        form = QFormLayout(dlg)

        e_netto = QLineEdit(cena_netto)
        e_brutto = QLineEdit(cena_brutto)
        e_opis = QLineEdit(opis)

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
        form.addRow("Cena brutto:", e_brutto)
        form.addRow("Opis:", e_opis)
        form.addRow("VAT:", cb_vat)
        form.addRow("GTU:", cb_gtu)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        form.addRow(buttons)

        if dlg.exec() == QDialog.DialogCode.Accepted:
            try:
                netto_f = float(e_netto.text().replace(",", "."))
                brutto_f = float(e_brutto.text().replace(",", "."))
                opis_f = e_opis.text().strip()
                vat_f = cb_vat.currentText()
                gtu_f = cb_gtu.currentText()

                # zapis do DB
                conn = self.logic._get_conn()
                cur = conn.cursor()
                cur.execute("""
                    UPDATE cenniki_kadry
                    SET cena_netto_kadry = ?, cena_brutto_kadry = ?, opis = ?
                    WHERE klucz = ?
                """, (netto_f, brutto_f, opis_f, klucz))
                conn.commit()
                conn.close()

                # zapis VAT + GTU do app_settings
                self.logic.set_setting("kadry_domyslny_vat", vat_f)
                self.logic.set_setting("kadry_domyslne_gtu", gtu_f)

                self._refresh()
                QMessageBox.information(self, "OK", "Zapisano zmiany.")

            except Exception as e:
                QMessageBox.critical(self, "Błąd", f"Nie udało się zapisać: {e}")


    def _import(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Importuj cennik kadry",
            "",
            "Pliki cennika (*.csv *.xlsx *.xls);;CSV (*.csv);;Excel (*.xlsx *.xls)"
        )
        if not path:
            return

        try:
            self.logic.import_pricing_kadry_from_csv(path)
            self._refresh()
            QMessageBox.information(self, "OK", "Zaimportowano cennik kadry.")
        except Exception as e:
            QMessageBox.critical(self, "Błąd", f"Import nieudany: {e}")
