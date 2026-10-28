# app/companies/company_form.py
import re
from typing import Dict, Any, Optional

from PyQt6.QtWidgets import (
    QDialog, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QSpinBox, QDoubleSpinBox, QComboBox, QTextEdit, QPushButton,
    QCheckBox, QTabWidget, QTableWidget, QTableWidgetItem, QHeaderView,
    QScrollArea, QMessageBox, QGridLayout, QSizePolicy
)
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QCompleter

from app.db.dao_companies import (
    get_company, save_company, get_contacts, save_contact,
    delete_contact, get_pricing_kadry, get_ksiegowosc_options,
    get_partners, save_partner, delete_partner,
    get_company_field_options, get_tax_offices,
    get_employees_list, get_pelnomocnictwa, set_pelnomocnictwa,
)
from app.db.dao_notes import (
    get_notes_for_company, save_company_note)
from app.db.dao_app_settings import get_setting_bool
from app.companies.company_logic import ceidg_fetch
from app.email.email_storage import (
    get_all_email_accounts,
    get_known_sender_addresses,
    get_email_filters_for_company,
    add_email_filter,
    delete_email_filter_for_company,
    ensure_contact_email_filters,
)
import logging
from app.companies.constants import (
    FORMA_DZIALALNOSCI, FORMA_OPODATKOWANIA, SPOSOB_ZAPLATY,
    STATUS_PODATKOWY, MIESIACE_FAKTUROWANIE, FORMY_OSOBY_FIZYCZNEJ,
)
from app.companies.company_lookup import lookup_company_by_nip
from app.companies.cennik_keys import (
    filter_cennik_keys, parse_cennik_key, typ_from_forma, vat_flag_from_status,
)
from PyQt6.QtCore import QEvent, QObject
from PyQt6.QtWidgets import QAbstractSpinBox, QApplication


ADDR_PREFIXES = [
    ("Adres działalności", "dz"),
    ("Adres korespondencyjny", "ko"),
    ("Adres zamieszkania", "zam"),
]
ADDR_FIELDS = ["miasto", "kod", "ulica", "budynek", "lokal",
               "wojewodztwo", "powiat", "gmina", "kraj"]


def _merged_options(field: str, defaults) -> list:
    """Wartości domyślne + te, które już występują w bazie."""
    values = list(defaults)
    for value in get_company_field_options(field):
        if value not in values:
            values.append(value)
    return values


class _NoWheelFilter(QObject):
    """Spinboxy i listy nie przechwytują kółka myszy, nigdy;
    zdarzenie trafia zawsze do przewijanego formularza."""

    def __init__(self, scroll: QScrollArea):
        super().__init__(scroll)
        self._scroll = scroll

    def eventFilter(self, obj, event):
        if event.type() != QEvent.Type.Wheel:
            return False
        QApplication.sendEvent(self._scroll.viewport(), event)
        return True


def _grid_row(parent_layout, labels, widgets):
    """Wiersz etykiet + pól o równych szerokościach kolumn (siatka 12-kolumnowa)."""
    n = max(len(labels), len(widgets), 1)
    span = max(12 // n, 1)
    grid = QGridLayout()
    grid.setContentsMargins(0, 0, 0, 0)
    grid.setHorizontalSpacing(8)
    grid.setVerticalSpacing(2)
    for c in range(12):
        grid.setColumnStretch(c, 1)
        grid.setColumnMinimumWidth(c, 0)
    for i, text in enumerate(labels):
        if text:
            grid.addWidget(QLabel(text), 0, i * span, 1, span)
    for i, w in enumerate(widgets):
        if isinstance(w, QHBoxLayout):
            wrapper = QWidget()
            w.setContentsMargins(0, 0, 0, 0)
            wrapper.setLayout(w)
            w = wrapper
        if not isinstance(w, QCheckBox):
            w.setSizePolicy(QSizePolicy.Policy.Ignored, w.sizePolicy().verticalPolicy())
        grid.addWidget(w, 1, i * span, 1, span)
    parent_layout.addLayout(grid)


logger = logging.getLogger(__name__)


class CompanyForm(QDialog):
    _DEFAULT_CHECKED = {"fakturowac"}
    # pole firmy -> klucze ustawień globalnych (domyślna wartość dla nowych firm)
    _GLOBAL_DEFAULTS = {
        "metoda_kasowa": ("eksport_kasowa", "faktury_metoda_kasowa", "domyslnie_metoda_kasowa"),
    }

    @classmethod
    def _checkbox_default(cls, key: str) -> bool:
        if key in cls._DEFAULT_CHECKED:
            return True
        keys = cls._GLOBAL_DEFAULTS.get(key)
        return get_setting_bool(keys, default=False) if keys else False

    def __init__(
        self,
        firma_id: Optional[int] = None,
        parent: Optional[QWidget] = None,
        actor_id: Optional[int] = None,
    ):
        super().__init__(parent)

        self.firma_id = firma_id
        self.actor_id = actor_id
        self.inputs: Dict[str, Any] = {}

        self.setWindowTitle("Firma")
        self.resize(1200, 900)

        content = QWidget()
        main_layout = QVBoxLayout(content)
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setWidget(content)
        root_layout = QVBoxLayout(self)
        root_layout.addWidget(scroll)

        # SEKCJA: DANE FIRMY
        dane_box = QWidget()
        dane_layout = QVBoxLayout(dane_box)

        title = QLabel("Dane firmy")
        title.setStyleSheet("font-weight: bold; font-size: 16px;")
        dane_layout.addWidget(title)

        def add_row(labels, widgets):
            _grid_row(dane_layout, labels, widgets)

        def make_searchable(combo: QComboBox):
            combo.setEditable(True)
            combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
            completer = QCompleter(combo.model(), combo)
            completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
            completer.setFilterMode(Qt.MatchFlag.MatchContains)
            combo.setCompleter(completer)
            combo.lineEdit().setPlaceholderText("Wpisz, aby wyszukać...")
            return combo


        nazwa = QLineEdit()
        nazwa_skrocona = QLineEdit()
        numer_klienta = QLineEdit()

        add_row(
            ["Nazwa:", "Nazwa skrócona:", "Numer klienta:"],
            [nazwa, nazwa_skrocona, numer_klienta]
        )

        from app.security.authorization import is_administrator

        if is_administrator(self.actor_id):
            nazwa_saldeo = QLineEdit()
            nazwa_saldeo.setPlaceholderText("Nazwa skrócona z Saldeo (do rozpoznawania maili)")
            add_row(["Nazwa skrócona w Saldeo:"], [nazwa_saldeo])
            self.inputs["nazwa_saldeo"] = nazwa_saldeo

        numer_umowy = QLineEdit()

        nip_box = QHBoxLayout()
        nip = QLineEdit()
        self.btn_ceidg = QPushButton("Pobierz dane")
        self.btn_ceidg.clicked.connect(self._on_ceidg)

        nip_box.addWidget(nip)
        nip_box.addWidget(self.btn_ceidg)

        regon = QLineEdit()

        add_row(
            ["Numer umowy:", "NIP:", "REGON:"],
            [numer_umowy, nip_box, regon]
        )

        krs = QLineEdit()
        pesel = QLineEdit()
        forma_dzialalnosci = QComboBox()
        forma_dzialalnosci.addItems(_merged_options("forma_dzialalnosci", FORMA_DZIALALNOSCI))
        make_searchable(forma_dzialalnosci)

        add_row(
            ["KRS:", "PESEL:", "Forma działalności:"],
            [krs, pesel, forma_dzialalnosci]
        )


        status_vat = QComboBox()
        status_vat.addItems(["Czynny", "Zwolniony", "Brak danych"])

        urzad_skarbowy = QComboBox()
        urzad_skarbowy.addItem("", None)
        for office in get_tax_offices():
            urzad_skarbowy.addItem(office["nazwa"], office["nazwa"])
        make_searchable(urzad_skarbowy)
        forma_opodatkowania = QComboBox()
        forma_opodatkowania.addItems(_merged_options("forma_opodatkowania", FORMA_OPODATKOWANIA))
        make_searchable(forma_opodatkowania)

        add_row(
            ["Status VAT:", "Urząd skarbowy:", "Forma opodatkowania:"],
            [status_vat, urzad_skarbowy, forma_opodatkowania]
        )

        analityka_fk = QLineEdit()
        kody_pkd = QLineEdit()

        status = QComboBox()
        status.addItems(["Aktywne", "Zawieszone", "Archiwalne"])

        add_row(
            ["Analityka FK:", "Kody PKD:", "Status:"],
            [analityka_fk, kody_pkd, status]
        )

        uslugi_zawieszone = QCheckBox("Biuro wstrzymało wykonywanie usług (np. brak płatności)")
        add_row(["Zawieszone usługi:"], [uslugi_zawieszone])

        konto_bankowe = QLineEdit()
        seria_fv = QLineEdit()
        seria_kfv = QLineEdit()

        add_row(
            ["Konto bankowe:", "Seria FV:", "Seria KFV:"],
            [konto_bankowe, seria_fv, seria_kfv]
        )

        sposob_zaplaty = QComboBox()
        sposob_zaplaty.addItems(_merged_options("sposob_zaplaty", SPOSOB_ZAPLATY))
        make_searchable(sposob_zaplaty)
        status_podatkowy = QComboBox()
        status_podatkowy.addItems(_merged_options("status_podatkowy", STATUS_PODATKOWY))
        make_searchable(status_podatkowy)
        kategoria_domyslna = QLineEdit()

        add_row(
            ["Sposób zapłaty:", "Status podatkowy:", "Kategoria domyślna:"],
            [sposob_zaplaty, status_podatkowy, kategoria_domyslna]
        )

        miesiac_dok_fakturowanie = QComboBox()
        miesiac_dok_fakturowanie.addItems(MIESIACE_FAKTUROWANIE)
        miesiac_prac_fakturowanie = QComboBox()
        miesiac_prac_fakturowanie.addItems(MIESIACE_FAKTUROWANIE)

        dzien_dostarczania_dokumentow = QSpinBox()
        dzien_dostarczania_dokumentow.setRange(1, 31)
        dzien_dostarczania_dokumentow.setValue(7)

        add_row(
            ["Miesiąc dok. fakturowanie:", "Miesiąc prac. fakturowanie:", "Dzień dostarczania dokumentów:"],
            [miesiac_dok_fakturowanie, miesiac_prac_fakturowanie, dzien_dostarczania_dokumentow]
        )

        ksiegowa_id = QComboBox()
        kadrowa_id = QComboBox()
        for combo in (ksiegowa_id, kadrowa_id):
            combo.addItem("— brak —", 0)
            for emp in get_employees_list():
                combo.addItem(f"{emp['imie']} {emp['nazwisko']}", emp["id"])
            make_searchable(combo)

        add_row(
            ["Księgowa:", "Kadrowa:"],
            [ksiegowa_id, kadrowa_id]
        )

        uwagi_label = QLabel("Uwagi:")
        uwagi = QTextEdit()
        uwagi.setMinimumHeight(120)

        dane_layout.addWidget(uwagi_label)
        dane_layout.addWidget(uwagi)

        # Rejestracja pól
        self.inputs.update({
            "nazwa": nazwa,
            "nazwa_skrocona": nazwa_skrocona,
            "numer_klienta": numer_klienta,
            "numer_umowy": numer_umowy,
            "nip": nip,
            "regon": regon,
            "krs": krs,
            "pesel": pesel,
            "forma_dzialalnosci": forma_dzialalnosci,
            "status_vat": status_vat,
            "urzad_skarbowy": urzad_skarbowy,
            "forma_opodatkowania": forma_opodatkowania,
            "analityka_fk": analityka_fk,
            "kody_pkd": kody_pkd,
            "status": status,
            "uslugi_zawieszone": uslugi_zawieszone,
            "seria_fv": seria_fv,
            "seria_kfv": seria_kfv,
            "sposob_zaplaty": sposob_zaplaty,
            "status_podatkowy": status_podatkowy,
            "kategoria_domyslna": kategoria_domyslna,
            "miesiac_dok_fakturowanie": miesiac_dok_fakturowanie,
            "miesiac_prac_fakturowanie": miesiac_prac_fakturowanie,
            "dzien_dostarczania_dokumentow": dzien_dostarczania_dokumentow,
            "ksiegowa_id": ksiegowa_id,
            "kadrowa_id": kadrowa_id,
            "uwagi": uwagi,
        })

        main_layout.addWidget(dane_box)

        adresy_box = QWidget()
        adresy_layout = QVBoxLayout(adresy_box)

        title_addr = QLabel("Adresy")
        title_addr.setStyleSheet("font-weight: bold; font-size: 16px;")
        adresy_layout.addWidget(title_addr)

        tabs = QTabWidget()
        adresy_layout.addWidget(tabs)

        def add_addr_row(parent_layout, labels, widgets):
            _grid_row(parent_layout, labels, widgets)

        # ADRES DZIAŁALNOŚCI
        dz_widget = QWidget()
        dz_layout = QVBoxLayout(dz_widget)

        dz_miasto = QLineEdit()
        dz_kod = QLineEdit()
        dz_ulica = QLineEdit()
        add_addr_row(dz_layout, ["Miasto:", "Kod pocztowy:", "Ulica:"], [dz_miasto, dz_kod, dz_ulica])

        dz_budynek = QLineEdit()
        dz_lokal = QLineEdit()
        dz_wojewodztwo = QLineEdit()
        add_addr_row(dz_layout, ["Budynek:", "Lokal:", "Województwo:"], [dz_budynek, dz_lokal, dz_wojewodztwo])

        dz_powiat = QLineEdit()
        dz_gmina = QLineEdit()
        dz_kraj = QLineEdit()
        add_addr_row(dz_layout, ["Powiat:", "Gmina:", "Kraj:"], [dz_powiat, dz_gmina, dz_kraj])

        tabs.addTab(dz_widget, "Adres działalności")

        # ADRES KORESPONDENCYJNY
        ko_widget = QWidget()
        ko_layout = QVBoxLayout(ko_widget)

        ko_miasto = QLineEdit()
        ko_kod = QLineEdit()
        ko_ulica = QLineEdit()
        add_addr_row(ko_layout, ["Miasto:", "Kod pocztowy:", "Ulica:"], [ko_miasto, ko_kod, ko_ulica])

        ko_budynek = QLineEdit()
        ko_lokal = QLineEdit()
        ko_wojewodztwo = QLineEdit()
        add_addr_row(ko_layout, ["Budynek:", "Lokal:", "Województwo:"], [ko_budynek, ko_lokal, ko_wojewodztwo])

        ko_powiat = QLineEdit()
        ko_gmina = QLineEdit()
        ko_kraj = QLineEdit()
        add_addr_row(ko_layout, ["Powiat:", "Gmina:", "Kraj:"], [ko_powiat, ko_gmina, ko_kraj])

        tabs.addTab(ko_widget, "Adres korespondencyjny")

        # ADRES ZAMIESZKANIA
        zam_widget = QWidget()
        zam_layout = QVBoxLayout(zam_widget)

        zam_miasto = QLineEdit()
        zam_kod = QLineEdit()
        zam_ulica = QLineEdit()
        add_addr_row(zam_layout, ["Miasto:", "Kod pocztowy:", "Ulica:"], [zam_miasto, zam_kod, zam_ulica])

        zam_budynek = QLineEdit()
        zam_lokal = QLineEdit()
        zam_wojewodztwo = QLineEdit()
        add_addr_row(zam_layout, ["Budynek:", "Lokal:", "Województwo:"], [zam_budynek, zam_lokal, zam_wojewodztwo])

        zam_powiat = QLineEdit()
        zam_gmina = QLineEdit()
        zam_kraj = QLineEdit()
        add_addr_row(zam_layout, ["Powiat:", "Gmina:", "Kraj:"], [zam_powiat, zam_gmina, zam_kraj])

        tabs.addTab(zam_widget, "Adres zamieszkania")

        # Rejestracja pól
        self.inputs.update({
            "dz_miasto": dz_miasto,
            "dz_kod": dz_kod,
            "dz_ulica": dz_ulica,
            "dz_budynek": dz_budynek,
            "dz_lokal": dz_lokal,
            "dz_wojewodztwo": dz_wojewodztwo,
            "dz_powiat": dz_powiat,
            "dz_gmina": dz_gmina,
            "dz_kraj": dz_kraj,

            "ko_miasto": ko_miasto,
            "ko_kod": ko_kod,
            "ko_ulica": ko_ulica,
            "ko_budynek": ko_budynek,
            "ko_lokal": ko_lokal,
            "ko_wojewodztwo": ko_wojewodztwo,
            "ko_powiat": ko_powiat,
            "ko_gmina": ko_gmina,
            "ko_kraj": ko_kraj,

            "zam_miasto": zam_miasto,
            "zam_kod": zam_kod,
            "zam_ulica": zam_ulica,
            "zam_budynek": zam_budynek,
            "zam_lokal": zam_lokal,
            "zam_wojewodztwo": zam_wojewodztwo,
            "zam_powiat": zam_powiat,
            "zam_gmina": zam_gmina,
            "zam_kraj": zam_kraj,
        })

        copy_row = QHBoxLayout()
        copy_row.addWidget(QLabel("Kopiuj adres z:"))
        self.addr_copy_from = QComboBox()
        self.addr_copy_to = QComboBox()
        for combo in (self.addr_copy_from, self.addr_copy_to):
            for label, prefix in ADDR_PREFIXES:
                combo.addItem(label, prefix)
        self.addr_copy_to.setCurrentIndex(1)
        btn_copy_addr = QPushButton("Kopiuj")
        btn_copy_addr.clicked.connect(self._copy_address)
        copy_row.addWidget(self.addr_copy_from)
        copy_row.addWidget(QLabel("do:"))
        copy_row.addWidget(self.addr_copy_to)
        copy_row.addWidget(btn_copy_addr)
        copy_row.addStretch()
        adresy_layout.addLayout(copy_row)

        main_layout.addWidget(adresy_box)

        # SEKCJA: CENNIKI
        cenniki_box = QWidget()
        cenniki_layout = QVBoxLayout(cenniki_box)

        title_cenniki = QLabel("Cenniki")
        title_cenniki.setStyleSheet("font-weight: bold; font-size: 16px;")
        cenniki_layout.addWidget(title_cenniki)

        def add_cennik_row(labels, widgets):
            _grid_row(cenniki_layout, labels, widgets)

        glowny_cennik = QComboBox()
        glowny_cennik.addItem("", "")
        make_searchable(glowny_cennik)

        cena_netto_ksiegowosc = QDoubleSpinBox()
        cena_netto_ksiegowosc.setRange(0, 1000000)
        cena_netto_ksiegowosc.setDecimals(2)

        limit_dokumentow = QSpinBox()
        limit_dokumentow.setRange(0, 100000)

        cena_ponad_limit = QDoubleSpinBox()
        cena_ponad_limit.setRange(0, 100000)
        cena_ponad_limit.setDecimals(2)

        zdrowotna = QDoubleSpinBox()
        zdrowotna.setRange(0, 100000)
        zdrowotna.setDecimals(2)

        add_cennik_row(
            ["Główny cennik:", "Cena netto:", "Zdrowotna:"],
            [glowny_cennik, cena_netto_ksiegowosc, zdrowotna]
        )
        add_cennik_row(
            ["Limit dokumentów:", "Cena ponad limit:"],
            [limit_dokumentow, cena_ponad_limit]
        )

        wysylac_do_ksef = QCheckBox()
        wysylac_do_kontrahenta = QCheckBox()
        metoda_kasowa = QCheckBox()
        metoda_kasowa.setChecked(self._checkbox_default("metoda_kasowa"))
        wliczac_zdrowotna = QCheckBox()

        add_cennik_row(
            ["Wysyłać do KSeF:", "Wysyłać do kontrahenta:", "Metoda kasowa:", "Uwzględniać zdrowotną:"],
            [wysylac_do_ksef, wysylac_do_kontrahenta, metoda_kasowa, wliczac_zdrowotna]
        )

        fakturowac = QCheckBox()
        fakturowac.setChecked(True)
        add_cennik_row(["Fakturować:"], [fakturowac])

        stala_kwota_ksiegowosc = QDoubleSpinBox()
        stala_kwota_ksiegowosc.setRange(0, 1000000)
        stala_kwota_ksiegowosc.setDecimals(2)
        stala_kwota_ksiegowosc.setSpecialValueText("brak")
        stala_kwota_kadry = QDoubleSpinBox()
        stala_kwota_kadry.setRange(0, 1000000)
        stala_kwota_kadry.setDecimals(2)
        stala_kwota_kadry.setSpecialValueText("brak")
        add_cennik_row(
            ["Stała kwota netto - księgowość:", "Stała kwota netto - kadry:"],
            [stala_kwota_ksiegowosc, stala_kwota_kadry],
        )

        self.inputs.update({
            "stala_kwota_ksiegowosc": stala_kwota_ksiegowosc,
            "stala_kwota_kadry": stala_kwota_kadry,
        })

        self.inputs.update({
            "glowny_cennik": glowny_cennik,
            "cena_netto_ksiegowosc": cena_netto_ksiegowosc,
            "limit_dokumentow": limit_dokumentow,
            "cena_ponad_limit": cena_ponad_limit,
            "zdrowotna": zdrowotna,
            "wysylac_do_ksef": wysylac_do_ksef,
            "wysylac_do_kontrahenta": wysylac_do_kontrahenta,
            "metoda_kasowa": metoda_kasowa,
            "wliczac_zdrowotna": wliczac_zdrowotna,
            "fakturowac": fakturowac,
        })

        main_layout.addWidget(cenniki_box)
        # ============================================================
        # SEKCJA: KADRY
        # ============================================================
        kadry_box = QWidget()
        kadry_layout = QVBoxLayout(kadry_box)

        title_kadry = QLabel("Kadry")
        title_kadry.setStyleSheet("font-weight: bold; font-size: 16px;")
        kadry_layout.addWidget(title_kadry)

        def add_kadry_row(labels, widgets):
            _grid_row(kadry_layout, labels, widgets)

        kadry_opts = get_pricing_kadry()

        def add_kadry_cennik_options(combo: QComboBox, typ: str) -> None:
            """Pokaż nazwę usługi z cennika, zapisując techniczny klucz."""
            combo.addItem("", "")
            nazwy = {
                "uop": ("UoP", "umowa o pracę"),
                "uz": ("UZ", "umowa zlecenie"),
                "uod": ("UoD", "umowa o dzieło"),
            }
            skrot, pelna = nazwy[typ]
            for pozycja in kadry_opts:
                klucz = str(pozycja.get("klucz") or "").strip()
                opis = str(pozycja.get("opis") or "").strip()
                tekst = f"{klucz} {opis}".casefold()
                if not (
                    re.search(rf"(?<![a-z]){skrot.casefold()}(?![a-z])", tekst)
                    or pelna in tekst
                ):
                    continue
                nazwa = opis or f"Usługa kadrowo-płacowa - {skrot}"
                cena = float(pozycja.get("cena_netto_kadry") or 0)
                combo.addItem(f"{nazwa} — {cena:.2f} zł netto ({klucz})", klucz)
            make_searchable(combo)

        # -----------------------------
        # RZĄD 1
        # -----------------------------
        uop_ilosc = QSpinBox()
        uop_ilosc.setRange(0, 10000)

        uop_cennik = QComboBox()
        add_kadry_cennik_options(uop_cennik, "uop")

        add_kadry_row(["UoP ilość:", "UoP cennik:"], [uop_ilosc, uop_cennik])

        uz_ilosc = QSpinBox()
        uz_ilosc.setRange(0, 10000)

        uz_cennik = QComboBox()
        add_kadry_cennik_options(uz_cennik, "uz")

        add_kadry_row(["UZ ilość:", "UZ cennik:"], [uz_ilosc, uz_cennik])

        uod_ilosc = QSpinBox()
        uod_ilosc.setRange(0, 10000)

        uod_cennik = QComboBox()
        add_kadry_cennik_options(uod_cennik, "uod")

        add_kadry_row(["UoD ilość:", "UoD cennik:"], [uod_ilosc, uod_cennik])

        self.inputs.update({
            "uop_ilosc": uop_ilosc,
            "uop_cennik": uop_cennik,
            "uz_ilosc": uz_ilosc,
            "uz_cennik": uz_cennik,
            "uod_ilosc": uod_ilosc,
            "uod_cennik": uod_cennik,
        })

        main_layout.addWidget(kadry_box)

        # ============================================================
        # SEKCJA: KONTAKTY
        # ============================================================
        kontakty_box = QWidget()
        kontakty_layout = QVBoxLayout(kontakty_box)

        title_kontakty = QLabel("Kontakty")
        title_kontakty.setStyleSheet("font-weight: bold; font-size: 16px;")
        kontakty_layout.addWidget(title_kontakty)

        self.contacts_table = QTableWidget(0, 5)
        self.contacts_table.setHorizontalHeaderLabels(["Imię", "Nazwisko", "E-mail", "Telefon", "Rola"])
        self.contacts_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.contacts_table.setMinimumHeight(200)
        kontakty_layout.addWidget(self.contacts_table)

        kont_form = QGridLayout()
        kont_form.setHorizontalSpacing(8)
        kont_form.setVerticalSpacing(2)
        kont_form.setColumnStretch(0, 1)
        kont_form.setColumnStretch(1, 1)

        kont_imie = QLineEdit()
        kont_nazwisko = QLineEdit()
        kont_email = QLineEdit()
        kont_tel = QLineEdit()
        kont_rola = QLineEdit()

        for r, (lbl, widget, col) in enumerate([
            ("Imię:", kont_imie, 0),
            ("Nazwisko:", kont_nazwisko, 0),
            ("E-mail:", kont_email, 1),
            ("Telefon:", kont_tel, 1),
            ("Rola:", kont_rola, 1),
        ]):
            row = (r if col == 0 else r - 2) * 2
            kont_form.addWidget(QLabel(lbl), row, col)
            kont_form.addWidget(widget, row + 1, col)

        kontakty_layout.addLayout(kont_form)

        btn_add_contact = QPushButton("Dodaj kontakt")
        btn_del_contact = QPushButton("Usuń kontakt")
        btn_save_contact = QPushButton("Zapisz zaznaczony")

        btn_row = QHBoxLayout()
        btn_row.addWidget(btn_add_contact)
        btn_row.addWidget(btn_del_contact)
        btn_row.addWidget(btn_save_contact)
        kontakty_layout.addLayout(btn_row)

        self.inputs.update({
            "kont_imie": kont_imie,
            "kont_nazwisko": kont_nazwisko,
            "kont_email": kont_email,
            "kont_tel": kont_tel,
            "kont_rola": kont_rola,
        })

        main_layout.addWidget(kontakty_box)

        # ============================================================
        # SEKCJA: PEŁNOMOCNICTWA (PEL) / UPOWAŻNIENIA (UPL)
        # ============================================================
        pel_box = QWidget()
        pel_layout = QVBoxLayout(pel_box)
        title_pel = QLabel("Pełnomocnictwa pracowników")
        title_pel.setStyleSheet("font-weight: bold; font-size: 16px;")
        pel_layout.addWidget(title_pel)

        self.pel_table = QTableWidget(0, 3)
        self.pel_table.setHorizontalHeaderLabels(["Pracownik", "PEL", "UPL"])
        self.pel_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )
        self.pel_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.pel_table.setMinimumHeight(180)
        pel_layout.addWidget(self.pel_table)
        main_layout.addWidget(pel_box)
        self._populate_pelnomocnictwa()
        # ============================================================
        # FILTRY EMAIL FIRMY
        # ============================================================
        email_box = QWidget()
        email_layout = QVBoxLayout(email_box)
        email_layout.addWidget(QLabel("Filtry email firmy"))
        self.email_filters_table = QTableWidget(0, 3)
        self.email_filters_table.setHorizontalHeaderLabels(
            ["Adres/nadawca zawiera", "Konto", "Folder docelowy"]
        )
        self.email_filters_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )
        email_layout.addWidget(self.email_filters_table)

        filter_form = QHBoxLayout()
        self.filter_account = QComboBox()
        for account in get_all_email_accounts():
            self.filter_account.addItem(account["email"], account["id"])
        self.filter_sender = QComboBox()
        self.filter_sender.setEditable(True)
        self.filter_sender.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.filter_sender.lineEdit().setPlaceholderText("Wybierz nadawcę ze skrzynki lub wpisz adres")
        sender_completer = QCompleter(self.filter_sender.model(), self.filter_sender)
        sender_completer.setFilterMode(Qt.MatchFlag.MatchContains)
        sender_completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.filter_sender.setCompleter(sender_completer)
        self.filter_account.currentIndexChanged.connect(self._reload_filter_senders)
        self._reload_filter_senders()
        self.filter_folder = QComboBox()
        self.filter_folder.setEditable(True)
        self.filter_folder.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.filter_folder.lineEdit().setPlaceholderText("Wybierz folder lub wpisz nowy")
        folder_completer = QCompleter(self.filter_folder.model(), self.filter_folder)
        folder_completer.setFilterMode(Qt.MatchFlag.MatchContains)
        folder_completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.filter_folder.setCompleter(folder_completer)
        self.filter_account.currentIndexChanged.connect(self._reload_filter_folders)
        self._reload_filter_folders()
        btn_add_filter = QPushButton("Dodaj filtr")
        btn_delete_filter = QPushButton("Usuń zaznaczony")
        btn_add_filter.clicked.connect(self._add_company_email_filter)
        btn_delete_filter.clicked.connect(self._delete_company_email_filter)
        filter_form.addWidget(self.filter_account)
        filter_form.addWidget(self.filter_sender)
        filter_form.addWidget(self.filter_folder)
        filter_form.addWidget(btn_add_filter)
        filter_form.addWidget(btn_delete_filter)
        email_layout.addLayout(filter_form)
        main_layout.addWidget(email_box)

        # ============================================================
        # SEKCJA: WSPÓLNICY
        # ============================================================
        wsp_box = QWidget()
        wsp_layout = QVBoxLayout(wsp_box)

        title_wsp = QLabel("Wspólnicy")
        title_wsp.setStyleSheet("font-weight: bold; font-size: 16px;")
        wsp_layout.addWidget(title_wsp)

        self.partners_table = QTableWidget(0, 4)
        self.partners_table.setHorizontalHeaderLabels(["Imię pierwsze", "Imię drugie", "Nazwisko", "Procent"])
        self.partners_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.partners_table.setMinimumHeight(200)
        wsp_layout.addWidget(self.partners_table)

        wsp_form = QHBoxLayout()
        wsp_left = QVBoxLayout()
        wsp_right = QVBoxLayout()

        self.partner_imie1 = QLineEdit()
        self.partner_imie2 = QLineEdit()
        self.partner_nazwisko = QLineEdit()

        self.partner_procent = QDoubleSpinBox()
        self.partner_procent.setRange(0, 100)
        self.partner_procent.setDecimals(2)

        self.edited_partner_id = None

        for lbl, widget in [
            ("Imię pierwsze:", self.partner_imie1),
            ("Imię drugie:", self.partner_imie2),
        ]:
            lab = QLabel(lbl)
            wsp_left.addWidget(lab)
            wsp_left.addWidget(widget)

        for lbl, widget in [
            ("Nazwisko:", self.partner_nazwisko),
            ("Procent:", self.partner_procent),
        ]:
            lab = QLabel(lbl)
            wsp_right.addWidget(lab)
            wsp_right.addWidget(widget)

        wsp_form.addLayout(wsp_left)
        wsp_form.addLayout(wsp_right)
        wsp_layout.addLayout(wsp_form)

        btn_add_partner = QPushButton("Dodaj wspólnika")
        btn_del_partner = QPushButton("Usuń wspólnika")
        btn_save_partner = QPushButton("Zapisz zaznaczonego")

        wsp_btn_row = QHBoxLayout()
        wsp_btn_row.addWidget(btn_add_partner)
        wsp_btn_row.addWidget(btn_del_partner)
        wsp_btn_row.addWidget(btn_save_partner)
        wsp_layout.addLayout(wsp_btn_row)

        main_layout.addWidget(wsp_box)

        # ============================================================
        # SEKCJA: NOTATKI
        # ============================================================
        not_box = QWidget()
        not_layout = QVBoxLayout(not_box)

        title_not = QLabel("Notatki")
        title_not.setStyleSheet("font-weight: bold; font-size: 16px;")
        not_layout.addWidget(title_not)

        self.notes_table = QTableWidget(0, 3)
        self.notes_table.setHorizontalHeaderLabels(["Treść", "Autor", "Data"])
        self.notes_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.notes_table.setMinimumHeight(200)
        not_layout.addWidget(self.notes_table)

        self.note_edit = QTextEdit()
        self.note_edit.setMinimumHeight(120)
        not_layout.addWidget(QLabel("Nowa notatka:"))
        not_layout.addWidget(self.note_edit)

        btn_add_note = QPushButton("Dodaj notatkę")
        not_layout.addWidget(btn_add_note)

        main_layout.addWidget(not_box)

        # ============================================================
        # PRZYCISKI ZAPISZ / ANULUJ
        # ============================================================
        btn_save = QPushButton("Zapisz")
        btn_cancel = QPushButton("Anuluj")

        btn_bottom = QHBoxLayout()
        btn_bottom.addStretch()
        btn_bottom.addWidget(btn_save)
        btn_bottom.addWidget(btn_cancel)

        main_layout.addLayout(btn_bottom)

        # ============================================================
        # LOGIKA PRZYCISKÓW
        # ============================================================
        btn_save.clicked.connect(self._on_save)
        btn_cancel.clicked.connect(self.reject)

        btn_add_contact.clicked.connect(self._add_contact)
        btn_del_contact.clicked.connect(self._delete_contact)
        btn_save_contact.clicked.connect(self._save_selected_contact)

        btn_add_partner.clicked.connect(self._add_partner)
        btn_del_partner.clicked.connect(self._delete_partner)
        btn_save_partner.clicked.connect(self._save_selected_partner)
        self.partners_table.itemSelectionChanged.connect(self._partner_selected)

        btn_add_note.clicked.connect(self._add_note)

        # Kółko myszy przewija formularz, a nie zmienia wartości pól
        self._wheel_filter = _NoWheelFilter(scroll)
        for widget in self.findChildren((QAbstractSpinBox, QComboBox)):
            widget.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
            widget.installEventFilter(self._wheel_filter)

        self.inputs["pesel"].setPlaceholderText("Wymagany dla osoby fizycznej / JDG")

        # ============================================================
        # ŁADOWANIE DANYCH FIRMY
        # ============================================================
        if self.firma_id:
            self._load_company()
            self._load_contacts()
            self._load_email_filters()
            self._load_partners()
            self._load_notes()
        else:
            self._refresh_cennik_options()

        for name in ("forma_opodatkowania", "status_vat"):
            self.inputs[name].currentTextChanged.connect(lambda _t: self._refresh_cennik_options())
        self.inputs["glowny_cennik"].activated.connect(lambda _i: self._on_cennik_chosen())

    # ============================================================
    # CENNIK GŁÓWNY: filtr, sortowanie, autowypełnianie
    # ============================================================
    def _cennik_options(self) -> Dict[str, Dict[str, Any]]:
        if not hasattr(self, "_cennik_cache"):
            self._cennik_cache = {o["klucz"]: o for o in get_ksiegowosc_options()}
        return self._cennik_cache

    def _refresh_cennik_options(self):
        combo = self.inputs["glowny_cennik"]
        current = combo.currentText().strip()
        typ = typ_from_forma(self.inputs["forma_opodatkowania"].currentText())
        vat = vat_flag_from_status(self.inputs["status_vat"].currentText())
        keys = filter_cennik_keys(self._cennik_options().keys(), typ, vat)
        if current and current not in keys:
            keys.insert(0, current)
        combo.blockSignals(True)
        combo.clear()
        combo.addItem("", "")
        for key in keys:
            combo.addItem(key, key)
        combo.setCurrentIndex(max(combo.findText(current), 0))
        combo.blockSignals(False)

    def _fill_prices_from_cennik(self, only_missing: bool):
        option = self._cennik_options().get(self.inputs["glowny_cennik"].currentText().strip())
        if not option:
            return
        info = parse_cennik_key(option["klucz"])
        values = {
            "cena_netto_ksiegowosc": option["netto"],
            "zdrowotna": option["zdrowotna"],
            "cena_ponad_limit": option["ponad_limit"],
        }
        for name, value in values.items():
            widget = self.inputs[name]
            if not only_missing or not widget.value():
                widget.setValue(float(value or 0))
        limit_widget = self.inputs["limit_dokumentow"]
        if info["limit"] is not None and (not only_missing or not limit_widget.value()):
            limit_widget.setValue(info["limit"])

    def _on_cennik_chosen(self):
        self._fill_prices_from_cennik(only_missing=False)

    # ============================================================
    # CEIDG / GUS
    # ============================================================
    def _on_ceidg(self):
        try:
            form = self.inputs["forma_dzialalnosci"].currentText()
            ceidg_fetch(
                self,
                fetch=lambda nip: lookup_company_by_nip(nip, forma_dzialalnosci=form),
                label="Pobierz dane",
                title="Dane firmy",
            )
        except Exception as e:
            logger.warning("Błąd pobierania danych firmy: %s", e)
            QMessageBox.warning(self, "Dane firmy", f"Błąd pobierania danych: {e}")

    def _copy_address(self):
        src = self.addr_copy_from.currentData()
        dst = self.addr_copy_to.currentData()
        if src == dst:
            QMessageBox.information(self, "Adres", "Wybierz dwa różne adresy.")
            return
        for field in ADDR_FIELDS:
            self.inputs[f"{dst}_{field}"].setText(self.inputs[f"{src}_{field}"].text())

    def _is_natural_person(self) -> bool:
        forma = self.inputs["forma_dzialalnosci"].currentText().strip().lower()
        return any(forma.startswith(f.lower()) for f in FORMY_OSOBY_FIZYCZNEJ)

    def _validate(self) -> Optional[str]:
        if not self.inputs["nazwa"].text().strip():
            return "Podaj nazwę firmy."
        if self._is_natural_person():
            pesel = self.inputs["pesel"].text().strip()
            if not pesel:
                return "PESEL jest wymagany dla jednoosobowej działalności gospodarczej."
            if not (pesel.isdigit() and len(pesel) == 11):
                return "PESEL musi składać się z 11 cyfr."
        return None

    # ============================================================
    # ZBIERANIE PAYLOADU
    # ============================================================
    def _collect_payload(self) -> Dict[str, Any]:
        payload = {}

        if self.firma_id:
            payload["id"] = self.firma_id

        for key, widget in self.inputs.items():
            if isinstance(widget, QLineEdit):
                payload[key] = widget.text().strip()
            elif isinstance(widget, QTextEdit):
                payload[key] = widget.toPlainText().strip()
            elif isinstance(widget, QSpinBox):
                payload[key] = widget.value()
            elif isinstance(widget, QDoubleSpinBox):
                payload[key] = widget.value()
            elif isinstance(widget, QComboBox):
                if key in ("ksiegowa_id", "kadrowa_id"):
                    payload[key] = int(widget.currentData() or 0)
                elif key in ("uop_cennik", "uz_cennik", "uod_cennik"):
                    payload[key] = widget.currentData() or ""
                else:
                    payload[key] = widget.currentText()
            elif isinstance(widget, QCheckBox):
                if key in self._GLOBAL_DEFAULTS:
                    # zgodne z ustawieniem globalnym = brak nadpisania (NULL)
                    checked = widget.isChecked()
                    payload[key] = None if checked == self._checkbox_default(key) else int(checked)
                else:
                    payload[key] = 1 if widget.isChecked() else 0
        return payload

    def _load_email_filters(self):
        company = get_company(self.firma_id)
        folder_name = (
            (company or {}).get("nazwa_skrocona")
            or (company or {}).get("nazwa")
            or str(self.firma_id)
        )
        ensure_contact_email_filters(self.firma_id, folder_name)
        self.email_filters_table.setRowCount(0)
        for rule in get_email_filters_for_company(self.firma_id):
            row = self.email_filters_table.rowCount()
            self.email_filters_table.insertRow(row)
            self.email_filters_table.setItem(row, 0, QTableWidgetItem(rule.get("od") or rule.get("nadawca_zawiera") or ""))
            self.email_filters_table.setItem(row, 1, QTableWidgetItem(rule.get("konto_email", "")))
            from app.email.email_helpers import friendly_folder_name
            self.email_filters_table.setItem(row, 2, QTableWidgetItem(friendly_folder_name(rule.get("folder_docelowy", ""))))
            self.email_filters_table.item(row, 0).setData(Qt.ItemDataRole.UserRole, rule["id"])

    def _reload_filter_folders(self):
        from app.email.email_helpers import friendly_folder_name
        from app.email.email_sync import selectable_folder_names
        account_id = self.filter_account.currentData()
        names = (
            sorted({friendly_folder_name(n) for n in selectable_folder_names(int(account_id))} - {""}, key=str.casefold)
            if account_id is not None else []
        )
        self.filter_folder.clear()
        self.filter_folder.addItems(names)
        self.filter_folder.setCurrentIndex(-1)
        self.filter_folder.setEditText("")

    def _reload_filter_senders(self):
        current = self.filter_sender.currentText()
        addresses = set(get_known_sender_addresses(self.filter_account.currentData()))
        if self.firma_id:
            for contact in get_contacts(self.firma_id):
                email = (contact.get("email") or "").strip().lower()
                if "@" in email:
                    addresses.add(email)
        self.filter_sender.clear()
        self.filter_sender.addItems(sorted(addresses))
        self.filter_sender.setEditText(current)

    def _ensure_company_saved(self) -> bool:
        """Nowa firma musi istnieć w bazie, zanim dodamy do niej wspólników, kontakty i filtry."""
        if self.firma_id:
            return True
        error = self._validate()
        if error:
            QMessageBox.warning(
                self, "Firma", f"Aby dodać pozycję, najpierw uzupełnij dane firmy.\n{error}"
            )
            return False
        try:
            self.firma_id = save_company(self._collect_payload(), actor_id=self.actor_id)
        except Exception as e:
            logger.exception("Błąd zapisu firmy")
            QMessageBox.critical(self, "Firma", f"Nie udało się zapisać firmy: {e}")
            return False
        self._populate_pelnomocnictwa()
        return True

    def _add_company_email_filter(self):
        if not self._ensure_company_saved():
            return
        sender = self.filter_sender.currentText().strip()
        folder = self.filter_folder.currentText().strip()
        if not folder and "@" in sender:
            folder = sender.rsplit("@", 1)[1].strip().lower()
        if not folder:
            folder = self.inputs["nazwa_skrocona"].text().strip()
        account_id = self.filter_account.currentData()
        if not sender or account_id is None or not folder:
            QMessageBox.information(
                self, "Filtr email", "Wybierz konto i nadawcę oraz podaj folder docelowy."
            )
            return
        add_email_filter(
            int(account_id), self.firma_id, sender, None, None, None, None,
            folder, 0, 0, 1,
        )
        self.filter_sender.setEditText("")
        self.filter_folder.setCurrentIndex(-1)
        self.filter_folder.setEditText("")

    def _delete_company_email_filter(self):
        row = self.email_filters_table.currentRow()
        if row < 0 or not self.firma_id:
            return
        item = self.email_filters_table.item(row, 0)
        filter_id = item.data(Qt.ItemDataRole.UserRole) if item else None
        if filter_id:
            delete_email_filter_for_company(int(filter_id), self.firma_id)
            self._load_email_filters()

    # ============================================================
    # ZAPIS
    # ============================================================
    # ============================================================
    # PEL / UPL
    # ============================================================
    def _populate_pelnomocnictwa(self):
        granted = get_pelnomocnictwa(self.firma_id) if self.firma_id else {}
        employees = get_employees_list()
        self.pel_table.setRowCount(len(employees))
        for row, emp in enumerate(employees):
            name_item = QTableWidgetItem(f"{emp['imie']} {emp['nazwisko']}")
            name_item.setData(Qt.ItemDataRole.UserRole, emp["id"])
            self.pel_table.setItem(row, 0, name_item)
            for col, typ in ((1, "PEL"), (2, "UPL")):
                item = QTableWidgetItem()
                item.setFlags(
                    Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable
                )
                item.setCheckState(
                    Qt.CheckState.Checked
                    if emp["id"] in granted.get(typ, [])
                    else Qt.CheckState.Unchecked
                )
                self.pel_table.setItem(row, col, item)

    def _save_pelnomocnictwa(self):
        selected = {"PEL": [], "UPL": []}
        for row in range(self.pel_table.rowCount()):
            emp_id = self.pel_table.item(row, 0).data(Qt.ItemDataRole.UserRole)
            for col, typ in ((1, "PEL"), (2, "UPL")):
                if self.pel_table.item(row, col).checkState() == Qt.CheckState.Checked:
                    selected[typ].append(emp_id)
        for typ, ids in selected.items():
            set_pelnomocnictwa(self.firma_id, typ, ids)

    def _on_save(self):
        error = self._validate()
        if error:
            QMessageBox.warning(self, "Firma", error)
            return
        payload = self._collect_payload()
        try:
            self.firma_id = save_company(payload, actor_id=self.actor_id)
            self._save_pelnomocnictwa()
            self._sync_contact_email_filters()
            self.accept()
        except Exception as e:
            logger.exception("Błąd zapisu firmy")
            QMessageBox.critical(self, "Firma", f"Nie udało się zapisać firmy: {e}")

    @staticmethod
    def _set_combo_value(key: str, widget: QComboBox, val) -> None:
        if val is None:
            return
        if key in ("ksiegowa_id", "kadrowa_id"):
            idx = widget.findData(int(val or 0))
            widget.setCurrentIndex(max(idx, 0))
            return
        text = str(val)
        if key in ("uop_cennik", "uz_cennik", "uod_cennik"):
            idx = widget.findData(text)
            if idx < 0:
                widget.addItem(text, text)  # Zachowaj historyczny klucz spoza cennika
                idx = widget.findData(text)
        else:
            if text and widget.findText(text) < 0:
                widget.addItem(text, text)
            idx = widget.findText(text)
        if idx >= 0:
            widget.setCurrentIndex(idx)

    # ============================================================
    # ŁADOWANIE DANYCH
    # ============================================================
    def _load_company(self):
        data = get_company(self.firma_id)
        if not data:
            return

        # Kolumny DB (miasto, adres_koresp_..., adres_zam_...) mają
        # inne nazwy niż pola formularza (dz_..., ko_..., zam_...).
        # Bez mapowania ponowne otwarcie pokazuje puste adresy, a kolejny
        # zapis formularza może nadpisać istniejące adresy pustymi danymi.
        data = self._payload_to_form_keys(data)

        for key, widget in self.inputs.items():
            val = data.get(key)
            if isinstance(widget, QLineEdit):
                widget.setText(str(val or ""))
            elif isinstance(widget, QTextEdit):
                widget.setPlainText(str(val or ""))
            elif isinstance(widget, QSpinBox):
                widget.setValue(int(val or (7 if key == "dzien_dostarczania_dokumentow" else 0)))
            elif isinstance(widget, QDoubleSpinBox):
                widget.setValue(float(val or 0))
            elif isinstance(widget, QComboBox):
                self._set_combo_value(key, widget, val)
            elif isinstance(widget, QCheckBox):
                widget.setChecked(bool(val) if val is not None else self._checkbox_default(key))
        self._refresh_cennik_options()
        self._fill_prices_from_cennik(only_missing=True)

    @staticmethod
    def _payload_to_form_keys(payload: Dict[str, Any]) -> Dict[str, Any]:
        """Klucze z importu (kolumny bazy) -> klucze pól formularza."""
        out = dict(payload)
        address_map = {
            "dz": {"miasto": "miasto", "kod": "kod_pocztowy", "ulica": "ulica",
                   "budynek": "adres_budynek", "lokal": "adres_lokal",
                   "wojewodztwo": "wojewodztwo", "powiat": "powiat",
                   "gmina": "gmina", "kraj": "kraj"},
            "ko": {f: f"adres_koresp_{c}" for f, c in (
                ("miasto", "miasto"), ("kod", "kod_pocztowy"), ("ulica", "ulica"),
                ("budynek", "budynek"), ("lokal", "lokal"), ("wojewodztwo", "wojewodztwo"),
                ("powiat", "powiat"), ("gmina", "gmina"), ("kraj", "kraj"))},
            "zam": {f: f"adres_zam_{c}" for f, c in (
                ("miasto", "miasto"), ("kod", "kod_pocztowy"), ("ulica", "ulica"),
                ("budynek", "budynek"), ("lokal", "lokal"), ("wojewodztwo", "wojewodztwo"),
                ("powiat", "powiat"), ("gmina", "gmina"), ("kraj", "kraj"))},
        }
        for prefix, fields in address_map.items():
            for field, source in fields.items():
                if payload.get(source) not in (None, ""):
                    out.setdefault(f"{prefix}_{field}", payload[source])
        person = str(payload.get("osoba_kontaktowa") or "").strip()
        first, _, last = person.partition(" ")
        out.setdefault("kont_imie", first)
        out.setdefault("kont_nazwisko", last.strip())
        out.setdefault("kont_email", payload.get("adres_email") or "")
        out.setdefault("kont_tel", payload.get("nr_telefonu") or "")
        return out

    # ============================================================
    # ŁADOWANIE DANYCH Z PAYLOADU (IMPORT WIZARD)
    # ============================================================
    def _load_company_from_payload(self, payload: Dict[str, Any]):
        """
        Ładuje dane z payloadu wygenerowanego przez importer.
        Payload zawiera tylko podstawowe pola — reszta zostaje pusta.
        """
        payload = self._payload_to_form_keys(payload)
        for key, widget in self.inputs.items():
            val = payload.get(key)

            if isinstance(widget, QLineEdit):
                widget.setText(str(val or ""))

            elif isinstance(widget, QTextEdit):
                widget.setPlainText(str(val or ""))

            elif isinstance(widget, QSpinBox):
                try:
                    widget.setValue(int(val or (7 if key == "dzien_dostarczania_dokumentow" else 0)))
                except Exception:
                    widget.setValue(7 if key == "dzien_dostarczania_dokumentow" else 0)

            elif isinstance(widget, QDoubleSpinBox):
                try:
                    widget.setValue(float(val or 0))
                except Exception:
                    widget.setValue(0.0)

            elif isinstance(widget, QComboBox):
                self._set_combo_value(key, widget, val)


            elif isinstance(widget, QCheckBox):
                widget.setChecked(bool(val) if val is not None else self._checkbox_default(key))

        self._refresh_cennik_options()
        self._fill_prices_from_cennik(only_missing=True)

    # ============================================================
    def _load_contacts(self):
        if not self.firma_id:
            return
        rows = get_contacts(self.firma_id)
        self.contacts_table.setRowCount(0)
        for r in rows:
            row = self.contacts_table.rowCount()
            self.contacts_table.insertRow(row)
            for col, key in enumerate(["imie", "nazwisko", "email", "telefon", "rola"]):
                self.contacts_table.setItem(row, col, QTableWidgetItem(str(r.get(key) or "")))
            self.contacts_table.item(row, 0).setData(Qt.ItemDataRole.UserRole, r.get("id"))

    def _add_contact(self):
        if not self._ensure_company_saved():
            return
        payload = {
            "firma_id": self.firma_id,
            "imie": self.inputs["kont_imie"].text().strip(),
            "nazwisko": self.inputs["kont_nazwisko"].text().strip(),
            "email": self.inputs["kont_email"].text().strip(),
            "telefon": self.inputs["kont_tel"].text().strip(),
            "rola": self.inputs["kont_rola"].text().strip(),
        }
        if not any(payload[k] for k in ("imie", "nazwisko", "email", "telefon")):
            QMessageBox.information(self, "Kontakty", "Wpisz dane kontaktu.")
            return
        save_contact(self.firma_id, payload)
        for key in ("kont_imie", "kont_nazwisko", "kont_email", "kont_tel", "kont_rola"):
            self.inputs[key].clear()
        self._sync_contact_email_filters()
        self._load_contacts()
        self._load_email_filters()
        self._reload_filter_senders()

    def _delete_contact(self):
        row = self.contacts_table.currentRow()
        if row < 0:
            return
        email = self.contacts_table.item(row, 2).text()
        contact_id = self.contacts_table.item(row, 0).data(Qt.ItemDataRole.UserRole)
        if contact_id:
            delete_contact(int(contact_id))
        self._load_contacts()

    def _save_selected_contact(self):
        row = self.contacts_table.currentRow()
        if row < 0:
            return
        payload = {
            "firma_id": self.firma_id,
            "imie": self.contacts_table.item(row, 0).text(),
            "nazwisko": self.contacts_table.item(row, 1).text(),
            "email": self.contacts_table.item(row, 2).text(),
            "telefon": self.contacts_table.item(row, 3).text(),
            "rola": self.contacts_table.item(row, 4).text(),
        }
        contact_id = self.contacts_table.item(row, 0).data(Qt.ItemDataRole.UserRole)
        if contact_id:
            payload["id"] = int(contact_id)
        save_contact(self.firma_id, payload)
        self._sync_contact_email_filters()
        self._load_contacts()

    def _sync_contact_email_filters(self):
        company = get_company(self.firma_id) or {}
        folder_name = (
            company.get("nazwa_skrocona")
            or self.inputs["nazwa_skrocona"].text().strip()
            or company.get("nazwa")
            or self.inputs["nazwa"].text().strip()
            or str(self.firma_id)
        )
        ensure_contact_email_filters(self.firma_id, folder_name)

    # ============================================================
    # WSPÓLNICY
    # ============================================================
    def _load_partners(self):
        if not self.firma_id:
            return

        rows = get_partners(self.firma_id)
        self.partners_table.setRowCount(0)

        for r in rows:
            row = self.partners_table.rowCount()
            self.partners_table.insertRow(row)
            for col, key in enumerate(
                    ["imie_pierwsze", "imie_drugie", "nazwisko", "procent"]
            ):
                self.partners_table.setItem(
                    row,
                    col,
                    QTableWidgetItem(
                        str(r.get(key) or "")))

            self.partners_table.item(row,0).setData(Qt.ItemDataRole.UserRole, r.get("id"))

    def _add_partner(self):

        if not self._ensure_company_saved():
            return

        if not self._validate_partner_percent():
            return

        payload = {
            "imie_pierwsze": self.partner_imie1.text().strip(),
            "imie_drugie": self.partner_imie2.text().strip(),
            "nazwisko": self.partner_nazwisko.text().strip(),
            "procent": self.partner_procent.value(),
        }
        if not payload["imie_pierwsze"] and not payload["nazwisko"]:
            QMessageBox.information(self, "Wspólnicy", "Podaj imię lub nazwisko wspólnika.")
            return

        save_partner(self.firma_id, payload)

        self.partner_imie1.clear()
        self.partner_imie2.clear()
        self.partner_nazwisko.clear()
        self.partner_procent.setValue(0)

        self._load_partners()

    def _delete_partner(self):
        row = self.partners_table.currentRow()
        if row < 0:
            return
        partner_id = self.partners_table.item(row, 0).data(Qt.ItemDataRole.UserRole)
        if partner_id is not None:
            delete_partner(partner_id)
        self.edited_partner_id = None
        self._load_partners()

    def _save_selected_partner(self):

        if not self.firma_id:
            return

        if self.edited_partner_id is None:
            QMessageBox.information(
                self,
                "Wspólnicy",
                "Wybierz wspólnika z listy."
            )
            return

        if not self._validate_partner_percent(
                self.edited_partner_id
        ):
            return

        payload = {
            "id": self.edited_partner_id,
            "imie_pierwsze": self.partner_imie1.text().strip(),
            "imie_drugie": self.partner_imie2.text().strip(),
            "nazwisko": self.partner_nazwisko.text().strip(),
            "procent": self.partner_procent.value(),
        }

        save_partner(self.firma_id, payload)

        self._load_partners()

    def _partner_selected(self):

        row = self.partners_table.currentRow()

        if row < 0:
            return

        self.edited_partner_id = self.partners_table.item(row,0).data(Qt.ItemDataRole.UserRole)

        self.partner_imie1.setText(
            self.partners_table.item(row,0).text())

        self.partner_imie2.setText(
            self.partners_table.item(row,1).text())

        self.partner_nazwisko.setText(
            self.partners_table.item(row,2).text())

        try:
            self.partner_procent.setValue(
                float(self.partners_table.item(row,3).text()))
        except Exception:
            self.partner_procent.setValue(0)

    def _validate_partner_percent(
            self,
            current_partner_id=None,
    ):

        total = 0.0

        rows = get_partners(self.firma_id)

        for row in rows:

            rid = row.get("id")

            if (
                    current_partner_id is not None
                    and rid == current_partner_id
            ):
                continue

            try:
                total += float(
                    row.get("procent") or 0
                )
            except Exception:
                logger.warning("Zignorowany błąd", exc_info=True)

        total += self.partner_procent.value()

        if total > 100:
            QMessageBox.warning(
                self,
                "Wspólnicy",
                (
                    f"Suma udziałów przekracza 100%.\n"
                    f"Obecnie byłoby: {total:.2f}%"
                ),
            )
            return False

        return True

    # ============================================================
    # NOTATKI
    # ============================================================
    def _load_notes(self):
        if not self.firma_id:
            return
        rows = get_notes_for_company(self.firma_id)
        self.notes_table.setRowCount(0)
        for r in rows:
            row = self.notes_table.rowCount()
            self.notes_table.insertRow(row)
            for col, key in enumerate(["tresc", "autor_nazwa", "data_utworzenia"]):
                self.notes_table.setItem(row, col, QTableWidgetItem(str(r.get(key) or "")))
        self.notes_table.resizeRowsToContents()

    def _add_note(self):
        text = self.note_edit.toPlainText().strip()
        if not text:
            return
        if not self.firma_id:
            QMessageBox.information(
                self, "Notatki", "Zapisz firmę, aby móc dodawać notatki.")
            return
        save_company_note(self.firma_id, text, self.actor_id)
        self.note_edit.clear()
        self._load_notes()
