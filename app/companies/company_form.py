# app/companies/company_form.py
from typing import Dict, Any, Optional, List

import sqlite3
from datetime import datetime

from PyQt6.QtCore import Qt, QDate
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QGroupBox, QLabel,
    QLineEdit, QComboBox, QCheckBox, QSpinBox, QDateEdit, QPushButton,
    QTabWidget, QScrollArea, QMessageBox, QTableWidget, QTableWidgetItem,
    QDialog, QVBoxLayout as QVBoxLayoutDialog, QHBoxLayout as QHBoxLayoutDialog,
    QHeaderView, QTextEdit
)

from app.companies.constants import (
    FORMA_DZIALALNOSCI, FORMA_OPODATKOWANIA, STATUS_PODATKOWY,
    URZEDY_SKARBOWE, MIESIACE_FAKTUROWANIE, SPOSOB_ZAPLATY,
    DEFAULT_SERIA_FV, DEFAULT_SERIA_KFV,
    PARTNER_FIELDS, CONTACT_FIELDS
)

from app.db.dao_companies import (
    save_company, get_company,
    save_partner, delete_partner, get_partners,
    save_contact, delete_contact, get_contacts
)

from app.db.dao_conn import get_conn
from app.admin.global_settings_logic import GlobalSettingsLogic
from app.companies.ceidg_api import get_ceidg_firma_by_nip


# ============================================================
#  CUSTOM DATE EDIT (no wheel)
# ============================================================

class NoWheelDateEdit(QDateEdit):
    def wheelEvent(self, event):
        event.ignore()


# ============================================================
#  DIALOG — WSPÓLNIK / REPREZENTANT
# ============================================================

class PartnerEditDialog(QDialog):
    def __init__(self, parent=None, data: Optional[Dict[str, Any]] = None):
        super().__init__(parent)
        self.setWindowTitle("Wspólnik / Reprezentant")
        self.resize(650, 650)
        self.result_data: Optional[Dict[str, Any]] = None

        layout = QVBoxLayoutDialog(self)

        form_box = QGroupBox("Dane wspólnika / reprezentanta")
        form = QFormLayout(form_box)

        self.fields: Dict[str, QLineEdit] = {}

        for field in PARTNER_FIELDS:
            edit = QLineEdit()
            self.fields[field] = edit
            label = field.replace("_", " ").capitalize()
            form.addRow(label, edit)

        layout.addWidget(form_box)

        btn_row = QHBoxLayoutDialog()
        btn_ok = QPushButton("Zapisz")
        btn_cancel = QPushButton("Anuluj")
        btn_row.addStretch()
        btn_row.addWidget(btn_ok)
        btn_row.addWidget(btn_cancel)
        layout.addLayout(btn_row)

        btn_ok.clicked.connect(self._on_ok)
        btn_cancel.clicked.connect(self.reject)

        if data:
            self._populate(data)

    def _populate(self, d: Dict[str, Any]):
        for field in PARTNER_FIELDS:
            if field in d:
                self.fields[field].setText(str(d[field]))

    def _on_ok(self):
        self.result_data = {
            field: self.fields[field].text().strip()
            for field in PARTNER_FIELDS
        }
        self.accept()

    def get_data(self) -> Optional[Dict[str, Any]]:
        return self.result_data


# ============================================================
#  DIALOG — OSOBA KONTAKTOWA
# ============================================================

class ContactEditDialog(QDialog):
    def __init__(self, parent=None, data: Optional[Dict[str, Any]] = None):
        super().__init__(parent)
        self.setWindowTitle("Osoba kontaktowa")
        self.resize(450, 300)
        self.result_data: Optional[Dict[str, Any]] = None

        layout = QVBoxLayoutDialog(self)

        form_box = QGroupBox("Dane kontaktowe")
        form = QFormLayout(form_box)

        self.fields: Dict[str, QLineEdit] = {}

        for field in CONTACT_FIELDS:
            edit = QLineEdit()
            self.fields[field] = edit
            label = field.replace("_", " ").capitalize()
            form.addRow(label, edit)

        layout.addWidget(form_box)

        btn_row = QHBoxLayoutDialog()
        btn_ok = QPushButton("Zapisz")
        btn_cancel = QPushButton("Anuluj")
        btn_row.addStretch()
        btn_row.addWidget(btn_ok)
        btn_row.addWidget(btn_cancel)
        layout.addLayout(btn_row)

        btn_ok.clicked.connect(self._on_ok)
        btn_cancel.clicked.connect(self.reject)

        if data:
            self._populate(data)

    def _populate(self, d: Dict[str, Any]):
        for field in CONTACT_FIELDS:
            if field in d:
                self.fields[field].setText(str(d[field]))

    def _on_ok(self):
        self.result_data = {
            field: self.fields[field].text().strip()
            for field in CONTACT_FIELDS
        }
        self.accept()

    def get_data(self) -> Optional[Dict[str, Any]]:
        return self.result_data


# ============================================================
#  COMPANY FORM — MAIN CLASS
# ============================================================

class CompanyForm(QWidget):
    def __init__(self, pracownik_id: int, company_data: Optional[dict], parent=None):
        super().__init__(parent)
        self.pracownik_id = pracownik_id
        self.company_data = company_data or {}
        self.settings_logic = GlobalSettingsLogic()

        self.inputs: Dict[str, Any] = {}
        self.partners: List[Dict[str, Any]] = []
        self.contacts: List[Dict[str, Any]] = []

        self.setMinimumWidth(1200)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        container = QWidget()
        scroll.setWidget(container)

        self.main_layout = QVBoxLayout(container)
        self.main_layout.setContentsMargins(20, 20, 20, 20)
        self.main_layout.setSpacing(12)

        header = QLabel("Edycja danych Firmy")
        header.setFont(QFont("Arial", 20, QFont.Weight.Bold))
        self.main_layout.addWidget(header)

        # KOLEJNOŚĆ BUDOWY SEKCJI
        self._build_ident_section()
        self._build_basic_section()
        self._build_activity_section()
        self._build_address_tabs()
        self._build_pricing_section()
        self._build_invoicing_section()
        self._build_partners_section()
        self._build_contacts_section()
        self._build_notes_section()
        self._build_save_buttons()

        # Ładowanie danych
        self._load_existing_data()

        final = QVBoxLayout(self)
        final.addWidget(scroll)

    # ============================================================
    #  SEKCJA IDENTYFIKATORA
    # ============================================================

    def _build_ident_section(self):
        box = QGroupBox("Identyfikator podatnika")
        form = QFormLayout(box)

        self.id_type_combo = QComboBox()
        self.id_type_combo.addItems(["NIP", "REGON", "KRS", "PESEL"])

        self.f_nip = QLineEdit()
        self.f_regon = QLineEdit()
        self.f_krs = QLineEdit()
        self.f_pesel = QLineEdit()

        self.btn_ceidg = QPushButton("Pobierz z CEIDG (NIP)")
        self.btn_ceidg.clicked.connect(self._fetch_ceidg)

        form.addRow("Rodzaj identyfikatora", self.id_type_combo)
        form.addRow("NIP", self.f_nip)
        form.addRow("REGON", self.f_regon)
        form.addRow("KRS", self.f_krs)
        form.addRow("PESEL", self.f_pesel)
        form.addRow(self.btn_ceidg)

        self.main_layout.addWidget(box)

    def _fetch_ceidg(self):
        nip = "".join(ch for ch in self.f_nip.text().strip() if ch.isdigit())
        if not nip or len(nip) != 10:
            QMessageBox.warning(self, "CEIDG", "Podaj poprawny NIP (10 cyfr).")
            return

        try:
            data = get_ceidg_firma_by_nip(nip)
        except Exception as e:
            QMessageBox.critical(self, "CEIDG", f"Błąd pobierania danych: {e}")
            return

        if not data or data.get("error"):
            QMessageBox.warning(self, "CEIDG", "Nie znaleziono firmy w CEIDG.")
            return

        self.f_nazwa.setText(data.get("nazwa", ""))
        self.f_nazwa_skrocona.setText(data.get("nazwa_skrocona", ""))
        self.f_kody_pkd.setText(data.get("kody_pkd", ""))

        self.dz_miasto.setText(data.get("miasto", ""))
        self.dz_kod.setText(data.get("kod_pocztowy", ""))
        self.dz_ulica.setText(data.get("ulica", ""))
        self.dz_budynek.setText(data.get("adres_budynek", ""))
        self.dz_lokal.setText(data.get("adres_lokal", ""))
        self.dz_woj.setText(data.get("wojewodztwo", ""))
        self.dz_pow.setText(data.get("powiat", ""))
        self.dz_gmi.setText(data.get("gmina", ""))
        self.dz_kraj.setText(data.get("kraj", ""))

    # ============================================================
    #  SEKCJA PODSTAWOWA
    # ============================================================

    def _build_basic_section(self):
        box = QGroupBox("Podstawowe dane")
        form = QFormLayout(box)

        self.f_nazwa = QLineEdit()
        self.f_nazwa_skrocona = QLineEdit()
        self.f_numer_klienta = QLineEdit()

        self.kont_imie = QLineEdit()
        self.kont_nazw = QLineEdit()
        self.kont_email = QLineEdit()
        self.kont_tel = QLineEdit()

        self.f_edoreczenia = QLineEdit()

        form.addRow("Nazwa", self.f_nazwa)
        form.addRow("Nazwa skrócona", self.f_nazwa_skrocona)
        form.addRow("Numer klienta", self.f_numer_klienta)

        form.addRow("Imię kontaktowe", self.kont_imie)
        form.addRow("Nazwisko kontaktowe", self.kont_nazw)
        form.addRow("Email kontaktowy", self.kont_email)
        form.addRow("Telefon kontaktowy", self.kont_tel)

        form.addRow("Adres e-doręczeń", self.f_edoreczenia)

        self.main_layout.addWidget(box)

    # ============================================================
    #  SEKCJA FORMA DZIAŁALNOŚCI / OPODATKOWANIE
    # ============================================================

    def _build_activity_section(self):
        box = QGroupBox("Forma działalności i opodatkowanie")
        form = QFormLayout(box)

        self.f_forma_dzial = QComboBox()
        self.f_forma_dzial.addItems(FORMA_DZIALALNOSCI)

        self.f_forma_opodatk = QComboBox()
        self.f_forma_opodatk.addItems(FORMA_OPODATKOWANIA)

        self.f_urzad_skarbowy = QComboBox()
        self.f_urzad_skarbowy.addItems(URZEDY_SKARBOWE)

        self.f_kody_pkd = QLineEdit()

        self.VAT_checkbox = QCheckBox("VAT czynny")

        self.f_start = NoWheelDateEdit()
        self.f_start.setCalendarPopup(True)
        self.f_start.setDate(QDate.currentDate())

        form.addRow("Forma działalności", self.f_forma_dzial)
        form.addRow("Forma opodatkowania", self.f_forma_opodatk)
        form.addRow("Urząd skarbowy", self.f_urzad_skarbowy)
        form.addRow("Kody PKD", self.f_kody_pkd)
        form.addRow(self.VAT_checkbox)
        form.addRow("Data rozpoczęcia działalności", self.f_start)

        self.main_layout.addWidget(box)

    # ============================================================
    #  SEKCJA ADRESÓW (3 zakładki)
    # ============================================================

    def _build_address_tabs(self):
        self.tabs = QTabWidget()

        dz = QWidget()
        dz_form = QFormLayout(dz)

        self.dz_kraj = QLineEdit()
        self.dz_woj = QLineEdit()
        self.dz_gmi = QLineEdit()
        self.dz_pow = QLineEdit()
        self.dz_miasto = QLineEdit()
        self.dz_ulica = QLineEdit()
        self.dz_budynek = QLineEdit()
        self.dz_lokal = QLineEdit()
        self.dz_kod = QLineEdit()

        dz_form.addRow("Kraj", self.dz_kraj)
        dz_form.addRow("Województwo", self.dz_woj)
        dz_form.addRow("Gmina", self.dz_gmi)
        dz_form.addRow("Powiat", self.dz_pow)
        dz_form.addRow("Miasto", self.dz_miasto)
        dz_form.addRow("Ulica", self.dz_ulica)
        dz_form.addRow("Budynek", self.dz_budynek)
        dz_form.addRow("Lokal", self.dz_lokal)
        dz_form.addRow("Kod pocztowy", self.dz_kod)

        ko = QWidget()
        ko_form = QFormLayout(ko)

        self.ko_kraj = QLineEdit()
        self.ko_woj = QLineEdit()
        self.ko_gmi = QLineEdit()
        self.ko_pow = QLineEdit()
        self.ko_miasto = QLineEdit()
        self.ko_ulica = QLineEdit()
        self.ko_budynek = QLineEdit()
        self.ko_lokal = QLineEdit()
        self.ko_kod = QLineEdit()

        ko_form.addRow("Kraj", self.ko_kraj)
        ko_form.addRow("Województwo", self.ko_woj)
        ko_form.addRow("Gmina", self.ko_gmi)
        ko_form.addRow("Powiat", self.ko_pow)
        ko_form.addRow("Miasto", self.ko_miasto)
        ko_form.addRow("Ulica", self.ko_ulica)
        ko_form.addRow("Budynek", self.ko_budynek)
        ko_form.addRow("Lokal", self.ko_lokal)
        ko_form.addRow("Kod pocztowy", self.ko_kod)

        zam = QWidget()
        zam_form = QFormLayout(zam)

        self.zam_kraj = QLineEdit()
        self.zam_woj = QLineEdit()
        self.zam_gmi = QLineEdit()
        self.zam_pow = QLineEdit()
        self.zam_miasto = QLineEdit()
        self.zam_ulica = QLineEdit()
        self.zam_budynek = QLineEdit()
        self.zam_lokal = QLineEdit()
        self.zam_kod = QLineEdit()

        zam_form.addRow("Kraj", self.zam_kraj)
        zam_form.addRow("Województwo", self.zam_woj)
        zam_form.addRow("Gmina", self.zam_gmi)
        zam_form.addRow("Powiat", self.zam_pow)
        zam_form.addRow("Miasto", self.zam_miasto)
        zam_form.addRow("Ulica", self.zam_ulica)
        zam_form.addRow("Budynek", self.zam_budynek)
        zam_form.addRow("Lokal", self.zam_lokal)
        zam_form.addRow("Kod pocztowy", self.zam_kod)

        self.tabs.addTab(dz, "Adres działalności")
        self.tabs.addTab(ko, "Adres korespondencji")
        self.tabs.addTab(zam, "Adres zamieszkania")

        self.main_layout.addWidget(self.tabs)

    # ============================================================
    #  CENNIKI / LIMITY
    # ============================================================

    def _build_pricing_section(self):
        box = QGroupBox("Cenniki i limity")
        form = QFormLayout(box)

        self.f_glowny_cennik = QLineEdit()
        self.f_glowny_cennik_manualny = QCheckBox("Cennik ustawiony ręcznie")

        self.f_limit_dokumentow = QSpinBox()
        self.f_limit_dokumentow.setRange(0, 999999)
        self.f_limit_dokumentow.setValue(0)

        self.f_liczba_pracownikow = QSpinBox()
        self.f_liczba_pracownikow.setRange(0, 9999)

        self.f_uop_ilosc = QSpinBox()
        self.f_uz_ilosc = QSpinBox()
        self.f_uod_ilosc = QSpinBox()

        self.f_uop_cennik = QLineEdit()
        self.f_uz_cennik = QLineEdit()
        self.f_uod_cennik = QLineEdit()

        form.addRow("Główny cennik", self.f_glowny_cennik)
        form.addRow(self.f_glowny_cennik_manualny)
        form.addRow("Limit dokumentów", self.f_limit_dokumentow)
        form.addRow("Liczba pracowników", self.f_liczba_pracownikow)

        form.addRow("UoP - ilość", self.f_uop_ilosc)
        form.addRow("UoP - cennik", self.f_uop_cennik)

        form.addRow("UZ - ilość", self.f_uz_ilosc)
        form.addRow("UZ - cennik", self.f_uz_cennik)

        form.addRow("UoD - ilość", self.f_uod_ilosc)
        form.addRow("UoD - cennik", self.f_uod_cennik)

        self.main_layout.addWidget(box)

    def _load_pricing_data(self, d: Dict[str, Any]):
        self.f_glowny_cennik.setText(d.get("glowny_cennik", ""))
        self.f_glowny_cennik_manualny.setChecked(bool(d.get("glowny_cennik_manualny", 0)))

        self.f_limit_dokumentow.setValue(int(d.get("limit_dokumentow", 0)))
        self.f_liczba_pracownikow.setValue(int(d.get("liczba_pracownikow", 0)))

        self.f_uop_ilosc.setValue(int(d.get("uop_ilosc", 0)))
        self.f_uz_ilosc.setValue(int(d.get("uz_ilosc", 0)))
        self.f_uod_ilosc.setValue(int(d.get("uod_ilosc", 0)))

        self.f_uop_cennik.setText(d.get("uop_cennik", ""))
        self.f_uz_cennik.setText(d.get("uz_cennik", ""))
        self.f_uod_cennik.setText(d.get("uod_cennik", ""))

    # ============================================================
    #  FAKTUROWANIE
    # ============================================================

    def _build_invoicing_section(self):
        box = QGroupBox("Fakturowanie")
        form = QFormLayout(box)

        self.f_fakturowac = QCheckBox("Generować faktury")
        self.f_wysylac_do_ksef = QCheckBox("Wysyłać do KSeF")
        self.f_wysylac_do_kontrahenta = QCheckBox("Wysyłać do kontrahenta")
        self.f_metoda_kasowa = QCheckBox("Metoda kasowa")

        self.f_miesiac_dok = QComboBox()
        self.f_miesiac_dok.addItems(MIESIACE_FAKTUROWANIE)

        self.f_miesiac_prac = QComboBox()
        self.f_miesiac_prac.addItems(MIESIACE_FAKTUROWANIE)

        self.f_dzien_dok = QSpinBox()
        self.f_dzien_dok.setRange(1, 31)
        self.f_dzien_dok.setValue(7)

        self.f_seria_fv = QLineEdit()
        self.f_seria_kfv = QLineEdit()

        self.f_sposob_zaplaty = QComboBox()
        self.f_sposob_zaplaty.addItems(SPOSOB_ZAPLATY)

        form.addRow(self.f_fakturowac)
        form.addRow(self.f_wysylac_do_ksef)
        form.addRow(self.f_wysylac_do_kontrahenta)
        form.addRow(self.f_metoda_kasowa)

        form.addRow("Miesiąc dokumentów", self.f_miesiac_dok)
        form.addRow("Miesiąc pracowników", self.f_miesiac_prac)
        form.addRow("Dzień dostarczania dokumentów", self.f_dzien_dok)

        form.addRow("Seria FV", self.f_seria_fv)
        form.addRow("Seria KFV", self.f_seria_kfv)

        form.addRow("Sposób zapłaty", self.f_sposob_zaplaty)

        self.main_layout.addWidget(box)

    def _load_invoicing_data(self, d: Dict[str, Any]):
        self.f_fakturowac.setChecked(bool(d.get("fakturowac", 0)))
        self.f_wysylac_do_ksef.setChecked(bool(d.get("wysylac_do_ksef", 0)))
        self.f_wysylac_do_kontrahenta.setChecked(bool(d.get("wysylac_do_kontrahenta", 0)))
        self.f_metoda_kasowa.setChecked(bool(d.get("metoda_kasowa", 0)))

        if d.get("miesiac_dok_fakturowanie") in MIESIACE_FAKTUROWANIE:
            self.f_miesiac_dok.setCurrentText(d["miesiac_dok_fakturowanie"])
        if d.get("miesiac_prac_fakturowanie") in MIESIACE_FAKTUROWANIE:
            self.f_miesiac_prac.setCurrentText(d["miesiac_prac_fakturowanie"])

        self.f_dzien_dok.setValue(int(d.get("dzien_dostarczania_dokumentow", 7)))

        self.f_seria_fv.setText(d.get("seria_fv", DEFAULT_SERIA_FV))
        self.f_seria_kfv.setText(d.get("seria_kfv", DEFAULT_SERIA_KFV))

        if d.get("sposob_zaplaty") in SPOSOB_ZAPLATY:
            self.f_sposob_zaplaty.setCurrentText(d["sposob_zaplaty"])

    # ============================================================
    #  WSPÓLNICY
    # ============================================================

    def _build_partners_section(self):
        box = QGroupBox("Wspólnicy / reprezentanci")
        layout = QVBoxLayout(box)

        self.partners_table = QTableWidget(0, len(PARTNER_FIELDS) + 1)
        self.partners_table.setHorizontalHeaderLabels(
            [f.replace("_", " ").capitalize() for f in PARTNER_FIELDS] + ["Usuń"]
        )
        self.partners_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)

        btn_add = QPushButton("Dodaj wspólnika")
        btn_add.clicked.connect(self._add_partner)

        layout.addWidget(self.partners_table)
        layout.addWidget(btn_add)

        self.main_layout.addWidget(box)

    def _refresh_partners_table(self):
        self.partners_table.setRowCount(0)
        for p in self.partners:
            row = self.partners_table.rowCount()
            self.partners_table.insertRow(row)

            for col, field in enumerate(PARTNER_FIELDS):
                item = QTableWidgetItem(str(p.get(field, "")))
                self.partners_table.setItem(row, col, item)

            btn_del = QPushButton("Usuń")
            btn_del.clicked.connect(lambda _, pid=p.get("id"): self._delete_partner(pid))
            self.partners_table.setCellWidget(row, len(PARTNER_FIELDS), btn_del)

    def _add_partner(self):
        dlg = PartnerEditDialog(self)
        if dlg.exec():
            data = dlg.get_data()
            if data:
                self.partners.append(data)
                self._refresh_partners_table()

    def _delete_partner(self, partner_id: Optional[int]):
        if partner_id:
            delete_partner(partner_id)
        self.partners = [p for p in self.partners if p.get("id") != partner_id]
        self._refresh_partners_table()

    # ============================================================
    #  OSOBY KONTAKTOWE
    # ============================================================

    def _build_contacts_section(self):
        box = QGroupBox("Osoby kontaktowe")
        layout = QVBoxLayout(box)

        self.contacts_table = QTableWidget(0, len(CONTACT_FIELDS) + 1)
        self.contacts_table.setHorizontalHeaderLabels(
            [f.replace("_", " ").capitalize() for f in CONTACT_FIELDS] + ["Usuń"]
        )
        self.contacts_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)

        btn_add = QPushButton("Dodaj osobę kontaktową")
        btn_add.clicked.connect(self._add_contact)

        layout.addWidget(self.contacts_table)
        layout.addWidget(btn_add)

        self.main_layout.addWidget(box)

    def _refresh_contacts_table(self):
        self.contacts_table.setRowCount(0)
        for c in self.contacts:
            row = self.contacts_table.rowCount()
            self.contacts_table.insertRow(row)

            for col, field in enumerate(CONTACT_FIELDS):
                item = QTableWidgetItem(str(c.get(field, "")))
                self.contacts_table.setItem(row, col, item)

            btn_del = QPushButton("Usuń")
            btn_del.clicked.connect(lambda _, cid=c.get("id"): self._delete_contact(cid))
            self.contacts_table.setCellWidget(row, len(CONTACT_FIELDS), btn_del)

    def _add_contact(self):
        dlg = ContactEditDialog(self)
        if dlg.exec():
            data = dlg.get_data()
            if data:
                self.contacts.append(data)
                self._refresh_contacts_table()

    def _delete_contact(self, contact_id: Optional[int]):
        if contact_id:
            delete_contact(contact_id)
        self.contacts = [c for c in self.contacts if c.get("id") != contact_id]
        self._refresh_contacts_table()

    # ============================================================
    #  NOTATKI
    # ============================================================

    def _build_notes_section(self):
        box = QGroupBox("Notatki")
        layout = QVBoxLayout(box)

        self.notes_edit = QTextEdit()
        layout.addWidget(self.notes_edit)

        self.main_layout.addWidget(box)

    def _load_notes(self, d: Dict[str, Any]):
        self.notes_edit.setText(d.get("uwagi", ""))

    # ============================================================
    #  PRZYCISKI ZAPISU
    # ============================================================

    def _build_save_buttons(self):
        row = QHBoxLayout()
        btn_save = QPushButton("Zapisz firmę")
        btn_save.clicked.connect(self._save)

        btn_cancel = QPushButton("Anuluj")
        btn_cancel.clicked.connect(self._cancel)

        row.addStretch()
        row.addWidget(btn_save)
        row.addWidget(btn_cancel)

        self.main_layout.addLayout(row)

    # ============================================================
    #  ŁADOWANIE DANYCH
    # ============================================================

    def _load_existing_data(self):
        d = self.company_data
        if not d:
            return

        self.f_nip.setText(d.get("nip", ""))
        self.f_regon.setText(d.get("regon", ""))
        self.f_krs.setText(d.get("krs", ""))
        self.f_pesel.setText(d.get("pesel", ""))

        self.f_nazwa.setText(d.get("nazwa", ""))
        self.f_nazwa_skrocona.setText(d.get("nazwa_skrocona", ""))
        self.f_numer_klienta.setText(d.get("numer_klienta", ""))

        osoba = d.get("osoba_kontaktowa", "")
        if osoba:
            parts = osoba.split()
            if len(parts) >= 1:
                self.kont_imie.setText(parts[0])
            if len(parts) >= 2:
                self.kont_nazw.setText(" ".join(parts[1:]))

        self.kont_email.setText(d.get("adres_email", ""))
        self.kont_tel.setText(d.get("nr_telefonu", ""))

        self.f_edoreczenia.setText(d.get("adres_doreczen_elektronicznych", ""))

        if d.get("forma_dzialalnosci") in FORMA_DZIALALNOSCI:
            self.f_forma_dzial.setCurrentText(d["forma_dzialalnosci"])
        if d.get("forma_opodatkowania") in FORMA_OPODATKOWANIA:
            self.f_forma_opodatk.setCurrentText(d["forma_opodatkowania"])

        if d.get("urzad_skarbowy") in URZEDY_SKARBOWE:
            self.f_urzad_skarbowy.setCurrentText(d["urzad_skarbowy"])

        self.f_kody_pkd.setText(d.get("kody_pkd", ""))

        self.VAT_checkbox.setChecked(bool(d.get("vat_czynny", 0)))

        if d.get("data_rozpoczecia_dg"):
            try:
                dt = QDate.fromString(d["data_rozpoczecia_dg"], "yyyy-MM-dd")
                if dt.isValid():
                    self.f_start.setDate(dt)
            except Exception:
                pass

        self.dz_kraj.setText(d.get("kraj", ""))
        self.dz_woj.setText(d.get("wojewodztwo", ""))
        self.dz_pow.setText(d.get("powiat", ""))
        self.dz_gmi.setText(d.get("gmina", ""))
        self.dz_miasto.setText(d.get("miasto", ""))
        self.dz_ulica.setText(d.get("ulica", ""))
        self.dz_budynek.setText(d.get("adres_budynek", ""))
        self.dz_lokal.setText(d.get("adres_lokal", ""))
        self.dz_kod.setText(d.get("kod_pocztowy", ""))

        self.ko_kraj.setText(d.get("adres_koresp_kraj", ""))
        self.ko_woj.setText(d.get("adres_koresp_wojewodztwo", ""))
        self.ko_pow.setText(d.get("adres_koresp_powiat", ""))
        self.ko_gmi.setText(d.get("adres_koresp_gmina", ""))
        self.ko_miasto.setText(d.get("adres_koresp_miasto", ""))
        self.ko_ulica.setText(d.get("adres_koresp_ulica", ""))
        self.ko_budynek.setText(d.get("adres_koresp_budynek", ""))
        self.ko_lokal.setText(d.get("adres_koresp_lokal", ""))
        self.ko_kod.setText(d.get("adres_koresp_kod_pocztowy", ""))

        self.zam_kraj.setText(d.get("adres_zam_kraj", ""))
        self.zam_woj.setText(d.get("adres_zam_wojewodztwo", ""))
        self.zam_pow.setText(d.get("adres_zam_powiat", ""))
        self.zam_gmi.setText(d.get("adres_zam_gmina", ""))
        self.zam_miasto.setText(d.get("adres_zam_miasto", ""))
        self.zam_ulica.setText(d.get("adres_zam_ulica", ""))
        self.zam_budynek.setText(d.get("adres_zam_budynek", ""))
        self.zam_lokal.setText(d.get("adres_zam_lokal", ""))
        self.zam_kod.setText(d.get("adres_zam_kod_pocztowy", ""))

        self._load_pricing_data(d)
        self._load_invoicing_data(d)

        self.partners = get_partners(d["id"]) if d.get("id") else []
        self._refresh_partners_table()

        self.contacts = get_contacts(d["id"]) if d.get("id") else []
        self._refresh_contacts_table()

        self._load_notes(d)

    # ============================================================
    #  ZBIERANIE DANYCH Z FORMULARZA
    # ============================================================

    def _collect_payload(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {}

        d["nip"] = self.f_nip.text().strip()
        d["regon"] = self.f_regon.text().strip()
        d["krs"] = self.f_krs.text().strip()
        d["pesel"] = self.f_pesel.text().strip()

        d["nazwa"] = self.f_nazwa.text().strip()
        d["nazwa_skrocona"] = self.f_nazwa_skrocona.text().strip()
        d["numer_klienta"] = self.f_numer_klienta.text().strip()

        imie = self.kont_imie.text().strip()
        nazw = self.kont_nazw.text().strip()
        d["osoba_kontaktowa"] = " ".join([x for x in [imie, nazw] if x]).strip()
        d["adres_email"] = self.kont_email.text().strip()
        d["nr_telefonu"] = self.kont_tel.text().strip()

        d["adres_doreczen_elektronicznych"] = self.f_edoreczenia.text().strip()

        d["forma_dzialalnosci"] = self.f_forma_dzial.currentText()
        d["forma_opodatkowania"] = self.f_forma_opodatk.currentText()
        d["urzad_skarbowy"] = self.f_urzad_skarbowy.currentText()
        d["kody_pkd"] = self.f_kody_pkd.text().strip()

        d["vat_czynny"] = 1 if self.VAT_checkbox.isChecked() else 0

        d["data_rozpoczecia_dg"] = self.f_start.date().toString("yyyy-MM-dd")

        d["kraj"] = self.dz_kraj.text().strip()
        d["wojewodztwo"] = self.dz_woj.text().strip()
        d["powiat"] = self.dz_pow.text().strip()
        d["gmina"] = self.dz_gmi.text().strip()
        d["miasto"] = self.dz_miasto.text().strip()
        d["ulica"] = self.dz_ulica.text().strip()
        d["adres_budynek"] = self.dz_budynek.text().strip()
        d["adres_lokal"] = self.dz_lokal.text().strip()
        d["kod_pocztowy"] = self.dz_kod.text().strip()

        d["adres_koresp_kraj"] = self.ko_kraj.text().strip()
        d["adres_koresp_wojewodztwo"] = self.ko_woj.text().strip()
        d["adres_koresp_powiat"] = self.ko_pow.text().strip()
        d["adres_koresp_gmina"] = self.ko_gmi.text().strip()
        d["adres_koresp_miasto"] = self.ko_miasto.text().strip()
        d["adres_koresp_ulica"] = self.ko_ulica.text().strip()
        d["adres_koresp_budynek"] = self.ko_budynek.text().strip()
        d["adres_koresp_lokal"] = self.ko_lokal.text().strip()
        d["adres_koresp_kod_pocztowy"] = self.ko_kod.text().strip()

        d["adres_zam_kraj"] = self.zam_kraj.text().strip()
        d["adres_zam_wojewodztwo"] = self.zam_woj.text().strip()
        d["adres_zam_powiat"] = self.zam_pow.text().strip()
        d["adres_zam_gmina"] = self.zam_gmi.text().strip()
        d["adres_zam_miasto"] = self.zam_miasto.text().strip()
        d["adres_zam_ulica"] = self.zam_ulica.text().strip()
        d["adres_zam_budynek"] = self.zam_budynek.text().strip()
        d["adres_zam_lokal"] = self.zam_lokal.text().strip()
        d["adres_zam_kod_pocztowy"] = self.zam_kod.text().strip()

        d["glowny_cennik"] = self.f_glowny_cennik.text().strip()
        d["glowny_cennik_manualny"] = 1 if self.f_glowny_cennik_manualny.isChecked() else 0

        d["limit_dokumentow"] = self.f_limit_dokumentow.value()
        d["liczba_pracownikow"] = self.f_liczba_pracownikow.value()

        d["uop_ilosc"] = self.f_uop_ilosc.value()
        d["uz_ilosc"] = self.f_uz_ilosc.value()
        d["uod_ilosc"] = self.f_uod_ilosc.value()

        d["uop_cennik"] = self.f_uop_cennik.text().strip()
        d["uz_cennik"] = self.f_uz_cennik.text().strip()
        d["uod_cennik"] = self.f_uod_cennik.text().strip()

        d["fakturowac"] = 1 if self.f_fakturowac.isChecked() else 0
        d["wysylac_do_ksef"] = 1 if self.f_wysylac_do_ksef.isChecked() else 0
        d["wysylac_do_kontrahenta"] = 1 if self.f_wysylac_do_kontrahenta.isChecked() else 0
        d["metoda_kasowa"] = 1 if self.f_metoda_kasowa.isChecked() else 0

        d["miesiac_dok_fakturowanie"] = self.f_miesiac_dok.currentText()
        d["miesiac_prac_fakturowanie"] = self.f_miesiac_prac.currentText()
        d["dzien_dostarczania_dokumentow"] = self.f_dzien_dok.value()

        d["seria_fv"] = self.f_seria_fv.text().strip()
        d["seria_kfv"] = self.f_seria_kfv.text().strip()

        d["sposob_zaplaty"] = self.f_sposob_zaplaty.currentText()

        d["status_podatkowy"] = self.f_forma_opodatk.currentText()

        d["uwagi"] = self.notes_edit.toPlainText().strip()

        if self.company_data.get("id"):
            d["id"] = self.company_data["id"]

        return d

    # ============================================================
    #  WALIDACJE
    # ============================================================

    def _validate(self, d: Dict[str, Any]) -> bool:
        if not d.get("nazwa"):
            QMessageBox.warning(self, "Błąd", "Nazwa firmy jest wymagana.")
            return False

        nip = d.get("nip", "")
        if nip and not nip.isdigit():
            QMessageBox.warning(self, "Błąd", "NIP musi zawierać tylko cyfry.")
            return False

        return True

    # ============================================================
    #  ZAPIS FIRMY + WSPÓLNIKÓW + KONTAKTÓW
    # ============================================================

    def _save(self):
        payload = self._collect_payload()

        if not self._validate(payload):
            return

        try:
            firma_id = save_company(payload)
        except Exception as e:
            QMessageBox.critical(self, "Błąd", f"Nie udało się zapisać firmy:\n{e}")
            return

        for p in self.partners:
            p["firma_id"] = firma_id
            save_partner(firma_id, p)

        for c in self.contacts:
            c["firma_id"] = firma_id
            save_contact(firma_id, c)

        QMessageBox.information(self, "OK", "Firma została zapisana.")
        self.close()

    # ============================================================
    #  ANULOWANIE
    # ============================================================

    def _cancel(self):
        self.close()

    # ============================================================
    #  DEBUG — PRINT PAYLOAD (opcjonalnie)
    # ============================================================

    def debug_print_payload(self):
        payload = self._collect_payload()
        print("=== PAYLOAD ===")
        for k, v in payload.items():
            print(f"{k}: {v}")
        print("=== END ===")
