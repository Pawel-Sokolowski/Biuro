# app/companies/companies_ui.py
"""
Pełny plik UI: LoginWindow, CompanyDialog, OfficeManagerApp.
Gotowy do wklejenia — integruje chat, email (jeśli dostępne), notatki,
stoper jako mały przycisk ▶ / ⏸, UoP/UZ/UoD, udziałowcy i eksport Saldeo.
"""

from typing import Optional, Dict, Any, List, Tuple
import datetime
import sqlite3
import openpyxl

from PyQt6.QtWidgets import (
    QDialog, QDialogButtonBox, QFormLayout, QLineEdit, QLabel, QVBoxLayout,
    QMessageBox, QMainWindow, QWidget, QHBoxLayout, QListWidget, QStackedWidget,
    QPushButton, QTableWidget, QTableWidgetItem, QHeaderView, QComboBox,
    QTabWidget, QSpinBox, QSplitter, QGroupBox, QFileDialog, QScrollArea,
    QListWidgetItem, QGridLayout, QAbstractItemView, QCheckBox, QTextEdit
)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QFont

# Lokalny DAO i serwisy
from app.db.dao_companies import get_conn, DB_NAME
from app.email.email_actions import SyncRunnable
from app.email.email_sync import EmailSyncer
from app.utils.crypto import login_user, register_user, get_employee_info, is_admin
from app.utils.parsing import parse_int_value, normalize_identifier
from app.companies.companies_model import apply_default_main_pricing, save_company, get_company_assigned_employee_ids
from app.companies.constants import COMPANY_STAGE_OPTIONS

# Email UI mixin i Chat widget (jeśli istnieją)
try:
    from app.email.email_ui import EmailUIMixin
except Exception:
    EmailUIMixin = None

try:
    from app.chat.chat import ChatWidget
except Exception:
    ChatWidget = None

# Opcjonalny serwis Saldeo
try:
    from app.services.saldeo_service import SaldeoService
except Exception:
    SaldeoService = None


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

        lbl_title = QLabel("Zaloguj się do systemu")
        lbl_title.setFont(QFont("Arial", 14, QFont.Weight.Bold))
        layout.addWidget(lbl_title)

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

        btn_layout = QHBoxLayout()
        btn_login = QPushButton("Zaloguj")
        btn_login.clicked.connect(self.login)
        btn_login.setStyleSheet(
            "background-color: #3498db; color: white; padding: 8px; border-radius: 4px; font-weight: bold;"
        )

        btn_register = QPushButton("Zarejestruj")
        btn_register.clicked.connect(self.show_register_dialog)
        btn_register.setStyleSheet(
            "background-color: #27ae60; color: white; padding: 8px; border-radius: 4px; font-weight: bold;"
        )

        btn_layout.addWidget(btn_login)
        btn_layout.addWidget(btn_register)
        layout.addLayout(btn_layout)

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


# -------------------------
# CompanyDialog (jasny motyw, CEIDG button, udziałowcy)
# -------------------------
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
        self._build_ui()
        self._load_employees()
        self._toggle_main_price_mode(bool(self.company_data.get("glowny_cennik_manualny", 0)))
        # jeśli edycja istniejącej firmy, załaduj notatki
        try:
            self._load_company_notes()
        except Exception:
            pass

    def _build_ui(self):
        # Jasny, czytelny motyw
        self.setStyleSheet("""
            QDialog { background-color: #ffffff; color: #222222; }
            QGroupBox { border: 1px solid #d0d7de; border-radius: 6px; margin-top: 10px; padding: 12px; }
            QGroupBox::title { color: #0b5fff; font-weight: bold; }
            QLabel[sectionHint="true"] { color: #6b7280; font-size: 11px; }
            QLabel[fieldLabel="true"] { color: #374151; font-size: 12px; }
            QLineEdit, QSpinBox, QListWidget, QComboBox { background-color: #ffffff; border: 1px solid #cbd5e1; border-radius: 4px; padding: 6px 8px; color: #111827; }
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

        # IDENTYFIKATORY (NIP z przyciskiem CEIDG)
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
        self._add_grid_line_edit(business_grid, 1, 0, "Cennik usług księgowych", "cennik_ksiegowy")
        self._add_grid_line_edit(business_grid, 1, 1, "Cennik usług kadrowych", "cennik_kadrowy")
        self._add_grid_line_edit(business_grid, 2, 0, "Cennik czasowy", "cennik_czasowy")
        self._add_grid_line_edit(business_grid, 2, 1, "Główny cennik", "glowny_cennik")
        self.main_price_manual_checkbox = QCheckBox("Ręcznie nadpisz główny cennik")
        self.main_price_manual_checkbox.setChecked(bool(self.company_data.get("glowny_cennik_manualny", 0)))
        self.main_price_manual_checkbox.toggled.connect(self._toggle_main_price_mode)
        business_grid.addWidget(self.main_price_manual_checkbox, 3, 0, 1, 2)

        # Zamiast jednej liczby pracowników: UoP, UZ, UoD
        self._add_grid_spin_box(business_grid, 4, 0, "Pracownicy UoP", "uop")
        self._add_grid_spin_box(business_grid, 4, 1, "Umowy zlecenia (UZ)", "uz")
        self._add_grid_spin_box(business_grid, 5, 0, "Umowy o dzieło (UoD)", "uod")
        self._add_grid_spin_box(business_grid, 5, 1, "Limit dokumentów", "limit_dokumentow")
        content_layout.addWidget(business_group)

        # ASSIGNED EMPLOYEES
        employee_group, employee_grid = self._create_section("Przypisani pracownicy", "Wybierz osoby odpowiedzialne za obsługę tej firmy.")
        self.employee_list = QListWidget()
        self.employee_list.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
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

        # Ostatnie notatki (fragmenty)
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

    # CEIDG fetch
    def _on_fetch_ceidg_by_nip(self):
        nip = self.inputs.get("nip").text().strip() if self.inputs.get("nip") else ""
        if not nip:
            QMessageBox.warning(self, "Brak NIP", "Wprowadź NIP przed pobraniem z CEIDG.")
            return
        try:
            from app.companies.ceidg_api import search_ceidg_by_nip
        except Exception:
            search_ceidg_by_nip = None

        if not search_ceidg_by_nip:
            QMessageBox.information(self, "Brak integracji", "Funkcja CEIDG nie jest dostępna w tej instalacji.")
            return

        try:
            result = search_ceidg_by_nip(nip)
        except Exception as e:
            QMessageBox.critical(self, "Błąd CEIDG", f"Nie udało się pobrać danych: {e}")
            return

        if not result:
            QMessageBox.information(self, "Brak danych", "Nie znaleziono firmy o podanym NIP w CEIDG.")
            return

        mapping = {
            "nazwa": ["nazwa", "nazwa_firmy", "company_name"],
            "miasto": ["miasto", "city"],
            "ulica": ["ulica", "street"],
            "kod_pocztowy": ["kod_pocztowy", "postal_code", "postcode"],
            "adres_email": ["email", "adres_email"]
        }
        for field, keys in mapping.items():
            for k in keys:
                if k in result and result[k]:
                    widget = self.inputs.get(field)
                    if widget and (not widget.text().strip()):
                        widget.setText(str(result[k]))
                    break

        QMessageBox.information(self, "Pobrano z CEIDG", "Dane zostały pobrane i wstawione do formularza (tam gdzie pola były puste).")

    # Shareholders helpers
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

    # UI helpers
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

    def _add_grid_line_edit(self, grid_layout: QGridLayout, row: int, column: int, label_text: str, field_name: str, placeholder: str = "", required: bool = False, col_span: int = 1):
        input_widget = self._create_text_input(field_name, placeholder=placeholder, required=required)
        field_container = self._create_field_container(label_text, input_widget)
        grid_layout.addWidget(field_container, row, column, 1, col_span)

    def _add_grid_spin_box(self, grid_layout: QGridLayout, row: int, column: int, label_text: str, field_name: str, col_span: int = 1):
        spin_box = self._create_spin_input(field_name)
        field_container = self._create_field_container(label_text, spin_box)
        grid_layout.addWidget(field_container, row, column, 1, col_span)

    def _toggle_main_price_mode(self, manual_enabled: bool):
        widget = self.inputs.get("glowny_cennik")
        if widget:
            widget.setReadOnly(not manual_enabled)
            if not manual_enabled and not widget.text().strip():
                widget.setPlaceholderText("Zostanie dobrany po przekroczeniu limitu dokumentów")
            else:
                widget.setPlaceholderText("")

    def _load_employees(self):
        selected_ids = set(self.company_data.get("assigned_employee_ids", []))
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

        # Save shareholders into company_data for save_company to handle
        self.company_data["udzialowcy"] = self.shareholders
        self.accept()

    def get_payload(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {}
        for field_name, widget in self.inputs.items():
            if isinstance(widget, QSpinBox):
                payload[field_name] = widget.value()
            else:
                payload[field_name] = widget.text().strip()
        payload["glowny_cennik_manualny"] = 1 if self.main_price_manual_checkbox.isChecked() else 0
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
        conn = get_conn()
        cur = conn.cursor()
        try:
            cur.execute("SELECT tresc, data_utworzenia FROM company_notes WHERE firma_id = ? ORDER BY data_utworzenia DESC LIMIT 5", (fid,))
            for tresc, dt in cur.fetchall():
                snippet = (tresc[:140] + "…") if len(tresc) > 140 else tresc
                self.notes_list_widget.addItem(f"[{dt}] {snippet}")
        finally:
            conn.close()


# -------------------------
# OfficeManagerApp (główny window) - integruje Email i Chat
# -------------------------
BaseClasses = (QMainWindow, EmailUIMixin) if EmailUIMixin else (QMainWindow,)

class OfficeManagerApp(*BaseClasses):  # type: ignore
    def __init__(self, pracownik_id: Optional[int] = None):
        super().__init__()
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
        self.sidebar.addItems(["Widok Miesięczny", "Klienci i Pracownicy", "Pracownicy i Uprawnienia", "Email", "Chat", "Eksport Saldeo"])
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

        self.pages = QStackedWidget()
        content_layout.addWidget(self.pages)
        main_layout.addWidget(content_widget)

        # Create pages
        self.create_monthly_view_page()
        self.create_companies_page()
        self.create_employees_page()

        # Email page from mixin if available
        if EmailUIMixin:
            self.create_email_page()
            # ensure email auto-sync timer exists
            try:
                self._init_email_auto_sync(interval_minutes=5, start_immediately=False)
            except Exception:
                pass
        else:
            self.create_email_placeholder()

        # Chat page if available
        if ChatWidget:
            self.create_chat_page()
        else:
            self.create_chat_placeholder()

        self.create_saldeo_page()

        # Navigation
        self.sidebar.currentRowChanged.connect(self.zmien_zakladke)
        self.sidebar.setCurrentRow(0)

    def zmien_zakladke(self, index: int):
        self.pages.setCurrentIndex(index)
        if index == 0:
            self.refresh_monthly_table()
        elif index == 1:
            self.refresh_firms_table()
        elif index == 2:
            self.refresh_employees_table()
        elif index == 3:
            if EmailUIMixin:
                try:
                    self.refresh_email_accounts()
                except Exception:
                    pass
        elif index == 4:
            if ChatWidget:
                try:
                    self.refresh_chat_conversations()
                except Exception:
                    pass

    # -------------------------
    # Monthly view (stoper, notatki)
    # -------------------------
    def create_monthly_view_page(self):
        page_ksiegowosc = QWidget()
        layout = QVBoxLayout(page_ksiegowosc)
        layout.setContentsMargins(20, 20, 20, 20)

        header = QHBoxLayout()
        lbl_month = QLabel("Miesiąc rozliczeniowy:")
        lbl_month.setFont(QFont("Arial", 14))
        header.addWidget(lbl_month)

        from datetime import datetime, timedelta
        today = datetime.today()
        previous_month = (today.replace(day=1) - timedelta(days=1)).strftime("%Y-%m")
        self.aktualny_miesiac = previous_month

        self.month_selector = QComboBox()
        for i in range(12):
            d = today.replace(day=1) - timedelta(days=30 * i)
            self.month_selector.addItem(d.strftime("%Y-%m"))

        self.month_selector.setCurrentText(previous_month)
        self.month_selector.currentTextChanged.connect(self._change_month)
        header.addWidget(self.month_selector)
        layout.addLayout(header)

        self.table_monthly = QTableWidget()
        self.table_monthly.setColumnCount(9)
        self.table_monthly.setHorizontalHeaderLabels([
            "ID", "Nazwa Firmy", "Ilość dokumentów", "Etap pracy",
            "Czas pracy (Suma)", "Stoper", "Notatka", "Akcja Saldeo", "Akcje"
        ])
        self.table_monthly.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table_monthly.hideColumn(0)
        layout.addWidget(self.table_monthly)

        page_kadry = QWidget()
        kadry_layout = QVBoxLayout(page_kadry)
        kadry_layout.addWidget(QLabel("Widok Kadrowy — w przygotowaniu"))

        self.month_tabs = QTabWidget()
        self.month_tabs.addTab(page_ksiegowosc, "Księgowość")
        self.month_tabs.addTab(page_kadry, "Kadry")

        self.pages.addWidget(self.month_tabs)
        self.refresh_monthly_table()

    def refresh_monthly_table(self):
        self.table_monthly.setRowCount(0)
        conn = get_conn()
        cur = conn.cursor()

        if is_admin(self.pracownik_id):
            cur.execute("""
                SELECT f.id, f.nazwa, f.sekundy_pracy, sm.etap, sm.ilosc_dok 
                FROM firmy f
                LEFT JOIN statusy_miesieczne sm ON f.id = sm.firma_id AND sm.miesiac = ?
                ORDER BY f.nazwa
            """, (self.aktualny_miesiac,))
        else:
            cur.execute("""
                SELECT f.id, f.nazwa, f.sekundy_pracy, sm.etap, sm.ilosc_dok 
                FROM firmy f
                LEFT JOIN statusy_miesieczne sm ON f.id = sm.firma_id AND sm.miesiac = ?
                WHERE f.id IN (
                    SELECT DISTINCT firma_id FROM pracownik_firma WHERE pracownik_id = ?
                )
                ORDER BY f.nazwa
            """, (self.aktualny_miesiac, self.pracownik_id))

        dane = cur.fetchall()
        conn.close()

        self.status_widgets = {}

        for row_idx, row in enumerate(dane):
            f_id, nazwa, sekundy, etap, ilosc_dok = row
            etap = etap or "Brak"
            ilosc_dok = ilosc_dok or 0

            self.table_monthly.insertRow(row_idx)
            self.table_monthly.setItem(row_idx, 0, QTableWidgetItem(str(f_id)))
            self.table_monthly.setItem(row_idx, 1, QTableWidgetItem(nazwa))

            # Ilość dokumentów (edytowalne pole)
            txt_docs = QLineEdit(str(ilosc_dok))
            txt_docs.setAlignment(Qt.AlignmentFlag.AlignCenter)
            txt_docs.setFixedWidth(70)
            txt_docs.textChanged.connect(lambda _txt, fid=f_id: self.schedule_status_autosave(fid))
            self.table_monthly.setCellWidget(row_idx, 2, txt_docs)

            # Etap (combo)
            cb_etap = QComboBox()
            cb_etap.blockSignals(True)
            cb_etap.addItems(COMPANY_STAGE_OPTIONS)
            cb_etap.setCurrentText(etap)
            cb_etap.setStyleSheet(f"padding: 3px;")
            cb_etap.blockSignals(False)
            cb_etap.currentTextChanged.connect(lambda _txt, fid=f_id: self.schedule_status_autosave(fid))
            self.table_monthly.setCellWidget(row_idx, 3, cb_etap)
            self.status_widgets[f_id] = (txt_docs, cb_etap)

            # Czas pracy (suma)
            czas_format = str(datetime.timedelta(seconds=sekundy or 0))
            lbl_time = QLabel(czas_format)
            lbl_time.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table_monthly.setCellWidget(row_idx, 4, lbl_time)

            # Mały przycisk stoper (ikonka ▶ / ⏸)
            btn_stoper = QPushButton("▶")
            btn_stoper.setFixedSize(36, 28)
            btn_stoper.setToolTip("Start / Stop")
            btn_stoper.setStyleSheet("font-weight: bold;")
            if self.aktywna_firma_stoper == f_id:
                btn_stoper.setText("⏸")
                btn_stoper.setStyleSheet("background-color: #e74c3c; color: white; font-weight: bold;")
            else:
                btn_stoper.setText("▶")
                btn_stoper.setStyleSheet("background-color: #2ecc71; color: white; font-weight: bold;")
            btn_stoper.clicked.connect(lambda checked, fid=f_id, btn=btn_stoper: self._toggle_stoper_button(fid, btn))
            self.table_monthly.setCellWidget(row_idx, 5, btn_stoper)

            # Notatka - pobierz ostatnią notatkę i pokaż fragment (np. 120 znaków)
            conn2 = get_conn()
            cur2 = conn2.cursor()
            cur2.execute("SELECT tresc, data_utworzenia FROM company_notes WHERE firma_id = ? ORDER BY data_utworzenia DESC LIMIT 1", (f_id,))
            note_row = cur2.fetchone()
            conn2.close()
            if note_row:
                tresc, dt = note_row
                snippet = (tresc[:120] + "…") if len(tresc) > 120 else tresc
                lbl_note = QLabel(snippet)
                lbl_note.setToolTip(f"{dt}")
            else:
                lbl_note = QLabel("")
            lbl_note.setWordWrap(True)
            self.table_monthly.setCellWidget(row_idx, 6, lbl_note)

            # +Faktura
            btn_add_faktura = QPushButton("+Faktura")
            btn_add_faktura.setStyleSheet("background-color: #9b59b6; color: white;")
            btn_add_faktura.clicked.connect(lambda checked, fid=f_id: self.dodaj_fakture_okno(fid))
            self.table_monthly.setCellWidget(row_idx, 7, btn_add_faktura)

            # Akcje (Edytuj)
            btn_actions = QPushButton("Edytuj")
            btn_actions.clicked.connect(lambda checked, fid=f_id: self.open_edit_company_dialog(fid))
            self.table_monthly.setCellWidget(row_idx, 8, btn_actions)

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

        # przełącz na inną firmę
        self.obsluga_stopera(firma_id, None)
        self.refresh_monthly_table()

    def _change_month(self, new_month: str):
        self.aktualny_miesiac = new_month
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
                f"Timer: {self.aktywna_firma_stoper_nazwa or f'firma #{self.aktywna_firma_stoper}'} od {roznica} sek."
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

    def dodaj_fakture_okno(self, firma_id: int):
        conn = get_conn()
        cur = conn.cursor()
        nr_faktury = f"FV/{datetime.datetime.now().strftime('%Y%m%d%H%M%S')}/{firma_id}"
        cur.execute(
            "INSERT INTO dokumenty (firma_id, numer, typ, cena_netto, data_wyst) VALUES (?, ?, ?, ?, ?)",
            (firma_id, nr_faktury, 'Usługa księgowa', 1500.00, datetime.datetime.now().strftime("%Y-%m-%d"))
        )
        cur.execute(
            """
            INSERT INTO statusy_miesieczne (firma_id, miesiac, ilosc_dok)
            VALUES (?, ?, 1)
            ON CONFLICT(firma_id, miesiac) DO UPDATE SET ilosc_dok = ilosc_dok + 1
            """, (firma_id, self.aktualny_miesiac)
        )
        cur.execute("SELECT ilosc_dok FROM statusy_miesieczne WHERE firma_id = ? AND miesiac = ?", (firma_id, self.aktualny_miesiac))
        document_count = cur.fetchone()
        apply_default_main_pricing(cur, firma_id, document_count[0] if document_count else 0)
        conn.commit()
        conn.close()
        self.refresh_monthly_table()
        QMessageBox.information(self, "Dodano dokument", f"Utworzono fakturę próbną: {nr_faktury}\nTrafi ona bezpośrednio do raportu Saldeo.")

    # -------------------------
    # Companies page (UoP/UZ/UoD)
    # -------------------------
    def create_companies_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(20, 20, 20, 20)

        header_layout = QHBoxLayout()
        header_layout.addWidget(QLabel("Lista firm i parametry obsługi"))
        btn_add_company = QPushButton("Dodaj firmę")
        btn_add_company.setStyleSheet("background-color: #3498db; color: white; font-weight: bold; padding: 8px 14px;")
        btn_add_company.clicked.connect(self.open_new_company_dialog)
        header_layout.addWidget(btn_add_company, alignment=Qt.AlignmentFlag.AlignRight)
        layout.addLayout(header_layout)

        self.table_firms = QTableWidget()
        self.table_firms.setColumnCount(10)
        self.table_firms.setHorizontalHeaderLabels([
            "ID", "Nazwa firmy", "NIP", "Miasto", "UoP", "UZ", "UoD", "Główny cennik", "Limit dokumentów", "Akcje"
        ])
        self.table_firms.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table_firms.hideColumn(0)
        layout.addWidget(self.table_firms)
        self.pages.addWidget(page)

    def refresh_firms_table(self):
        self.table_firms.setRowCount(0)
        conn = get_conn()
        cur = conn.cursor()

        if is_admin(self.pracownik_id):
            cur.execute("""
                SELECT id, nazwa, nip, miasto, uop, uz, uod, glowny_cennik, limit_dokumentow
                FROM firmy
                ORDER BY nazwa
            """)
        else:
            cur.execute("""
                SELECT DISTINCT f.id, f.nazwa, f.nip, f.miasto, f.uop, f.uz, f.uod, f.glowny_cennik, f.limit_dokumentow
                FROM firmy f
                WHERE f.id IN (
                    SELECT firma_id FROM pracownik_firma WHERE pracownik_id = ?
                )
                ORDER BY f.nazwa
            """, (self.pracownik_id,))

        rows = cur.fetchall()
        conn.close()

        for row_idx, row in enumerate(rows):
            self.table_firms.insertRow(row_idx)
            company_id, company_name, nip, city, uop, uz, uod, main_price, document_limit = row
            self.table_firms.setItem(row_idx, 0, QTableWidgetItem(str(company_id)))
            self.table_firms.setItem(row_idx, 1, QTableWidgetItem(company_name or ""))
            self.table_firms.setItem(row_idx, 2, QTableWidgetItem(nip or ""))
            self.table_firms.setItem(row_idx, 3, QTableWidgetItem(city or ""))

            spin_uop = QSpinBox()
            spin_uop.setMaximum(999999)
            spin_uop.setValue(parse_int_value(uop, 0))
            spin_uop.valueChanged.connect(lambda value, cid=company_id: self._update_company_field(cid, "uop", value))
            self.table_firms.setCellWidget(row_idx, 4, spin_uop)

            spin_uz = QSpinBox()
            spin_uz.setMaximum(999999)
            spin_uz.setValue(parse_int_value(uz, 0))
            spin_uz.valueChanged.connect(lambda value, cid=company_id: self._update_company_field(cid, "uz", value))
            self.table_firms.setCellWidget(row_idx, 5, spin_uz)

            spin_uod = QSpinBox()
            spin_uod.setMaximum(999999)
            spin_uod.setValue(parse_int_value(uod, 0))
            spin_uod.valueChanged.connect(lambda value, cid=company_id: self._update_company_field(cid, "uod", value))
            self.table_firms.setCellWidget(row_idx, 6, spin_uod)

            self.table_firms.setItem(row_idx, 7, QTableWidgetItem(main_price or ""))
            self.table_firms.setItem(row_idx, 8, QTableWidgetItem(str(parse_int_value(document_limit, 0))))

            action_widget = QWidget()
            action_layout = QHBoxLayout(action_widget)
            action_layout.setContentsMargins(4, 0, 4, 0)
            btn_edit = QPushButton("Edytuj")
            btn_edit.setStyleSheet("background-color: #2ecc71; color: white;")
            btn_edit.clicked.connect(lambda checked, cid=company_id: self.open_edit_company_dialog(cid))
            action_layout.addWidget(btn_edit)
            self.table_firms.setCellWidget(row_idx, 9, action_widget)

    def _update_company_field(self, company_id: int, field: str, value: int):
        conn = get_conn()
        cur = conn.cursor()
        cur.execute(f"UPDATE firmy SET {field} = ? WHERE id = ?", (value, company_id))
        conn.commit()
        conn.close()
        try:
            self.statusBar().showMessage("Zapisano.", 1200)
        except Exception:
            pass

    def get_company_details(self, company_id: int) -> Optional[Dict[str, Any]]:
        conn = get_conn()
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("SELECT * FROM firmy WHERE id = ?", (company_id,))
        row = cur.fetchone()
        if not row:
            conn.close()
            return None
        company_data = dict(row)
        company_data["assigned_employee_ids"] = get_company_assigned_employee_ids(cur, company_id)
        # load shareholders if table exists
        try:
            cur.execute("SELECT imie_nazwisko, procent FROM udzialowcy WHERE firma_id = ?", (company_id,))
            sh = [{"name": r[0], "share": r[1]} for r in cur.fetchall()]
            company_data["udzialowcy"] = sh
        except Exception:
            company_data["udzialowcy"] = company_data.get("udzialowcy", [])
        conn.close()
        return company_data

    def open_new_company_dialog(self):
        dialog = CompanyDialog(self.pracownik_id, parent=self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.save_company_to_db(dialog.get_payload())

    def open_edit_company_dialog(self, company_id: int):
        company_data = self.get_company_details(company_id)
        if not company_data:
            QMessageBox.warning(self, "Błąd", "Nie znaleziono firmy do edycji.")
            return
        dialog = CompanyDialog(self.pracownik_id, company_data=company_data, parent=self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.save_company_to_db(dialog.get_payload(), company_id=company_id)

    def save_company_to_db(self, payload: Dict[str, Any], company_id: Optional[int] = None):
        if not str(payload.get("nazwa", "")).strip():
            QMessageBox.warning(self, "Błąd", "Pole 'Nazwa firmy' jest wymagane.")
            return

        normalized_nip = normalize_identifier(payload.get("nip"), {10})
        if payload.get("nip") and len(normalized_nip) != 10:
            QMessageBox.warning(self, "Błąd", "Podany numer NIP jest nieprawidłowy.")
            return
        payload["nip"] = normalized_nip

        conn = get_conn()
        cur = conn.cursor()
        try:
            final_company_id = save_company(cur, payload, company_id=company_id)
            # zapis udziałowców (jeśli tabela istnieje)
            try:
                cur.execute("CREATE TABLE IF NOT EXISTS udzialowcy (id INTEGER PRIMARY KEY AUTOINCREMENT, firma_id INTEGER NOT NULL, imie_nazwisko TEXT NOT NULL, procent REAL NOT NULL, FOREIGN KEY(firma_id) REFERENCES firmy(id))")
                cur.execute("DELETE FROM udzialowcy WHERE firma_id = ?", (final_company_id,))
                for sh in payload.get("udzialowcy", []):
                    name = sh.get("name") or sh.get("imie_nazwisko")
                    pct = float(sh.get("share", 0))
                    cur.execute("INSERT INTO udzialowcy (firma_id, imie_nazwisko, procent) VALUES (?, ?, ?)", (final_company_id, name, pct))
            except Exception:
                pass

            conn.commit()
            self.refresh_firms_table()
            try:
                self.refresh_employee_combos()
            except Exception:
                pass
            QMessageBox.information(
                self,
                "Zapisano",
                "Dane firmy zostały zapisane." if company_id else f"Dodano nową firmę (ID: {final_company_id})."
            )
        except sqlite3.IntegrityError:
            QMessageBox.critical(self, "Błąd", "Podany numer NIP figuruje już w systemie!")
        finally:
            conn.close()

    # -------------------------
    # Employees page
    # -------------------------
    def create_employees_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(20, 20, 20, 20)

        lbl_title = QLabel("Zarządzanie pracownikami i uprawnieniami")
        lbl_title.setFont(QFont("Arial", 14))
        layout.addWidget(lbl_title)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.addWidget(QLabel("Pracownicy w systemie:"))

        self.table_employees = QTableWidget()
        self.table_employees.setColumnCount(6)
        self.table_employees.setHorizontalHeaderLabels(["ID", "Email", "Imię", "Nazwisko", "Aktywny", "Działania"])
        self.table_employees.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table_employees.hideColumn(0)
        left_layout.addWidget(self.table_employees)

        right_widget = QWidget()
        right_widget.setFixedWidth(350)
        right_layout = QVBoxLayout(right_widget)
        right_layout.addWidget(QLabel("Dodaj nowego pracownika"))

        form_layout = QFormLayout()
        self.emp_email = QLineEdit()
        self.emp_imie = QLineEdit()
        self.emp_nazwisko = QLineEdit()
        self.emp_haslo = QLineEdit()
        self.emp_haslo.setEchoMode(QLineEdit.EchoMode.Password)
        form_layout.addRow("Email:", self.emp_email)
        form_layout.addRow("Imię:", self.emp_imie)
        form_layout.addRow("Nazwisko:", self.emp_nazwisko)
        form_layout.addRow("Hasło:", self.emp_haslo)
        right_layout.addLayout(form_layout)

        btn_add_emp = QPushButton("Dodaj pracownika")
        btn_add_emp.setStyleSheet("background-color: #3498db; color: white; font-weight: bold; padding: 10px;")
        btn_add_emp.clicked.connect(self.add_employee)
        right_layout.addWidget(btn_add_emp)

        right_layout.addWidget(QLabel("Przypisz do firmy"))
        assign_form = QFormLayout()
        self.assign_employee_combo = QComboBox()
        self.assign_firma_combo = QComboBox()
        self.assign_rola_combo = QComboBox()
        self.assign_rola_combo.addItems(["księgowa", "kadrowa", "manager", "admin", "opiekun"])
        assign_form.addRow("Pracownik:", self.assign_employee_combo)
        assign_form.addRow("Firma:", self.assign_firma_combo)
        assign_form.addRow("Rola:", self.assign_rola_combo)
        right_layout.addLayout(assign_form)

        btn_assign = QPushButton("Przypisz pracownika")
        btn_assign.setStyleSheet("background-color: #27ae60; color: white; font-weight: bold; padding: 10px;")
        btn_assign.clicked.connect(self.assign_employee_to_company)
        right_layout.addWidget(btn_assign)

        right_layout.addStretch()
        splitter.addWidget(left_widget)
        splitter.addWidget(right_widget)
        layout.addWidget(splitter)
        self.pages.addWidget(page)

    def refresh_employees_table(self):
        self.table_employees.setRowCount(0)
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("SELECT id, email, imie, nazwisko, aktywny FROM pracownicy")
        rows = cur.fetchall()
        conn.close()

        for row_idx, row in enumerate(rows):
            self.table_employees.insertRow(row_idx)
            emp_id, email, imie, nazwisko, aktywny = row
            self.table_employees.setItem(row_idx, 0, QTableWidgetItem(str(emp_id)))
            self.table_employees.setItem(row_idx, 1, QTableWidgetItem(email or ""))
            self.table_employees.setItem(row_idx, 2, QTableWidgetItem(imie or ""))
            self.table_employees.setItem(row_idx, 3, QTableWidgetItem(nazwisko or ""))
            self.table_employees.setItem(row_idx, 4, QTableWidgetItem("Tak" if aktywny else "Nie"))

            btn_delete = QPushButton("Usuń")
            btn_delete.setStyleSheet("background-color: #e74c3c; color: white;")
            btn_delete.clicked.connect(lambda checked, eid=emp_id: self.delete_employee(eid))
            self.table_employees.setCellWidget(row_idx, 5, btn_delete)

        try:
            self.refresh_employee_combos()
        except Exception:
            pass

    def refresh_employee_combos(self):
        self.assign_employee_combo.clear()
        self.assign_firma_combo.clear()

        conn = get_conn()
        cur = conn.cursor()
        cur.execute("SELECT id, imie || ' ' || nazwisko FROM pracownicy WHERE aktywny = 1")
        for emp_id, name in cur.fetchall():
            self.assign_employee_combo.addItem(f"{name} ({emp_id})", emp_id)

        cur.execute("SELECT id, nazwa FROM firmy ORDER BY nazwa")
        for cid, cname in cur.fetchall():
            self.assign_firma_combo.addItem(cname, cid)
        conn.close()

    def add_employee(self):
        email = self.emp_email.text().strip()
        imie = self.emp_imie.text().strip()
        nazwisko = self.emp_nazwisko.text().strip()
        haslo = self.emp_haslo.text()
        if not email or not haslo:
            QMessageBox.warning(self, "Błąd", "Email i hasło są wymagane.")
            return
        result = register_user(email, haslo, imie=imie, nazwisko=nazwisko, is_admin_flag=False)
        ok = bool(result[0]) if isinstance(result, tuple) else bool(result)
        if ok:
            QMessageBox.information(self, "Dodano", "Pracownik został dodany.")
            self.refresh_employees_table()
        else:
            QMessageBox.warning(self, "Błąd", "Nie udało się dodać pracownika.")

    def assign_employee_to_company(self):
        emp_id = self.assign_employee_combo.currentData()
        firma_id = self.assign_firma_combo.currentData()
        rola = self.assign_rola_combo.currentText()
        if not emp_id or not firma_id:
            QMessageBox.warning(self, "Błąd", "Wybierz pracownika i firmę.")
            return
        conn = get_conn()
        cur = conn.cursor()
        try:
            cur.execute("SELECT id FROM role WHERE nazwa = ?", (rola,))
            r = cur.fetchone()
            if r:
                rola_id = r[0]
                cur.execute("INSERT OR IGNORE INTO pracownik_firma (pracownik_id, firma_id, rola_id) VALUES (?, ?, ?)", (emp_id, firma_id, rola_id))
            else:
                cur.execute("INSERT OR IGNORE INTO pracownik_firma (pracownik_id, firma_id, rola) VALUES (?, ?, ?)", (emp_id, firma_id, rola))
            conn.commit()
            QMessageBox.information(self, "Przypisano", "Pracownik przypisany do firmy.")
            self.refresh_employees_table()
        except Exception as e:
            QMessageBox.critical(self, "Błąd", f"Nie udało się przypisać pracownika: {e}")
        finally:
            conn.close()

    def delete_employee(self, emp_id: int):
        confirm = QMessageBox.question(self, "Usuń pracownika", "Czy na pewno chcesz usunąć pracownika?")
        if confirm != QMessageBox.StandardButton.Yes:
            return
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("DELETE FROM pracownicy WHERE id = ?", (emp_id,))
        conn.commit()
        conn.close()
        self.refresh_employees_table()

    # -------------------------
    # Email / Chat placeholders and integration
    # -------------------------
    def create_email_placeholder(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(QLabel("Email - integracja niedostępna"))
        self.pages.addWidget(page)

    def create_chat_placeholder(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(QLabel("Chat - integracja niedostępna"))
        self.pages.addWidget(page)

    # Chat page (uses provided ChatWidget if available)
    def create_chat_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(20, 20, 20, 20)

        lbl_title = QLabel("Chat wewnętrzny")
        lbl_title.setFont(QFont("Arial", 14, QFont.Weight.Bold))
        layout.addWidget(lbl_title)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        # Left: conversations
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.addWidget(QLabel("Konwersacje:"))

        self.chat_conversations = QListWidget()
        self.chat_conversations.itemSelectionChanged.connect(self.on_chat_selected)
        left_layout.addWidget(self.chat_conversations)

        btn_new_chat = QPushButton("➕ Nowa konwersacja")
        btn_new_chat.setStyleSheet("background-color: #27ae60; color: white; font-weight: bold;")
        btn_new_chat.clicked.connect(self.create_new_conversation)
        left_layout.addWidget(btn_new_chat)

        left_widget.setFixedWidth(300)
        splitter.addWidget(left_widget)

        # Right: messages
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.addWidget(QLabel("Wiadomości:"))

        self.chat_messages = QTextEdit()
        self.chat_messages.setReadOnly(True)
        right_layout.addWidget(self.chat_messages)

        bottom_layout = QHBoxLayout()
        self.chat_input = QLineEdit()
        self.chat_input.setPlaceholderText("Wpisz wiadomość...")
        bottom_layout.addWidget(self.chat_input)

        btn_send = QPushButton("Wyślij")
        btn_send.setStyleSheet("background-color: #3498db; color: white; font-weight: bold;")
        btn_send.clicked.connect(self.send_chat_message)
        bottom_layout.addWidget(btn_send)

        right_layout.addLayout(bottom_layout)
        splitter.addWidget(right_widget)

        layout.addWidget(splitter)
        self.pages.addWidget(page)

        # Odśwież co 2 sekundy
        self.chat_timer = QTimer()
        self.chat_timer.timeout.connect(self.refresh_chat_display)
        self.chat_timer.start(2000)

    # Chat helper methods (refresh_chat_conversations, refresh_chat_display, on_chat_selected,
    # send_chat_message, create_new_conversation) are expected to be present (from earlier code).
    # If ChatWidget is used instead, it will be integrated automatically.

    # -------------------------
    # Saldeo export
    # -------------------------
    def create_saldeo_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.addWidget(QLabel("Generator struktury arkusza integracyjnego SaldeoSMART"))
        btn_export = QPushButton("📁 Wygeneruj i zapisz plik Excel (*.xlsx)")
        btn_export.setStyleSheet("background-color: #2ecc71; color: white; font-size: 15px; padding: 15px; font-weight: bold;")
        btn_export.clicked.connect(self.on_export_saldeo)
        layout.addWidget(btn_export)
        layout.addStretch()
        self.pages.addWidget(page)

    def on_export_saldeo(self):
        filepath, _ = QFileDialog.getSaveFileName(self, "Wskaż miejsce zapisu szablonu Saldeo", "", "Pliki Excel (*.xlsx)")
        if not filepath:
            return
        try:
            if SaldeoService:
                SaldeoService.export_monthly_report(filepath)
                QMessageBox.information(self, "Sukces eksportu", f"Plik integracyjny został wygenerowany poprawnie.\nŚcieżka: {filepath}")
                return

            conn = get_conn()
            cur = conn.cursor()
            cur.execute("SELECT id, nazwa, nip, miasto, uop, uz, uod FROM firmy ORDER BY nazwa")
            firms = cur.fetchall()

            wb = openpyxl.Workbook()
            ws = wb.active
            ws.title = "SaldeoExport"
            ws.append(["Firma / dokument", "Typ", "Cena netto", "Data wystawienia", "Dodatkowe pozycje"])

            for f in firms:
                fid, fname, fnip, fcity, uop, uz, uod = f
                ws.append([f"Firma: {fname} (NIP: {fnip})", "", "", "", ""])
                cur.execute("SELECT numer, typ, cena_netto, data_wyst FROM dokumenty WHERE firma_id = ? ORDER BY data_wyst", (fid,))
                docs = cur.fetchall()
                for d in docs:
                    numer, typ, cena_netto, data_wyst = d
                    positions = []
                    if uop and int(uop) > 0:
                        positions.append(f"UoP x{uop}")
                    if uz and int(uz) > 0:
                        positions.append(f"UZ x{uz}")
                    if uod and int(uod) > 0:
                        positions.append(f"UoD x{uod}")
                    positions_text = "; ".join(positions)
                    ws.append([f"  {numer}", typ, cena_netto, data_wyst, positions_text])
                ws.append(["", "", "", "", ""])

            wb.save(filepath)
            conn.close()
            QMessageBox.information(self, "Sukces eksportu", f"Plik integracyjny został wygenerowany poprawnie.\nŚcieżka: {filepath}")
        except Exception as e:
            QMessageBox.critical(self, "Błąd zapisu", f"Nie można było utworzyć pliku Excel. Powód:\n{str(e)}")

    # -------------------------
    # Email helpers (required by EmailUIMixin)
    # -------------------------
    def _init_email_auto_sync(self, interval_minutes: int = 5, start_immediately: bool = False):
        if not hasattr(self, "email_auto_sync_timer") or self.email_auto_sync_timer is None:
            self.email_auto_sync_timer = QTimer(self)
            self.email_auto_sync_timer.timeout.connect(lambda: self._trigger_email_sync())
            self.email_auto_sync_timer.setInterval(max(1, interval_minutes) * 60 * 1000)
        if start_immediately:
            self._trigger_email_sync()
        self.email_auto_sync_timer.start()

    def _trigger_email_sync(self):
        if not hasattr(self, "thread_pool") or self.thread_pool is None:
            from PyQt6.QtCore import QThreadPool
            self.thread_pool = QThreadPool.globalInstance()
        runnable = SyncRunnable(self)
        runnable.signals.progress.connect(lambda p: self.statusBar().showMessage(f"Synchronizacja: {p}%"))
        runnable.signals.error.connect(lambda msg: QMessageBox.critical(self, "Błąd synchronizacji", msg))
        runnable.signals.finished.connect(lambda ok: (
            self.statusBar().showMessage("Synchronizacja zakończona" if ok else "Synchronizacja nie powiodła się", 3000),
            self.refresh_email_list() if ok else None
        ))
        self.thread_pool.start(runnable)

    def refresh_email_list(self):
        try:
            konto_id = self.email_account_combo.currentData()
            folder = self.email_folder_combo.currentText() if hasattr(self, "email_folder_combo") else "INBOX"
        except Exception:
            konto_id = None
            folder = "INBOX"

        self.table_email_list.setRowCount(0)
        if not konto_id:
            return

        conn = get_conn()
        cur = conn.cursor()
        try:
            cur.execute("""
                SELECT id, od, temat, data_otrzymania, przeczytana
                FROM wiadomosci
                WHERE konto_id = ? AND folder = ?
                ORDER BY data_otrzymania DESC
                LIMIT 500
            """, (konto_id, folder))
            rows = cur.fetchall()
        finally:
            conn.close()

        for row_idx, row in enumerate(rows):
            msg_id, od, temat, data_otrzymania, przeczytana = row
            self.table_email_list.insertRow(row_idx)
            self.table_email_list.setItem(row_idx, 0, QTableWidgetItem(str(msg_id)))
            self.table_email_list.setItem(row_idx, 1, QTableWidgetItem(od or ""))
            self.table_email_list.setItem(row_idx, 2, QTableWidgetItem(temat or ""))
            self.table_email_list.setItem(row_idx, 3, QTableWidgetItem(str(data_otrzymania or "")))
            self.table_email_list.setItem(row_idx, 4, QTableWidgetItem("Tak" if przeczytana else "Nie"))
        try:
            self.table_email_list.resizeColumnsToContents()
        except Exception:
            pass

    def reply_to_email(self):
        row = self.table_email_list.currentRow()
        if row < 0:
            QMessageBox.warning(self, "Błąd", "Wybierz wiadomość do odpowiedzi.")
            return
        msg_id_item = self.table_email_list.item(row, 0)
        if not msg_id_item:
            QMessageBox.warning(self, "Błąd", "Brak identyfikatora wiadomości.")
            return
        msg_id = int(msg_id_item.text())

        conn = get_conn()
        cur = conn.cursor()
        try:
            cur.execute("SELECT konto_id, od, temat FROM wiadomosci WHERE id = ?", (msg_id,))
            row = cur.fetchone()
        finally:
            conn.close()
        if not row:
            QMessageBox.critical(self, "Błąd", "Nie można pobrać wiadomości.")
            return
        konto_id, od, temat = row

        from PyQt6.QtWidgets import QInputDialog
        tresc, ok = QInputDialog.getMultiLineText(self, "Odpowiedź", f"Odpowiedź do: {od}\nTemat: Re: {temat}", "")
        if not ok or not tresc.strip():
            return

        ok_send, msg = EmailSyncer.send_email(konto_id, od, f"Re: {temat}", tresc)
        if ok_send:
            QMessageBox.information(self, "Wysłano", "Odpowiedź została wysłana.")
        else:
            QMessageBox.critical(self, "Błąd wysyłki", f"Nie udało się wysłać: {msg}")

    def forward_email(self):
        row = self.table_email_list.currentRow()
        if row < 0:
            QMessageBox.warning(self, "Błąd", "Wybierz wiadomość do przesłania.")
            return
        msg_id_item = self.table_email_list.item(row, 0)
        if not msg_id_item:
            QMessageBox.warning(self, "Błąd", "Brak identyfikatora wiadomości.")
            return
        msg_id = int(msg_id_item.text())

        conn = get_conn()
        cur = conn.cursor()
        try:
            cur.execute("SELECT konto_id, od, temat, tresc, raw_msg FROM wiadomosci WHERE id = ?", (msg_id,))
            row = cur.fetchone()
        finally:
            conn.close()
        if not row:
            QMessageBox.critical(self, "Błąd", "Nie można pobrać wiadomości.")
            return
        konto_id, od, temat, tresc, raw = row

        from PyQt6.QtWidgets import QInputDialog
        to_addr, ok = QInputDialog.getText(self, "Prześlij dalej", "Adres odbiorcy:")
        if not ok or not to_addr.strip():
            return

        body = f"Przesyłam wiadomość od: {od}\nTemat: {temat}\n\n{tresc or ''}"
        ok_send, msg = EmailSyncer.send_email(konto_id, to_addr.strip(), f"Fwd: {temat}", body)
        if ok_send:
            QMessageBox.information(self, "Wysłano", "Wiadomość została przesłana.")
        else:
            QMessageBox.critical(self, "Błąd wysyłki", f"Nie udało się wysłać: {msg}")

    def sync_all_emails(self):
        try:
            conn = get_conn()
            cur = conn.cursor()
            cur.execute("SELECT id FROM konta_email WHERE pracownik_id = ? AND aktywna = 1", (self.pracownik_id,))
            accounts = [r[0] for r in cur.fetchall()]
            conn.close()

            if not accounts:
                return True

            all_ok = True
            for konto_id in accounts:
                ok = EmailSyncer.sync_emails(konto_id, self.pracownik_id)
                if not ok:
                    all_ok = False
            try:
                self.refresh_email_list()
                self.refresh_email_accounts()
                self.refresh_email_filters()
            except Exception:
                pass
            return all_ok
        except Exception as e:
            raise
