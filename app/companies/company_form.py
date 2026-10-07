# app/companies/company_form.py
from typing import Dict, Any, Optional

from PyQt6.QtWidgets import (
    QDialog, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QSpinBox, QDoubleSpinBox, QComboBox, QTextEdit, QPushButton,
    QCheckBox, QTabWidget, QTableWidget, QTableWidgetItem, QHeaderView
)
from PyQt6.QtCore import Qt

from app.db.dao_companies import (
    get_company, save_company, get_contacts, save_contact,
    delete_contact, get_pricing_kadry, get_ksiegowosc_options,
    get_partners, save_partner, delete_partner,
    get_company_field_options, get_tax_offices,
)
from app.db.dao_notes import (
    get_notes_for_company, save_company_note)
from app.companies.company_logic import ceidg_fetch
from app.email.email_storage import (
    get_all_email_accounts,
    get_email_filters_for_company,
    add_email_filter,
    delete_email_filter_for_company,
)


class CompanyForm(QDialog):
    def __init__(self, firma_id: Optional[int] = None, parent: Optional[QWidget] = None):
        super().__init__(parent)

        self.firma_id = firma_id
        self.inputs: Dict[str, Any] = {}

        self.setWindowTitle("Firma")
        self.resize(1200, 900)

        # ============================================================
        # GŁÓWNY LAYOUT
        # ============================================================
        main_layout = QVBoxLayout(self)

        # ============================================================
        # SEKCJA: DANE FIRMY
        # ============================================================
        dane_box = QWidget()
        dane_layout = QVBoxLayout(dane_box)

        title = QLabel("Dane firmy")
        title.setStyleSheet("font-weight: bold; font-size: 16px;")
        dane_layout.addWidget(title)

        def add_row(labels, widgets):
            row_labels = QHBoxLayout()
            row_fields = QHBoxLayout()

            for lbl in labels:
                lab = QLabel(lbl)
                lab.setMinimumWidth(250)
                row_labels.addWidget(lab)

            for w in widgets:
                if isinstance(w, QHBoxLayout):
                    row_fields.addLayout(w)
                else:
                    w.setMinimumWidth(250)
                    row_fields.addWidget(w)

            dane_layout.addLayout(row_labels)
            dane_layout.addLayout(row_fields)


        # -----------------------------
        # RZĄD 1
        # -----------------------------
        nazwa = QLineEdit()
        nazwa_skrocona = QLineEdit()
        numer_klienta = QLineEdit()

        add_row(
            ["Nazwa:", "Nazwa skrócona:", "Numer klienta:"],
            [nazwa, nazwa_skrocona, numer_klienta]
        )

        # -----------------------------
        # RZĄD 2
        # -----------------------------
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

        # -----------------------------
        # RZĄD 3
        # -----------------------------
        krs = QLineEdit()
        pesel = QLineEdit()
        forma_dzialalnosci = QComboBox()
        forma_dzialalnosci.addItems(get_company_field_options("forma_dzialalnosci"))

        add_row(
            ["KRS:", "PESEL:", "Forma działalności:"],
            [krs, pesel, forma_dzialalnosci]
        )

        # -----------------------------
        # RZĄD 4
        # -----------------------------
        status_vat = QComboBox()
        status_vat.addItems(["Czynny", "Zwolniony", "Brak danych"])

        urzad_skarbowy = QComboBox()
        urzad_skarbowy.addItem("", None)
        for office in get_tax_offices():
            urzad_skarbowy.addItem(office["nazwa"], office["nazwa"])
        forma_opodatkowania = QComboBox()
        forma_opodatkowania.addItems(get_company_field_options("forma_opodatkowania"))

        add_row(
            ["Status VAT:", "Urząd skarbowy:", "Forma opodatkowania:"],
            [status_vat, urzad_skarbowy, forma_opodatkowania]
        )

        # -----------------------------
        # RZĄD 5
        # -----------------------------
        analityka_fk = QLineEdit()
        kody_pkd = QLineEdit()

        status = QComboBox()
        status.addItems(["Aktywne", "Zawieszone", "Archiwalne"])

        add_row(
            ["Analityka FK:", "Kody PKD:", "Status:"],
            [analityka_fk, kody_pkd, status]
        )

        # -----------------------------
        # RZĄD 6
        # -----------------------------
        konto_bankowe = QLineEdit()
        seria_fv = QLineEdit()
        seria_kfv = QLineEdit()

        add_row(
            ["Konto bankowe:", "Seria FV:", "Seria KFV:"],
            [konto_bankowe, seria_fv, seria_kfv]
        )

        # -----------------------------
        # RZĄD 7
        # -----------------------------
        sposob_zaplaty = QComboBox()
        sposob_zaplaty.addItems(get_company_field_options("sposob_zaplaty"))
        status_podatkowy = QComboBox()
        status_podatkowy.addItems(get_company_field_options("status_podatkowy"))
        kategoria_domyslna = QLineEdit()

        add_row(
            ["Sposób zapłaty:", "Status podatkowy:", "Kategoria domyślna:"],
            [sposob_zaplaty, status_podatkowy, kategoria_domyslna]
        )

        # -----------------------------
        # RZĄD 8
        # -----------------------------
        miesiac_dok_fakturowanie = QLineEdit()
        miesiac_prac_fakturowanie = QLineEdit()

        dzien_dostarczania_dokumentow = QSpinBox()
        dzien_dostarczania_dokumentow.setRange(1, 31)

        add_row(
            ["Miesiąc dok. fakturowanie:", "Miesiąc prac. fakturowanie:", "Dzień dostarczania dokumentów:"],
            [miesiac_dok_fakturowanie, miesiac_prac_fakturowanie, dzien_dostarczania_dokumentow]
        )

        # -----------------------------
        # Uwagi
        # -----------------------------
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
            "konto_bankowe": konto_bankowe,
            "seria_fv": seria_fv,
            "seria_kfv": seria_kfv,
            "sposob_zaplaty": sposob_zaplaty,
            "status_podatkowy": status_podatkowy,
            "kategoria_domyslna": kategoria_domyslna,
            "miesiac_dok_fakturowanie": miesiac_dok_fakturowanie,
            "miesiac_prac_fakturowanie": miesiac_prac_fakturowanie,
            "dzien_dostarczania_dokumentow": dzien_dostarczania_dokumentow,
            "uwagi": uwagi,
        })

        main_layout.addWidget(dane_box)

        # ============================================================
        # SEKCJA: ADRESY
        # ============================================================
        adresy_box = QWidget()
        adresy_layout = QVBoxLayout(adresy_box)

        title_addr = QLabel("Adresy")
        title_addr.setStyleSheet("font-weight: bold; font-size: 16px;")
        adresy_layout.addWidget(title_addr)

        tabs = QTabWidget()
        adresy_layout.addWidget(tabs)

        def add_addr_row(parent_layout, labels, widgets):
            row_labels = QHBoxLayout()
            row_fields = QHBoxLayout()

            for lbl in labels:
                lab = QLabel(lbl)
                lab.setMinimumWidth(250)
                row_labels.addWidget(lab)

            for w in widgets:
                w.setMinimumWidth(250)
                row_fields.addWidget(w)

            parent_layout.addLayout(row_labels)
            parent_layout.addLayout(row_fields)

        # -----------------------------
        # ADRES DZIAŁALNOŚCI
        # -----------------------------
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

        # -----------------------------
        # ADRES KORESPONDENCYJNY
        # -----------------------------
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

        # -----------------------------
        # ADRES ZAMIESZKANIA
        # -----------------------------
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

        main_layout.addWidget(adresy_box)

        # ============================================================
        # SEKCJA: CENNIKI
        # ============================================================
        cenniki_box = QWidget()
        cenniki_layout = QVBoxLayout(cenniki_box)

        title_cenniki = QLabel("Cenniki")
        title_cenniki.setStyleSheet("font-weight: bold; font-size: 16px;")
        cenniki_layout.addWidget(title_cenniki)

        def add_cennik_row(labels, widgets):
            row_labels = QHBoxLayout()
            row_fields = QHBoxLayout()

            for lbl in labels:
                lab = QLabel(lbl)
                lab.setMinimumWidth(250)
                row_labels.addWidget(lab)

            for w in widgets:
                if isinstance(w, QWidget):
                    w.setMinimumWidth(250)
                row_fields.addWidget(w)

            cenniki_layout.addLayout(row_labels)
            cenniki_layout.addLayout(row_fields)

        glowny_cennik = QComboBox()
        glowny_cennik.setMinimumWidth(250)

        ks_opts = get_ksiegowosc_options()
        glowny_cennik.addItem("", "")
        for item in ks_opts:
            glowny_cennik.addItem(item["klucz"], item["klucz"])

        limit_dokumentow = QSpinBox()
        limit_dokumentow.setRange(0, 100000)

        zdrowotna = QDoubleSpinBox()
        zdrowotna.setRange(0, 100000)
        zdrowotna.setDecimals(2)

        add_cennik_row(
            ["Główny cennik:", "Limit dokumentów:", "Zdrowotna:"],
            [glowny_cennik, limit_dokumentow, zdrowotna]
        )

        wysylac_do_ksef = QCheckBox()
        wysylac_do_kontrahenta = QCheckBox()
        metoda_kasowa = QCheckBox()

        add_cennik_row(
            ["Wysyłać do KSeF:", "Wysyłać do kontrahenta:", "Metoda kasowa:"],
            [wysylac_do_ksef, wysylac_do_kontrahenta, metoda_kasowa]
        )

        fakturowac = QCheckBox()
        add_cennik_row(["Fakturować:", "", ""], [fakturowac, QWidget(), QWidget()])

        self.inputs.update({
            "glowny_cennik": glowny_cennik,
            "limit_dokumentow": limit_dokumentow,
            "zdrowotna": zdrowotna,
            "wysylac_do_ksef": wysylac_do_ksef,
            "wysylac_do_kontrahenta": wysylac_do_kontrahenta,
            "metoda_kasowa": metoda_kasowa,
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
            row_labels = QHBoxLayout()
            row_fields = QHBoxLayout()

            for lbl in labels:
                lab = QLabel(lbl)
                lab.setMinimumWidth(250)
                row_labels.addWidget(lab)

            for w in widgets:
                w.setMinimumWidth(250)
                row_fields.addWidget(w)

            kadry_layout.addLayout(row_labels)
            kadry_layout.addLayout(row_fields)

        kadry_opts = get_pricing_kadry()

        # -----------------------------
        # RZĄD 1
        # -----------------------------
        uop_ilosc = QSpinBox()
        uop_ilosc.setRange(0, 10000)

        uop_cennik = QComboBox()
        uop_cennik.addItem("", "")
        for item in kadry_opts:
            uop_cennik.addItem(item["klucz"], item["klucz"])

        uz_ilosc = QSpinBox()
        uz_ilosc.setRange(0, 10000)

        add_kadry_row(
            ["UoP ilość:", "UoP cennik:", "UZ ilość:"],
            [uop_ilosc, uop_cennik, uz_ilosc]
        )

        # -----------------------------
        # RZĄD 2
        # -----------------------------
        uz_cennik = QComboBox()
        uz_cennik.addItem("", "")
        for item in kadry_opts:
            uz_cennik.addItem(item["klucz"], item["klucz"])

        uod_ilosc = QSpinBox()
        uod_ilosc.setRange(0, 10000)

        uod_cennik = QComboBox()
        uod_cennik.addItem("", "")
        for item in kadry_opts:
            uod_cennik.addItem(item["klucz"], item["klucz"])

        add_kadry_row(
            ["UZ cennik:", "UoD ilość:", "UoD cennik:"],
            [uz_cennik, uod_ilosc, uod_cennik]
        )

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
        self.contacts_table.setHorizontalHeaderLabels(["Imię", "Nazwisko", "Email", "Telefon", "Rola"])
        self.contacts_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.contacts_table.setMinimumHeight(200)
        kontakty_layout.addWidget(self.contacts_table)

        kont_form = QHBoxLayout()
        kont_left = QVBoxLayout()
        kont_right = QVBoxLayout()

        kont_imie = QLineEdit()
        kont_nazwisko = QLineEdit()
        kont_email = QLineEdit()
        kont_tel = QLineEdit()
        kont_rola = QLineEdit()

        for lbl, widget in [
            ("Imię:", kont_imie),
            ("Nazwisko:", kont_nazwisko),
        ]:
            lab = QLabel(lbl)
            lab.setMinimumWidth(250)
            widget.setMinimumWidth(250)
            kont_left.addWidget(lab)
            kont_left.addWidget(widget)

        for lbl, widget in [
            ("Email:", kont_email),
            ("Telefon:", kont_tel),
            ("Rola:", kont_rola),
        ]:
            lab = QLabel(lbl)
            lab.setMinimumWidth(250)
            widget.setMinimumWidth(250)
            kont_right.addWidget(lab)
            kont_right.addWidget(widget)

        kont_form.addLayout(kont_left)
        kont_form.addLayout(kont_right)
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
        self.filter_sender = QLineEdit()
        self.filter_sender.setPlaceholderText("np. admin@firmaa.pl lub firmaa@gmail.com")
        self.filter_folder = QLineEdit()
        self.filter_folder.setPlaceholderText("Folder, np. nazwa skrócona firmy")
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

        partner_imie1 = QLineEdit()
        partner_imie2 = QLineEdit()
        partner_nazwisko = QLineEdit()
        partner_procent = QDoubleSpinBox()
        partner_procent.setRange(0, 100)
        partner_procent.setDecimals(2)

        for lbl, widget in [
            ("Imię pierwsze:", partner_imie1),
            ("Imię drugie:", partner_imie2),
        ]:
            lab = QLabel(lbl)
            lab.setMinimumWidth(250)
            widget.setMinimumWidth(250)
            wsp_left.addWidget(lab)
            wsp_left.addWidget(widget)

        for lbl, widget in [
            ("Nazwisko:", partner_nazwisko),
            ("Procent:", partner_procent),
        ]:
            lab = QLabel(lbl)
            lab.setMinimumWidth(250)
            widget.setMinimumWidth(250)
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

        note_edit = QTextEdit()
        note_edit.setMinimumHeight(120)
        not_layout.addWidget(QLabel("Nowa notatka:"))
        not_layout.addWidget(note_edit)

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

        btn_add_note.clicked.connect(self._add_note)

        # ============================================================
        # ŁADOWANIE DANYCH FIRMY
        # ============================================================
        if self.firma_id:
            self._load_company()
            self._load_email_filters()

    # ============================================================
    # CEIDG
    # ============================================================
    def _on_ceidg(self):
        try:
            ceidg_fetch(self)
        except Exception as e:
            print("CEIDG error:", e)

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
                payload[key] = widget.currentText()
            elif isinstance(widget, QCheckBox):
                payload[key] = 1 if widget.isChecked() else 0

        return payload

    def _load_email_filters(self):
        self.email_filters_table.setRowCount(0)
        for rule in get_email_filters_for_company(self.firma_id):
            row = self.email_filters_table.rowCount()
            self.email_filters_table.insertRow(row)
            self.email_filters_table.setItem(row, 0, QTableWidgetItem(rule.get("od") or rule.get("nadawca_zawiera") or ""))
            self.email_filters_table.setItem(row, 1, QTableWidgetItem(rule.get("konto_email", "")))
            self.email_filters_table.setItem(row, 2, QTableWidgetItem(rule.get("folder_docelowy", "")))
            self.email_filters_table.item(row, 0).setData(Qt.ItemDataRole.UserRole, rule["id"])

    def _add_company_email_filter(self):
        if not self.firma_id:
            return
        sender = self.filter_sender.text().strip()
        folder = self.filter_folder.text().strip()
        if not folder and "@" in sender:
            folder = sender.rsplit("@", 1)[1].strip().lower()
        if not folder:
            folder = self.inputs["nazwa_skrocona"].text().strip()
        account_id = self.filter_account.currentData()
        if not sender or account_id is None or not folder:
            return
        add_email_filter(
            int(account_id), self.firma_id, sender, None, None, None, None,
            folder, 0, 0, 1,
        )
        self.filter_sender.clear()
        self.filter_folder.clear()
        self._load_email_filters()

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
    def _on_save(self):
        payload = self._collect_payload()
        try:
            save_company(payload)
            self.accept()
        except Exception as e:
            print("Save error:", e)

    # ============================================================
    # ŁADOWANIE DANYCH
    # ============================================================
    def _load_company(self):
        data = get_company(self.firma_id)
        if not data:
            return

        for key, widget in self.inputs.items():
            val = data.get(key)
            if isinstance(widget, QLineEdit):
                widget.setText(str(val or ""))
            elif isinstance(widget, QTextEdit):
                widget.setPlainText(str(val or ""))
            elif isinstance(widget, QSpinBox):
                widget.setValue(int(val or 0))
            elif isinstance(widget, QDoubleSpinBox):
                widget.setValue(float(val or 0))
            elif isinstance(widget, QComboBox):
                if val is not None:
                    if widget.findText(str(val)) < 0 and key in (
                        "forma_dzialalnosci", "forma_opodatkowania", "urzad_skarbowy",
                        "sposob_zaplaty", "status_podatkowy"
                    ):
                        widget.addItem(str(val), str(val))
                    idx = widget.findText(str(val))
                    if idx >= 0:
                        widget.setCurrentIndex(idx)

    # ============================================================
    # ŁADOWANIE DANYCH Z PAYLOADU (IMPORT WIZARD)
    # ============================================================
    def _load_company_from_payload(self, payload: Dict[str, Any]):
        """
        Ładuje dane z payloadu wygenerowanego przez importer.
        Payload zawiera tylko podstawowe pola — reszta zostaje pusta.
        """
        for key, widget in self.inputs.items():
            val = payload.get(key)

            if isinstance(widget, QLineEdit):
                widget.setText(str(val or ""))

            elif isinstance(widget, QTextEdit):
                widget.setPlainText(str(val or ""))

            elif isinstance(widget, QSpinBox):
                try:
                    widget.setValue(int(val or 0))
                except Exception:
                    widget.setValue(0)

            elif isinstance(widget, QDoubleSpinBox):
                try:
                    widget.setValue(float(val or 0))
                except Exception:
                    widget.setValue(0.0)

            elif isinstance(widget, QComboBox):
                if val is not None:
                    if widget.findText(str(val)) < 0 and key in (
                        "forma_dzialalnosci", "forma_opodatkowania", "urzad_skarbowy",
                        "sposob_zaplaty", "status_podatkowy"
                    ):
                        widget.addItem(str(val), str(val))
                    idx = widget.findText(str(val))
                    if idx >= 0:
                        widget.setCurrentIndex(idx)

            elif isinstance(widget, QCheckBox):
                widget.setChecked(bool(val))

    # ============================================================
    # KONTAKTY
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
                self.contacts_table.setItem(row, col, QTableWidgetItem(str(r.get(key, ""))))

    def _add_contact(self):
        if not self.firma_id:
            return
        payload = {
            "firma_id": self.firma_id,
            "imie": self.inputs["kont_imie"].text().strip(),
            "nazwisko": self.inputs["kont_nazwisko"].text().strip(),
            "email": self.inputs["kont_email"].text().strip(),
            "telefon": self.inputs["kont_tel"].text().strip(),
            "rola": self.inputs["kont_rola"].text().strip(),
        }
        save_contact(payload)
        self._load_contacts()

    def _delete_contact(self):
        row = self.contacts_table.currentRow()
        if row < 0:
            return
        email = self.contacts_table.item(row, 2).text()
        delete_contact(self.firma_id, email)
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
        save_contact(payload)
        self._load_contacts()

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
            for col, key in enumerate(["imie1", "imie2", "nazwisko", "procent"]):
                self.partners_table.setItem(row, col, QTableWidgetItem(str(r.get(key, ""))))

    def _add_partner(self):
        if not self.firma_id:
            return
        payload = {
            "firma_id": self.firma_id,
            "imie1": self.inputs["partner_imie1"].text().strip(),
            "imie2": self.inputs["partner_imie2"].text().strip(),
            "nazwisko": self.inputs["partner_nazwisko"].text().strip(),
            "procent": self.inputs["partner_procent"].value(),
        }
        save_partner(payload)
        self._load_partners()

    def _delete_partner(self):
        row = self.partners_table.currentRow()
        if row < 0:
            return
        nazwisko = self.partners_table.item(row, 2).text()
        delete_partner(self.firma_id, nazwisko)
        self._load_partners()

    def _save_selected_partner(self):
        row = self.partners_table.currentRow()
        if row < 0:
            return
        payload = {
            "firma_id": self.firma_id,
            "imie1": self.partners_table.item(row, 0).text(),
            "imie2": self.partners_table.item(row, 1).text(),
            "nazwisko": self.partners_table.item(row, 2).text(),
            "procent": float(self.partners_table.item(row, 3).text()),
        }
        save_partner(payload)
        self._load_partners()

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
            for col, key in enumerate(["tresc", "autor", "data"]):
                self.notes_table.setItem(row, col, QTableWidgetItem(str(r.get(key, ""))))

    def _add_note(self):
        if not self.firma_id:
            return
        text = self.findChild(QTextEdit).toPlainText().strip()
        if not text:
            return
        save_company_note(self.firma_id, text)
        self._load_notes()
