# app/companies/companies_ui.py

from typing import Optional, Dict, Any, List, Tuple
import datetime
import sqlite3

from PyQt6.QtWidgets import (
    QDialog, QDialogButtonBox, QFormLayout, QLineEdit, QLabel, QVBoxLayout,
    QMessageBox, QMainWindow, QWidget, QHBoxLayout, QListWidget, QStackedWidget,
    QPushButton, QTableWidget, QTableWidgetItem, QHeaderView, QComboBox,
    QSpinBox, QSplitter, QGroupBox, QScrollArea,
    QListWidgetItem, QGridLayout, QAbstractItemView, QCheckBox, QTabWidget
)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QFont

from app.companies.constants import COMPANY_STAGE_OPTIONS
# Lokalny DAO i serwisy
from app.db.dao_companies import get_conn
from app.utils.crypto import login_user, register_user, get_employee_info, is_admin
from app.utils.parsing import parse_int_value, normalize_identifier
from app.companies.companies_model import apply_default_main_pricing, save_company, get_company_assigned_employee_ids
from app.email.email_ui import EmailUIMixin
from app.chat.chat import ChatPage
from app.db.dao_companies import wybierz_cennik, pobierz_cene_za_dokument
from app.utils.saldeo_exporter import export_saldeo_csv

# -------------------------
# LoginWindow
# -------------------------
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
        self._add_grid_line_edit(business_grid, 0, 0, "Kody PKD", "kody_pkd", placeholder="62.01.Z, 69.20.Z", col_span=2)

        # Typ rozliczenia + VAT
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

        self._add_grid_line_edit(business_grid, 2, 0, "Cennik usług księgowych", "cennik_ksiegowy")
        self._add_grid_line_edit(business_grid, 2, 1, "Cennik usług kadrowych", "cennik_kadrowy")
        self._add_grid_line_edit(business_grid, 3, 0, "Cennik czasowy", "cennik_czasowy")
        self._add_grid_line_edit(business_grid, 3, 1, "Główny cennik (klucz)", "glowny_cennik")

        try:
            from PyQt6.QtWidgets import QCheckBox
            self.main_price_manual_checkbox = QCheckBox("Ręcznie nadpisz główny cennik")
            self.main_price_manual_checkbox.setChecked(bool(self.company_data.get("glowny_cennik_manualny", 0)))
            self.main_price_manual_checkbox.toggled.connect(self._toggle_main_price_mode)
            business_grid.addWidget(self.main_price_manual_checkbox, 4, 0, 1, 2)
        except Exception:
            btn = QPushButton("Ręcznie nadpisz główny cennik")
            business_grid.addWidget(btn, 4, 0, 1, 2)

        self._add_grid_spin_box(business_grid, 5, 0, "Pracownicy UoP", "uop")
        self._add_grid_spin_box(business_grid, 5, 1, "Umowy zlecenia (UZ)", "uz")
        self._add_grid_spin_box(business_grid, 6, 0, "Umowy o dzieło (UoD)", "uod")

        # Limit dokumentów z automatycznym doborem cennika
        self.limit_spin = self._create_spin_input("limit_dokumentow")
        limit_container = self._create_field_container("Limit dokumentów", self.limit_spin)
        business_grid.addWidget(limit_container, 6, 1, 1, 1)
        self.limit_spin.valueChanged.connect(self._on_limit_changed)

        content_layout.addWidget(business_group)

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



BaseClasses = (QMainWindow, EmailUIMixin) if EmailUIMixin else (QMainWindow,)

#class office manager app
class OfficeManagerApp(*BaseClasses):  # type: ignore
    def __init__(self, pracownik_id: Optional[int] = None):
        super().__init__()
        self.employees_table = None
        self.pracownik_id = pracownik_id
        self.setWindowTitle("System Zarządzania Biurem Rachunkowego v1.1")
        self.resize(1250, 750)

        # Thread pool for background tasks
        from PyQt6.QtCore import QThreadPool
        self.thread_pool = QThreadPool.globalInstance()

        # Stoper
        self.aktywna_firma_stoper: Optional[int] = None
        self.aktywna_firma_stoper_nazwa: str = ""
        self.czas_start: Optional[datetime.datetime] = None
        self.aktualny_miesiac: str = datetime.datetime.now().strftime("%Y-%m")
        self.status_autosave_timers = {}
        self.status_widgets = {}

        # UI build
        self._build_ui()

        # Global timer for updating stoper GUI
        self.global_timer = QTimer(self)
        self.global_timer.timeout.connect(self.aktualizuj_stoper_gui)
        self.global_timer.start(1000)

    # -------------------------
    # UI construction
    # -------------------------
    def _build_ui(self):
        main_widget = QWidget()
        self.setCentralWidget(main_widget)
        main_layout = QHBoxLayout(main_widget)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # Sidebar
        self.sidebar = QListWidget()
        self.sidebar.setFixedWidth(220)
        self.sidebar.setStyleSheet("""
            QListWidget { background-color: #2c3e50; color: #ecf0f1; font-size: 14px; border: none; }
            QListWidget::item { padding: 15px 10px; border-bottom: 1px solid #34495e; }
            QListWidget::item:selected { background-color: #3498db; color: white; font-weight: bold; }
            QListWidget::item:hover { background-color: #34495e; }
        """)
        # order: Monthly view (with tabs Księgowość/Kadry), Companies, Employees, Email, Chat, Export
        self.sidebar.addItems([
            "Widok Miesięczny",
            "Klienci",
            "Pracownicy",
            "Email",
            "Chat",
            "Eksport Saldeo",
            "Raporty",
            "Ustawienia"
        ])
        self.sidebar.currentRowChanged.connect(self.zmien_zakladke)
        main_layout.addWidget(self.sidebar)

        # Content
        content_widget = QWidget()
        content_layout = QVBoxLayout(content_widget)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)

        # Header
        global_header = QWidget()
        global_header.setStyleSheet("background-color: #f5f7fa; border-bottom: 1px solid #dfe6e9;")
        global_header_layout = QHBoxLayout(global_header)
        global_header_layout.setContentsMargins(16, 10, 16, 10)

        emp_info = get_employee_info(self.pracownik_id) if self.pracownik_id else None
        if isinstance(emp_info, dict):
            employee_name = f"{emp_info.get('imie','')} {emp_info.get('nazwisko','')}".strip() or "Nieznany"
        else:
            try:
                employee_name = f"{emp_info[2]} {emp_info[3]}" if emp_info else "Nieznany"
            except Exception:
                employee_name = "Nieznany"

        self.lbl_global_user = QLabel(f"<b>Zalogowany:</b> {employee_name}")
        self.lbl_global_user.setStyleSheet("color: #2c3e50;")
        global_header_layout.addWidget(self.lbl_global_user)

        self.lbl_global_timer_status = QLabel("Timer: brak aktywnej firmy")
        self.lbl_global_timer_status.setStyleSheet(
            "color: #2c3e50; font-weight: bold; background-color: #ecf0f1; padding: 6px 10px; border-radius: 4px;"
        )
        global_header_layout.addWidget(self.lbl_global_timer_status, alignment=Qt.AlignmentFlag.AlignRight)
        content_layout.addWidget(global_header)

        # Pages stack
        self.pages = QStackedWidget()
        content_layout.addWidget(self.pages)
        main_layout.addWidget(content_widget)

        # Create pages
        self.create_monthly_view_page()
        self.create_companies_page()
        self.create_employees_page()
        self.create_email_page()
        self.create_chat_page()
        self.create_saldeo_page()
        self.create_raporty_page()
        self.create_settings_page()

        # default to first page
        self.pages.setCurrentIndex(0)
        self.sidebar.setCurrentRow(0)
        print("=== STACK DEBUG ===")
        print("Sidebar count:", self.sidebar.count())
        print("Pages count:", self.pages.count())

        for i in range(self.pages.count()):
            w = self.pages.widget(i)
            print(i, type(w).__name__)

    # -------------------------
    # Monthly view (with month selector, employee filter and tabs)
    # -------------------------
    def create_monthly_view_page(self):
        page_container = QWidget()
        layout = QVBoxLayout(page_container)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        # ============================
        # HEADER
        # ============================
        header_row = QWidget()
        header_layout = QHBoxLayout(header_row)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(12)

        lbl_month = QLabel("Miesiąc rozliczeniowy:")
        lbl_month.setFont(QFont("Arial", 12))
        header_layout.addWidget(lbl_month)

        from datetime import datetime, timedelta
        today = datetime.today()
        self.month_selector = QComboBox()
        months = []
        for i in range(0, 24):
            d = (today.replace(day=1) - timedelta(days=30 * i))
            months.append(d.strftime("%Y-%m"))
        months = list(dict.fromkeys(months))
        months.sort(reverse=True)
        self.month_selector.addItems(months)
        cur_month = today.strftime("%Y-%m")
        if cur_month in months:
            self.month_selector.setCurrentText(cur_month)
        else:
            self.month_selector.setCurrentIndex(0)
        self.month_selector.currentTextChanged.connect(self._change_month)
        header_layout.addWidget(self.month_selector)

        lbl_emp = QLabel("Filtr pracownika:")
        lbl_emp.setFont(QFont("Arial", 12))
        header_layout.addWidget(lbl_emp)

        self.month_employee_filter = QComboBox()
        self.month_employee_filter.addItem("Wszyscy", None)
        self.month_employee_filter.currentIndexChanged.connect(lambda _: self.refresh_monthly_table())
        header_layout.addWidget(self.month_employee_filter)

        btn_refresh = QPushButton("Odśwież")
        btn_refresh.clicked.connect(self.refresh_monthly_table)
        header_layout.addWidget(btn_refresh, alignment=Qt.AlignmentFlag.AlignRight)

        layout.addWidget(header_row)

        # ============================
        # TABS
        # ============================
        self.month_tabs = QTabWidget()

        # --- KSIĘGOWOŚĆ ---
        tab_ks = QWidget()
        ks_layout = QVBoxLayout(tab_ks)
        ks_layout.setContentsMargins(0, 0, 0, 0)

        self.table_monthly = QTableWidget()
        self.table_monthly.setColumnCount(9)
        self.table_monthly.setHorizontalHeaderLabels([
            "ID",
            "Nazwa",
            "Etap",
            "Ilość dokumentów",
            "Czas pracy (suma)",
            "Stoper",
            "Notatka",
            "Usługa",
            "Edytuj"
        ])
        self.table_monthly.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)

        try:
            self.table_monthly.hideColumn(0)
        except Exception:
            pass

        ks_layout.addWidget(self.table_monthly)

        # --- KADRY ---
        tab_kadry = QWidget()
        kadry_layout = QVBoxLayout(tab_kadry)
        kadry_layout.setContentsMargins(0, 0, 0, 0)
        kadry_layout.addWidget(QLabel("Widok Kadrowy — w przygotowaniu"))

        self.month_tabs.addTab(tab_ks, "Księgowość")
        self.month_tabs.addTab(tab_kadry, "Kadry")

        layout.addWidget(self.month_tabs)
        self.pages.addWidget(page_container)

        # ============================
        # INIT
        # ============================
        try:
            self.refresh_monthly_filters()
        except Exception:
            pass
        try:
            self.refresh_monthly_table()
        except Exception:
            pass

    def refresh_monthly_filters(self):
        """Populate employee filter combobox with active employees (Wszyscy + list)."""
        self.month_employee_filter.blockSignals(True)
        self.month_employee_filter.clear()
        self.month_employee_filter.addItem("Wszyscy", None)

        conn = get_conn()
        cur = conn.cursor()
        try:
            cur.execute("""
                SELECT id, imie, nazwisko
                FROM pracownicy
                WHERE aktywny = 1
                ORDER BY nazwisko, imie
            """)
            for _id, imie, nazwisko in cur.fetchall():
                label = f"{imie} {nazwisko}"
                self.month_employee_filter.addItem(label, _id)
        finally:
            conn.close()

        self.month_employee_filter.blockSignals(False)

    def _change_month(self, new_month: str):
        self.aktualny_miesiac = new_month
        self.refresh_monthly_table()

    def refresh_monthly_table(self):
        selected_month = self.month_selector.currentText() if getattr(self, "month_selector",
                                                                      None) else self.aktualny_miesiac
        selected_emp_id = self.month_employee_filter.currentData() if getattr(self, "month_employee_filter",
                                                                              None) else None

        conn = get_conn()
        cur = conn.cursor()

        if is_admin(self.pracownik_id):
            if selected_emp_id:
                cur.execute("""
                    SELECT f.id, f.nazwa, COALESCE(sm.etap, 'Brak'),
                           COALESCE(sm.ilosc_dok, 0), COALESCE(f.sekundy_pracy, 0)
                    FROM firmy f
                    JOIN pracownik_firma pf ON pf.firma_id = f.id
                    LEFT JOIN statusy_miesieczne sm ON sm.firma_id = f.id AND sm.miesiac = ?
                    WHERE pf.pracownik_id = ?
                    GROUP BY f.id
                    ORDER BY f.nazwa
                """, (selected_month, selected_emp_id))
            else:
                cur.execute("""
                    SELECT f.id, f.nazwa, COALESCE(sm.etap, 'Brak'),
                           COALESCE(sm.ilosc_dok, 0), COALESCE(f.sekundy_pracy, 0)
                    FROM firmy f
                    LEFT JOIN statusy_miesieczne sm ON sm.firma_id = f.id AND sm.miesiac = ?
                    ORDER BY f.nazwa
                """, (selected_month,))
        else:
            cur.execute("""
                SELECT f.id, f.nazwa, COALESCE(sm.etap, 'Brak'),
                       COALESCE(sm.ilosc_dok, 0), COALESCE(f.sekundy_pracy, 0)
                FROM firmy f
                JOIN pracownik_firma pf ON pf.firma_id = f.id
                LEFT JOIN statusy_miesieczne sm ON sm.firma_id = f.id AND sm.miesiac = ?
                WHERE pf.pracownik_id = ?
                GROUP BY f.id
                ORDER BY f.nazwa
            """, (selected_month, self.pracownik_id))

        rows = cur.fetchall()
        conn.close()

        self.table_monthly.setRowCount(0)
        self.status_widgets = {}

        for row_idx, row in enumerate(rows):
            f_id, nazwa, etap, ilosc_dok, sekundy = row
            self.table_monthly.insertRow(row_idx)

            # 0: ID
            self.table_monthly.setItem(row_idx, 0, QTableWidgetItem(str(f_id)))

            # 1: Nazwa
            self.table_monthly.setItem(row_idx, 1, QTableWidgetItem(nazwa or ""))

            # 2: Etap
            cb_etap = QComboBox()
            cb_etap.addItems(COMPANY_STAGE_OPTIONS)
            cb_etap.setCurrentText(etap or "Brak")
            cb_etap.currentTextChanged.connect(lambda _txt, fid=f_id: self.schedule_status_autosave(fid))
            self.table_monthly.setCellWidget(row_idx, 2, cb_etap)

            # 3: Ilość dokumentów
            txt_docs = QLineEdit(str(ilosc_dok))
            txt_docs.setAlignment(Qt.AlignmentFlag.AlignCenter)
            txt_docs.setFixedWidth(80)
            txt_docs.textChanged.connect(lambda _txt, fid=f_id: self.schedule_status_autosave(fid))
            self.table_monthly.setCellWidget(row_idx, 3, txt_docs)

            # 4: Czas pracy
            czas_format = str(datetime.timedelta(seconds=sekundy or 0))
            lbl_time = QLabel(czas_format)
            lbl_time.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table_monthly.setCellWidget(row_idx, 4, lbl_time)

            # 5: Stoper
            btn_stoper = QPushButton("▶" if self.aktywna_firma_stoper != f_id else "⏸")
            btn_stoper.setFixedSize(36, 28)
            btn_stoper.setToolTip("Start / Stop")
            if self.aktywna_firma_stoper == f_id:
                btn_stoper.setStyleSheet("background-color: #e74c3c; color: white; font-weight: bold;")
            else:
                btn_stoper.setStyleSheet("background-color: #2ecc71; color: white; font-weight: bold;")
            btn_stoper.clicked.connect(lambda checked, fid=f_id, btn=btn_stoper: self._toggle_stoper_button(fid, btn))
            self.table_monthly.setCellWidget(row_idx, 5, btn_stoper)

            # 6: Notatka (skrót)
            conn2 = get_conn()
            cur2 = conn2.cursor()
            cur2.execute("""
                SELECT tresc, data_utworzenia
                FROM company_notes
                WHERE firma_id = ?
                ORDER BY data_utworzenia DESC LIMIT 1
            """, (f_id,))
            note_row = cur2.fetchone()
            conn2.close()

            if note_row:
                tresc, dt = note_row
                snippet = (tresc[:120] + "…") if len(tresc) > 120 else tresc
                lbl_note = QLabel(snippet)
                lbl_note.setToolTip(str(dt))
            else:
                lbl_note = QLabel("")

            lbl_note.setWordWrap(True)
            lbl_note.mouseDoubleClickEvent = lambda event, fid=f_id: self._open_notes_window(fid)
            self.table_monthly.setCellWidget(row_idx, 6, lbl_note)

            # 7: Usługa dodatkowa
            btn_usluga = QPushButton("Usługa")
            btn_usluga.clicked.connect(lambda checked, fid=f_id: self.dodaj_usluge_okno(fid))
            self.table_monthly.setCellWidget(row_idx, 7, btn_usluga)

            # 8: Edytuj firmę
            btn_actions = QPushButton("Edytuj")
            btn_actions.clicked.connect(lambda checked, fid=f_id: self._on_edit_company_from_table())
            self.table_monthly.setCellWidget(row_idx, 8, btn_actions)

            self.status_widgets[f_id] = (txt_docs, cb_etap)

    # -------------------------
    # Page creators: Companies, Employees, Email, Chat, Saldeo
    # -------------------------
    def create_companies_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(12, 12, 12, 12)

        header = QWidget()
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.addWidget(QLabel("<b>Klienci i Pracownicy</b>"))
        btn_add = QPushButton("Dodaj firmę")
        btn_add.setProperty("class", "primary")
        btn_add.clicked.connect(self._on_add_company)
        header_layout.addWidget(btn_add, alignment=Qt.AlignmentFlag.AlignRight)
        layout.addWidget(header)

        self.companies_table = QTableWidget(0, 6)
        self.companies_table.setHorizontalHeaderLabels(["ID", "Nazwa", "NIP", "Miasto", "Limit dok.", "Przypisani"])
        self.companies_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.companies_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.companies_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.companies_table.doubleClicked.connect(self._on_edit_company_from_table)
        layout.addWidget(self.companies_table)

        refresh_btn = QPushButton("Odśwież listę")
        refresh_btn.clicked.connect(self.refresh_firms_table)
        layout.addWidget(refresh_btn, alignment=Qt.AlignmentFlag.AlignRight)

        self.pages.addWidget(page)
        self.refresh_firms_table()

    def create_employees_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(12, 12, 12, 12)

        title = QLabel("Pracownicy i uprawnienia")
        title.setFont(QFont("Arial", 14, QFont.Weight.Bold))
        layout.addWidget(title)

        self.employees_table = QTableWidget(0, 5)
        self.employees_table.setHorizontalHeaderLabels(["ID", "Email", "Imię", "Nazwisko", "Aktywny"])
        self.employees_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.employees_table)

        refresh_btn = QPushButton("Odśwież pracowników")
        refresh_btn.clicked.connect(self.refresh_employees_table)
        layout.addWidget(refresh_btn, alignment=Qt.AlignmentFlag.AlignRight)

        self.pages.addWidget(page)
        self.refresh_employees_table()

    def create_email_page(self):
        try:
            from app.email.email_page import EmailPage
            print("EMAILPAGE IMPORT OK")
        except Exception as e:
            print("EMAILPAGE IMPORT ERROR:", e)
            EmailPage = None

        if not EmailPage:
            page = QWidget()
            layout = QVBoxLayout(page)
            layout.addWidget(QLabel("Email niedostępny"))
            self.pages.addWidget(page)
            return

        try:
            # KLUCZOWA LINIJKA — MUSI BYĆ TAK
            email_page = EmailPage(self, self.pracownik_id)

        except Exception as e:
            print("EMAILPAGE ERROR:", e)
            page = QWidget()
            layout = QVBoxLayout(page)
            layout.addWidget(QLabel(f"Email niedostępny ({e})"))
            self.pages.addWidget(page)
            return

        # jeśli wszystko OK — dodajemy stronę email
        self.pages.addWidget(email_page)

    def create_email_placeholder(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(QLabel("Email (integracja nieaktywna)"))
        self.pages.addWidget(page)

    def create_raporty_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(QLabel("Raporty (integracja nieaktywna)"))
        self.pages.addWidget(page)

    def create_chat_page(self):
        try:
            from app.chat.chat import ChatPage
        except Exception:
            ChatPage = None

        if not ChatPage:
            page = QWidget()
            layout = QVBoxLayout(page)
            layout.addWidget(QLabel("Chat niedostępny"))
            self.pages.addWidget(page)
            return

        try:
            chat_page = ChatPage(self.pracownik_id)
        except sqlite3.OperationalError:
            try:
                from app.db.schema import init_db
                init_db()
                chat_page = ChatPage(self.pracownik_id)
            except Exception:
                page = QWidget()
                layout = QVBoxLayout(page)
                layout.addWidget(QLabel("Chat chwilowo niedostępny (błąd bazy danych)"))
                self.pages.addWidget(page)
                return
        except Exception:
            page = QWidget()
            layout = QVBoxLayout(page)
            layout.addWidget(QLabel("Chat niedostępny"))
            self.pages.addWidget(page)
            return

        self.pages.addWidget(chat_page)

    def create_saldeo_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)

        title = QLabel("<b>Eksport do Saldeo</b>")
        layout.addWidget(title)

        btn_preview = QPushButton("Podgląd faktur")
        btn_preview.setProperty("class", "primary")
        btn_preview.clicked.connect(self._open_saldeo_preview)
        layout.addWidget(btn_preview)

        btn_export = QPushButton("Eksportuj do Saldeo (CSV)")
        btn_export.clicked.connect(self._export_saldeo_csv)
        layout.addWidget(btn_export)

        self.pages.addWidget(page)

    def create_settings_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(15)

        # dostęp tylko dla admina
        if not is_admin(self.pracownik_id):
            layout.addWidget(QLabel("Brak dostępu — tylko administrator."))
            self.pages.addWidget(page)
            return

        title = QLabel("<b>Ustawienia globalne</b>")
        title.setFont(QFont("Arial", 14))
        layout.addWidget(title)

        # Import cennika z Excela
        btn_import_cennik = QPushButton("Importuj globalny cennik z Excela")
        btn_import_cennik.setStyleSheet("background-color: #3498db; color: white; padding: 8px; border-radius: 4px;")
        btn_import_cennik.clicked.connect(self._import_global_cennik)
        layout.addWidget(btn_import_cennik)

        # Podgląd cennika
        btn_show_cennik = QPushButton("Pokaż aktualny cennik")
        btn_show_cennik.setStyleSheet("background-color: #2ecc71; color: white; padding: 8px; border-radius: 4px;")
        btn_show_cennik.clicked.connect(self._show_global_cennik)
        layout.addWidget(btn_show_cennik)

        self.pages.addWidget(page)

    def _import_global_cennik(self):
        """
        Importuje globalny cennik z Excela do tabeli 'cenniki'.
        Funkcja musi znajdować się wewnątrz klasy OfficeManagerApp.
        """
        try:
            from app.db.import_cennik import import_cennik_excel
            import_cennik_excel()
            QMessageBox.information(self, "Sukces", "Globalny cennik został zaimportowany.")
        except Exception as e:
            QMessageBox.critical(self, "Błąd importu", f"Import nie powiódł się:\n{e}")

    def _open_saldeo_preview(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("Podgląd faktur")
        dialog.resize(900, 600)

        layout = QVBoxLayout(dialog)

        table = QTableWidget(0, 6)
        table.setHorizontalHeaderLabels(["Numer faktury", "Firma", "Usługa", "Ilość", "Cena netto", "Cena brutto"])
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        layout.addWidget(table)

        conn = get_conn()
        cur = conn.cursor()
        try:
            cur.execute("""
                SELECT d.numer, f.nazwa, d.typ, d.cena_netto, d.data_wyst
                FROM dokumenty d
                JOIN firmy f ON f.id = d.firma_id
                ORDER BY d.data_wyst DESC, d.id DESC
            """)
            rows = cur.fetchall()
        finally:
            conn.close()

        for numer, firma_nazwa, usluga, cena_netto, data_wyst in rows:
            row_idx = table.rowCount()
            table.insertRow(row_idx)

            cena_brutto = round(float(cena_netto) * 1.23, 2)

            table.setItem(row_idx, 0, QTableWidgetItem(numer))
            table.setItem(row_idx, 1, QTableWidgetItem(firma_nazwa))
            table.setItem(row_idx, 2, QTableWidgetItem(usluga))
            table.setItem(row_idx, 3, QTableWidgetItem("1"))
            table.setItem(row_idx, 4, QTableWidgetItem(f"{cena_netto:.2f}"))
            table.setItem(row_idx, 5, QTableWidgetItem(f"{cena_brutto:.2f}"))

        btn_close = QPushButton("Zamknij")
        btn_close.clicked.connect(dialog.close)
        layout.addWidget(btn_close, alignment=Qt.AlignmentFlag.AlignRight)

        dialog.exec()

    def _export_saldeo_csv(self):
        from app.utils.saldeo_exporter import export_saldeo_csv
        import datetime

        miesiac = datetime.datetime.now().strftime("%Y-%m")
        output_path = f"saldeo_export_{miesiac}.csv"

        try:
            export_saldeo_csv(None, output_path, miesiac)
            QMessageBox.information(self, "Eksport zakończony", f"Plik zapisano jako:\n{output_path}")
        except Exception as e:
            QMessageBox.critical(self, "Błąd eksportu", str(e))

    def _on_add_company(self):
        dialog = CompanyDialog(self.pracownik_id, company_data={})
        if dialog.exec() == QDialog.DialogCode.Accepted:
            payload = dialog.get_payload()
            try:
                conn = get_conn()
                cur = conn.cursor()
                final_company_id = save_company(cur, payload)
                conn.commit()
                conn.close()
                QMessageBox.information(self, "Sukces", "Firma została dodana.")
                self.refresh_firms_table()
            except Exception as e:
                QMessageBox.critical(self, "Błąd", f"Nie udało się zapisać firmy: {e}")

    def _on_edit_company_from_table(self, firma_id: Optional[int] = None):
        """
        Obsługuje edycję firmy zarówno z tabeli firm (companies_table),
        jak i z widoku miesięcznego (table_monthly).
        """

        # --- WYWOŁANIE Z WIDOKU MIESIĘCZNEGO ---
        if firma_id is not None:
            company_id = firma_id

        # --- WYWOŁANIE Z TABELI FIRM ---
        else:
            if not self.companies_table:
                return
            row = self.companies_table.currentRow()
            if row < 0:
                return
            try:
                company_id = int(self.companies_table.item(row, 0).text())
            except Exception:
                return

        # --- POBRANIE DANYCH FIRMY ---
        conn = get_conn()
        cur = conn.cursor()
        try:
            cur.execute("""
                SELECT id, nazwa, nip, miasto, limit_dokumentow, glowny_cennik
                FROM firmy WHERE id = ?
            """, (company_id,))
            r = cur.fetchone()
            if not r:
                QMessageBox.warning(self, "Brak", "Nie znaleziono firmy w bazie.")
                return

            company_data = {
                "id": r[0],
                "nazwa": r[1],
                "nip": r[2],
                "miasto": r[3],
                "limit_dokumentow": r[4],
                "glowny_cennik": r[5]
            }
        finally:
            conn.close()

        # --- OKNO EDYCJI ---
        dialog = CompanyDialog(self.pracownik_id, company_data=company_data)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            payload = dialog.get_payload()
            try:
                conn = get_conn()
                cur = conn.cursor()
                save_company(cur, payload, company_id=company_id)
                conn.commit()
                conn.close()
                QMessageBox.information(self, "Sukces", "Zmiany zapisane.")

                # odśwież obie tabele jeśli istnieją
                try:
                    self.refresh_firms_table()
                except:
                    pass

                try:
                    self.refresh_monthly_table()
                except:
                    pass

            except Exception as e:
                QMessageBox.critical(self, "Błąd", f"Nie udało się zapisać zmian: {e}")

    def refresh_firms_table(self):
        conn = get_conn()
        cur = conn.cursor()
        try:
            cur.execute("SELECT id, nazwa, nip, miasto, limit_dokumentow FROM firmy ORDER BY nazwa")
            rows = cur.fetchall()
        finally:
            conn.close()

        self.companies_table.setRowCount(0)
        for r in rows:
            row_idx = self.companies_table.rowCount()
            self.companies_table.insertRow(row_idx)
            for col_idx, val in enumerate(r):
                item = QTableWidgetItem(str(val) if val is not None else "")
                self.companies_table.setItem(row_idx, col_idx, item)

    def refresh_employees_table(self):
        conn = get_conn()
        cur = conn.cursor()
        try:
            cur.execute("SELECT id, email, imie, nazwisko, aktywny FROM pracownicy ORDER BY imie, nazwisko")
            rows = cur.fetchall()
        finally:
            conn.close()

        self.employees_table.setRowCount(0)
        for r in rows:
            row_idx = self.employees_table.rowCount()
            self.employees_table.insertRow(row_idx)
            for col_idx, val in enumerate(r):
                item = QTableWidgetItem(str(val) if val is not None else "")
                self.employees_table.setItem(row_idx, col_idx, item)

    # -------------------------
    # Navigation handler (required by sidebar)
    # -------------------------
    def zmien_zakladke(self, index: int):
        if index < 0 or index >= self.pages.count():
            return
        self.pages.setCurrentIndex(index)
        try:
            if index == 0:
                try:
                    self.refresh_monthly_filters()
                except Exception:
                    pass
                try:
                    self.refresh_monthly_table()
                except Exception:
                    pass
            elif index == 1:
                try:
                    self.refresh_firms_table()
                except Exception:
                    pass
            elif index == 2:
                try:
                    self.refresh_employees_table()
                except Exception:
                    pass
            elif index == 3:
                if EmailUIMixin and hasattr(self, "refresh_email_accounts"):
                    try:
                        self.refresh_email_accounts()
                    except Exception:
                        pass
            elif index == 4:
                try:
                    page = self.pages.widget(index)
                    if hasattr(page, "refresh_chat_conversations"):
                        page.refresh_chat_conversations()
                except Exception:
                    pass
            elif index == 5:
                try:
                    if SaldeoService and hasattr(self, "refresh_saldeo_page"):
                        self.refresh_saldeo_page()
                except Exception:
                    pass
        except Exception:
            pass

    # -------------------------
    # Timer / stoper helpers
    # -------------------------
    def _toggle_stoper_button(self, firma_id: int, btn: QPushButton):
        if self.aktywna_firma_stoper is None:
            self.obsluga_stopera(firma_id, None)
            btn.setText("⏸")
            btn.setStyleSheet("background-color: #e74c3c; color: white; font-weight: bold;")
            return

        if self.aktywna_firma_stoper == firma_id:
            self.obsluga_stopera(firma_id, None)
            btn.setText("▶")
            btn.setStyleSheet("background-color: #2ecc71; color: white; font-weight: bold;")
            return

        # switch to another company
        self.obsluga_stopera(firma_id, None)
        self.refresh_monthly_table()

    def obsluga_stopera(self, firma_id: int, row_idx: Optional[int]):
        conn = get_conn()
        cur = conn.cursor()

        if self.aktywna_firma_stoper is None:
            self.aktywna_firma_stoper = firma_id
            self.czas_start = datetime.datetime.now()
            cur.execute("SELECT nazwa FROM firmy WHERE id=?", (firma_id,))
            res = cur.fetchone()
            nazwa = res[0] if res else str(firma_id)
            self.aktywna_firma_stoper_nazwa = nazwa
            self.lbl_global_timer_status.setText(f"Timer: {nazwa}")
        elif self.aktywna_firma_stoper == firma_id:
            roznica_sekund = int((datetime.datetime.now() - self.czas_start).total_seconds())
            cur.execute("UPDATE firmy SET sekundy_pracy = sekundy_pracy + ? WHERE id = ?", (roznica_sekund, firma_id))
            conn.commit()
            self.aktywna_firma_stoper = None
            self.aktywna_firma_stoper_nazwa = ""
            self.czas_start = None
            self.lbl_global_timer_status.setText("Timer: brak aktywnej firmy")
        else:
            previous_company_id = self.aktywna_firma_stoper
            previous_delta = int((datetime.datetime.now() - self.czas_start).total_seconds())
            cur.execute("UPDATE firmy SET sekundy_pracy = sekundy_pracy + ? WHERE id = ?", (previous_delta, previous_company_id))
            self.aktywna_firma_stoper = firma_id
            self.czas_start = datetime.datetime.now()
            cur.execute("SELECT nazwa FROM firmy WHERE id=?", (firma_id,))
            res = cur.fetchone()
            nazwa = res[0] if res else str(firma_id)
            self.aktywna_firma_stoper_nazwa = nazwa
            self.lbl_global_timer_status.setText(f"Timer: {nazwa}")
            conn.commit()

        conn.close()
        self.refresh_monthly_table()

    def aktualizuj_stoper_gui(self):
        if self.aktywna_firma_stoper is not None and self.czas_start is not None:
            roznica = int((datetime.datetime.now() - self.czas_start).total_seconds())
            self.lbl_global_timer_status.setText(
                f"Timer: {self.aktywna_firma_stoper_nazwa or f'firma #{self.aktywna_firma_stoper}'} — {roznica} s"
            )
        else:
            self.lbl_global_timer_status.setText("Timer: brak aktywnej firmy")

    def schedule_status_autosave(self, firma_id: int):
        timer = self.status_autosave_timers.get(firma_id)
        if timer is None:
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.timeout.connect(lambda fid=firma_id: self.zapisz_wiersz_statusu(fid, show_feedback=False))
            self.status_autosave_timers[firma_id] = timer
        timer.start(700)

    def zapisz_wiersz_statusu(self, firma_id: int, show_feedback: bool = True):
        txt_widget, cb_widget = self.status_widgets.get(firma_id, (None, None))
        txt_docs = txt_widget.text() if txt_widget is not None else "0"
        cb_etap = cb_widget.currentText() if cb_widget is not None else "Brak"

        try:
            ilosc_dok = int(txt_docs)
        except ValueError:
            if show_feedback:
                QMessageBox.critical(self, "Błąd", "Pole ilość dokumentów musi posiadać wartość liczbową!")
            return

        conn = get_conn()
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO statusy_miesieczne (firma_id, miesiac, etap, ilosc_dok)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(firma_id, miesiac) DO UPDATE SET etap = excluded.etap, ilosc_dok = excluded.ilosc_dok
            """, (firma_id, self.aktualny_miesiac, cb_etap, ilosc_dok)
        )
        apply_default_main_pricing(cur, firma_id, ilosc_dok)
        conn.commit()
        conn.close()
        if show_feedback:
            QMessageBox.information(self, "Zapisano", "Stan firmy został zaktualizowany pomyślnie.")
        else:
            try:
                self.statusBar().showMessage("Status firmy zapisano automatycznie.", 1500)
            except Exception:
                pass

    def dodaj_usluge_okno(self, firma_id: int):
        conn = get_conn()
        cur = conn.cursor()

        # Pobranie danych firmy
        cur.execute("SELECT typ_rozliczenia, vat_czynny, limit_dokumentow FROM firmy WHERE id = ?", (firma_id,))
        firma = cur.fetchone()
        if not firma:
            QMessageBox.critical(self, "Błąd", "Nie znaleziono firmy.")
            return

        typ = firma["typ_rozliczenia"]
        vat = bool(firma["vat_czynny"])
        limit = firma["limit_dokumentow"]

        # Pobranie cennika głównego
        cennik = wybierz_cennik(conn, typ, vat, limit)
        cena_za_dok = pobierz_cene_za_dokument(conn, typ, vat)

        # Lista usług dodatkowych
        uslugi = [
            ("Usługa księgowa", cennik["cena_netto_2024"]),
            ("Usługa kadrowa", cennik["zdrowotna"]),
            ("Pojedynczy dokument", cena_za_dok),
            ("Doradztwo IT", 250.00),
            ("Obsługa ZUS", 100.00),
            ("Inna usługa", 0.00)
        ]

        # Okno dialogowe
        dialog = QDialog(self)
        dialog.setWindowTitle("Dodaj usługę dodatkową")
        layout = QVBoxLayout(dialog)

        cb_usluga = QComboBox()
        for nazwa, cena in uslugi:
            cb_usluga.addItem(nazwa, cena)
        layout.addWidget(QLabel("Rodzaj usługi:"))
        layout.addWidget(cb_usluga)

        txt_cena = QLineEdit()
        txt_cena.setPlaceholderText("Cena netto")
        layout.addWidget(QLabel("Cena netto:"))
        layout.addWidget(txt_cena)

        def on_usluga_changed():
            cena = cb_usluga.currentData()
            txt_cena.setText(str(cena))

        cb_usluga.currentIndexChanged.connect(on_usluga_changed)
        on_usluga_changed()

        btn_ok = QPushButton("Dodaj")
        btn_ok.setProperty("class", "primary")
        btn_ok.clicked.connect(dialog.accept)
        layout.addWidget(btn_ok)

        if dialog.exec() != QDialog.DialogCode.Accepted:
            conn.close()
            return

        # Pobranie danych z okna
        nazwa_uslugi = cb_usluga.currentText()
        try:
            cena_netto = float(txt_cena.text().replace(",", "."))
        except ValueError:
            QMessageBox.critical(self, "Błąd", "Cena musi być liczbą.")
            conn.close()
            return

        # Numer dokumentu
        nr_faktury = f"USL/{datetime.datetime.now().strftime('%Y%m%d%H%M%S')}/{firma_id}"

        # Zapis do tabeli dokumenty
        cur.execute(
            "INSERT INTO dokumenty (firma_id, numer, typ, cena_netto, data_wyst) VALUES (?, ?, ?, ?, ?)",
            (firma_id, nr_faktury, nazwa_uslugi, cena_netto, datetime.datetime.now().strftime("%Y-%m-%d"))
        )

        # Aktualizacja statusu miesięcznego
        cur.execute("""
            INSERT INTO statusy_miesieczne (firma_id, miesiac, ilosc_dok)
            VALUES (?, ?, 1)
            ON CONFLICT(firma_id, miesiac) DO UPDATE SET ilosc_dok = ilosc_dok + 1
        """, (firma_id, self.aktualny_miesiac))

        cur.execute("SELECT ilosc_dok FROM statusy_miesieczne WHERE firma_id = ? AND miesiac = ?",
                    (firma_id, self.aktualny_miesiac))
        document_count = cur.fetchone()
        apply_default_main_pricing(cur, firma_id, document_count[0] if document_count else 0)

        conn.commit()
        conn.close()

        self.refresh_monthly_table()
        QMessageBox.information(self, "Dodano usługę", f"Utworzono usługę: {nazwa_uslugi}\nZapisano do raportu Saldeo.")

    # -------------------------
    # Cleanup / close
    # -------------------------
    def closeEvent(self, event):
        try:
            self.global_timer.stop()
        except Exception:
            pass
        super().closeEvent(event)
