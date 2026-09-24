# app/admin/settings/extra_services_settings.py

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QLabel, QTableWidget, QTableWidgetItem,
    QPushButton, QHeaderView, QFileDialog, QMessageBox, QHBoxLayout,
    QDialog, QFormLayout, QLineEdit, QComboBox, QDialogButtonBox
)
from PyQt6.QtGui import QFont

from app.admin.global_settings_logic import GlobalSettingsLogic


class ExtraServicesSettings(QWidget):
    """Usługi dodatkowe: podgląd, edycja, dodawanie, import."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.logic = GlobalSettingsLogic()

        layout = QVBoxLayout(self)

        title = QLabel("Usługi dodatkowe")
        title.setFont(QFont("Arial", 12, QFont.Weight.Bold))
        layout.addWidget(title)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels([
            "Nazwa", "Cena netto", "VAT", "GTU", "Kategoria"
        ])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.table)

        btn_row = QHBoxLayout()

        btn_refresh = QPushButton("Odśwież")
        btn_refresh.clicked.connect(self._refresh)
        btn_row.addWidget(btn_refresh)

        btn_add = QPushButton("Dodaj usługę")
        btn_add.clicked.connect(self._add)
        btn_row.addWidget(btn_add)

        btn_edit = QPushButton("Edytuj wybraną")
        btn_edit.clicked.connect(self._edit_selected)
        btn_row.addWidget(btn_edit)

        btn_import = QPushButton("Importuj CSV/XLSX")
        btn_import.clicked.connect(self._import)
        btn_row.addWidget(btn_import)

        btn_row.addStretch()
        layout.addLayout(btn_row)

        self._refresh()

    def _refresh(self):
        rows = self.logic.get_extra_services()
        self.table.setRowCount(0)

        vat_default = self.logic.get_setting("uslugi_domyslny_vat") or "23%"
        gtu_default = self.logic.get_setting("uslugi_domyslne_gtu") or "GTU_12"

        for r in rows:
            row = self.table.rowCount()
            self.table.insertRow(row)

            nazwa = r["nazwa"] if "nazwa" in r.keys() else ""
            cena = float(r["cena_netto"]) if "cena_netto" in r.keys() else 0
            vat = r["stawka_vat"] if "stawka_vat" in r.keys() else vat_default
            gtu = gtu_default
            kat = r["kategoria"] if "kategoria" in r.keys() else ""

            self.table.setItem(row, 0, QTableWidgetItem(nazwa))
            self.table.setItem(row, 1, QTableWidgetItem(f"{cena:.2f}"))
            self.table.setItem(row, 2, QTableWidgetItem(vat))
            self.table.setItem(row, 3, QTableWidgetItem(gtu))
            self.table.setItem(row, 4, QTableWidgetItem(kat))

    def _add(self):
        dlg = QDialog(self)
        dlg.setWindowTitle("Dodaj usługę")
        form = QFormLayout(dlg)

        e_nazwa = QLineEdit()
        e_cena = QLineEdit()

        cb_vat = QComboBox()
        cb_vat.addItems(["23%", "8%", "5%", "0%", "zw", "np", "np UE oo", "0 WDT", "0 EX"])
        cb_vat.setCurrentText(self.logic.get_setting("uslugi_domyslny_vat") or "23%")

        cb_gtu = QComboBox()
        cb_gtu.addItems([
            "GTU_01", "GTU_02", "GTU_03", "GTU_04", "GTU_05",
            "GTU_06", "GTU_07", "GTU_08", "GTU_09", "GTU_10",
            "GTU_11", "GTU_12", "GTU_13"
        ])
        cb_gtu.setCurrentText(self.logic.get_setting("uslugi_domyslne_gtu") or "GTU_12")

        cb_kat = QComboBox()
        cb_kat.addItems(["księgowość", "kadry", "inne"])

        form.addRow("Nazwa:", e_nazwa)
        form.addRow("Cena netto:", e_cena)
        form.addRow("VAT:", cb_vat)
        form.addRow("GTU:", cb_gtu)
        form.addRow("Kategoria:", cb_kat)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        form.addRow(buttons)

        if dlg.exec() == QDialog.DialogCode.Accepted:
            try:
                nazwa = e_nazwa.text().strip()
                cena = float(e_cena.text().replace(",", "."))
                vat = cb_vat.currentText()
                gtu = cb_gtu.currentText()
                kat = cb_kat.currentText()

                self.logic.add_extra_service(
                    nazwa=nazwa,
                    cena_netto=cena,
                    stawka_vat=vat,
                    kategoria=kat,
                )

                self.logic.set_setting("uslugi_domyslny_vat", vat)
                self.logic.set_setting("uslugi_domyslne_gtu", gtu)

                self._refresh()
                QMessageBox.information(self, "OK", "Dodano usługę.")

            except Exception as e:
                QMessageBox.critical(self, "Błąd", f"Nie udało się dodać: {e}")

    def _edit_selected(self):
        row = self.table.currentRow()
        if row < 0:
            QMessageBox.information(self, "Usługi", "Wybierz pozycję.")
            return

        nazwa = self.table.item(row, 0).text()
        cena = self.table.item(row, 1).text()
        vat = self.table.item(row, 2).text()
        gtu = self.table.item(row, 3).text()
        kat = self.table.item(row, 4).text()

        dlg = QDialog(self)
        dlg.setWindowTitle(f"Edytuj usługę: {nazwa}")
        form = QFormLayout(dlg)

        e_nazwa = QLineEdit(nazwa)
        e_cena = QLineEdit(cena)

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

        cb_kat = QComboBox()
        cb_kat.addItems(["księgowość", "kadry", "inne"])
        cb_kat.setCurrentText(kat)

        form.addRow("Nazwa:", e_nazwa)
        form.addRow("Cena netto:", e_cena)
        form.addRow("VAT:", cb_vat)
        form.addRow("GTU:", cb_gtu)
        form.addRow("Kategoria:", cb_kat)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        form.addRow(buttons)

        if dlg.exec() == QDialog.DialogCode.Accepted:
            try:
                nazwa_f = e_nazwa.text().strip()
                cena_f = float(e_cena.text().replace(",", "."))
                vat_f = cb_vat.currentText()
                gtu_f = cb_gtu.currentText()
                kat_f = cb_kat.currentText()

                self.logic.update_extra_service_by_name(
                    old_name=nazwa,
                    nazwa=nazwa_f,
                    cena_netto=cena_f,
                    stawka_vat=vat_f,
                    kategoria=kat_f,
                )

                self.logic.set_setting("uslugi_domyslny_vat", vat_f)
                self.logic.set_setting("uslugi_domyslne_gtu", gtu_f)

                self._refresh()
                QMessageBox.information(self, "OK", "Zapisano zmiany.")

            except Exception as e:
                QMessageBox.critical(self, "Błąd", f"Nie udało się zapisać: {e}")

    def _import(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Importuj usługi dodatkowe",
            "",
            "Pliki cennika (*.csv *.xlsx *.xls);;CSV (*.csv);;Excel (*.xlsx *.xls)"
        )
        if not path:
            return

        try:
            self.logic.import_extra_services_from_csv(path)
            self._refresh()
            QMessageBox.information(self, "OK", "Zaimportowano usługi.")
        except Exception as e:
            QMessageBox.critical(self, "Błąd", f"Import nieudany: {e}")
