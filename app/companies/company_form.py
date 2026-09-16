# app/companies/company_form.py
from typing import Optional, Dict, Any
import sqlite3

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QComboBox, QTabWidget, QFormLayout, QScrollArea, QDateEdit,
    QMessageBox, QGroupBox, QCheckBox, QSpinBox
)
from PyQt6.QtCore import Qt, QDate
from PyQt6.QtGui import QFont

from app.db.dao_conn import get_conn
from app.db.dao_companies import save_company, get_company


class NoWheelDateEdit(QDateEdit):
    def wheelEvent(self, event):
        event.ignore()


class CompanyForm(QWidget):
    """
    Formularz dodawania / edycji firmy.
    Zgodnie z wymaganiem:
      - pole wyboru głównego cennika (glowny_cennik)
      - pole limit dokumentów (limit_dokumentow) z możliwością nadpisania
      - checkbox zdrowotna (wartość zapisywana w polu 'zdrowotna')
      - wyświetlanie ceny netto i VAT pobieranych z cennika
    Po zapisie wywoływany jest save_company(payload).
    """

    def __init__(self, pracownik_id: int, company_data: Optional[dict], parent=None):
        super().__init__(parent)
        self.pracownik_id = pracownik_id
        self.company_data = company_data or {}

        self.setMinimumWidth(900)

        # główny scroll
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)

        container = QWidget()
        scroll.setWidget(container)

        self.main_layout = QVBoxLayout(container)
        self.main_layout.setContentsMargins(20, 20, 20, 20)
        self.main_layout.setSpacing(18)

        # header
        header = QLabel("Dane firmy")
        header.setFont(QFont("Arial", 18, QFont.Weight.Bold))
        self.main_layout.addWidget(header)

        # dane główne
        self._build_main_section()
        self._build_address_tabs()
        self._build_forma_dzial(self.main_layout)
        self._build_kontakt(self.main_layout)
        self._build_daty_zus(self.main_layout)
        self._build_cenniki(self.main_layout)

        # przyciski
        btn_row = QWidget()
        btn_layout = QHBoxLayout(btn_row)
        btn_layout.addStretch()

        btn_save = QPushButton("Zapisz")
        btn_save.clicked.connect(self._save)
        btn_cancel = QPushButton("Anuluj")
        btn_cancel.clicked.connect(lambda: self._close_parent())

        btn_layout.addWidget(btn_save)
        btn_layout.addWidget(btn_cancel)

        self.main_layout.addWidget(btn_row)

        final_layout = QVBoxLayout(self)
        final_layout.addWidget(scroll)

        # załaduj cenniki i wypełnij pola
        self._load_cenniki()
        self._populate_from_data()

    def _close_parent(self):
        if self.parent():
            # jeśli formularz jest osadzony w oknie, zamknij rodzica
            try:
                self.parent().close()
            except Exception:
                pass
        else:
            self.setParent(None)
            self.deleteLater()

    def _build_main_section(self):
        box_main = QGroupBox("Dane główne")
        layout_main = QFormLayout()
        layout_main.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)
        box_main.setLayout(layout_main)

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

        self.f_nip = QLineEdit(self.company_data.get("nip", ""))
        self.f_regon = QLineEdit(self.company_data.get("regon", ""))

        row_nip = QWidget()
        row_nip_layout = QHBoxLayout(row_nip)
        row_nip_layout.setContentsMargins(0, 0, 0, 0)
        row_nip_layout.addWidget(QLabel("NIP"))
        row_nip_layout.addWidget(self.f_nip)
        row_nip_layout.addWidget(QLabel("REGON"))
        row_nip_layout.addWidget(self.f_regon)
        layout_main.addRow(row_nip)

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

        self.main_layout.addWidget(box_main)

    def _build_address_tabs(self):
        self.tabs_address = QTabWidget()

        self.tab_dzial = QWidget()
        self.tab_kor = QWidget()
        self.tab_zam = QWidget()

        self._build_address_tab(self.tab_dzial, "dzial")
        self._build_address_tab(self.tab_kor, "kor")
        self._build_address_tab(self.tab_zam, "zam")

        self.tabs_address.addTab(self.tab_dzial, "Adres działalności")
        self.tabs_address.addTab(self.tab_kor, "Adres korespondencji")
        self.tabs_address.addTab(self.tab_zam, "Adres zamieszkania")

        self.main_layout.addWidget(self.tabs_address)

    def _build_address_tab(self, tab: QWidget, prefix: str):
        from PyQt6.QtWidgets import QFormLayout
        layout = QFormLayout(tab)
        layout.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)

        setattr(self, f"{prefix}_miasto", QLineEdit(self.company_data.get(f"{prefix}_miasto", "")))
        setattr(self, f"{prefix}_kod", QLineEdit(self.company_data.get(f"{prefix}_kod", "")))
        setattr(self, f"{prefix}_ulica", QLineEdit(self.company_data.get(f"{prefix}_ulica", "")))
        setattr(self, f"{prefix}_budynek", QLineEdit(self.company_data.get(f"{prefix}_budynek", "")))
        setattr(self, f"{prefix}_lokal", QLineEdit(self.company_data.get(f"{prefix}_lokal", "")))

        layout.addRow("Miasto", getattr(self, f"{prefix}_miasto"))
        layout.addRow("Kod pocztowy", getattr(self, f"{prefix}_kod"))
        layout.addRow("Ulica", getattr(self, f"{prefix}_ulica"))
        layout.addRow("Budynek", getattr(self, f"{prefix}_budynek"))
        layout.addRow("Lokal", getattr(self, f"{prefix}_lokal"))

    def _build_forma_dzial(self, main_layout: QVBoxLayout):
        box = QGroupBox("Forma działalności i opodatkowanie")
        form = QFormLayout(box)

        self.f_forma = QComboBox()
        self.f_forma.addItems(["JDG", "Spółka z o.o.", "Spółka cywilna", "Inna"])
        self.f_forma.setCurrentText(self.company_data.get("forma_dzialalnosci", "JDG"))

        self.f_opodatk = QComboBox()
        self.f_opodatk.addItems(["KPiR", "Ryczałt", "Księgi rachunkowe"])
        self.f_opodatk.setCurrentText(self.company_data.get("forma_opodatkowania", "KPiR"))

        self.f_analityka = QLineEdit(self.company_data.get("analityka_fk", ""))

        form.addRow("Forma działalności", self.f_forma)
        form.addRow("Forma opodatkowania", self.f_opodatk)
        form.addRow("Analityka FK", self.f_analityka)

        main_layout.addWidget(box)

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

    def _build_daty_zus(self, main_layout: QVBoxLayout):
        box = QGroupBox("Daty działalności i ZUS")
        form = QFormLayout(box)

        self.f_start = NoWheelDateEdit()
        self.f_start.setCalendarPopup(True)
        if self.company_data.get("data_rozpoczecia_dg"):
            self.f_start.setDate(QDate.fromString(self.company_data["data_rozpoczecia_dg"], "yyyy-MM-dd"))
        else:
            self.f_start.setDate(QDate(2000, 1, 1))

        self.f_obs = NoWheelDateEdit()
        self.f_obs.setCalendarPopup(True)
        if self.company_data.get("data_rozpoczecia_obslugi"):
            self.f_obs.setDate(QDate.fromString(self.company_data["data_rozpoczecia_obslugi"], "yyyy-MM-dd"))
        else:
            self.f_obs.setDate(QDate(2000, 1, 1))

        form.addRow("Start działalności", self.f_start)
        form.addRow("Rozpoczęcie obsługi", self.f_obs)

        main_layout.addWidget(box)

    def _build_cenniki(self, main_layout: QVBoxLayout):
        box = QGroupBox("Cenniki i limity")
        form = QFormLayout(box)

        # główny cennik (glowny_cennik)
        self.cennik_combo = QComboBox()
        self.cennik_combo.addItem("— brak —", None)
        self.cennik_combo.currentIndexChanged.connect(self._on_cennik_changed)
        form.addRow("Główny cennik", self.cennik_combo)

        # wyświetlane pola: cena netto i VAT
        self.cena_ksiegowosc_display = QLineEdit()
        self.cena_ksiegowosc_display.setReadOnly(True)
        form.addRow("Cena księgowość (netto)", self.cena_ksiegowosc_display)

        self.vat_display = QLineEdit()
        self.vat_display.setReadOnly(True)
        form.addRow("VAT (z klucza)", self.vat_display)

        # limit dokumentów (limit_dokumentow) - główny limit księgowości
        self.f_limit_dokumentow = QSpinBox()
        self.f_limit_dokumentow.setMaximum(1000000)
        self.f_limit_dokumentow.setValue(int(self.company_data.get("limit_dokumentow") or 0))
        form.addRow("Limit dokumentów (księgowość)", self.f_limit_dokumentow)

        # dodatkowe limity (opcjonalne)
        self.f_limit_kadry = QSpinBox()
        self.f_limit_kadry.setMaximum(1000000)
        self.f_limit_kadry.setValue(int(self.company_data.get("limit_kadry") or 0))
        form.addRow("Limit kadry", self.f_limit_kadry)

        self.f_limit_ryczalt = QSpinBox()
        self.f_limit_ryczalt.setMaximum(1000000)
        self.f_limit_ryczalt.setValue(int(self.company_data.get("limit_ryczalt") or 0))
        form.addRow("Limit ryczałt", self.f_limit_ryczalt)

        # zdrowotna checkbox + wartość (jeśli cennik ma wartość, pokaż ją)
        self.zdrowotna_checkbox = QCheckBox("Naliczaj zdrowotną")
        form.addRow(self.zdrowotna_checkbox)

        main_layout.addWidget(box)

    def _load_cenniki(self):
        """
        Wczytuje cenniki z tabeli cenniki i wypełnia combobox.
        Mapujemy klucz -> wartości (cena_netto_ksiegowosc, cena_za_dokument_ponad_limit, zdrowotna).
        """
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("""
            SELECT klucz,
                   cena_netto_ksiegowosc,
                   cena_brutto_ksiegowosc,
                   cena_za_dokument_ponad_limit,
                   zdrowotna,
                   netto,
                   brutto
            FROM cenniki
            ORDER BY klucz
        """)
        rows = cur.fetchall()
        conn.close()

        self._cenniki_map = {}
        # zachowaj pierwszy element jako brak
        # combobox już ma "— brak —"
        for r in rows:
            klucz = r["klucz"]
            label = klucz
            if r["cena_netto_ksiegowosc"] is not None:
                try:
                    label = f"{klucz} ({float(r['cena_netto_ksiegowosc']):.2f})"
                except Exception:
                    label = f"{klucz}"
            self.cennik_combo.addItem(label, klucz)
            self._cenniki_map[klucz] = {
                "cena_netto_ksiegowosc": r["cena_netto_ksiegowosc"],
                "cena_brutto_ksiegowosc": r["cena_brutto_ksiegowosc"],
                "cena_za_dokument_ponad_limit": r["cena_za_dokument_ponad_limit"],
                "zdrowotna": r["zdrowotna"],
                "netto": r["netto"],
                "brutto": r["brutto"]
            }

    def _populate_from_data(self):
        d = self.company_data
        if not d:
            return

        self.f_nazwa.setText(str(d.get("nazwa", "")))
        self.f_nazwa_skr.setText(str(d.get("nazwa_skrocona", "")))
        self.f_nip.setText(str(d.get("nip", "")))
        self.f_regon.setText(str(d.get("regon", "")))
        self.f_krs.setText(str(d.get("krs", "")))
        self.f_pesel.setText(str(d.get("pesel", "")))

        # adresy
        for prefix in ("dzial", "kor", "zam"):
            miasto = d.get(f"{prefix}_miasto")
            if miasto is not None:
                getattr(self, f"{prefix}_miasto").setText(str(miasto))
            kod = d.get(f"{prefix}_kod")
            if kod is not None:
                getattr(self, f"{prefix}_kod").setText(str(kod))
            ulica = d.get(f"{prefix}_ulica")
            if ulica is not None:
                getattr(self, f"{prefix}_ulica").setText(str(ulica))
            bud = d.get(f"{prefix}_budynek")
            if bud is not None:
                getattr(self, f"{prefix}_budynek").setText(str(bud))
            lok = d.get(f"{prefix}_lokal")
            if lok is not None:
                getattr(self, f"{prefix}_lokal").setText(str(lok))

        # forma/opodatkowanie
        if d.get("forma_dzialalnosci"):
            self.f_forma.setCurrentText(d.get("forma_dzialalnosci"))
        if d.get("forma_opodatkowania"):
            self.f_opodatk.setCurrentText(d.get("forma_opodatkowania"))
        if d.get("analityka_fk"):
            self.f_analityka.setText(d.get("analityka_fk"))

        # kontakt
        self.f_kont_imie.setText(d.get("kont_imie", ""))
        self.f_kont_nazw.setText(d.get("kont_nazw", ""))
        self.f_kont_email.setText(d.get("kont_email", ""))
        self.f_kont_tel.setText(d.get("kont_tel", ""))

        # daty
        if d.get("data_rozpoczecia_dg"):
            try:
                self.f_start.setDate(QDate.fromString(d["data_rozpoczecia_dg"], "yyyy-MM-dd"))
            except Exception:
                pass
        if d.get("data_rozpoczecia_obslugi"):
            try:
                self.f_obs.setDate(QDate.fromString(d["data_rozpoczecia_obslugi"], "yyyy-MM-dd"))
            except Exception:
                pass

        # cennik i limity
        gl = d.get("glowny_cennik")
        if gl:
            idx = self.cennik_combo.findData(gl)
            if idx >= 0:
                self.cennik_combo.setCurrentIndex(idx)

        self.f_limit_dokumentow.setValue(int(d.get("limit_dokumentow") or 0))
        self.f_limit_kadry.setValue(int(d.get("limit_kadry") or 0))
        self.f_limit_ryczalt.setValue(int(d.get("limit_ryczalt") or 0))

        zdrow = d.get("zdrowotna")
        if zdrow is not None:
            try:
                self.zdrowotna_checkbox.setChecked(float(zdrow) > 0)
            except Exception:
                self.zdrowotna_checkbox.setChecked(False)

        # zaktualizuj wyświetlane ceny
        self._on_cennik_changed(self.cennik_combo.currentIndex())

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

        # VAT z klucza: format TYP_VAT_LIMIT (np. KH_N_10, Ryczałt_23_5)
        vat_val = ""
        try:
            parts = klucz.split("_")
            if len(parts) >= 2:
                suf = parts[1]
                if isinstance(suf, str) and suf.upper() == "N":
                    vat_val = "0"
                else:
                    try:
                        vat_val = str(int(suf))
                    except Exception:
                        # spróbuj znaleźć liczbę w częściach
                        found = None
                        for p in parts[1:]:
                            try:
                                found = int(p)
                                break
                            except:
                                continue
                        vat_val = str(found) if found is not None else ""
        except Exception:
            vat_val = ""

        self.vat_display.setText(vat_val)

        # jeśli cennik ma wartość zdrowotna, zaznacz checkbox (ale nie nadpisuj użytkownika)
        zdrow = info.get("zdrowotna")
        if zdrow is not None:
            try:
                self.zdrowotna_checkbox.setChecked(float(zdrow) > 0)
            except Exception:
                pass

    def _validate(self) -> Optional[str]:
        if not self.f_nazwa.text().strip():
            return "Nazwa firmy jest wymagana."
        return None

    def _save(self):
        err = self._validate()
        if err:
            QMessageBox.warning(self, "Walidacja", err)
            return

        payload: Dict[str, Any] = {}

        # wypełniamy pola zgodnie z FIRMA_FIELDS w dao_companies
        payload["nazwa"] = self.f_nazwa.text().strip()
        payload["nazwa_skrocona"] = self.f_nazwa_skr.text().strip()
        payload["nip"] = self.f_nip.text().strip()
        payload["regon"] = self.f_regon.text().strip()
        payload["krs"] = self.f_krs.text().strip()
        payload["pesel"] = self.f_pesel.text().strip()

        # adresy (tylko podstawowe pola, reszta może być dodana analogicznie)
        payload["dzial_miasto"] = self.dzial_miasto.text()
        payload["dzial_kod"] = self.dzial_kod.text()
        payload["dzial_ulica"] = self.dzial_ulica.text()
        payload["dzial_budynek"] = self.dzial_budynek.text()
        payload["dzial_lokal"] = self.dzial_lokal.text()

        payload["kor_miasto"] = self.kor_miasto.text()
        payload["kor_kod"] = self.kor_kod.text()
        payload["kor_ulica"] = self.kor_ulica.text()
        payload["kor_budynek"] = self.kor_budynek.text()
        payload["kor_lokal"] = self.kor_lokal.text()

        payload["zam_miasto"] = self.zam_miasto.text()
        payload["zam_kod"] = self.zam_kod.text()
        payload["zam_ulica"] = self.zam_ulica.text()
        payload["zam_budynek"] = self.zam_budynek.text()
        payload["zam_lokal"] = self.zam_lokal.text()

        # forma/opodatkowanie
        payload["forma_dzialalnosci"] = self.f_forma.currentText()
        payload["forma_opodatkowania"] = self.f_opodatk.currentText()
        payload["analityka_fk"] = self.f_analityka.text()

        # kontakt
        payload["kont_imie"] = self.f_kont_imie.text()
        payload["kont_nazw"] = self.f_kont_nazw.text()
        payload["kont_email"] = self.f_kont_email.text()
        payload["kont_tel"] = self.f_kont_tel.text()

        # daty
        payload["data_rozpoczecia_dg"] = self.f_start.date().toString("yyyy-MM-dd")
        payload["data_rozpoczecia_obslugi"] = self.f_obs.date().toString("yyyy-MM-dd")

        # cennik i limity - kluczowe pola dla generowania faktur
        payload["glowny_cennik"] = self.cennik_combo.currentData()
        payload["limit_dokumentow"] = int(self.f_limit_dokumentow.value())
        payload["limit_kadry"] = int(self.f_limit_kadry.value())
        payload["limit_ryczalt"] = int(self.f_limit_ryczalt.value())

        # zdrowotna - jeśli checkbox zaznaczony, spróbuj pobrać wartość z cennika, jeśli brak -> 0.0
        zdrow_val = 0.0
        if self.zdrowotna_checkbox.isChecked():
            kl = payload.get("glowny_cennik")
            if kl and kl in self._cenniki_map:
                zdrow_val = self._cenniki_map[kl].get("zdrowotna") or 0.0
            else:
                zdrow_val = 0.0
        payload["zdrowotna"] = zdrow_val

        # jeśli edycja, dołącz id
        if self.company_data and self.company_data.get("id"):
            payload["id"] = self.company_data.get("id")

        try:
            save_company(payload)
        except Exception as e:
            QMessageBox.critical(self, "Błąd zapisu", f"Nie udało się zapisać firmy: {e}")
            return

        QMessageBox.information(self, "Zapisano", "Dane firmy zostały zapisane.")
        # zamknij formularz / powiadom rodzica
        if self.parent():
            try:
                # jeśli rodzic implementuje on_saved, wywołaj go
                if hasattr(self.parent(), "on_saved") and callable(getattr(self.parent(), "on_saved")):
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
