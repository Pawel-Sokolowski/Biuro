# app/companies/companies_ui.py
from app.utils.ui_imports import *

from typing import Optional, Dict, Any, List, Tuple

from app.db.dao_companies import get_conn, wybierz_cennik
from app.utils.crypto import login_user, register_user
from app.utils.parsing import parse_int_value, normalize_identifier


class LoginWindow(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Logowanie - System Biura Rachunkowego")
        self.setFixedSize(400, 300)
        self.pracownik_id: Optional[int] = None
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout()
        layout.setContentsMargins(30, 30, 30, 30)
        layout.setSpacing(15)

        # Tytuł
        lbl_title = QLabel("Zaloguj się do systemu")
        lbl_title.setFont(QFont("Arial", 14, QFont.Weight.Bold))
        layout.addWidget(lbl_title)

        # Formularz logowania
        form_layout = QFormLayout()
        form_layout.setSpacing(10)

        self.email_input = QLineEdit()
        self.email_input.setPlaceholderText("Email")
        form_layout.addRow("Email:", self.email_input)

        self.password_input = QLineEdit()
        self.password_input.setPlaceholderText("Hasło")
        self.password_input.setEchoMode(QLineEdit.EchoMode.Password)
        form_layout.addRow("Hasło:", self.password_input)

        layout.addLayout(form_layout)

        # Przyciski
        btn_layout = QHBoxLayout()

        btn_login = QPushButton("Zaloguj")
        btn_login.clicked.connect(self.login)
        btn_login.setStyleSheet(
            "background-color: #3498db; color: white; padding: 8px; "
            "border-radius: 4px; font-weight: bold;"
        )

        btn_register = QPushButton("Zarejestruj")
        btn_register.clicked.connect(self.show_register_dialog)
        btn_register.setStyleSheet(
            "background-color: #27ae60; color: white; padding: 8px; "
            "border-radius: 4px; font-weight: bold;"
        )

        btn_layout.addWidget(btn_login)
        btn_layout.addWidget(btn_register)
        layout.addLayout(btn_layout)

        # Styl okna
        self.setLayout(layout)
        self.setStyleSheet("QDialog { background-color: #f8f9fa; }")

    def login(self):
        email = self.email_input.text().strip()
        password = self.password_input.text()

        if not email or not password:
            QMessageBox.warning(self, "Błąd", "Uzupełnij email i hasło")
            return

        try:
            result = login_user(email, password)
        except Exception as e:
            QMessageBox.critical(self, "Błąd", f"Błąd podczas logowania: {e}")
            return

        user_id = None
        success = False
        message = "Nieprawidłowy email lub hasło"

        # Obsługa różnych formatów zwrotu z login_user
        if isinstance(result, tuple) and len(result) >= 2:
            if isinstance(result[0], bool):
                success = result[0]
                user_id = result[1]
                message = result[2] if len(result) > 2 else message
            else:
                user_id = result[0]
                success = bool(user_id)
                message = result[1] if len(result) > 1 else message
        else:
            user_id = result
            success = bool(user_id)

        if success and user_id:
            self.pracownik_id = user_id
            self.accept()
        else:
            QMessageBox.critical(self, "Błąd", message)
            self.password_input.clear()

    def show_register_dialog(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("Rejestracja nowego pracownika")
        dialog.setFixedSize(400, 300)

        layout = QVBoxLayout()
        form = QFormLayout()

        email_field = QLineEdit()
        imie_field = QLineEdit()
        nazwisko_field = QLineEdit()
        haslo_field = QLineEdit()
        haslo_field.setEchoMode(QLineEdit.EchoMode.Password)

        form.addRow("Email:", email_field)
        form.addRow("Imię:", imie_field)
        form.addRow("Nazwisko:", nazwisko_field)
        form.addRow("Hasło:", haslo_field)

        layout.addLayout(form)

        btn_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btn_box.accepted.connect(dialog.accept)
        btn_box.rejected.connect(dialog.reject)
        layout.addWidget(btn_box)

        dialog.setLayout(layout)

        if dialog.exec() == QDialog.DialogCode.Accepted:
            email = email_field.text().strip()
            imie = imie_field.text().strip()
            nazwisko = nazwisko_field.text().strip()
            haslo = haslo_field.text()

            if not all([email, imie, nazwisko, haslo]):
                QMessageBox.warning(self, "Błąd", "Uzupełnij wszystkie pola")
                return

            try:
                result = register_user(email, haslo, imie=imie, nazwisko=nazwisko, is_admin_flag=False)
            except Exception as e:
                QMessageBox.critical(self, "Błąd", f"Błąd podczas rejestracji: {e}")
                return

            ok = False
            if isinstance(result, tuple):
                ok = bool(result[0])
            else:
                ok = bool(result)

            if ok:
                QMessageBox.information(self, "Sukces", "Rejestracja przebiegła pomyślnie. Zaloguj się teraz.")
            else:
                QMessageBox.critical(self, "Błąd", "Rejestracja nie powiodła się.")


class CompanyDialog(QDialog):
    def __init__(self, pracownik_id: int, company_data: Optional[Dict[str, Any]] = None, parent=None):
        super().__init__(parent)
        self.company_data = company_data or {}
        self.pracownik_id = pracownik_id
        self.employee_items: List[QListWidgetItem] = []
        self.shareholders: List[Dict[str, Any]] = list(self.company_data.get("udzialowcy", []))

        self.setWindowTitle("Dodaj firmę" if not company_data else "Edytuj firmę")
        self.resize(980, 820)
        self.setMinimumSize(900, 720)

        self.inputs: Dict[str, QWidget] = {}
        self.vat_checkbox = None
        self.typ_rozliczenia_combo = None
        self.limit_spin: Optional[QSpinBox] = None

        self._build_ui()
        self._load_employees()
        self._toggle_main_price_mode(bool(self.company_data.get("glowny_cennik_manualny", 0)))

        try:
            self._load_company_notes()
        except Exception:
            pass

    def _build_ui(self):
        self.setStyleSheet("""
            QDialog { background-color: #ffffff; color: #222222; }
            QGroupBox { border: 1px solid #d0d7de; border-radius: 6px; margin-top: 10px; padding: 12px; }
            QGroupBox::title { color: #0b5fff; font-weight: bold; }
            QLabel[sectionHint="true"] { color: #6b7280; font-size: 11px; }
            QLabel[fieldLabel="true"] { color: #374151; font-size: 12px; }
            QLineEdit, QSpinBox, QListWidget, QComboBox {
                background-color: #ffffff; border: 1px solid #cbd5e1;
                border-radius: 4px; padding: 6px 8px; color: #111827;
            }
            QPushButton { padding: 6px 10px; border-radius: 4px; }
            QPushButton.primary { background-color: #0b5fff; color: white; font-weight: 600; }
            QPushButton.positive { background-color: #16a34a; color: white; }
            QPushButton.danger { background-color: #dc2626; color: white; }
        """)

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(12, 12, 12, 12)
        main_layout.setSpacing(10)

        title = QLabel("Edycja danych firmy" if self.company_data else "Dodawanie firmy")
        title.setFont(QFont("Arial", 14, QFont.Weight.Bold))
        main_layout.addWidget(title)

        subtitle = QLabel("Wypełnij pola lub użyj przycisku CEIDG przy NIP, aby pobrać dane z rejestru.")
        subtitle.setWordWrap(True)
        subtitle.setProperty("sectionHint", True)
        main_layout.addWidget(subtitle)

        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        content_widget = QWidget()
        content_layout = QVBoxLayout(content_widget)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(12)

        # IDENTYFIKATORY
        identifiers_group, identifiers_grid = self._create_section("Identyfikatory", "Kluczowe numery rejestrowe firmy.")
        nip_container = QWidget()
        nip_layout = QHBoxLayout(nip_container)
        nip_layout.setContentsMargins(0, 0, 0, 0)
        nip_layout.setSpacing(8)
        nip_input = QLineEdit()
        nip_input.setText(str(self.company_data.get("nip", "")))
        nip_input.setPlaceholderText("NIP (tylko cyfry)")
        self.inputs["nip"] = nip_input
        nip_layout.addWidget(nip_input)

        btn_ceidg = QPushButton("Pobierz z CEIDG")
        btn_ceidg.clicked.connect(self._on_fetch_ceidg_by_nip)
        nip_layout.addWidget(btn_ceidg)
        identifiers_grid.addWidget(self._create_field_container("NIP", nip_container), 0, 0, 1, 1)

        self._add_grid_line_edit(identifiers_grid, 0, 1, "REGON", "regon")
        self._add_grid_line_edit(identifiers_grid, 1, 0, "KRS", "krs")
        self._add_grid_line_edit(identifiers_grid, 1, 1, "PESEL", "pesel")
        content_layout.addWidget(identifiers_group)

        # BASIC
        basic_group, basic_grid = self._create_section("Podstawowe informacje")
        self._add_grid_line_edit(basic_grid, 0, 0, "Nazwa firmy", "nazwa", required=True)
        self._add_grid_line_edit(basic_grid, 0, 1, "Nazwa skrócona", "nazwa_skrocona")
        self._add_grid_line_edit(basic_grid, 1, 0, "Forma działalności", "forma_dzialalnosci")
        self._add_grid_line_edit(basic_grid, 1, 1, "Forma opodatkowania", "forma_opodatkowania")
        content_layout.addWidget(basic_group)

        # CONTACT & ADDRESS
        contact_group, contact_grid = self._create_section("Kontakt i adres")
        self._add_grid_line_edit(contact_grid, 0, 0, "Osoba kontaktowa", "osoba_kontaktowa")
        self._add_grid_line_edit(contact_grid, 0, 1, "Adres email", "adres_email")
        self._add_grid_line_edit(contact_grid, 1, 0, "Nr telefonu", "nr_telefonu")
        self._add_grid_line_edit(contact_grid, 1, 1, "Miasto", "miasto")
        self._add_grid_line_edit(contact_grid, 2, 0, "Kod pocztowy", "kod_pocztowy")
        self._add_grid_line_edit(contact_grid, 2, 1, "Ulica", "ulica")
        content_layout.addWidget(contact_group)

        # DATES
        dates_group, dates_grid = self._create_section("Daty i ZUS")
        date_fields = [
            ("Data rozpoczęcia działalności", "data_rozpoczecia_dg"),
            ("Data rozpoczęcia sprzedaży", "data_rozpoczecia_sprzedazy"),
            ("Koniec ulgi ZUS na start", "data_koniec_zus_start"),
            ("Koniec ZUS preferencyjny", "data_koniec_zus_pref"),
        ]
        for index, (label_text, field_name) in enumerate(date_fields):
            self._add_grid_line_edit(dates_grid, index // 2, index % 2, label_text, field_name, placeholder="YYYY-MM-DD")
        content_layout.addWidget(dates_group)

        # BUSINESS / PRICING
        business_group, business_grid = self._create_section("PKD, cenniki i zatrudnienie")
        self._add_grid_line_edit(business_grid, 0, 0, "Kody PKD", "kody_pkd", placeholder="62.01.Z, 69.20.Z",
                                 col_span=2)

        # ============================
        #  TYP ROZLICZENIA + VAT
        # ============================

        self.typ_rozliczenia_combo = QComboBox()
        self.typ_rozliczenia_combo.addItems(["Ryczałt", "KPIR", "KH"])
        current_typ = str(self.company_data.get("typ_rozliczenia", "Ryczałt"))
        idx = self.typ_rozliczenia_combo.findText(current_typ)
        if idx >= 0:
            self.typ_rozliczenia_combo.setCurrentIndex(idx)
        self.inputs["typ_rozliczenia"] = self.typ_rozliczenia_combo
        business_grid.addWidget(self._create_field_container("Typ rozliczenia", self.typ_rozliczenia_combo), 1, 0, 1, 1)

        from PyQt6.QtWidgets import QCheckBox
        self.vat_checkbox = QCheckBox("Podatnik VAT czynny")
        self.vat_checkbox.setChecked(bool(self.company_data.get("vat_czynny", 0)))
        business_grid.addWidget(self._create_field_container("VAT", self.vat_checkbox), 1, 1, 1, 1)

        # ============================
        #  LIMIT DOKUMENTÓW
        # ============================

        self.limit_spin = self._create_spin_input("limit_dokumentow")
        limit_container = self._create_field_container("Limit dokumentów", self.limit_spin)
        business_grid.addWidget(limit_container, 2, 0, 1, 1)

        # ============================
        #  AUTOMATYCZNY DOBÓR CENNIKA
        # ============================

        self.cennik_combo = QComboBox()
        self.cennik_combo.setEditable(False)
        self.inputs["glowny_cennik"] = self.cennik_combo
        business_grid.addWidget(self._create_field_container("Główny cennik (automatyczny)", self.cennik_combo), 3, 0,
                                1, 2)

        # ============================
        #  RĘCZNE NADPISANIE CENNIKA
        # ============================

        self.main_price_manual_checkbox = QCheckBox("Ręcznie nadpisz główny cennik")
        self.main_price_manual_checkbox.setChecked(bool(self.company_data.get("glowny_cennik_manualny", 0)))
        business_grid.addWidget(self.main_price_manual_checkbox, 4, 0, 1, 2)

        self.manual_price_edit = QLineEdit()
        self.manual_price_edit.setPlaceholderText("Cena netto (ręczna)")
        self.manual_price_edit.setEnabled(self.main_price_manual_checkbox.isChecked())
        business_grid.addWidget(self._create_field_container("Cena ręczna", self.manual_price_edit), 5, 0, 1, 2)

        def toggle_manual_price():
            manual = self.main_price_manual_checkbox.isChecked()
            self.manual_price_edit.setEnabled(manual)
            self.cennik_combo.setEnabled(not manual)

        self.main_price_manual_checkbox.toggled.connect(toggle_manual_price)

        # ============================
        #  PRACOWNICY / ZATRUDNIENIE
        # ============================

        self._add_grid_spin_box(business_grid, 6, 0, "Pracownicy UoP", "uop")
        self._add_grid_spin_box(business_grid, 6, 1, "Umowy zlecenia (UZ)", "uz")
        self._add_grid_spin_box(business_grid, 7, 0, "Umowy o dzieło (UoD)", "uod")

        content_layout.addWidget(business_group)

        # ============================
        #  PODPIĘCIE AUTOMATYCZNEGO DOBIERANIA
        # ============================

        self.typ_rozliczenia_combo.currentTextChanged.connect(self._load_matching_cenniki)
        self.vat_checkbox.stateChanged.connect(self._load_matching_cenniki)
        self.limit_spin.valueChanged.connect(self._load_matching_cenniki)

        # ASSIGNED EMPLOYEES
        employee_group, employee_grid = self._create_section("Przypisani pracownicy", "Wybierz osoby odpowiedzialne za obsługę tej firmy.")
        self.employee_list = QListWidget()
        self.employee_list.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        self.employee_list.setMinimumHeight(120)
        employee_grid.addWidget(self.employee_list, 0, 0, 1, 2)
        content_layout.addWidget(employee_group)

        # SHAREHOLDERS
        shareholders_group, shareholders_grid = self._create_section("Udziałowcy", "Dodaj udziałowców i procent udziałów.")
        self.shareholders_list = QListWidget()
        self.shareholders_list.setMinimumHeight(120)
        shareholders_grid.addWidget(self.shareholders_list, 0, 0, 1, 2)

        sh_form = QWidget()
        sh_layout = QHBoxLayout(sh_form)
        sh_layout.setContentsMargins(0, 0, 0, 0)
        sh_layout.setSpacing(8)
        self.sh_name_input = QLineEdit()
        self.sh_name_input.setPlaceholderText("Imię i nazwisko")
        self.sh_share_input = QLineEdit()
        self.sh_share_input.setPlaceholderText("Udział %")
        btn_add_sh = QPushButton("Dodaj")
        btn_add_sh.clicked.connect(self._add_shareholder_from_form)
        btn_remove_sh = QPushButton("Usuń zaznaczone")
        btn_remove_sh.clicked.connect(self._remove_selected_shareholder)
        sh_layout.addWidget(self.sh_name_input)
        sh_layout.addWidget(self.sh_share_input)
        sh_layout.addWidget(btn_add_sh)
        sh_layout.addWidget(btn_remove_sh)
        shareholders_grid.addWidget(sh_form, 1, 0, 1, 2)
        content_layout.addWidget(shareholders_group)

        # NOTES
        notes_group, notes_grid = self._create_section("Ostatnie notatki", "Ostatnie notatki dotyczące firmy")
        self.notes_list_widget = QListWidget()
        self.notes_list_widget.setMinimumHeight(120)
        notes_grid.addWidget(self.notes_list_widget, 0, 0, 1, 2)
        content_layout.addWidget(notes_group)

        content_layout.addStretch(1)
        scroll_area.setWidget(content_widget)
        main_layout.addWidget(scroll_area, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.validate_and_accept)
        buttons.rejected.connect(self.reject)
        main_layout.addWidget(buttons)

        self._refresh_shareholders_list()

    def _load_matching_cenniki(self):
        """
        Automatyczne dobieranie cennika po typie rozliczenia, VAT i limicie dokumentów.
        Zgodne z NOWYM formatem tabeli cenniki:
            klucz
            cena_netto_ksiegowosc
            cena_za_dokument_ponad_limit
            zdrowotna
            netto
            brutto
        """

        typ = self.typ_rozliczenia_combo.currentText()  # Ryczałt / KPIR / KH
        vat = "T" if self.vat_checkbox.isChecked() else "N"
        limit = self.limit_spin.value()

        conn = get_conn()
        cur = conn.cursor()

        # pobieramy tylko pasujące cenniki
        cur.execute("""
            SELECT klucz, cena_netto_ksiegowosc, cena_za_dokument_ponad_limit,
                   zdrowotna, netto, brutto
            FROM cenniki
            WHERE klucz LIKE ? AND klucz LIKE ?
            ORDER BY klucz ASC
        """, (f"{typ}%", f"%_{vat}_%"))

        rows = cur.fetchall()
        conn.close()

        self.cennik_combo.clear()

        for r in rows:
            klucz = r["klucz"]
            parts = klucz.split("_")
            limit_klucz = int(parts[2]) if len(parts) > 2 else 0

            # filtr po limicie
            if limit_klucz != limit:
                continue

            label = (
                f"{typ} | VAT {vat} | Limit {limit_klucz} | "
                f"Księgowość netto: {r['cena_netto_ksiegowosc']} zł | "
                f"Kadry: {r['zdrowotna']} zł"
            )

            self.cennik_combo.addItem(label, klucz)

        # jeśli jest dokładne dopasowanie → ustaw automatycznie
        if self.cennik_combo.count() > 0:
            self.cennik_combo.setCurrentIndex(0)

    def _on_fetch_ceidg_by_nip(self):
        nip_widget = self.inputs.get("nip")
        nip = nip_widget.text().strip() if nip_widget else ""
        if not nip:
            QMessageBox.warning(self, "Brak NIP", "Wprowadź NIP przed pobraniem z CEIDG.")
            return

        try:
            from app.companies.ceidg_api import search_ceidg_by_nip
            from app.config import CEIDG_API_KEY, CEIDG_BASE_URL
        except Exception:
            QMessageBox.information(self, "Brak integracji", "Funkcja CEIDG nie jest dostępna w tej instalacji.")
            return

        try:
            from PyQt6.QtWidgets import QApplication
            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        except Exception:
            pass

        try:
            result = search_ceidg_by_nip(nip, api_key=CEIDG_API_KEY, base_url=CEIDG_BASE_URL, limit=25)
        except Exception as e:
            try:
                from PyQt6.QtWidgets import QApplication
                QApplication.restoreOverrideCursor()
            except Exception:
                pass
            QMessageBox.critical(self, "Błąd CEIDG", f"Nie udało się pobrać danych: {e}")
            return
        finally:
            try:
                from PyQt6.QtWidgets import QApplication
                QApplication.restoreOverrideCursor()
            except Exception:
                pass

        if not result:
            QMessageBox.information(self, "Brak danych", "Nie znaleziono firmy o podanym NIP w CEIDG.")
            return

        if "raw" in result and isinstance(result["raw"], dict):
            raw = result["raw"]
            wl = raw.get("wlasciciel") or raw.get("wlascicielDzialalnosci") or {}
            if wl:
                imie = wl.get("imie") or wl.get("firstName") or ""
                nazwisko = wl.get("nazwisko") or wl.get("lastName") or ""
                if imie or nazwisko:
                    result.setdefault("wlasciciel_imie_nazwisko", f"{imie} {nazwisko}".strip())

        mapping = {
            "nazwa": ["nazwa", "company_name", "name"],
            "nazwa_skrocona": ["nazwa_skrocona", "nazwaSkrócona", "shortName"],
            "osoba_kontaktowa": ["wlasciciel_imie_nazwisko", "osoba_kontaktowa", "kontakt_imie_nazwisko"],
            "adres_email": ["email", "adres_email"],
            "nr_telefonu": ["telefon", "phone"],
            "miasto": ["miasto", "city"],
            "kod_pocztowy": ["kod_pocztowy", "kodPocztowy", "postal_code", "kod"],
            "ulica": ["adres_ulica", "ulica", "street"],
            "regon": ["regon"],
            "kody_pkd": ["pkd", "kody_pkd", "kodyPKD", "kody"],
            "data_rozpoczecia_dg": ["data_rozpoczecia", "data_rozpoczecia_dg"]
        }

        def get_first_present(d: dict, keys: list):
            for k in keys:
                if k in d and d[k] not in (None, "", [], {}):
                    return d[k]
            return None

        for field, keys in mapping.items():
            widget = self.inputs.get(field)
            if not widget:
                continue

            existing = ""
            if isinstance(widget, QLineEdit):
                existing = widget.text().strip()
            elif isinstance(widget, QSpinBox):
                existing = str(widget.value())
            else:
                existing = ""

            if existing:
                continue

            val = get_first_present(result, keys)
            if val is None:
                continue

            if field == "kody_pkd":
                if isinstance(val, list):
                    val_str = ", ".join(map(str, val))
                else:
                    val_str = str(val)
                if isinstance(widget, QLineEdit):
                    widget.setText(val_str)
                continue

            if field == "data_rozpoczecia_dg":
                if isinstance(val, str) and val:
                    if isinstance(widget, QLineEdit):
                        widget.setText(val)
                continue

            if isinstance(widget, QLineEdit):
                widget.setText(str(val))

        QMessageBox.information(self, "Pobrano z CEIDG", "Dane zostały pobrane i wstawione do formularza (tam gdzie pola były puste).")

    def _add_shareholder_from_form(self):
        name = self.sh_name_input.text().strip()
        share = self.sh_share_input.text().strip()
        if not name or not share:
            QMessageBox.warning(self, "Błąd", "Wprowadź imię i nazwisko oraz udział procentowy.")
            return
        try:
            share_val = float(str(share).replace(",", "."))
        except Exception:
            QMessageBox.warning(self, "Błąd", "Udział powinien być liczbą (np. 25 lub 12.5).")
            return
        self.shareholders.append({"name": name, "share": share_val})
        self.sh_name_input.clear()
        self.sh_share_input.clear()
        self._refresh_shareholders_list()

    def _refresh_shareholders_list(self):
        self.shareholders_list.clear()
        for sh in self.shareholders:
            self.shareholders_list.addItem(f"{sh['name']} — {sh['share']}%")

    def _remove_selected_shareholder(self):
        sel = self.shareholders_list.currentRow()
        if sel < 0:
            QMessageBox.information(self, "Brak zaznaczenia", "Zaznacz udziałowca do usunięcia.")
            return
        del self.shareholders[sel]
        self._refresh_shareholders_list()

    def _create_section(self, title: str, description: str = "") -> Tuple[QGroupBox, QGridLayout]:
        section = QGroupBox(title)
        layout = QVBoxLayout(section)
        layout.setSpacing(8)
        if description:
            hint = QLabel(description)
            hint.setProperty("sectionHint", True)
            hint.setWordWrap(True)
            layout.addWidget(hint)
        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(8)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        layout.addLayout(grid)
        return section, grid

    def _create_text_input(self, field_name: str, placeholder: str = "", required: bool = False) -> QLineEdit:
        input_widget = QLineEdit()
        input_widget.setText(str(self.company_data.get(field_name, "")))
        if placeholder:
            input_widget.setPlaceholderText(placeholder)
        if required:
            input_widget.setProperty("required", True)
        self.inputs[field_name] = input_widget
        return input_widget

    def _create_spin_input(self, field_name: str) -> QSpinBox:
        spin_box = QSpinBox()
        spin_box.setMaximum(999999)
        spin_box.setValue(parse_int_value(self.company_data.get(field_name), 0))
        self.inputs[field_name] = spin_box
        return spin_box

    def _create_field_container(self, label_text: str, widget: QWidget) -> QWidget:
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        label = QLabel(label_text)
        label.setProperty("fieldLabel", True)
        layout.addWidget(label)
        layout.addWidget(widget)
        return container

    def _add_grid_line_edit(self, grid_layout: QGridLayout, row: int, column: int, label_text: str,
                            field_name: str, placeholder: str = "", required: bool = False, col_span: int = 1):
        input_widget = self._create_text_input(field_name, placeholder=placeholder, required=required)
        field_container = self._create_field_container(label_text, input_widget)
        grid_layout.addWidget(field_container, row, column, 1, col_span)

    def _add_grid_spin_box(self, grid_layout: QGridLayout, row: int, column: int, label_text: str,
                           field_name: str, col_span: int = 1):
        spin_box = self._create_spin_input(field_name)
        field_container = self._create_field_container(label_text, spin_box)
        grid_layout.addWidget(field_container, row, column, 1, col_span)

    def _toggle_main_price_mode(self, manual_enabled: bool):
        widget = self.inputs.get("glowny_cennik")
        if widget and hasattr(widget, "setReadOnly"):
            widget.setReadOnly(not manual_enabled)
            if not manual_enabled and isinstance(widget, QLineEdit) and not widget.text().strip():
                widget.setPlaceholderText("Zostanie dobrany po przekroczeniu limitu dokumentów")
            else:
                widget.setPlaceholderText("")

    def _on_limit_changed(self, value: int):
        if not self.main_price_manual_checkbox or self.main_price_manual_checkbox.isChecked():
            return
        typ = self.typ_rozliczenia_combo.currentText() if self.typ_rozliczenia_combo else "Ryczałt"
        vat_czynny = bool(self.vat_checkbox.isChecked()) if self.vat_checkbox else False

        conn = get_conn()
        cur = conn.cursor()
        try:
            klucz = wybierz_cennik(cur, typ, vat_czynny, value)
            if klucz:
                widget = self.inputs.get("glowny_cennik")
                if isinstance(widget, QLineEdit):
                    widget.setText(klucz)
        finally:
            conn.close()

    def _load_employees(self):
        selected_ids = set(self.company_data.get("assigned_employee_ids", []))
        if not get_conn:
            return
        conn = get_conn()
        cur = conn.cursor()
        try:
            cur.execute("SELECT id, imie, nazwisko, email FROM pracownicy WHERE aktywny = 1 ORDER BY imie, nazwisko")
            for employee_id, first_name, last_name, email_address in cur.fetchall():
                item = QListWidgetItem(f"{first_name} {last_name} ({email_address})")
                item.setData(Qt.ItemDataRole.UserRole, employee_id)
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(Qt.CheckState.Checked if employee_id in selected_ids else Qt.CheckState.Unchecked)
                self.employee_list.addItem(item)
        finally:
            conn.close()

    def validate_and_accept(self):
        if not self.inputs.get("nazwa") or not self.inputs["nazwa"].text().strip():
            QMessageBox.warning(self, "Błąd", "Nazwa firmy jest wymagana.")
            return

        nip_text = self.inputs.get("nip").text().strip() if self.inputs.get("nip") else ""
        if nip_text:
            normalized_nip = normalize_identifier(nip_text, {10})
            if len(normalized_nip) != 10:
                QMessageBox.warning(self, "Błąd", "NIP powinien zawierać 10 cyfr.")
                return

        self.company_data["udzialowcy"] = self.shareholders
        self.accept()

    def get_payload(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {}
        for field_name, widget in self.inputs.items():
            if isinstance(widget, QSpinBox):
                payload[field_name] = widget.value()
            elif isinstance(widget, QComboBox):
                payload[field_name] = widget.currentText().strip()
            else:
                payload[field_name] = widget.text().strip()
        try:
            payload["glowny_cennik_manualny"] = 1 if getattr(self, "main_price_manual_checkbox", None) and getattr(self.main_price_manual_checkbox, "isChecked", lambda: False)() else 0
        except Exception:
            payload["glowny_cennik_manualny"] = 0
        payload["vat_czynny"] = 1 if self.vat_checkbox and self.vat_checkbox.isChecked() else 0
        payload["assigned_employee_ids"] = [
            self.employee_list.item(index).data(Qt.ItemDataRole.UserRole)
            for index in range(self.employee_list.count())
            if self.employee_list.item(index).checkState() == Qt.CheckState.Checked
        ]
        payload["udzialowcy"] = self.shareholders
        return payload

    def _load_company_notes(self):
        fid = self.company_data.get("id")
        self.notes_list_widget.clear()
        if not fid:
            return
        if not get_conn:
            return
        conn = get_conn()
        cur = conn.cursor()
        try:
            cur.execute("SELECT tresc, data_utworzenia FROM company_notes WHERE firma_id = ? ORDER BY data_utworzenia DESC LIMIT 5", (fid,))
            for tresc, dt in cur.fetchall():
                snippet = (tresc[:140] + "…") if len(tresc) > 140 else tresc
                self.notes_list_widget.addItem(f"[{dt}] {snippet}")
        finally:
            conn.close()




