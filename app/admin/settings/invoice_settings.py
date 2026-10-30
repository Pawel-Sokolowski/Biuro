# app/admin/settings/invoice_settings.py

import base64
from pathlib import Path

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QLabel, QCheckBox, QComboBox,
    QPushButton, QMessageBox, QHBoxLayout, QLineEdit, QSpinBox, QFileDialog, QFormLayout
)
from PyQt6.QtGui import QFont

from app.admin.global_settings_logic import GlobalSettingsLogic
from app.security.authorization import is_administrator
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

    def __init__(self, parent=None, pracownik_id=None):
        super().__init__(parent)
        self.logic = GlobalSettingsLogic()
        self.pracownik_id = pracownik_id

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
        self.term_mode.addItem("Konkretny dzień miesiąca (01–31)", MODE_DAY_OF_MONTH)
        self.term_days = QSpinBox()
        self.term_days.setRange(0, 365)
        self.term_days.setSuffix(" dni")
        # To numer dnia miesiąca, a nie konkretna data kalendarzowa.
        # Zapisujemy np. '7' i wyświetlamy '07'.
        self.term_day_of_month = QComboBox()
        self.term_day_of_month.addItems([f"{day:02d}" for day in range(1, 32)])
        try:
            days = int(self.logic.get_setting(DAYS_KEY) or self.logic.get_setting("faktury_termin_dni") or 7)
        except (TypeError, ValueError):
            days = 7
        try:
            day = int(self.logic.get_setting(DAY_OF_MONTH_KEY) or 7)
        except (TypeError, ValueError):
            day = 7
        self.term_days.setValue(max(0, min(days, 365)))
        self.term_day_of_month.setCurrentIndex(max(1, min(day, 31)) - 1)
        mode = self.logic.get_setting(MODE_KEY) or MODE_DAYS
        self.term_mode.setCurrentIndex(max(self.term_mode.findData(mode), 0))
        self.term_mode.currentIndexChanged.connect(self._sync_term_widgets)
        row = QHBoxLayout()
        row.addWidget(self.term_mode)
        row.addWidget(self.term_days)
        row.addWidget(self.term_day_of_month)
        row.addStretch()
        layout.addLayout(row)
        self._sync_term_widgets()
        # -------------------------
        # Dane sprzedawcy i logo na podglądzie/PDF
        # -------------------------
        seller_form = QFormLayout()
        self.seller_fields = {}
        for label, key in (
            ("Nazwa sprzedawcy:", "faktury_sprzedawca_nazwa"),
            ("NIP sprzedawcy:", "faktury_sprzedawca_nip"),
            ("Adres sprzedawcy:", "faktury_sprzedawca_adres"),
            ("Kod pocztowy:", "faktury_sprzedawca_kod"),
            ("Miejscowość:", "faktury_sprzedawca_miasto"),
            ("Kraj:", "faktury_sprzedawca_kraj"),
        ):
            field = QLineEdit(self.logic.get_setting(key) or "")
            self.seller_fields[key] = field
            seller_form.addRow(label, field)
        layout.addWidget(QLabel("Dane sprzedawcy na fakturze i PDF:"))
        layout.addLayout(seller_form)
        self.logo_data = self.logic.get_setting("faktury_logo_data") or ""
        logo_row = QHBoxLayout()
        self.logo_status = QLabel("Logo zapisane" if self.logo_data else "Brak logo")
        choose_logo = QPushButton("Wgraj logo…")
        choose_logo.clicked.connect(self._choose_logo)
        clear_logo = QPushButton("Usuń logo")
        clear_logo.clicked.connect(self._clear_logo)
        logo_row.addWidget(self.logo_status)
        logo_row.addWidget(choose_logo)
        logo_row.addWidget(clear_logo)
        logo_row.addStretch()
        layout.addLayout(logo_row)
        # -------------------------
        # KSeF - odnośnik do podglądu faktury
        # -------------------------
        layout.addWidget(QLabel(
            "Adres otwierany po kliknięciu numeru KSeF "
            "(pola: {numer}, {nip}, {data}, {hash}):"
        ))
        self.ksef_url = QLineEdit(self.logic.get_setting(URL_TEMPLATE_KEY) or DEFAULT_URL_TEMPLATE)
        layout.addWidget(self.ksef_url)

        # Rachunek wystawcy wspólny dla wszystkich faktur eksportowanych do Saldeo.
        # Edycję globalnego numeru rachunku udostępniamy wyłącznie administratorowi.
        self.bank_account = None
        if is_administrator(self.pracownik_id):
            layout.addWidget(QLabel("Globalny numer konta bankowego do eksportu faktur (rachunek wystawcy):"))
            self.bank_account = QLineEdit(
                self.logic.get_setting("konto_bankowe_biura")
                or self.logic.get_setting("faktury_konto_bankowe") or ""
            )
            self.bank_account.setPlaceholderText("26 cyfr rachunku lub numer IBAN")
            layout.addWidget(self.bank_account)

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
        self.term_day_of_month.setVisible(by_day)

    def _choose_logo(self):
        path, _ = QFileDialog.getOpenFileName(self, "Wybierz logo", "", "Obrazy (*.png *.jpg *.jpeg)")
        if not path:
            return
        source = Path(path)
        if source.stat().st_size > 2 * 1024 * 1024:
            QMessageBox.warning(self, "Logo", "Plik logo nie może przekraczać 2 MB.")
            return
        mime = "image/png" if source.suffix.lower() == ".png" else "image/jpeg"
        self.logo_data = f"data:{mime};base64," + base64.b64encode(source.read_bytes()).decode("ascii")
        self.logo_status.setText(f"Logo: {source.name}")

    def _clear_logo(self):
        self.logo_data = ""
        self.logo_status.setText("Brak logo")

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
        if self.bank_account is not None and is_administrator(self.pracownik_id):
            import re
            account = re.sub(r"\s+", "", self.bank_account.text().strip()).upper()
            if account and not (re.fullmatch(r"\d{26}", account) or
                                re.fullmatch(r"[A-Z]{2}[A-Z0-9]{13,32}", account)):
                QMessageBox.warning(self, "Numer rachunku", "Podaj 26 cyfr rachunku lub prawidłowy format IBAN.")
                return
            self.logic.set_setting("konto_bankowe_biura", account)
            self.logic.set_setting("faktury_konto_bankowe", account)

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
        self.logic.set_setting(DAY_OF_MONTH_KEY, self.term_day_of_month.currentText())

        self.logic.set_setting(URL_TEMPLATE_KEY, self.ksef_url.text().strip() or DEFAULT_URL_TEMPLATE)
        for key, field in self.seller_fields.items():
            self.logic.set_setting(key, field.text().strip())
        self.logic.set_setting("faktury_logo_data", self.logo_data)

        QMessageBox.information(self, "OK", "Ustawienia faktur zapisane.")
