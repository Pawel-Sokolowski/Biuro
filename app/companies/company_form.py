# app/companies/company_form.py
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QComboBox, QTabWidget, QFormLayout, QScrollArea, QDateEdit,
    QMessageBox, QGroupBox, QCheckBox
)
from PyQt6.QtCore import Qt, QDate
from PyQt6.QtGui import QFont

from app.db.dao_companies import save_company, get_company
from app.companies.ceidg_api import get_ceidg_firma_by_nip


class NoWheelDateEdit(QDateEdit):
    def wheelEvent(self, event):
        event.ignore()


class CompanyForm(QWidget):
    def __init__(self, pracownik_id: int, company_data: dict | None, parent=None):
        super().__init__(parent)
        self.pracownik_id = pracownik_id
        self.company_data = company_data or {}

        self.setMinimumWidth(900)

        # główny scroll
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)

        container = QWidget()
        scroll.setWidget(container)

        main_layout = QVBoxLayout(container)
        main_layout.setContentsMargins(20, 20, 20, 20)
        main_layout.setSpacing(25)

        # ============================
        # NAGŁÓWEK
        # ============================
        header = QLabel("Dane firmy")
        header.setFont(QFont("Arial", 18, QFont.Weight.Bold))
        main_layout.addWidget(header)

        # ============================
        # DANE GŁÓWNE
        # ============================
        box_main = QGroupBox("Dane główne")
        layout_main = QFormLayout()
        layout_main.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)
        layout_main.setFormAlignment(Qt.AlignmentFlag.AlignLeft)
        box_main.setLayout(layout_main)

        # nazwa
        self.f_nazwa = QLineEdit(self.company_data.get("nazwa", ""))
        self.f_nazwa_skr = QLineEdit(self.company_data.get("nazwa_skrocona", ""))

        row_nazwa = QWidget()
        row_nazwa_layout = QHBoxLayout(row_nazwa)
        row_nazwa_layout.setContentsMargins(0, 0, 0, 0)
        row_nazwa_layout.addWidget(QLabel("Nazwa"))
        row_nazwa_layout.addWidget(self.f_nazwa)
        row_nazwa_layout.addWidget(QLabel("Nazwa skrócona"))
        row_nazwa_layout.addWidget(self.f_nazwa_skr)
        layout_main.addRow(row_nazwa)

        # NIP + CEIDG
        self.f_nip = QLineEdit(self.company_data.get("nip", ""))
        self.btn_ceidg = QPushButton("Pobierz z CEIDG")
        self.btn_ceidg.clicked.connect(self._fetch_ceidg)

        self.f_regon = QLineEdit(self.company_data.get("regon", ""))

        row_nip = QWidget()
        row_nip_layout = QHBoxLayout(row_nip)
        row_nip_layout.setContentsMargins(0, 0, 0, 0)
        row_nip_layout.addWidget(QLabel("NIP"))
        row_nip_layout.addWidget(self.f_nip)
        row_nip_layout.addWidget(self.btn_ceidg)
        row_nip_layout.addWidget(QLabel("REGON"))
        row_nip_layout.addWidget(self.f_regon)
        layout_main.addRow(row_nip)

        # KRS + PESEL
        self.f_krs = QLineEdit(self.company_data.get("krs", ""))
        self.f_pesel = QLineEdit(self.company_data.get("pesel", ""))

        row_krs = QWidget()
        row_krs_layout = QHBoxLayout(row_krs)
        row_krs_layout.setContentsMargins(0, 0, 0, 0)
        row_krs_layout.addWidget(QLabel("KRS"))
        row_krs_layout.addWidget(self.f_krs)
        row_krs_layout.addWidget(QLabel("PESEL"))
        row_krs_layout.addWidget(self.f_pesel)
        layout_main.addRow(row_krs)

        main_layout.addWidget(box_main)

        # ============================
        # ADRESY — ZAKŁADKI
        # ============================
        self.tabs_address = QTabWidget()
        self._build_address_tabs()
        main_layout.addWidget(self.tabs_address)

        self._build_forma_dzial(main_layout)
        self._build_kontakt(main_layout)
        self._build_daty_zus(main_layout)
        self._build_cenniki(main_layout)

        btn_row = QWidget()
        btn_layout = QHBoxLayout(btn_row)
        btn_layout.addStretch()

        btn_save = QPushButton("Zapisz")
        btn_save.clicked.connect(self._save)

        btn_cancel = QPushButton("Anuluj")
        btn_cancel.clicked.connect(lambda: self.parent().close() if self.parent() else None)

        btn_layout.addWidget(btn_save)
        btn_layout.addWidget(btn_cancel)

        main_layout.addWidget(btn_row)

        # finalne osadzenie
        final_layout = QVBoxLayout(self)
        final_layout.addWidget(scroll)

    # ============================
    # CEIDG
    # ============================
    def _fetch_ceidg(self):
        nip = self.f_nip.text().strip()
        if not nip:
            QMessageBox.warning(self, "Błąd", "Podaj NIP")
            return

        data = get_ceidg_firma_by_nip(nip)
        if not data or data.get("error"):
            QMessageBox.warning(self, "Błąd", "Nie znaleziono firmy w CEIDG")
            return

        self.f_nazwa.setText(data.get("nazwa", ""))
        self.f_regon.setText(data.get("regon", ""))
        self.f_krs.setText(data.get("krs", ""))
        self.f_pesel.setText(data.get("pesel", ""))
    # ============================
    # ADRESY — ZAKŁADKI
    # ============================
    def _build_address_tabs(self):
        self.tabs_address.clear()

        # trzy zakładki
        self.tab_dzial = QWidget()
        self.tab_kor = QWidget()
        self.tab_zam = QWidget()

        self._build_address_tab(self.tab_dzial, "dzial")
        self._build_address_tab(self.tab_kor, "kor")
        self._build_address_tab(self.tab_zam, "zam")

        self.tabs_address.addTab(self.tab_dzial, "Adres działalności")
        self.tabs_address.addTab(self.tab_kor, "Adres korespondencji")
        self.tabs_address.addTab(self.tab_zam, "Adres zamieszkania")

    # ============================
    # POJEDYNCZA ZAKŁADKA ADRESU
    # ============================
    def _build_address_tab(self, tab: QWidget, prefix: str):
        layout = QFormLayout(tab)
        layout.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)

        # pola
        setattr(self, f"{prefix}_kraj", QLineEdit(self.company_data.get(f"{prefix}_kraj", "")))
        setattr(self, f"{prefix}_woj", QLineEdit(self.company_data.get(f"{prefix}_woj", "")))
        setattr(self, f"{prefix}_powiat", QLineEdit(self.company_data.get(f"{prefix}_powiat", "")))
        setattr(self, f"{prefix}_gmina", QLineEdit(self.company_data.get(f"{prefix}_gmina", "")))
        setattr(self, f"{prefix}_miasto", QLineEdit(self.company_data.get(f"{prefix}_miasto", "")))
        setattr(self, f"{prefix}_kod", QLineEdit(self.company_data.get(f"{prefix}_kod", "")))
        setattr(self, f"{prefix}_ulica", QLineEdit(self.company_data.get(f"{prefix}_ulica", "")))
        setattr(self, f"{prefix}_budynek", QLineEdit(self.company_data.get(f"{prefix}_budynek", "")))
        setattr(self, f"{prefix}_lokal", QLineEdit(self.company_data.get(f"{prefix}_lokal", "")))

        layout.addRow("Kraj", getattr(self, f"{prefix}_kraj"))
        layout.addRow("Województwo", getattr(self, f"{prefix}_woj"))
        layout.addRow("Powiat", getattr(self, f"{prefix}_powiat"))
        layout.addRow("Gmina", getattr(self, f"{prefix}_gmina"))
        layout.addRow("Miasto", getattr(self, f"{prefix}_miasto"))
        layout.addRow("Kod pocztowy", getattr(self, f"{prefix}_kod"))
        layout.addRow("Ulica", getattr(self, f"{prefix}_ulica"))
        layout.addRow("Budynek", getattr(self, f"{prefix}_budynek"))
        layout.addRow("Lokal", getattr(self, f"{prefix}_lokal"))

        # kopiowanie adresów
        btn_copy = QPushButton("Kopiuj z...")
        btn_copy.clicked.connect(lambda: self._copy_address_dialog(prefix))
        layout.addRow(btn_copy)

    # ============================
    # KOPIOWANIE ADRESÓW
    # ============================
    def _copy_address_dialog(self, target_prefix: str):
        from PyQt6.QtWidgets import QDialog, QVBoxLayout, QListWidget

        dialog = QDialog(self)
        dialog.setWindowTitle("Kopiuj adres")
        dialog.resize(300, 200)

        v = QVBoxLayout(dialog)
        listw = QListWidget()
        listw.addItem("Adres działalności")
        listw.addItem("Adres korespondencji")
        listw.addItem("Adres zamieszkania")
        v.addWidget(listw)

        btn = QPushButton("Kopiuj")
        v.addWidget(btn)

        def _copy():
            idx = listw.currentRow()
            if idx == -1:
                return

            source = ["dzial", "kor", "zam"][idx]
            self._copy_address(source, target_prefix)
            dialog.accept()

        btn.clicked.connect(_copy)
        dialog.exec()

    def _copy_address(self, src: str, dst: str):
        fields = ["kraj", "woj", "powiat", "gmina", "miasto", "kod", "ulica", "budynek", "lokal"]
        for f in fields:
            getattr(self, f"{dst}_{f}").setText(getattr(self, f"{src}_{f}").text())

    # ============================
    # FORMA DZIAŁALNOŚCI I OPODATKOWANIE
    # ============================
    def _build_forma_dzial(self, main_layout: QVBoxLayout):
        box = QGroupBox("Forma działalności i opodatkowanie")
        form = QFormLayout(box)

        self.f_forma = QComboBox()
        self.f_forma.addItems(["JDG", "Spółka z o.o.", "Spółka cywilna", "Inna"])
        self.f_forma.setCurrentText(self.company_data.get("forma_dzial", "JDG"))

        self.f_opodatk = QComboBox()
        self.f_opodatk.addItems(["KPiR", "Ryczałt", "Księgi rachunkowe"])
        self.f_opodatk.setCurrentText(self.company_data.get("forma_opodatk", "KPiR"))

        self.f_analityka = QLineEdit(self.company_data.get("analityka_fk", ""))

        form.addRow("Forma działalności", self.f_forma)
        form.addRow("Forma opodatkowania", self.f_opodatk)
        form.addRow("Analityka FK", self.f_analityka)

        main_layout.addWidget(box)

    # ============================
    # KONTAKT
    # ============================
    def _build_kontakt(self, main_layout: QVBoxLayout):
        box = QGroupBox("Kontakt")
        form = QFormLayout(box)

        self.f_kont_imie = QLineEdit(self.company_data.get("kont_imie", ""))
        self.f_kont_nazw = QLineEdit(self.company_data.get("kont_nazw", ""))
        self.f_kont_email = QLineEdit(self.company_data.get("kont_email", ""))
        self.f_kont_tel = QLineEdit(self.company_data.get("kont_tel", ""))

        form.addRow("Imię", self.f_kont_imie)
        form.addRow("Nazwisko", self.f_kont_nazw)
        form.addRow("Email", self.f_kont_email)
        form.addRow("Telefon", self.f_kont_tel)

        main_layout.addWidget(box)
    # ============================
    # DATY DZIAŁALNOŚCI I ZUS
    # ============================
    def _build_daty_zus(self, main_layout: QVBoxLayout):
        box = QGroupBox("Daty działalności i ZUS")
        form = QFormLayout(box)

        # start działalności
        self.f_start = NoWheelDateEdit()
        self.f_start.setCalendarPopup(True)
        if self.company_data.get("start_dzial"):
            self.f_start.setDate(QDate.fromString(self.company_data["start_dzial"], "yyyy-MM-dd"))
        else:
            self.f_start.setDate(QDate(2000, 1, 1))  # neutralna data

        # rozpoczęcie obsługi
        self.f_obs = NoWheelDateEdit()
        self.f_obs.setCalendarPopup(True)
        if self.company_data.get("start_obs"):
            self.f_obs.setDate(QDate.fromString(self.company_data["start_obs"], "yyyy-MM-dd"))
        else:
            self.f_obs.setDate(QDate(2000, 1, 1))

        # ZUS forma
        self.zus_ulga = QCheckBox("Ulga na start")
        self.zus_pref = QCheckBox("ZUS preferencyjny")
        self.zus_maly = QCheckBox("Mały ZUS")
        self.zus_a1 = QCheckBox("A1")

        # koniec ulg
        self.f_koniec_ulgi = NoWheelDateEdit()
        self.f_koniec_ulgi.setCalendarPopup(True)
        self.f_koniec_ulgi.setDate(QDate(2000, 1, 1))

        self.f_koniec_a1 = NoWheelDateEdit()
        self.f_koniec_a1.setCalendarPopup(True)
        self.f_koniec_a1.setDate(QDate(2000, 1, 1))

        # eventy
        self.zus_ulga.stateChanged.connect(self._recalc_zus)
        self.zus_pref.stateChanged.connect(self._recalc_zus)
        self.zus_maly.stateChanged.connect(self._recalc_zus)
        self.zus_a1.stateChanged.connect(self._recalc_zus)
        self.f_start.dateChanged.connect(self._recalc_zus)

        # układ
        form.addRow("Start działalności", self.f_start)
        form.addRow("Rozpoczęcie obsługi", self.f_obs)

        zus_row = QWidget()
        zus_layout = QHBoxLayout(zus_row)
        zus_layout.setContentsMargins(0, 0, 0, 0)
        zus_layout.addWidget(self.zus_ulga)
        zus_layout.addWidget(self.zus_pref)
        zus_layout.addWidget(self.zus_maly)
        zus_layout.addWidget(self.zus_a1)
        form.addRow("Forma ZUS", zus_row)

        form.addRow("Koniec ulg", self.f_koniec_ulgi)
        form.addRow("Koniec A1", self.f_koniec_a1)

        main_layout.addWidget(box)

    # ============================
    # WYLICZANIE ZUS
    # ============================
    def _recalc_zus(self):
        start = self.f_start.date()
        if not start.isValid():
            return

        # ulga na start — 6 miesięcy
        if self.zus_ulga.isChecked():
            koniec = start.addMonths(6)
            self.f_koniec_ulgi.setDate(koniec)

        # preferencyjny — 24 miesiące
        elif self.zus_pref.isChecked():
            koniec = start.addMonths(24)
            self.f_koniec_ulgi.setDate(koniec)

        # mały ZUS — 36 miesięcy
        elif self.zus_maly.isChecked():
            koniec = start.addMonths(36)
            self.f_koniec_ulgi.setDate(koniec)

        # brak ulg
        else:
            self.f_koniec_ulgi.setDate(QDate(2000, 1, 1))

        # A1 — 12 miesięcy
        if self.zus_a1.isChecked():
            koniec_a1 = start.addMonths(12)
            self.f_koniec_a1.setDate(koniec_a1)
        else:
            self.f_koniec_a1.setDate(QDate(2000, 1, 1))

    # ============================
    # CENNIKI I LIMITY
    # ============================
    def _build_cenniki(self, main_layout: QVBoxLayout):
        box = QGroupBox("Cenniki i limity")
        form = QFormLayout(box)

        self.f_limit_ks = QLineEdit(str(self.company_data.get("limit_ksiegowosc", "")))
        self.f_limit_kadry = QLineEdit(str(self.company_data.get("limit_kadry", "")))
        self.f_limit_ryczalt = QLineEdit(str(self.company_data.get("limit_ryczalt", "")))

        form.addRow("Limit księgowość", self.f_limit_ks)
        form.addRow("Limit kadry", self.f_limit_kadry)
        form.addRow("Limit ryczałt", self.f_limit_ryczalt)

        main_layout.addWidget(box)

    # ============================
    # ZAPIS
    # ============================
    def _save(self):
        payload = {
            "nazwa": self.f_nazwa.text(),
            "nazwa_skrocona": self.f_nazwa_skr.text(),
            "nip": self.f_nip.text(),
            "regon": self.f_regon.text(),
            "krs": self.f_krs.text(),
            "pesel": self.f_pesel.text(),

            # adresy
            "dzial_kraj": self.dzial_kraj.text(),
            "dzial_woj": self.dzial_woj.text(),
            "dzial_powiat": self.dzial_powiat.text(),
            "dzial_gmina": self.dzial_gmina.text(),
            "dzial_miasto": self.dzial_miasto.text(),
            "dzial_kod": self.dzial_kod.text(),
            "dzial_ulica": self.dzial_ulica.text(),
            "dzial_budynek": self.dzial_budynek.text(),
            "dzial_lokal": self.dzial_lokal.text(),

            "kor_kraj": self.kor_kraj.text(),
            "kor_woj": self.kor_woj.text(),
            "kor_powiat": self.kor_powiat.text(),
            "kor_gmina": self.kor_gmina.text(),
            "kor_miasto": self.kor_miasto.text(),
            "kor_kod": self.kor_kod.text(),
            "kor_ulica": self.kor_ulica.text(),
            "kor_budynek": self.kor_budynek.text(),
            "kor_lokal": self.kor_lokal.text(),

            "zam_kraj": self.zam_kraj.text(),
            "zam_woj": self.zam_woj.text(),
            "zam_powiat": self.zam_powiat.text(),
            "zam_gmina": self.zam_gmina.text(),
            "zam_miasto": self.zam_miasto.text(),
            "zam_kod": self.zam_kod.text(),
            "zam_ulica": self.zam_ulica.text(),
            "zam_budynek": self.zam_budynek.text(),
            "zam_lokal": self.zam_lokal.text(),

            # forma działalności
            "forma_dzial": self.f_forma.currentText(),
            "forma_opodatk": self.f_opodatk.currentText(),
            "analityka_fk": self.f_analityka.text(),

            # kontakt
            "kont_imie": self.f_kont_imie.text(),
            "kont_nazw": self.f_kont_nazw.text(),
            "kont_email": self.f_kont_email.text(),
            "kont_tel": self.f_kont_tel.text(),

            # daty
            "start_dzial": self.f_start.date().toString("yyyy-MM-dd"),
            "start_obs": self.f_obs.date().toString("yyyy-MM-dd"),
            "koniec_ulgi": self.f_koniec_ulgi.date().toString("yyyy-MM-dd"),
            "koniec_a1": self.f_koniec_a1.date().toString("yyyy-MM-dd"),

            # limity
            "limit_ksiegowosc": self.f_limit_ks.text(),
            "limit_kadry": self.f_limit_kadry.text(),
            "limit_ryczalt": self.f_limit_ryczalt.text(),
        }

        save_company(payload)
        QMessageBox.information(self, "Sukces", "Dane firmy zapisane")
        if self.parent():
            self.parent().close()
