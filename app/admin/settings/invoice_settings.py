# app/admin/settings/invoice_settings.py

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QLabel, QCheckBox, QComboBox,
    QPushButton, QMessageBox, QHBoxLayout
)
from PyQt6.QtGui import QFont

from app.admin.global_settings_logic import GlobalSettingsLogic


class InvoiceSettings(QWidget):
    """
    Zakładka ustawień faktur:
    - Metoda kasowa
    - Wysyłka do KSeF
    - Wysyłka do kontrahenta
    - Domyślne GTU
    - Domyślna stawka VAT
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.logic = GlobalSettingsLogic()

        layout = QVBoxLayout(self)

        title = QLabel("Ustawienia faktur")
        title.setFont(QFont("Arial", 12, QFont.Weight.Bold))
        layout.addWidget(title)

        # -------------------------
        # Metoda kasowa
        # -------------------------
        self.cb_metoda_kasowa = QCheckBox("Metoda kasowa (X w eksporcie Saldeo)")
        val = self.logic.get_setting("faktury_metoda_kasowa") or ""
        self.cb_metoda_kasowa.setChecked(val == "X")
        layout.addWidget(self.cb_metoda_kasowa)

        # -------------------------
        # Wysyłka do KSeF
        # -------------------------
        self.cb_ksef = QCheckBox("Automatyczna wysyłka do KSeF")
        val = self.logic.get_setting("faktury_ksef") or ""
        self.cb_ksef.setChecked(val == "X")
        layout.addWidget(self.cb_ksef)

        # -------------------------
        # Wysyłka do kontrahenta
        # -------------------------
        self.cb_kontrahent = QCheckBox("Automatyczna wysyłka do kontrahenta")
        val = self.logic.get_setting("faktury_wysylka_kontrahent") or ""
        self.cb_kontrahent.setChecked(val == "X")
        layout.addWidget(self.cb_kontrahent)

        # -------------------------
        # Domyślne GTU
        # -------------------------
        layout.addWidget(QLabel("Domyślne GTU dla faktur:"))

        self.gtu_combo = QComboBox()
        gtu_list = [
            "GTU_01", "GTU_02", "GTU_03", "GTU_04", "GTU_05",
            "GTU_06", "GTU_07", "GTU_08", "GTU_09", "GTU_10",
            "GTU_11", "GTU_12", "GTU_13"
        ]
        self.gtu_combo.addItems(gtu_list)

        current_gtu = self.logic.get_setting("faktury_domyslne_gtu") or "GTU_12"
        if current_gtu in gtu_list:
            self.gtu_combo.setCurrentText(current_gtu)

        layout.addWidget(self.gtu_combo)

        # -------------------------
        # Domyślna stawka VAT
        # -------------------------
        layout.addWidget(QLabel("Domyślna stawka VAT dla pozycji:"))

        self.vat_combo = QComboBox()
        vat_list = ["23%", "8%", "5%", "0%", "zw", "np", "np UE oo", "0 WDT", "0 EX"]
        self.vat_combo.addItems(vat_list)

        current_vat = self.logic.get_setting("faktury_domyslny_vat") or "23%"
        if current_vat in vat_list:
            self.vat_combo.setCurrentText(current_vat)

        layout.addWidget(self.vat_combo)

        # -------------------------
        # Zapis
        # -------------------------
        btn_row = QHBoxLayout()
        btn_save = QPushButton("Zapisz ustawienia")
        btn_save.clicked.connect(self._save)
        btn_row.addWidget(btn_save)
        btn_row.addStretch()
        layout.addLayout(btn_row)

    # ----------------------------------------------------
    # ZAPIS USTAWIEŃ
    # ----------------------------------------------------
    def _save(self):
        # metoda kasowa
        self.logic.set_setting(
            "faktury_metoda_kasowa",
            "X" if self.cb_metoda_kasowa.isChecked() else ""
        )

        # KSeF
        self.logic.set_setting(
            "faktury_ksef",
            "X" if self.cb_ksef.isChecked() else ""
        )

        # wysyłka do kontrahenta
        self.logic.set_setting(
            "faktury_wysylka_kontrahent",
            "X" if self.cb_kontrahent.isChecked() else ""
        )

        # GTU
        self.logic.set_setting(
            "faktury_domyslne_gtu",
            self.gtu_combo.currentText()
        )

        # VAT
        self.logic.set_setting(
            "faktury_domyslny_vat",
            self.vat_combo.currentText()
        )

        QMessageBox.information(self, "OK", "Ustawienia faktur zapisane.")
