# app/companies/company_form.py
from typing import Dict, Any, Optional, List

from PyQt6.QtWidgets import (
    QDialog,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QFormLayout,
    QTabWidget,
    QLabel,
    QLineEdit,
    QSpinBox,
    QDoubleSpinBox,
    QComboBox,
    QTextEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QMessageBox,
)
from PyQt6.QtCore import Qt

from app.db.dao_companies import (
    get_company,
    save_company,
    get_contacts,
    save_contact,
    delete_contact,
    get_partners,
    save_partner,
    delete_partner,
    get_company_notes,
    get_pricing_kadry,
    get_ksiegowosc_options,
)
from app.db.dao_conn import get_conn
from app.companies.company_logic import CompanyLogic, ceidg_fetch


class CompanyForm(QDialog):
    def __init__(self, firma_id: Optional[int] = None, parent=None):
        super().__init__(parent)
        self.firma_id = firma_id
        self.logic = CompanyLogic()
        self.inputs: Dict[str, Any] = {}
        self._ceidg_in_progress = False
        self._last_ceidg_raw: Dict[str, Any] = {}

        self.setWindowTitle("Firma")
        self.resize(1000, 750)

        main_layout = QVBoxLayout(self)
        self.tabs = QTabWidget()
        main_layout.addWidget(self.tabs)

        self._build_tab_dane()
        self._build_tab_adresy()
        self._build_tab_cenniki()
        self._build_tab_kadry()
        self._build_tab_kontakty()
        self._build_tab_wspolnicy()
        self._build_tab_notatki()

        btn_layout = QHBoxLayout()
        self.btn_ceidg = QPushButton("Pobierz z CEIDG")
        self.btn_save = QPushButton("Zapisz")
        self.btn_cancel = QPushButton("Anuluj")
        btn_layout.addWidget(self.btn_ceidg)
        btn_layout.addStretch()
        btn_layout.addWidget(self.btn_save)
        btn_layout.addWidget(self.btn_cancel)
        main_layout.addLayout(btn_layout)

        self.btn_ceidg.clicked.connect(self._on_ceidg)
        self.btn_save.clicked.connect(self._on_save)
        self.btn_cancel.clicked.connect(self.reject)

        if self.firma_id:
            self._load_company()
            self._load_contacts()
            self._load_partners()
            self._load_notes()

    def _build_tab_dane(self):
        w = QWidget()
        layout = QFormLayout(w)

        nazwa = QLineEdit()
        nazwa_skrocona = QLineEdit()
        numer_klienta = QLineEdit()
        nip = QLineEdit()
        regon = QLineEdit()
        krs = QLineEdit()
        pesel = QLineEdit()

        forma_dzialalnosci = QLineEdit()
        forma_opodatkowania = QLineEdit()
        analityka_fk = QLineEdit()

        kody_pkd = QLineEdit()

        typ_rozliczenia = QComboBox()
        typ_rozliczenia.addItems(["Ryczałt", "KPiR", "Księgi rachunkowe"])

        vat_czynny = QComboBox()
        vat_czynny.addItems(["0", "1"])

        status = QComboBox()
        status.addItems(["Aktywne", "Zawieszone", "Archiwalne"])

        adres_doreczen_elektronicznych = QLineEdit()
        urzad_skarbowy = QLineEdit()

        miesiac_dok_fakturowanie = QLineEdit()
        miesiac_prac_fakturowanie = QLineEdit()
        dzien_dostarczania_dokumentow = QSpinBox()
        dzien_dostarczania_dokumentow.setRange(1, 31)

        seria_fv = QLineEdit()
        seria_kfv = QLineEdit()
        sposob_zaplaty = QLineEdit()
        status_podatkowy = QLineEdit()

        konto_bankowe = QLineEdit()
        uwagi = QTextEdit()
        kategoria_domyslna = QLineEdit()

        layout.addRow("Nazwa:", nazwa)
        layout.addRow("Nazwa skrócona:", nazwa_skrocona)
        layout.addRow("Numer klienta:", numer_klienta)
        layout.addRow("NIP:", nip)
        layout.addRow("REGON:", regon)
        layout.addRow("KRS:", krs)
        layout.addRow("PESEL:", pesel)
        layout.addRow("Forma działalności:", forma_dzialalnosci)
        layout.addRow("Forma opodatkowania:", forma_opodatkowania)
        layout.addRow("Analityka FK:", analityka_fk)
        layout.addRow("Kody PKD:", kody_pkd)
        layout.addRow("Typ rozliczenia:", typ_rozliczenia)
        layout.addRow("VAT czynny:", vat_czynny)
        layout.addRow("Status:", status)
        layout.addRow("Adres e-doręczeń:", adres_doreczen_elektronicznych)
        layout.addRow("Urząd skarbowy:", urzad_skarbowy)
        layout.addRow("Miesiąc dok. fakturowanie:", miesiac_dok_fakturowanie)
        layout.addRow("Miesiąc prac. fakturowanie:", miesiac_prac_fakturowanie)
        layout.addRow("Dzień dostarczania dokumentów:", dzien_dostarczania_dokumentow)
        layout.addRow("Seria FV:", seria_fv)
        layout.addRow("Seria KFV:", seria_kfv)
        layout.addRow("Sposób zapłaty:", sposob_zaplaty)
        layout.addRow("Status podatkowy:", status_podatkowy)
        layout.addRow("Konto bankowe:", konto_bankowe)
        layout.addRow("Uwagi:", uwagi)
        layout.addRow("Kategoria domyślna:", kategoria_domyslna)

        self.inputs["nazwa"] = nazwa
        self.inputs["nazwa_skrocona"] = nazwa_skrocona
        self.inputs["numer_klienta"] = numer_klienta
        self.inputs["nip"] = nip
        self.inputs["regon"] = regon
        self.inputs["krs"] = krs
        self.inputs["pesel"] = pesel
        self.inputs["forma_dzialalnosci"] = forma_dzialalnosci
        self.inputs["forma_opodatkowania"] = forma_opodatkowania
        self.inputs["analityka_fk"] = analityka_fk
        self.inputs["kody_pkd"] = kody_pkd
        self.inputs["typ_rozliczenia"] = typ_rozliczenia
        self.inputs["vat_czynny"] = vat_czynny
        self.inputs["status"] = status
        self.inputs["adres_doreczen_elektronicznych"] = adres_doreczen_elektronicznych
        self.inputs["urzad_skarbowy"] = urzad_skarbowy
        self.inputs["miesiac_dok_fakturowanie"] = miesiac_dok_fakturowanie
        self.inputs["miesiac_prac_fakturowanie"] = miesiac_prac_fakturowanie
        self.inputs["dzien_dostarczania_dokumentow"] = dzien_dostarczania_dokumentow
        self.inputs["seria_fv"] = seria_fv
        self.inputs["seria_kfv"] = seria_kfv
        self.inputs["sposob_zaplaty"] = sposob_zaplaty
        self.inputs["status_podatkowy"] = status_podatkowy
        self.inputs["konto_bankowe"] = konto_bankowe
        self.inputs["uwagi"] = uwagi
        self.inputs["kategoria_domyslna"] = kategoria_domyslna

        self.tabs.addTab(w, "Dane")

    def _build_tab_adresy(self):
        w = QWidget()
        layout = QVBoxLayout(w)

        dz_form = QFormLayout()
        dz_miasto = QLineEdit()
        dz_kod = QLineEdit()
        dz_ulica = QLineEdit()
        dz_budynek = QLineEdit()
        dz_lokal = QLineEdit()
        dz_wojewodztwo = QLineEdit()
        dz_powiat = QLineEdit()
        dz_gmina = QLineEdit()
        dz_kraj = QLineEdit()
        dz_form.addRow(QLabel("Działalność"))
        dz_form.addRow("Miasto:", dz_miasto)
        dz_form.addRow("Kod pocztowy:", dz_kod)
        dz_form.addRow("Ulica:", dz_ulica)
        dz_form.addRow("Budynek:", dz_budynek)
        dz_form.addRow("Lokal:", dz_lokal)
        dz_form.addRow("Województwo:", dz_wojewodztwo)
        dz_form.addRow("Powiat:", dz_powiat)
        dz_form.addRow("Gmina:", dz_gmina)
        dz_form.addRow("Kraj:", dz_kraj)

        ko_form = QFormLayout()
        ko_miasto = QLineEdit()
        ko_kod = QLineEdit()
        ko_ulica = QLineEdit()
        ko_budynek = QLineEdit()
        ko_lokal = QLineEdit()
        ko_wojewodztwo = QLineEdit()
        ko_powiat = QLineEdit()
        ko_gmina = QLineEdit()
        ko_kraj = QLineEdit()
        ko_form.addRow(QLabel("Korespondencja"))
        ko_form.addRow("Miasto:", ko_miasto)
        ko_form.addRow("Kod pocztowy:", ko_kod)
        ko_form.addRow("Ulica:", ko_ulica)
        ko_form.addRow("Budynek:", ko_budynek)
        ko_form.addRow("Lokal:", ko_lokal)
        ko_form.addRow("Województwo:", ko_wojewodztwo)
        ko_form.addRow("Powiat:", ko_powiat)
        ko_form.addRow("Gmina:", ko_gmina)
        ko_form.addRow("Kraj:", ko_kraj)

        zam_form = QFormLayout()
        zam_miasto = QLineEdit()
        zam_kod = QLineEdit()
        zam_ulica = QLineEdit()
        zam_budynek = QLineEdit()
        zam_lokal = QLineEdit()
        zam_wojewodztwo = QLineEdit()
        zam_powiat = QLineEdit()
        zam_gmina = QLineEdit()
        zam_kraj = QLineEdit()
        zam_form.addRow(QLabel("Zamieszkanie"))
        zam_form.addRow("Miasto:", zam_miasto)
        zam_form.addRow("Kod pocztowy:", zam_kod)
        zam_form.addRow("Ulica:", zam_ulica)
        zam_form.addRow("Budynek:", zam_budynek)
        zam_form.addRow("Lokal:", zam_lokal)
        zam_form.addRow("Województwo:", zam_wojewodztwo)
        zam_form.addRow("Powiat:", zam_powiat)
        zam_form.addRow("Gmina:", zam_gmina)
        zam_form.addRow("Kraj:", zam_kraj)

        layout.addLayout(dz_form)
        layout.addSpacing(10)
        layout.addLayout(ko_form)
        layout.addSpacing(10)
        layout.addLayout(zam_form)

        self.inputs["dz_miasto"] = dz_miasto
        self.inputs["dz_kod"] = dz_kod
        self.inputs["dz_ulica"] = dz_ulica
        self.inputs["dz_budynek"] = dz_budynek
        self.inputs["dz_lokal"] = dz_lokal
        self.inputs["dz_wojewodztwo"] = dz_wojewodztwo
        self.inputs["dz_powiat"] = dz_powiat
        self.inputs["dz_gmina"] = dz_gmina
        self.inputs["dz_kraj"] = dz_kraj

        self.inputs["ko_miasto"] = ko_miasto
        self.inputs["ko_kod"] = ko_kod
        self.inputs["ko_ulica"] = ko_ulica
        self.inputs["ko_budynek"] = ko_budynek
        self.inputs["ko_lokal"] = ko_lokal
        self.inputs["ko_wojewodztwo"] = ko_wojewodztwo
        self.inputs["ko_powiat"] = ko_powiat
        self.inputs["ko_gmina"] = ko_gmina
        self.inputs["ko_kraj"] = ko_kraj

        self.inputs["zam_miasto"] = zam_miasto
        self.inputs["zam_kod"] = zam_kod
        self.inputs["zam_ulica"] = zam_ulica
        self.inputs["zam_budynek"] = zam_budynek
        self.inputs["zam_lokal"] = zam_lokal
        self.inputs["zam_wojewodztwo"] = zam_wojewodztwo
        self.inputs["zam_powiat"] = zam_powiat
        self.inputs["zam_gmina"] = zam_gmina
        self.inputs["zam_kraj"] = zam_kraj

        self.tabs.addTab(w, "Adresy")

    def _build_tab_cenniki(self):
        w = QWidget()
        layout = QFormLayout(w)

        glowny_cennik = QComboBox()
        ks_opts = get_ksiegowosc_options()
        glowny_cennik.addItem("", "")
        for item in ks_opts:
            glowny_cennik.addItem(item["klucz"], item["klucz"])

        limit_dokumentow = QSpinBox()
        limit_dokumentow.setRange(0, 100000)

        zdrowotna = QDoubleSpinBox()
        zdrowotna.setRange(0, 100000)
        zdrowotna.setDecimals(2)

        wysylac_do_ksef = QComboBox()
        wysylac_do_ksef.addItems(["0", "1"])

        wysylac_do_kontrahenta = QComboBox()
        wysylac_do_kontrahenta.addItems(["0", "1"])

        metoda_kasowa = QComboBox()
        metoda_kasowa.addItems(["0", "1"])

        fakturowac = QComboBox()
        fakturowac.addItems(["0", "1"])

        layout.addRow("Główny cennik:", glowny_cennik)
        layout.addRow("Limit dokumentów:", limit_dokumentow)
        layout.addRow("Zdrowotna:", zdrowotna)
        layout.addRow("Wysyłać do KSeF:", wysylac_do_ksef)
        layout.addRow("Wysyłać do kontrahenta:", wysylac_do_kontrahenta)
        layout.addRow("Metoda kasowa:", metoda_kasowa)
        layout.addRow("Fakturować:", fakturowac)

        self.inputs["glowny_cennik"] = glowny_cennik
        self.inputs["limit_dokumentow"] = limit_dokumentow
        self.inputs["zdrowotna"] = zdrowotna
        self.inputs["wysylac_do_ksef"] = wysylac_do_ksef
        self.inputs["wysylac_do_kontrahenta"] = wysylac_do_kontrahenta
        self.inputs["metoda_kasowa"] = metoda_kasowa
        self.inputs["fakturowac"] = fakturowac

        self.tabs.addTab(w, "Cenniki")

    def _build_tab_kadry(self):
        w = QWidget()
        layout = QFormLayout(w)

        kadry_opts = get_pricing_kadry()

        uop_ilosc = QSpinBox()
        uop_ilosc.setRange(0, 10000)
        uop_cennik = QComboBox()
        uop_cennik.addItem("", "")
        for item in kadry_opts:
            uop_cennik.addItem(item["klucz"], item["klucz"])

        uz_ilosc = QSpinBox()
        uz_ilosc.setRange(0, 10000)
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

        layout.addRow("UoP ilość:", uop_ilosc)
        layout.addRow("UoP cennik:", uop_cennik)
        layout.addRow("UZ ilość:", uz_ilosc)
        layout.addRow("UZ cennik:", uz_cennik)
        layout.addRow("UoD ilość:", uod_ilosc)
        layout.addRow("UoD cennik:", uod_cennik)

        self.inputs["uop_ilosc"] = uop_ilosc
        self.inputs["uop_cennik"] = uop_cennik
        self.inputs["uz_ilosc"] = uz_ilosc
        self.inputs["uz_cennik"] = uz_cennik
        self.inputs["uod_ilosc"] = uod_ilosc
        self.inputs["uod_cennik"] = uod_cennik

        self.tabs.addTab(w, "Kadry")

    def _build_tab_kontakty(self):
        w = QWidget()
        layout = QVBoxLayout(w)

        self.contacts_table = QTableWidget(0, 5)
        self.contacts_table.setHorizontalHeaderLabels(["Imię", "Nazwisko", "Email", "Telefon", "Rola"])
        self.contacts_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)

        form = QFormLayout()
        self.kont_imie = QLineEdit()
        self.kont_nazw = QLineEdit()
        self.kont_email = QLineEdit()
        self.kont_tel = QLineEdit()
        self.kont_rola = QLineEdit()
        form.addRow("Imię:", self.kont_imie)
        form.addRow("Nazwisko:", self.kont_nazw)
        form.addRow("Email:", self.kont_email)
        form.addRow("Telefon:", self.kont_tel)
        form.addRow("Rola:", self.kont_rola)

        btn_layout = QHBoxLayout()
        btn_add = QPushButton("Dodaj kontakt")
        btn_del = QPushButton("Usuń kontakt")
        btn_save_row = QPushButton("Zapisz zaznaczony")
        btn_layout.addWidget(btn_add)
        btn_layout.addWidget(btn_del)
        btn_layout.addWidget(btn_save_row)

        btn_add.clicked.connect(self._add_contact)
        btn_del.clicked.connect(self._delete_contact)
        btn_save_row.clicked.connect(self._save_selected_contact)

        layout.addWidget(self.contacts_table)
        layout.addLayout(form)
        layout.addLayout(btn_layout)

        self.inputs["kont_imie"] = self.kont_imie
        self.inputs["kont_nazw"] = self.kont_nazw
        self.inputs["kont_email"] = self.kont_email
        self.inputs["kont_tel"] = self.kont_tel

        self.tabs.addTab(w, "Kontakty")

    def _build_tab_wspolnicy(self):
        w = QWidget()
        layout = QVBoxLayout(w)

        self.partners_table = QTableWidget(0, 4)
        self.partners_table.setHorizontalHeaderLabels(["Imię pierwsze", "Imię drugie", "Nazwisko", "Procent"])
        self.partners_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)

        form = QFormLayout()
        self.partner_imie1 = QLineEdit()
        self.partner_imie2 = QLineEdit()
        self.partner_nazwisko = QLineEdit()
        self.partner_procent = QDoubleSpinBox()
        self.partner_procent.setRange(0, 100)
        self.partner_procent.setDecimals(2)
        form.addRow("Imię pierwsze:", self.partner_imie1)
        form.addRow("Imię drugie:", self.partner_imie2)
        form.addRow("Nazwisko:", self.partner_nazwisko)
        form.addRow("Procent:", self.partner_procent)

        btn_layout = QHBoxLayout()
        btn_add = QPushButton("Dodaj wspólnika")
        btn_del = QPushButton("Usuń wspólnika")
        btn_save_row = QPushButton("Zapisz zaznaczonego")
        btn_layout.addWidget(btn_add)
        btn_layout.addWidget(btn_del)
        btn_layout.addWidget(btn_save_row)

        btn_add.clicked.connect(self._add_partner)
        btn_del.clicked.connect(self._delete_partner)
        btn_save_row.clicked.connect(self._save_selected_partner)

        layout.addWidget(self.partners_table)
        layout.addLayout(form)
        layout.addLayout(btn_layout)

        self.tabs.addTab(w, "Wspólnicy")

    def _build_tab_notatki(self):
        w = QWidget()
        layout = QVBoxLayout(w)

        self.notes_table = QTableWidget(0, 3)
        self.notes_table.setHorizontalHeaderLabels(["Treść", "Autor", "Data"])
        self.notes_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)

        self.note_edit = QTextEdit()
        btn_add = QPushButton("Dodaj notatkę")
        btn_add.clicked.connect(self._add_note)

        layout.addWidget(self.notes_table)
        layout.addWidget(QLabel("Nowa notatka:"))
        layout.addWidget(self.note_edit)
        layout.addWidget(btn_add)

        self.tabs.addTab(w, "Notatki")

    def _load_company(self):
        data = get_company(self.firma_id)
        if not data:
            return

        def set_text(name: str, value: Any):
            w = self.inputs.get(name)
            if w is None:
                return
            if isinstance(w, QLineEdit):
                w.setText(str(value or ""))
            elif isinstance(w, QTextEdit):
                w.setPlainText(str(value or ""))
            elif isinstance(w, QSpinBox):
                w.setValue(int(value or 0))
            elif isinstance(w, QDoubleSpinBox):
                w.setValue(float(value or 0))
            elif isinstance(w, QComboBox):
                if value is None:
                    return
                text = str(value)
                idx = w.findText(text)
                if idx >= 0:
                    w.setCurrentIndex(idx)

        for key, val in data.items():
            if key in self.inputs:
                set_text(key, val)

        set_text("dz_miasto", data.get("miasto"))
        set_text("dz_kod", data.get("kod_pocztowy"))
        set_text("dz_ulica", data.get("ulica"))
        set_text("dz_budynek", data.get("adres_budynek"))
        set_text("dz_lokal", data.get("adres_lokal"))
        set_text("dz_wojewodztwo", data.get("wojewodztwo"))
        set_text("dz_powiat", data.get("powiat"))
        set_text("dz_gmina", data.get("gmina"))
        set_text("dz_kraj", data.get("kraj"))

        set_text("ko_miasto", data.get("adres_koresp_miasto"))
        set_text("ko_kod", data.get("adres_koresp_kod_pocztowy"))
        set_text("ko_ulica", data.get("adres_koresp_ulica"))
        set_text("ko_budynek", data.get("adres_koresp_budynek"))
        set_text("ko_lokal", data.get("adres_koresp_lokal"))
        set_text("ko_wojewodztwo", data.get("adres_koresp_wojewodztwo"))
        set_text("ko_powiat", data.get("adres_koresp_powiat"))
        set_text("ko_gmina", data.get("adres_koresp_gmina"))
        set_text("ko_kraj", data.get("adres_koresp_kraj"))

        set_text("zam_miasto", data.get("adres_zam_miasto"))
        set_text("zam_kod", data.get("adres_zam_kod_pocztowy"))
        set_text("zam_ulica", data.get("adres_zam_ulica"))
        set_text("zam_budynek", data.get("adres_zam_budynek"))
        set_text("zam_lokal", data.get("adres_zam_lokal"))
        set_text("zam_wojewodztwo", data.get("adres_zam_wojewodztwo"))
        set_text("zam_powiat", data.get("adres_zam_powiat"))
        set_text("zam_gmina", data.get("adres_zam_gmina"))
        set_text("zam_kraj", data.get("adres_zam_kraj"))

    def _collect_payload(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {}
        if self.firma_id:
            payload["id"] = self.firma_id

        for name, w in self.inputs.items():
            if isinstance(w, QLineEdit):
                payload[name] = w.text().strip()
            elif isinstance(w, QTextEdit):
                payload[name] = w.toPlainText().strip()
            elif isinstance(w, QSpinBox):
                payload[name] = w.value()
            elif isinstance(w, QDoubleSpinBox):
                payload[name] = w.value()
            elif isinstance(w, QComboBox):
                if name in ("typ_rozliczenia", "status", "glowny_cennik"):
                    payload[name] = w.currentText()
                else:
                    try:
                        payload[name] = int(w.currentText())
                    except Exception:
                        payload[name] = w.currentText()

        return payload

    def _on_save(self):
        payload = self._collect_payload()
        try:
            firma_id = save_company(payload)
            self.firma_id = firma_id
            self.accept()
        except Exception as e:
            QMessageBox.critical(self, "Błąd zapisu", str(e))

    def _on_ceidg(self):
        try:
            ceidg_fetch(self)
        except Exception as e:
            QMessageBox.warning(self, "CEIDG", str(e))

    def _load_contacts(self):
        if not self.firma_id:
            return
        rows = get_contacts(self.firma_id)
        self.contacts_table.setRowCount(0)
        for r in rows:
            row = self.contacts_table.rowCount()
            self.contacts_table.insertRow(row)
            self.contacts_table.setItem(row, 0, QTableWidgetItem(r.get("imie") or ""))
            self.contacts_table.setItem(row, 1, QTableWidgetItem(r.get("nazwisko") or ""))
            self.contacts_table.setItem(row, 2, QTableWidgetItem(r.get("email") or ""))
            self.contacts_table.setItem(row, 3, QTableWidgetItem(r.get("telefon") or ""))
            self.contacts_table.setItem(row, 4, QTableWidgetItem(r.get("rola") or ""))
            self.contacts_table.setVerticalHeaderItem(row, QTableWidgetItem(str(r.get("id") or "")))

    def _add_contact(self):
        if not self.firma_id:
            QMessageBox.information(self, "Kontakt", "Najpierw zapisz firmę.")
            return
        data = {
            "imie": self.kont_imie.text().strip(),
            "nazwisko": self.kont_nazw.text().strip(),
            "email": self.kont_email.text().strip(),
            "telefon": self.kont_tel.text().strip(),
            "rola": self.kont_rola.text().strip(),
        }
        try:
            save_contact(self.firma_id, data)
            self.kont_imie.clear()
            self.kont_nazw.clear()
            self.kont_email.clear()
            self.kont_tel.clear()
            self.kont_rola.clear()
            self._load_contacts()
        except Exception as e:
            QMessageBox.critical(self, "Kontakt", str(e))

    def _delete_contact(self):
        row = self.contacts_table.currentRow()
        if row < 0:
            return
        header_item = self.contacts_table.verticalHeaderItem(row)
        if not header_item:
            return
        try:
            contact_id = int(header_item.text())
        except Exception:
            return
        try:
            delete_contact(contact_id)
            self._load_contacts()
        except Exception as e:
            QMessageBox.critical(self, "Kontakt", str(e))

    def _save_selected_contact(self):
        row = self.contacts_table.currentRow()
        if row < 0 or not self.firma_id:
            return
        header_item = self.contacts_table.verticalHeaderItem(row)
        if not header_item:
            return
        try:
            contact_id = int(header_item.text())
        except Exception:
            contact_id = None
        data = {
            "id": contact_id,
            "imie": self.contacts_table.item(row, 0).text() if self.contacts_table.item(row, 0) else "",
            "nazwisko": self.contacts_table.item(row, 1).text() if self.contacts_table.item(row, 1) else "",
            "email": self.contacts_table.item(row, 2).text() if self.contacts_table.item(row, 2) else "",
            "telefon": self.contacts_table.item(row, 3).text() if self.contacts_table.item(row, 3) else "",
            "rola": self.contacts_table.item(row, 4).text() if self.contacts_table.item(row, 4) else "",
        }
        try:
            save_contact(self.firma_id, data)
            self._load_contacts()
        except Exception as e:
            QMessageBox.critical(self, "Kontakt", str(e))

    def _load_partners(self):
        if not self.firma_id:
            return
        rows = get_partners(self.firma_id)
        self.partners_table.setRowCount(0)
        for r in rows:
            row = self.partners_table.rowCount()
            self.partners_table.insertRow(row)
            self.partners_table.setItem(row, 0, QTableWidgetItem(r.get("imie_pierwsze") or ""))
            self.partners_table.setItem(row, 1, QTableWidgetItem(r.get("imie_drugie") or ""))
            self.partners_table.setItem(row, 2, QTableWidgetItem(r.get("nazwisko") or ""))
            self.partners_table.setItem(row, 3, QTableWidgetItem(str(r.get("procent") or "")))
            self.partners_table.setVerticalHeaderItem(row, QTableWidgetItem(str(r.get("id") or "")))

    def _add_partner(self):
        if not self.firma_id:
            QMessageBox.information(self, "Wspólnik", "Najpierw zapisz firmę.")
            return
        data = {
            "imie_pierwsze": self.partner_imie1.text().strip(),
            "imie_drugie": self.partner_imie2.text().strip(),
            "nazwisko": self.partner_nazwisko.text().strip(),
            "procent": self.partner_procent.value(),
        }
        try:
            save_partner(self.firma_id, data)
            self.partner_imie1.clear()
            self.partner_imie2.clear()
            self.partner_nazwisko.clear()
            self.partner_procent.setValue(0.0)
            self._load_partners()
        except Exception as e:
            QMessageBox.critical(self, "Wspólnik", str(e))

    def _delete_partner(self):
        row = self.partners_table.currentRow()
        if row < 0:
            return
        header_item = self.partners_table.verticalHeaderItem(row)
        if not header_item:
            return
        try:
            partner_id = int(header_item.text())
        except Exception:
            return
        try:
            delete_partner(partner_id)
            self._load_partners()
        except Exception as e:
            QMessageBox.critical(self, "Wspólnik", str(e))

    def _save_selected_partner(self):
        row = self.partners_table.currentRow()
        if row < 0 or not self.firma_id:
            return
        header_item = self.partners_table.verticalHeaderItem(row)
        if not header_item:
            return
        try:
            partner_id = int(header_item.text())
        except Exception:
            partner_id = None
        data = {
            "id": partner_id,
            "imie_pierwsze": self.partners_table.item(row, 0).text() if self.partners_table.item(row, 0) else "",
            "imie_drugie": self.partners_table.item(row, 1).text() if self.partners_table.item(row, 1) else "",
            "nazwisko": self.partners_table.item(row, 2).text() if self.partners_table.item(row, 2) else "",
            "procent": float(self.partners_table.item(row, 3).text()) if self.partners_table.item(row, 3) else 0.0,
        }
        try:
            save_partner(self.firma_id, data)
            self._load_partners()
        except Exception as e:
            QMessageBox.critical(self, "Wspólnik", str(e))

    def _load_notes(self):
        if not self.firma_id:
            return
        rows = get_company_notes(self.firma_id, limit=50)
        self.notes_table.setRowCount(0)
        for r in rows:
            row = self.notes_table.rowCount()
            self.notes_table.insertRow(row)
            self.notes_table.setItem(row, 0, QTableWidgetItem(r.get("tresc") or ""))
            self.notes_table.setItem(row, 1, QTableWidgetItem(str(r.get("autor_id") or "")))
            self.notes_table.setItem(row, 2, QTableWidgetItem(r.get("data_utworzenia") or ""))

    def _add_note(self):
        if not self.firma_id:
            QMessageBox.information(self, "Notatka", "Najpierw zapisz firmę.")
            return
        text = self.note_edit.toPlainText().strip()
        if not text:
            return
        try:
            conn = get_conn()
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO company_notes (firma_id, tresc) VALUES (?, ?)",
                (self.firma_id, text),
            )
            conn.commit()
            conn.close()
            self.note_edit.clear()
            self._load_notes()
        except Exception as e:
            QMessageBox.critical(self, "Notatka", str(e))
