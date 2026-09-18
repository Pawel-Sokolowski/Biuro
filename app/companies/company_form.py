# app/companies/company_form.py
from typing import Optional, Dict, Any, List
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QComboBox, QTabWidget, QFormLayout, QScrollArea, QDateEdit,
    QMessageBox, QGroupBox, QCheckBox, QSpinBox
)
from PyQt6.QtCore import Qt, QDate
from PyQt6.QtGui import QFont

from app.db.dao_conn import get_conn
from app.db.dao_companies import save_company
from app.companies.company_logic import ceidg_fetch


class NoWheelDateEdit(QDateEdit):
    def wheelEvent(self, event):
        event.ignore()


class CompanyForm(QWidget):
    def __init__(self, pracownik_id: int, company_data: Optional[dict], parent=None):
        super().__init__(parent)
        self.pracownik_id = pracownik_id
        self.company_data = company_data or {}
        self.inputs: Dict[str, Any] = {}
        self._cenniki_rows: List[Dict[str, Any]] = []
        self._cenniki_map: Dict[str, Dict[str, Any]] = {}
        self._ceidg_in_progress = False
        self.setMinimumWidth(1000)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        container = QWidget()
        scroll.setWidget(container)
        self.main_layout = QVBoxLayout(container)
        self.main_layout.setContentsMargins(20, 20, 20, 20)
        self.main_layout.setSpacing(12)

        header = QLabel("Edycja danych Firmy")
        header.setFont(QFont("Arial", 18, QFont.Weight.Bold))
        self.main_layout.addWidget(header)

        self._build_ident_section()
        self._build_main_section()
        self._build_address_tabs()
        self._build_forma_section()
        self._build_contact_section()
        self._build_dates_section()
        self._build_mikro_section()
        self._build_cenniki_section()

        btn_row = QWidget()
        btn_l = QHBoxLayout(btn_row)
        btn_l.addStretch()
        b_save = QPushButton("Zapisz")
        b_save.clicked.connect(self._save)
        b_cancel = QPushButton("Anuluj")
        b_cancel.clicked.connect(self._close_parent)
        btn_l.addWidget(b_save)
        btn_l.addWidget(b_cancel)
        self.main_layout.addWidget(btn_row)

        final = QVBoxLayout(self)
        final.addWidget(scroll)

        self._load_cenniki()
        self._populate_from_data()
        self._update_cennik_auto()

    def _close_parent(self):
        if self.parent():
            try:
                if hasattr(self.parent(), "on_saved") and callable(self.parent().on_saved):
                    self.parent().on_saved()
            except Exception:
                pass
            try:
                self.parent().close()
            except Exception:
                pass
        else:
            self.setParent(None)
            self.deleteLater()

    # ---------------- IDENT ----------------
    def _build_ident_section(self):
        box = QGroupBox("Identyfikator podatnika")
        form = QFormLayout(box)

        row = QWidget()
        rl = QHBoxLayout(row)

        self.f_ident = QLineEdit(self.company_data.get("ident", ""))
        self.f_ident.setPlaceholderText("NIP / REGON / PESEL")
        self.f_ident.textChanged.connect(self._on_ident_changed)

        self.id_type_label = QLabel("UNKNOWN")
        self.id_type_label.setMinimumWidth(80)

        self.btn_ceidg = QPushButton("Pobierz z CEIDG")
        self.btn_ceidg.clicked.connect(lambda: ceidg_fetch(self))

        rl.addWidget(QLabel("Identyfikator"))
        rl.addWidget(self.f_ident)
        rl.addWidget(self.id_type_label)
        rl.addWidget(self.btn_ceidg)

        form.addRow(row)

        # CEIDG logic expects inputs["nip"]
        self.inputs["nip"] = self.f_ident

        self.main_layout.addWidget(box)

    def _detect_id_type(self, text: str) -> str:
        t = "".join(ch for ch in text if ch.isdigit())
        if len(t) == 10:
            return "NIP"
        if len(t) == 9:
            return "REGON"
        if len(t) == 11:
            return "PESEL"
        return "UNKNOWN"

    def _on_ident_changed(self, txt: str):
        kind = self._detect_id_type(txt)
        self.id_type_label.setText(kind)
        self.btn_ceidg.setEnabled(kind == "NIP")

    # ---------------- MAIN ----------------
    def _build_main_section(self):
        box = QGroupBox("Podstawowe dane")
        form = QFormLayout(box)

        self.f_nazwa = QLineEdit(self.company_data.get("nazwa", ""))
        self.inputs["nazwa"] = self.f_nazwa
        form.addRow("Nazwa", self.f_nazwa)

        self.f_regon = QLineEdit(self.company_data.get("regon", ""))
        self.inputs["regon"] = self.f_regon
        form.addRow("REGON", self.f_regon)

        self.f_edoreczenia = QLineEdit(self.company_data.get("adres_doreczen_elektronicznych", ""))
        self.inputs["adres_doreczen_elektronicznych"] = self.f_edoreczenia
        form.addRow("Adres e-doręczeń", self.f_edoreczenia)

        self.f_kody_pkd = QLineEdit(self.company_data.get("kody_pkd", ""))
        self.inputs["kody_pkd"] = self.f_kody_pkd
        form.addRow("Kody PKD", self.f_kody_pkd)

        self.main_layout.addWidget(box)

    # ---------------- ADDRESSES ----------------
    def _build_address_tabs(self):
        self.tabs = QTabWidget()

        # działalność
        dz = QWidget()
        dz_form = QFormLayout(dz)
        self.dz_miasto = QLineEdit()
        self.dz_kod = QLineEdit()
        self.dz_ulica = QLineEdit()
        self.dz_budynek = QLineEdit()
        self.dz_lokal = QLineEdit()
        self.dz_woj = QLineEdit()
        self.dz_pow = QLineEdit()
        self.dz_gmi = QLineEdit()
        self.dz_kraj = QLineEdit()

        dz_form.addRow("Miasto", self.dz_miasto)
        dz_form.addRow("Kod", self.dz_kod)
        dz_form.addRow("Ulica", self.dz_ulica)
        dz_form.addRow("Budynek", self.dz_budynek)
        dz_form.addRow("Lokal", self.dz_lokal)
        dz_form.addRow("Województwo", self.dz_woj)
        dz_form.addRow("Powiat", self.dz_pow)
        dz_form.addRow("Gmina", self.dz_gmi)
        dz_form.addRow("Kraj", self.dz_kraj)

        self.inputs.update({
            "dz_miasto": self.dz_miasto,
            "dz_kod": self.dz_kod,
            "dz_ulica": self.dz_ulica,
            "dz_budynek": self.dz_budynek,
            "dz_lokal": self.dz_lokal,
            "dz_wojewodztwo": self.dz_woj,
            "dz_powiat": self.dz_pow,
            "dz_gmina": self.dz_gmi,
            "dz_kraj": self.dz_kraj,
        })

        # korespondencja
        ko = QWidget()
        ko_form = QFormLayout(ko)
        self.ko_miasto = QLineEdit()
        self.ko_kod = QLineEdit()
        self.ko_ulica = QLineEdit()
        self.ko_budynek = QLineEdit()
        self.ko_lokal = QLineEdit()
        self.ko_woj = QLineEdit()
        self.ko_pow = QLineEdit()
        self.ko_gmi = QLineEdit()
        self.ko_kraj = QLineEdit()

        ko_form.addRow("Miasto", self.ko_miasto)
        ko_form.addRow("Kod", self.ko_kod)
        ko_form.addRow("Ulica", self.ko_ulica)
        ko_form.addRow("Budynek", self.ko_budynek)
        ko_form.addRow("Lokal", self.ko_lokal)
        ko_form.addRow("Województwo", self.ko_woj)
        ko_form.addRow("Powiat", self.ko_pow)
        ko_form.addRow("Gmina", self.ko_gmi)
        ko_form.addRow("Kraj", self.ko_kraj)

        self.inputs.update({
            "ko_miasto": self.ko_miasto,
            "ko_kod": self.ko_kod,
            "ko_ulica": self.ko_ulica,
            "ko_budynek": self.ko_budynek,
            "ko_lokal": self.ko_lokal,
            "ko_wojewodztwo": self.ko_woj,
            "ko_powiat": self.ko_pow,
            "ko_gmina": self.ko_gmi,
            "ko_kraj": self.ko_kraj,
        })

        # zamieszkanie (ukrywane dla nie-JDG)
        zam = QWidget()
        zam_form = QFormLayout(zam)
        self.zam_miasto = QLineEdit()
        self.zam_kod = QLineEdit()
        self.zam_ulica = QLineEdit()
        self.zam_budynek = QLineEdit()
        self.zam_lokal = QLineEdit()
        self.zam_woj = QLineEdit()
        self.zam_pow = QLineEdit()
        self.zam_gmi = QLineEdit()
        self.zam_kraj = QLineEdit()

        zam_form.addRow("Miasto", self.zam_miasto)
        zam_form.addRow("Kod", self.zam_kod)
        zam_form.addRow("Ulica", self.zam_ulica)
        zam_form.addRow("Budynek", self.zam_budynek)
        zam_form.addRow("Lokal", self.zam_lokal)
        zam_form.addRow("Województwo", self.zam_woj)
        zam_form.addRow("Powiat", self.zam_pow)
        zam_form.addRow("Gmina", self.zam_gmi)
        zam_form.addRow("Kraj", self.zam_kraj)

        # zam nie jest używany przez CEIDG logic, ale zapisujemy
        self.inputs.update({
            "zam_miasto": self.zam_miasto,
            "zam_kod": self.zam_kod,
            "zam_ulica": self.zam_ulica,
            "zam_budynek": self.zam_budynek,
            "zam_lokal": self.zam_lokal,
        })

        self.tabs.addTab(dz, "Adres działalności")
        self.tabs.addTab(ko, "Adres korespondencji")
        self.tabs.addTab(zam, "Adres zamieszkania")

        self.main_layout.addWidget(self.tabs)

    # ---------------- FORMA ----------------
    def _build_forma_section(self):
        box = QGroupBox("Forma działalności i opodatkowanie")
        form = QFormLayout(box)

        self.f_forma = QComboBox()
        self.f_forma.addItems(["JDG", "Spółka z o.o.", "Spółka cywilna", "Inna"])
        self.f_forma.currentIndexChanged.connect(self._on_forma_changed)
        form.addRow("Forma działalności", self.f_forma)

        self.f_opodatk = QComboBox()
        self.f_opodatk.addItems(["KPiR", "Ryczałt", "Księgi rachunkowe"])
        self.f_opodatk.currentIndexChanged.connect(self._update_cennik_auto)
        form.addRow("Forma opodatkowania", self.f_opodatk)

        self.VAT_checkbox = QCheckBox("VAT czynny")
        self.VAT_checkbox.stateChanged.connect(self._update_cennik_auto)
        form.addRow(self.VAT_checkbox)

        self.f_analityka = QLineEdit(self.company_data.get("analityka_fk", ""))
        form.addRow("Analityka FK", self.f_analityka)

        self.main_layout.addWidget(box)

    def _on_forma_changed(self, idx: int):
        forma = self.f_forma.currentText()
        # adres zamieszkania tylko dla JDG
        self.tabs.setTabVisible(2, forma == "JDG")

    # ---------------- CONTACT ----------------
    def _build_contact_section(self):
        box = QGroupBox("Kontakt")
        form = QFormLayout(box)

        self.kont_imie = QLineEdit()
        self.kont_nazw = QLineEdit()
        self.kont_email = QLineEdit()
        self.kont_tel = QLineEdit()

        form.addRow("Imię", self.kont_imie)
        form.addRow("Nazwisko", self.kont_nazw)
        form.addRow("Email", self.kont_email)
        form.addRow("Telefon", self.kont_tel)

        self.inputs.update({
            "kont_imie": self.kont_imie,
            "kont_nazw": self.kont_nazw,
            "kont_email": self.kont_email,
            "kont_tel": self.kont_tel,
        })

        self.main_layout.addWidget(box)

    # ---------------- DATES ----------------
    def _build_dates_section(self):
        box = QGroupBox("Daty")
        form = QFormLayout(box)

        self.f_start = NoWheelDateEdit()
        self.f_start.setCalendarPopup(True)
        self.inputs["data_rozpoczecia_dg"] = self.f_start
        form.addRow("Data rozpoczęcia DG", self.f_start)

        self.main_layout.addWidget(box)

    # ---------------- MIKRORACHUNKI ----------------
    def _build_mikro_section(self):
        box = QGroupBox("Mikrorachunki (ręczne wklejenie)")
        form = QFormLayout(box)

        self.f_mikro_pit = QLineEdit(self.company_data.get("mikrorachunek_pit", ""))
        self.f_mikro_zus = QLineEdit(self.company_data.get("mikrorachunek_zus", ""))

        form.addRow("Mikrorachunek PIT", self.f_mikro_pit)
        form.addRow("Mikrorachunek ZUS", self.f_mikro_zus)

        self.main_layout.addWidget(box)

    # ---------------- CENNIKI ----------------
    def _build_cenniki_section(self):
        box = QGroupBox("Cenniki i limity")
        form = QFormLayout(box)

        self.cennik_combo = QComboBox()
        self.cennik_combo.addItem("— brak —", None)
        self.cennik_combo.currentIndexChanged.connect(self._on_cennik_changed)
        form.addRow("Główny cennik", self.cennik_combo)

        self.cena_ksiegowosc_display = QLineEdit()
        self.cena_ksiegowosc_display.setReadOnly(True)
        form.addRow("Cena księgowość (netto)", self.cena_ksiegowosc_display)

        self.vat_display = QLineEdit()
        self.vat_display.setReadOnly(True)
        form.addRow("VAT (z klucza)", self.vat_display)

        self.f_limit_dokumentow = QSpinBox()
        self.f_limit_dokumentow.setMaximum(1000000)
        self.f_limit_dokumentow.setValue(int(self.company_data.get("limit_dokumentow", 0)))
        self.f_limit_dokumentow.valueChanged.connect(self._update_cennik_auto)
        form.addRow("Limit dokumentów", self.f_limit_dokumentow)

        self.f_limit_kadry = QSpinBox()
        self.f_limit_kadry.setMaximum(1000000)
        self.f_limit_kadry.setValue(int(self.company_data.get("limit_kadry", 0)))
        form.addRow("Limit kadry", self.f_limit_kadry)

        self.f_limit_ryczalt = QSpinBox()
        self.f_limit_ryczalt.setMaximum(1000000)
        self.f_limit_ryczalt.setValue(int(self.company_data.get("limit_ryczalt", 0)))
        form.addRow("Limit ryczałt", self.f_limit_ryczalt)

        self.zdrowotna_checkbox = QCheckBox("Naliczaj zdrowotną")
        self.zdrowotna_checkbox.setChecked(bool(self.company_data.get("zdrowotna", 0)))
        form.addRow(self.zdrowotna_checkbox)

        self.main_layout.addWidget(box)

    def _load_cenniki(self):
        conn = get_conn()
        conn.row_factory = None
        cur = conn.cursor()
        cur.execute("""
            SELECT klucz, cena_netto_ksiegowosc, cena_brutto_ksiegowosc,
                   zdrowotna, netto, brutto
            FROM cenniki
            ORDER BY klucz
        """)
        rows = cur.fetchall()
        conn.close()

        self._cenniki_rows = []
        self._cenniki_map = {}
        self.cennik_combo.clear()
        self.cennik_combo.addItem("— brak —", None)

        for r in rows:
            klucz = r[0]
            cena_netto_ks = r[1]
            zdrowotna = r[3]
            netto = r[4]
            brutto = r[5]
            label = klucz
            if cena_netto_ks is not None:
                try:
                    label = f"{klucz} ({float(cena_netto_ks):.2f})"
                except Exception:
                    label = f"{klucz} ({cena_netto_ks})"
            self.cennik_combo.addItem(label, klucz)
            info = {
                "klucz": klucz,
                "cena_netto_ksiegowosc": cena_netto_ks,
                "zdrowotna": zdrowotna,
                "netto": netto,
                "brutto": brutto,
            }
            self._cenniki_rows.append(info)
            self._cenniki_map[klucz] = info

    def _on_cennik_changed(self, index: int):
        klucz = self.cennik_combo.currentData()
        if not klucz:
            self.cena_ksiegowosc_display.setText("")
            self.vat_display.setText("")
            return

        info = self._cenniki_map.get(klucz, {})
        netto = info.get("cena_netto_ksiegowosc") or info.get("netto")

        if netto is None:
            self.cena_ksiegowosc_display.setText("")
        else:
            try:
                self.cena_ksiegowosc_display.setText(f"{float(netto):.2f}")
            except Exception:
                self.cena_ksiegowosc_display.setText(str(netto))

        vat_val = ""
        try:
            parts = klucz.split("_")
            if len(parts) >= 2:
                suf = parts[1]
                if suf.upper() == "N":
                    vat_val = "0"
                else:
                    try:
                        vat_val = str(int(suf))
                    except Exception:
                        found = None
                        for p in parts[1:]:
                            try:
                                found = int(p)
                                break
                            except Exception:
                                continue
                        vat_val = str(found) if found is not None else ""
        except Exception:
            vat_val = ""

        self.vat_display.setText(vat_val)

        zdrow = info.get("zdrowotna")
        if zdrow is not None:
            try:
                self.zdrowotna_checkbox.setChecked(float(zdrow) > 0)
            except Exception:
                pass

    def _pick_cennik_auto(self, forma: str, vat_czynny: bool, limit: int) -> Optional[Dict[str, Any]]:
        allowed: List[Dict[str, Any]] = []
        for r in self._cenniki_rows:
            k = r["klucz"]
            if forma == "KPiR":
                if vat_czynny and k.startswith("KPIR_T"):
                    allowed.append(r)
                elif not vat_czynny and k.startswith("KPIR_N"):
                    allowed.append(r)
            elif forma == "Ryczałt":
                if k.startswith("RYCZALT"):
                    allowed.append(r)
            elif forma == "Księgi rachunkowe":
                if k.startswith("KR"):
                    allowed.append(r)

        if not allowed:
            return None

        # tu można dodać logikę po limicie dokumentów (np. zakresy w kluczu),
        # na razie wybieramy pierwszy dopuszczalny
        return allowed[0]

    def _update_cennik_auto(self):
        forma = self.f_opodatk.currentText()
        vat = self.VAT_checkbox.isChecked()
        limit = int(self.f_limit_dokumentow.value())

        chosen = self._pick_cennik_auto(forma, vat, limit)
        if not chosen:
            return

        klucz = chosen["klucz"]
        idx = self.cennik_combo.findData(klucz)
        if idx >= 0:
            self.cennik_combo.setCurrentIndex(idx)

    # ---------------- POPULATE ----------------
    def _populate_from_data(self):
        d = self.company_data
        if not d:
            return

        # ident
        if d.get("nip"):
            self.f_ident.setText(d.get("nip", ""))

        # main
        self.f_nazwa.setText(d.get("nazwa", ""))
        self.f_regon.setText(d.get("regon", ""))
        self.f_edoreczenia.setText(d.get("adres_doreczen_elektronicznych", ""))
        self.f_kody_pkd.setText(d.get("kody_pkd", ""))

        # adres działalności
        self.dz_miasto.setText(d.get("miasto", ""))
        self.dz_kod.setText(d.get("kod_pocztowy", ""))
        self.dz_ulica.setText(d.get("ulica", ""))
        self.dz_budynek.setText(d.get("adres_budynek", ""))
        self.dz_lokal.setText(d.get("adres_lokal", ""))
        self.dz_woj.setText(d.get("wojewodztwo", ""))
        self.dz_pow.setText(d.get("powiat", ""))
        self.dz_gmi.setText(d.get("gmina", ""))
        self.dz_kraj.setText(d.get("kraj", ""))

        # adres korespondencyjny
        self.ko_miasto.setText(d.get("adres_koresp_miasto", ""))
        self.ko_kod.setText(d.get("adres_koresp_kod_pocztowy", ""))
        self.ko_ulica.setText(d.get("adres_koresp_ulica", ""))
        self.ko_budynek.setText(d.get("adres_koresp_budynek", ""))
        self.ko_lokal.setText(d.get("adres_koresp_lokal", ""))
        self.ko_woj.setText(d.get("adres_koresp_wojewodztwo", ""))
        self.ko_pow.setText(d.get("adres_koresp_powiat", ""))
        self.ko_gmi.setText(d.get("adres_koresp_gmina", ""))
        self.ko_kraj.setText(d.get("adres_koresp_kraj", ""))

        # adres zamieszkania
        self.zam_miasto.setText(d.get("adres_zam_miasto", ""))
        self.zam_kod.setText(d.get("adres_zam_kod_pocztowy", ""))
        self.zam_ulica.setText(d.get("adres_zam_ulica", ""))
        self.zam_budynek.setText(d.get("adres_zam_budynek", ""))
        self.zam_lokal.setText(d.get("adres_zam_lokal", ""))

        # kontakt
        osoba = d.get("osoba_kontaktowa", "")
        if osoba:
            parts = osoba.split(" ", 1)
            self.kont_imie.setText(parts[0])
            if len(parts) > 1:
                self.kont_nazw.setText(parts[1])
        self.kont_email.setText(d.get("adres_email", ""))
        self.kont_tel.setText(d.get("nr_telefonu", ""))

        # forma
        if d.get("forma_dzialalnosci"):
            self.f_forma.setCurrentText(d.get("forma_dzialalnosci"))
        if d.get("forma_opodatkowania"):
            self.f_opodatk.setCurrentText(d.get("forma_opodatkowania"))
        self.VAT_checkbox.setChecked(bool(d.get("vat_czynny", 0)))
        self.f_analityka.setText(d.get("analityka_fk", ""))

        # daty
        if d.get("data_rozpoczecia_dg"):
            try:
                self.f_start.setDate(QDate.fromString(d["data_rozpoczecia_dg"], "yyyy-MM-dd"))
            except Exception:
                pass

        # mikrorachunki
        self.f_mikro_pit.setText(d.get("mikrorachunek_pit", ""))
        self.f_mikro_zus.setText(d.get("mikrorachunek_zus", ""))

        # cennik
        gl = d.get("glowny_cennik")
        if gl:
            idx = self.cennik_combo.findData(gl)
            if idx >= 0:
                self.cennik_combo.setCurrentIndex(idx)

        # limity
        self.f_limit_dokumentow.setValue(int(d.get("limit_dokumentow", 0)))
        self.f_limit_kadry.setValue(int(d.get("limit_kadry", 0)))
        self.f_limit_ryczalt.setValue(int(d.get("limit_ryczalt", 0)))

        zdrow = d.get("zdrowotna")
        if zdrow is not None:
            try:
                self.zdrowotna_checkbox.setChecked(float(zdrow) > 0)
            except Exception:
                pass

    # ---------------- SAVE ----------------
    def _save(self):
        payload: Dict[str, Any] = {}

        # ident
        payload["nip"] = "".join(ch for ch in self.f_ident.text().strip() if ch.isdigit())

        # main
        payload["nazwa"] = self.f_nazwa.text().strip()
        payload["regon"] = self.f_regon.text().strip()
        payload["adres_doreczen_elektronicznych"] = self.f_edoreczenia.text().strip()
        payload["kody_pkd"] = self.f_kody_pkd.text().strip()

        # adres działalności
        payload["miasto"] = self.dz_miasto.text()
        payload["kod_pocztowy"] = self.dz_kod.text()
        payload["ulica"] = self.dz_ulica.text()
        payload["adres_budynek"] = self.dz_budynek.text()
        payload["adres_lokal"] = self.dz_lokal.text()
        payload["wojewodztwo"] = self.dz_woj.text()
        payload["powiat"] = self.dz_pow.text()
        payload["gmina"] = self.dz_gmi.text()
        payload["kraj"] = self.dz_kraj.text()

        # adres korespondencyjny
        payload["adres_koresp_miasto"] = self.ko_miasto.text()
        payload["adres_koresp_kod_pocztowy"] = self.ko_kod.text()
        payload["adres_koresp_ulica"] = self.ko_ulica.text()
        payload["adres_koresp_budynek"] = self.ko_budynek.text()
        payload["adres_koresp_lokal"] = self.ko_lokal.text()
        payload["adres_koresp_wojewodztwo"] = self.ko_woj.text()
        payload["adres_koresp_powiat"] = self.ko_pow.text()
        payload["adres_koresp_gmina"] = self.ko_gmi.text()
        payload["adres_koresp_kraj"] = self.ko_kraj.text()

        # adres zamieszkania
        payload["adres_zam_miasto"] = self.zam_miasto.text()
        payload["adres_zam_kod_pocztowy"] = self.zam_kod.text()
        payload["adres_zam_ulica"] = self.zam_ulica.text()
        payload["adres_zam_budynek"] = self.zam_budynek.text()
        payload["adres_zam_lokal"] = self.zam_lokal.text()

        # kontakt
        payload["osoba_kontaktowa"] = (self.kont_imie.text().strip() + " " + self.kont_nazw.text().strip()).strip()
        payload["adres_email"] = self.kont_email.text().strip()
        payload["nr_telefonu"] = self.kont_tel.text().strip()

        # forma
        payload["forma_dzialalnosci"] = self.f_forma.currentText()
        payload["forma_opodatkowania"] = self.f_opodatk.currentText()
        payload["vat_czynny"] = 1 if self.VAT_checkbox.isChecked() else 0
        payload["analityka_fk"] = self.f_analityka.text().strip()

        # daty
        payload["data_rozpoczecia_dg"] = self.f_start.date().toString("yyyy-MM-dd")

        # mikrorachunki (ręczne)
        payload["mikrorachunek_pit"] = self.f_mikro_pit.text().strip()
        payload["mikrorachunek_zus"] = self.f_mikro_zus.text().strip()

        # cennik i limity
        payload["glowny_cennik"] = self.cennik_combo.currentData()
        payload["limit_dokumentow"] = int(self.f_limit_dokumentow.value())
        payload["limit_kadry"] = int(self.f_limit_kadry.value())
        payload["limit_ryczalt"] = int(self.f_limit_ryczalt.value())
        payload["zdrowotna"] = 1 if self.zdrowotna_checkbox.isChecked() else 0

        if self.company_data.get("id"):
            payload["id"] = self.company_data["id"]

        try:
            save_company(payload)
        except Exception as e:
            QMessageBox.critical(self, "Błąd zapisu", f"Nie udało się zapisać firmy: {e}")
            return

        QMessageBox.information(self, "Zapisano", "Dane firmy zostały zapisane.")
        self._close_parent()
