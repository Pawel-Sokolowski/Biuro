# app/admin/settings/invoice_settings.py

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QLabel, QCheckBox, QComboBox,
    QPushButton, QMessageBox, QHBoxLayout, QLineEdit, QSpinBox, QDateEdit
)
from PyQt6.QtCore import QDate
from PyQt6.QtGui import QFont

from app.admin.global_settings_logic import GlobalSettingsLogic
from app.utils.payment_terms import (
    DAY_OF_MONTH_KEY, DAYS_KEY, MODE_DAY_OF_MONTH, MODE_DAYS, MODE_KEY,
)
from app.invoices.ksef import DEFAULT_URL_TEMPLATE, URL_TEMPLATE_KEY


class InvoiceSettings(QWidget):
    """
    Zakładka ustawień faktur:
    - Metoda kasowa
    - Wysyłka do KSeF
    - Wysyłka do kontrahenta
    - Domyślne GTU
    - Domyślna stawka VAT
    - Odnośnik do podglądu faktury w KSeF
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
        val = self._get_flag_value("eksport_kasowa", "faktury_metoda_kasowa", "domyslnie_metoda_kasowa")
        self.cb_metoda_kasowa.setChecked(val == "X")
        layout.addWidget(self.cb_metoda_kasowa)

        # -------------------------
        # Wysyłka do KSeF
        # -------------------------
        self.cb_ksef = QCheckBox("Automatyczna wysyłka do KSeF")
        val = self._get_flag_value("eksport_do_ksef", "faktury_ksef", "domyslnie_wysylac_do_ksef")
        self.cb_ksef.setChecked(val == "X")
        layout.addWidget(self.cb_ksef)

        # -------------------------
        # Wysyłka do kontrahenta
        # -------------------------
        self.cb_kontrahent = QCheckBox("Automatyczna wysyłka do kontrahenta")
        val = self._get_flag_value(
            "eksport_do_kontrahenta",
            "faktury_wysylka_kontrahent",
            "domyslnie_wysylac_do_kontrahenta",
        )
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
        # Domyślny termin płatności
        # -------------------------
        layout.addWidget(QLabel("Domyślny termin płatności faktur:"))
        self.term_mode = QComboBox()
        self.term_mode.addItem("Liczba dni od wystawienia", MODE_DAYS)
        self.term_mode.addItem("Konkretny dzień miesiąca (z kalendarza)", MODE_DAY_OF_MONTH)
        self.term_days = QSpinBox()
        self.term_days.setRange(0, 365)
        self.term_days.setSuffix(" dni")
        self.term_date = QDateEdit()
        self.term_date.setCalendarPopup(True)
        self.term_date.setDisplayFormat("dd.MM.yyyy")
        today = QDate.currentDate()
        try:
            days = int(self.logic.get_setting(DAYS_KEY) or self.logic.get_setting("faktury_termin_dni") or 7)
        except ValueError:
            days = 7
        try:
            day = int(self.logic.get_setting(DAY_OF_MONTH_KEY) or today.day())
        except ValueError:
            day = today.day()
        self.term_days.setValue(days)
        self.term_date.setDate(QDate(today.year(), today.month(), min(max(day, 1), today.daysInMonth())))
        mode = self.logic.get_setting(MODE_KEY) or MODE_DAYS
        self.term_mode.setCurrentIndex(max(self.term_mode.findData(mode), 0))
        self.term_mode.currentIndexChanged.connect(self._sync_term_widgets)
        row = QHBoxLayout()
        row.addWidget(self.term_mode)
        row.addWidget(self.term_days)
        row.addWidget(self.term_date)
        row.addStretch()
        layout.addLayout(row)
        self._sync_term_widgets()
        # -------------------------
        # KSeF - odnośnik do podglądu faktury
        # -------------------------
        layout.addWidget(QLabel("Adres otwierany po kliknięciu numeru KSeF (można użyć {numer}):"))
        self.ksef_url = QLineEdit(self.logic.get_setting(URL_TEMPLATE_KEY) or DEFAULT_URL_TEMPLATE)
        layout.addWidget(self.ksef_url)

        layout.addStretch()

        # -------------------------
        # Zapis
        # -------------------------
        btn_row = QHBoxLayout()
        btn_save = QPushButton("Zapisz ustawienia")
        btn_save.clicked.connect(self._save)
        btn_row.addWidget(btn_save)
        btn_row.addStretch()
        layout.addLayout(btn_row)

    def _sync_term_widgets(self):
        by_day = self.term_mode.currentData() == MODE_DAY_OF_MONTH
        self.term_days.setVisible(not by_day)
        self.term_date.setVisible(by_day)

    def _get_flag_value(self, *keys: str) -> str:
        for key in keys:
            val = self.logic.get_setting(key)
            if val is not None and str(val).strip() != "":
                s = str(val).strip()
                return "X" if s in ("1", "true", "True", "X") else ""
        return ""

    def _set_flag_value(self, enabled: bool, *keys: str) -> None:
        val = "X" if enabled else ""
        for key in keys:
            self.logic.set_setting(key, val)

    # ----------------------------------------------------
    # ZAPIS USTAWIEŃ
    # ----------------------------------------------------
    def _save(self):
        # metoda kasowa
        self._set_flag_value(
            self.cb_metoda_kasowa.isChecked(),
            "eksport_kasowa",
            "faktury_metoda_kasowa",
            "domyslnie_metoda_kasowa",
        )

        # KSeF
        self._set_flag_value(
            self.cb_ksef.isChecked(),
            "eksport_do_ksef",
            "faktury_ksef",
            "domyslnie_wysylac_do_ksef",
        )

        # wysyłka do kontrahenta
        self._set_flag_value(
            self.cb_kontrahent.isChecked(),
            "eksport_do_kontrahenta",
            "faktury_wysylka_kontrahent",
            "domyslnie_wysylac_do_kontrahenta",
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

        self.logic.set_setting(MODE_KEY, self.term_mode.currentData())
        self.logic.set_setting(DAYS_KEY, str(self.term_days.value()))
        self.logic.set_setting(DAY_OF_MONTH_KEY, str(self.term_date.date().day()))

        self.logic.set_setting(URL_TEMPLATE_KEY, self.ksef_url.text().strip() or DEFAULT_URL_TEMPLATE)

        QMessageBox.information(self, "OK", "Ustawienia faktur zapisane.")
