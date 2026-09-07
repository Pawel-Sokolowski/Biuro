# app/companies/company_form.py

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QGroupBox, QLabel,
    QLineEdit, QPushButton, QScrollArea, QComboBox, QCheckBox, QDateEdit
)
from PyQt6.QtCore import Qt, QDate
from PyQt6.QtGui import QFont

from app.companies import companies_logic
from app.db.dao_companies import save_company


class CompanyForm(QWidget):
    def __init__(self, pracownik_id: int, company_data=None, parent=None):
        super().__init__(parent)

        self.pracownik_id = pracownik_id
        self.company_data = company_data or {}
        self.on_saved = None

        self.inputs = {}
        self._ceidg_in_progress = False
        self._last_ceidg_raw = None

        self._build_ui()

    # ============================================================
    #  BUDOWA UI
    # ============================================================

    def _build_ui(self):
        layout = QVBoxLayout(self)

        title = QLabel("Edytuj firmę" if self.company_data else "Dodaj firmę")
        title.setFont(QFont("Arial", 16, QFont.Weight.Bold))
        layout.addWidget(title)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        content_layout = QVBoxLayout(content)

        # ------------------------------------------------------------
        #  IDENTYFIKATORY
        # ------------------------------------------------------------
        group, grid = self._section("Identyfikatory")

        nip_input = QLineEdit()
        nip_input.setText(self.company_data.get("nip", ""))
        self.inputs["nip"] = nip_input

        btn_ceidg = QPushButton("Pobierz z CEIDG")
        btn_ceidg.clicked.connect(self._ceidg_fetch)

        nip_row = QWidget()
        nip_row_l = QHBoxLayout(nip_row)
        nip_row_l.addWidget(self._field("NIP", nip_input))
        nip_row_l.addWidget(btn_ceidg)

        grid.addWidget(nip_row, 0, 0, 1, 3)

        self._add_line_to_grid(grid, 1, 0, "Nazwa", "nazwa")
        self._add_line_to_grid(grid, 1, 1, "Nazwa skrócona", "nazwa_skrocona")
        self._add_line_to_grid(grid, 2, 0, "REGON", "regon")
        self._add_line_to_grid(grid, 2, 1, "KRS", "krs")
        self._add_line_to_grid(grid, 2, 2, "PESEL", "pesel")

        content_layout.addWidget(group)

        # ------------------------------------------------------------
        #  ADRES SIEDZIBY
        # ------------------------------------------------------------
        group, grid = self._section("Adres siedziby")

        self._add_line_to_grid(grid, 0, 0, "Miasto", "miasto")
        self._add_line_to_grid(grid, 0, 1, "Kod pocztowy", "kod_pocztowy")
        self._add_line_to_grid(grid, 1, 0, "Ulica", "ulica")
        self._add_line_to_grid(grid, 1, 1, "Budynek / lokal", "adres_budynek")

        content_layout.addWidget(group)

        # ------------------------------------------------------------
        #  ADRESY DODATKOWE
        # ------------------------------------------------------------
        group, grid = self._section("Adresy dodatkowe")

        self._add_line_to_grid(grid, 0, 0, "Zam. miasto", "adres_zam_miasto")
        self._add_line_to_grid(grid, 0, 1, "Zam. kod", "adres_zam_kod_pocztowy")
        self._add_line_to_grid(grid, 1, 0, "Zam. ulica", "adres_zam_ulica")
        self._add_line_to_grid(grid, 1, 1, "Koresp. miasto", "adres_koresp_miasto")
        self._add_line_to_grid(grid, 2, 0, "Koresp. kod", "adres_koresp_kod_pocztowy")
        self._add_line_to_grid(grid, 2, 1, "Koresp. ulica", "adres_koresp_ulica")

        content_layout.addWidget(group)

        # ------------------------------------------------------------
        #  KONTAKT
        # ------------------------------------------------------------
        group, grid = self._section("Kontakt")

        self._add_line_to_grid(grid, 0, 0, "Email", "adres_email")
        self._add_line_to_grid(grid, 0, 1, "Telefon", "nr_telefonu")
        self._add_line_to_grid(grid, 1, 0, "Osoba kontaktowa", "osoba_kontaktowa")

        content_layout.addWidget(group)

        # ------------------------------------------------------------
        #  DANE CEIDG
        # ------------------------------------------------------------
        group, grid = self._section("Dane CEIDG")

        self._add_line_to_grid(grid, 0, 0, "Forma działalności", "forma_dzialalnosci")

        typ_combo = QComboBox()
        typ_combo.addItems(["Ryczałt", "KPIR", "KH"])
        typ_combo.setCurrentText(self.company_data.get("typ_rozliczenia", "Ryczałt"))
        self.inputs["typ_rozliczenia"] = typ_combo
        grid.addWidget(self._field("Rodzaj rozliczenia", typ_combo), 0, 1)

        self._add_line_to_grid(grid, 1, 0, "Kody PKD", "kody_pkd")

        vat_cb = QCheckBox("VAT czynny")
        vat_cb.setChecked(bool(int(self.company_data.get("vat_czynny", 0))))
        self.inputs["vat_czynny"] = vat_cb
        grid.addWidget(self._field("Status VAT", vat_cb), 1, 1)

        date_start = QDateEdit()
        date_start.setCalendarPopup(True)
        date_start.setDisplayFormat("yyyy-MM-dd")
        ds = self.company_data.get("data_rozpoczecia_dg")
        if ds:
            date_start.setDate(QDate.fromString(ds[:10], "yyyy-MM-dd"))
        self.inputs["data_rozpoczecia_dg"] = date_start
        grid.addWidget(self._field("Data rozpoczęcia", date_start), 2, 0)

        content_layout.addWidget(group)

        group, grid = self._section("Kadry")

        self._add_line_to_grid(grid, 0, 0, "Liczba UoP", "uop")
        self._add_line_to_grid(grid, 0, 1, "Liczba UZ", "uz")
        self._add_line_to_grid(grid, 1, 0, "Liczba UoD", "uod")

        kadry_options = companies_logic.get_kadry_options()
        self.kadry_items = []

        kadry_widget = QWidget()
        kadry_layout = QVBoxLayout(kadry_widget)

        for opt in kadry_options:
            cb = QCheckBox(f"{opt['nazwa']} — {opt['cena']} zł")
            self.kadry_items.append({"checkbox": cb, "klucz": opt["klucz"]})
            kadry_layout.addWidget(cb)

        selected = (self.company_data.get("cennik_kadrowy") or "").split(",")
        for item in self.kadry_items:
            if item["klucz"] in selected:
                item["checkbox"].setChecked(True)

        grid.addWidget(self._field("Cennik kadrowy", kadry_widget), 2, 0, 1, 2)

        self.cena_kadrowa_edit = QLineEdit()
        self.cena_kadrowa_edit.setReadOnly(True)
        self.cena_kadrowa_edit.setText(self.company_data.get("cena_kadrowa", "0.00"))
        grid.addWidget(self._field("Cena kadrowa (wyliczona)", self.cena_kadrowa_edit), 3, 0)

        self.inputs["uop"].textChanged.connect(self._recalculate_kadry)
        self.inputs["uz"].textChanged.connect(self._recalculate_kadry)
        self.inputs["uod"].textChanged.connect(self._recalculate_kadry)
        for item in self.kadry_items:
            item["checkbox"].stateChanged.connect(self._recalculate_kadry)

        content_layout.addWidget(group)

        group, grid = self._section("Faktura")

        self._add_line_to_grid(grid, 0, 0, "Limit dokumentów", "limit_dokumentow")

        ksieg_options = companies_logic.get_ksiegowosc_options()

        self.cennik_ks_combo = QComboBox()
        for opt in ksieg_options:
            self.cennik_ks_combo.addItem(
                f"{opt['klucz']} — {opt['netto']} zł netto",
                opt["klucz"]
            )

        if self.company_data.get("cennik_ksiegowy"):
            idx = self.cennik_ks_combo.findData(self.company_data["cennik_ksiegowy"])
            if idx >= 0:
                self.cennik_ks_combo.setCurrentIndex(idx)

        self.inputs["cennik_ksiegowy"] = self.cennik_ks_combo
        grid.addWidget(self._field("Cennik usług księgowych", self.cennik_ks_combo), 0, 1)

        self.cena_ponad_limit_edit = QLineEdit()
        self.cena_ponad_limit_edit.setReadOnly(True)
        grid.addWidget(self._field("Cena ponad limit", self.cena_ponad_limit_edit), 1, 0)

        self.cennik_ks_combo.currentIndexChanged.connect(self._update_ksiegowosc)

        content_layout.addWidget(group)

        scroll.setWidget(content)
        layout.addWidget(scroll)

        # przyciski
        btns = QHBoxLayout()
        btn_save = QPushButton("Zapisz")
        btn_cancel = QPushButton("Anuluj")
        btn_save.clicked.connect(self._save)
        btn_cancel.clicked.connect(self._cancel)
        btns.addWidget(btn_save)
        btns.addWidget(btn_cancel)
        layout.addLayout(btns)

    def _ceidg_fetch(self):
        companies_logic.ceidg_fetch(self)

    def _update_ksiegowosc(self):
        klucz = self.cennik_ks_combo.currentData()
        if not klucz:
            self.cena_ponad_limit_edit.setText("0.00")
            return

        opt = companies_logic.get_ksiegowosc_by_key(klucz)
        if not opt:
            self.cena_ponad_limit_edit.setText("0.00")
            return

        self.cena_ponad_limit_edit.setText(str(opt["ponad_limit"]))

    def _recalculate_kadry(self):
        try:
            uop = int(self.inputs["uop"].text() or 0)
            uz = int(self.inputs["uz"].text() or 0)
            uod = int(self.inputs["uod"].text() or 0)
        except:
            return

        selected_keys = [
            item["klucz"]
            for item in self.kadry_items
            if item["checkbox"].isChecked()
        ]

        total = companies_logic.calculate_kadry_price(uop, uz, uod, selected_keys)
        self.cena_kadrowa_edit.setText(f"{total:.2f}")

    def _save(self):
        payload = {}

        for key, widget in self.inputs.items():
            if isinstance(widget, QLineEdit):
                payload[key] = widget.text().strip()
            elif isinstance(widget, QComboBox):
                payload[key] = widget.currentData() or widget.currentText()
            elif isinstance(widget, QCheckBox):
                payload[key] = 1 if widget.isChecked() else 0
            elif isinstance(widget, QDateEdit):
                payload[key] = widget.date().toString("yyyy-MM-dd")

        selected_keys = [
            item["klucz"]
            for item in self.kadry_items
            if item["checkbox"].isChecked()
        ]
        payload["cennik_kadrowy"] = ",".join(selected_keys)

        payload["cena_kadrowa"] = self.cena_kadrowa_edit.text().strip()

        if self.company_data.get("id"):
            payload["id"] = self.company_data["id"]

        save_company(payload)

        if self.on_saved:
            self.on_saved()

    def _cancel(self):
        if self.on_saved:
            self.on_saved()

    def _section(self, title: str):
        group = QGroupBox(title)
        grid = QGridLayout(group)
        return group, grid

    def _field(self, label: str, widget: QWidget):
        w = QWidget()
        l = QVBoxLayout(w)
        l.setContentsMargins(0, 0, 0, 0)
        l.addWidget(QLabel(label))
        l.addWidget(widget)
        return w

    def _add_line_to_grid(self, grid, row, col, label, key):
        w = QLineEdit()
        w.setText(str(self.company_data.get(key, "")))
        self.inputs[key] = w
        grid.addWidget(self._field(label, w), row, col)
