from typing import Optional

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QLabel, QTabWidget, QWidget, QHBoxLayout,
    QTableWidget, QTableWidgetItem, QPushButton, QHeaderView, QMessageBox,
    QLineEdit, QSpinBox, QComboBox, QFileDialog, QListWidget, QListWidgetItem
)
from app.utils.importers import import_cennik_from_excel
from app.admin.global_settings_logic import GlobalSettingsLogic


class GlobalSettingsPage(QWidget):
    def __init__(self, pracownik_id: int, is_admin: bool, parent=None):
        super().__init__(parent)
        self.pracownik_id = pracownik_id
        self.is_admin = is_admin
        self.logic = GlobalSettingsLogic()

        self.setWindowTitle("Panel administratora – ustawienia globalne")
        self.resize(1000, 750)

        main_layout = QVBoxLayout(self)
        title = QLabel("Panel administratora – ustawienia globalne systemu")
        title.setFont(QFont("Arial", 14, QFont.Weight.Bold))
        main_layout.addWidget(title)

        perms = self.logic.get_user_permissions(self.pracownik_id)
        if not perms["can_access_admin_settings"]:
            main_layout.addWidget(QLabel("Brak uprawnień administratora."))
            return

        self.tabs = QTabWidget()
        main_layout.addWidget(self.tabs)

        self._create_users_tab()
        self._create_roles_tab()
        self._create_email_tab()
        self._create_pricing_tab()
        self._create_pricing_tab_kadry()
        self._create_extra_services_tab()
        self._create_companies_tab()
        self._create_logs_tab()
        self._create_system_tab()

    # UŻYTKOWNICY

    def _create_users_tab(self):
        page = QWidget()
        layout = QVBoxLayout(page)

        self.users_table = QTableWidget(0, 5)
        self.users_table.setHorizontalHeaderLabels(["ID", "Email", "Imię", "Nazwisko", "Aktywny"])
        self.users_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.users_table)

        btn_row = QHBoxLayout()
        btn_add = QPushButton("Dodaj użytkownika")
        btn_add.clicked.connect(self._add_user_dialog)
        btn_block = QPushButton("Zablokuj/odblokuj")
        btn_block.clicked.connect(self._toggle_user_active)
        btn_delete = QPushButton("Usuń")
        btn_delete.clicked.connect(self._delete_user)
        btn_refresh = QPushButton("Odśwież")
        btn_refresh.clicked.connect(self._refresh_users)
        btn_row.addWidget(btn_add)
        btn_row.addWidget(btn_block)
        btn_row.addWidget(btn_delete)
        btn_row.addWidget(btn_refresh)
        btn_row.addStretch(1)
        layout.addLayout(btn_row)

        self.tabs.addTab(page, "Użytkownicy")
        self._refresh_users()

    def _refresh_users(self):
        rows = self.logic.get_users()
        self.users_table.setRowCount(0)
        for r in rows:
            row_idx = self.users_table.rowCount()
            self.users_table.insertRow(row_idx)
            for col_idx, key in enumerate(["id", "email", "imie", "nazwisko", "aktywny"]):
                val = r[key]
                item = QTableWidgetItem(str(val))
                if key == "aktywny":
                    item.setText("TAK" if val else "NIE")
                self.users_table.setItem(row_idx, col_idx, item)

    def _add_user_dialog(self):
        dlg = QDialog(self)
        dlg.setWindowTitle("Dodaj użytkownika")
        lay = QVBoxLayout(dlg)

        email = QLineEdit()
        email.setPlaceholderText("Email")
        imie = QLineEdit()
        imie.setPlaceholderText("Imię")
        nazwisko = QLineEdit()
        nazwisko.setPlaceholderText("Nazwisko")
        haslo = QLineEdit()
        haslo.setPlaceholderText("Hasło (tymczasowe)")
        haslo.setEchoMode(QLineEdit.EchoMode.Password)

        lay.addWidget(email)
        lay.addWidget(imie)
        lay.addWidget(nazwisko)
        lay.addWidget(haslo)

        btns = QHBoxLayout()
        btn_ok = QPushButton("Zapisz")
        btn_cancel = QPushButton("Anuluj")
        btn_ok.clicked.connect(dlg.accept)
        btn_cancel.clicked.connect(dlg.reject)
        btns.addWidget(btn_ok)
        btns.addWidget(btn_cancel)
        lay.addLayout(btns)

        if dlg.exec() == QDialog.DialogCode.Accepted:
            try:
                self.logic.create_user(
                    email=email.text().strip(),
                    imie=imie.text().strip(),
                    nazwisko=nazwisko.text().strip(),
                    haslo=haslo.text().strip(),
                )
                self._refresh_users()
            except Exception as e:
                QMessageBox.critical(self, "Błąd", f"Nie udało się dodać użytkownika: {e}")

    def _get_selected_user_id(self) -> Optional[int]:
        row = self.users_table.currentRow()
        if row < 0:
            return None
        item = self.users_table.item(row, 0)
        if not item:
            return None
        try:
            return int(item.text())
        except ValueError:
            return None

    def _toggle_user_active(self):
        uid = self._get_selected_user_id()
        if uid is None:
            QMessageBox.information(self, "Użytkownicy", "Wybierz użytkownika.")
            return
        row = self.users_table.currentRow()
        active_item = self.users_table.item(row, 4)
        is_active = active_item.text() == "TAK"
        try:
            self.logic.set_user_active(uid, not is_active)
            self._refresh_users()
        except Exception as e:
            QMessageBox.critical(self, "Błąd", f"Nie udało się zmienić statusu: {e}")

    def _delete_user(self):
        uid = self._get_selected_user_id()
        if uid is None:
            QMessageBox.information(self, "Użytkownicy", "Wybierz użytkownika.")
            return
        if QMessageBox.question(self, "Usuń użytkownika",
                                "Czy na pewno chcesz usunąć tego użytkownika?",
                                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
                                ) == QMessageBox.StandardButton.Yes:
            try:
                self.logic.delete_user(uid)
                self._refresh_users()
            except Exception as e:
                QMessageBox.critical(self, "Błąd", f"Nie udało się usunąć użytkownika: {e}")

    # ROLE

    def _create_roles_tab(self):
        page = QWidget()
        layout = QVBoxLayout(page)

        self.roles_table = QTableWidget(0, 5)
        self.roles_table.setHorizontalHeaderLabels([
            "ID", "Nazwa roli", "Faktury", "Panel admina", "Użytkownicy"
        ])
        self.roles_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.roles_table)

        btn_row = QHBoxLayout()
        btn_add = QPushButton("Dodaj rolę")
        btn_add.clicked.connect(self._add_role_dialog)
        btn_edit = QPushButton("Edytuj uprawnienia")
        btn_edit.clicked.connect(self._edit_role_permissions)
        btn_delete = QPushButton("Usuń rolę")
        btn_delete.clicked.connect(self._delete_role)
        btn_refresh = QPushButton("Odśwież")
        btn_refresh.clicked.connect(self._refresh_roles)
        btn_row.addWidget(btn_add)
        btn_row.addWidget(btn_edit)
        btn_row.addWidget(btn_delete)
        btn_row.addWidget(btn_refresh)
        btn_row.addStretch(1)
        layout.addLayout(btn_row)

        self.tabs.addTab(page, "Role")
        self._refresh_roles()

    def _refresh_roles(self):
        rows = self.logic.get_roles()
        self.roles_table.setRowCount(0)
        for r in rows:
            row_idx = self.roles_table.rowCount()
            self.roles_table.insertRow(row_idx)
            self.roles_table.setItem(row_idx, 0, QTableWidgetItem(str(r["id"])))
            self.roles_table.setItem(row_idx, 1, QTableWidgetItem(r["nazwa"]))
            self.roles_table.setItem(row_idx, 2, QTableWidgetItem("TAK" if r["can_view_invoices"] else "NIE"))
            self.roles_table.setItem(row_idx, 3, QTableWidgetItem("TAK" if r["can_access_admin_settings"] else "NIE"))
            self.roles_table.setItem(row_idx, 4, QTableWidgetItem("TAK" if r["can_manage_users"] else "NIE"))

    def _add_role_dialog(self):
        dlg = QDialog(self)
        dlg.setWindowTitle("Dodaj rolę")
        lay = QVBoxLayout(dlg)

        name = QLineEdit()
        name.setPlaceholderText("Nazwa roli")
        lay.addWidget(name)

        cb_invoices = QComboBox()
        cb_invoices.addItems(["Brak dostępu do faktur", "Dostęp do faktur"])
        cb_admin = QComboBox()
        cb_admin.addItems(["Brak dostępu do panelu admina", "Dostęp do panelu admina"])
        cb_users = QComboBox()
        cb_users.addItems(["Brak zarządzania użytkownikami", "Może zarządzać użytkownikami"])

        lay.addWidget(cb_invoices)
        lay.addWidget(cb_admin)
        lay.addWidget(cb_users)

        btns = QHBoxLayout()
        btn_ok = QPushButton("Zapisz")
        btn_cancel = QPushButton("Anuluj")
        btn_ok.clicked.connect(dlg.accept)
        btn_cancel.clicked.connect(dlg.reject)
        btns.addWidget(btn_ok)
        btns.addWidget(btn_cancel)
        lay.addLayout(btns)

        if dlg.exec() == QDialog.DialogCode.Accepted:
            try:
                self.logic.add_role(
                    nazwa=name.text().strip(),
                    can_view_invoices=cb_invoices.currentIndex() == 1,
                    can_access_admin_settings=cb_admin.currentIndex() == 1,
                    can_manage_users=cb_users.currentIndex() == 1,
                )
                self._refresh_roles()
            except Exception as e:
                QMessageBox.critical(self, "Błąd", f"Nie udało się dodać roli: {e}")

    def _get_selected_role_id(self) -> Optional[int]:
        row = self.roles_table.currentRow()
        if row < 0:
            return None
        item = self.roles_table.item(row, 0)
        if not item:
            return None
        try:
            return int(item.text())
        except ValueError:
            return None

    def _edit_role_permissions(self):
        rid = self._get_selected_role_id()
        if rid is None:
            QMessageBox.information(self, "Role", "Wybierz rolę.")
            return

        row = self.roles_table.currentRow()
        name_item = self.roles_table.item(row, 1)
        inv_item = self.roles_table.item(row, 2)
        adm_item = self.roles_table.item(row, 3)
        usr_item = self.roles_table.item(row, 4)

        dlg = QDialog(self)
        dlg.setWindowTitle(f"Uprawnienia roli: {name_item.text()}")
        lay = QVBoxLayout(dlg)

        cb_invoices = QComboBox()
        cb_invoices.addItems(["Brak dostępu do faktur", "Dostęp do faktur"])
        cb_invoices.setCurrentIndex(1 if inv_item.text() == "TAK" else 0)

        cb_admin = QComboBox()
        cb_admin.addItems(["Brak dostępu do panelu admina", "Dostęp do panelu admina"])
        cb_admin.setCurrentIndex(1 if adm_item.text() == "TAK" else 0)

        cb_users = QComboBox()
        cb_users.addItems(["Brak zarządzania użytkownikami", "Może zarządzać użytkownikami"])
        cb_users.setCurrentIndex(1 if usr_item.text() == "TAK" else 0)

        lay.addWidget(cb_invoices)
        lay.addWidget(cb_admin)
        lay.addWidget(cb_users)

        btns = QHBoxLayout()
        btn_ok = QPushButton("Zapisz")
        btn_cancel = QPushButton("Anuluj")
        btn_ok.clicked.connect(dlg.accept)
        btn_cancel.clicked.connect(dlg.reject)
        btns.addWidget(btn_ok)
        btns.addWidget(btn_cancel)
        lay.addLayout(btns)

        if dlg.exec() == QDialog.DialogCode.Accepted:
            try:
                self.logic.update_role_permissions(
                    rola_id=rid,
                    can_view_invoices=cb_invoices.currentIndex() == 1,
                    can_access_admin_settings=cb_admin.currentIndex() == 1,
                    can_manage_users=cb_users.currentIndex() == 1,
                )
                self._refresh_roles()
            except Exception as e:
                QMessageBox.critical(self, "Błąd", f"Nie udało się zaktualizować roli: {e}")

    def _delete_role(self):
        rid = self._get_selected_role_id()
        if rid is None:
            QMessageBox.information(self, "Role", "Wybierz rolę.")
            return
        if QMessageBox.question(self, "Usuń rolę",
                                "Czy na pewno chcesz usunąć tę rolę?",
                                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
                                ) == QMessageBox.StandardButton.Yes:
            try:
                self.logic.delete_role(rid)
                self._refresh_roles()
            except Exception as e:
                QMessageBox.critical(self, "Błąd", f"Nie udało się usunąć roli: {e}")

    # KONTA EMAIL + PRZYPISANIA

    def _create_email_tab(self):
        page = QWidget()
        layout = QVBoxLayout(page)

        self.email_table = QTableWidget(0, 5)
        self.email_table.setHorizontalHeaderLabels(["ID", "Pracownik ID", "Email", "Serwer IMAP", "Serwer SMTP"])
        self.email_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.email_table)

        self.assign_table = QTableWidget(0, 4)
        self.assign_table.setHorizontalHeaderLabels(["Konto ID", "Email konta", "Pracownik ID", "Email pracownika"])
        self.assign_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.assign_table)

        btn_row = QHBoxLayout()
        btn_add = QPushButton("Dodaj konto email")
        btn_add.clicked.connect(self._add_email_dialog)
        btn_delete = QPushButton("Usuń konto")
        btn_delete.clicked.connect(self._delete_email_account)
        btn_assign = QPushButton("Przypisz konto do użytkownika")
        btn_assign.clicked.connect(self._assign_email_dialog)
        btn_unassign = QPushButton("Usuń przypisanie")
        btn_unassign.clicked.connect(self._unassign_email)
        btn_refresh = QPushButton("Odśwież")
        btn_refresh.clicked.connect(self._refresh_email)
        btn_row.addWidget(btn_add)
        btn_row.addWidget(btn_delete)
        btn_row.addWidget(btn_assign)
        btn_row.addWidget(btn_unassign)
        btn_row.addWidget(btn_refresh)
        btn_row.addStretch(1)
        layout.addLayout(btn_row)

        self.tabs.addTab(page, "Konta email")
        self._refresh_email()

    def _refresh_email(self):
        rows = self.logic.get_email_accounts()
        self.email_table.setRowCount(0)
        for r in rows:
            row_idx = self.email_table.rowCount()
            self.email_table.insertRow(row_idx)
            for col_idx, key in enumerate(["id", "pracownik_id", "email", "serwer_imap", "serwer_smtp"]):
                value = r[key] if key in r.keys() else ""
                self.email_table.setItem(row_idx, col_idx, QTableWidgetItem(str(value)))

        assigns = self.logic.get_email_assignments()
        self.assign_table.setRowCount(0)
        for r in assigns:
            row_idx = self.assign_table.rowCount()
            self.assign_table.insertRow(row_idx)
            self.assign_table.setItem(row_idx, 0, QTableWidgetItem(str(r["konto_id"])))
            self.assign_table.setItem(row_idx, 1, QTableWidgetItem(r["email"]))
            self.assign_table.setItem(row_idx, 2, QTableWidgetItem(str(r["pracownik_id"])))
            self.assign_table.setItem(row_idx, 3, QTableWidgetItem(r["pracownik_email"]))

    def _add_email_dialog(self):
        dlg = QDialog(self)
        dlg.setWindowTitle("Dodaj konto email")
        lay = QVBoxLayout(dlg)

        # --- Użytkownicy (multi-select) ---
        lay.addWidget(QLabel("Wybierz użytkowników (możesz zaznaczyć wielu):"))

        users_list = QListWidget()
        users_list.setSelectionMode(QListWidget.SelectionMode.NoSelection)

        users = self.logic.get_users()
        for u in users:
            item = QListWidgetItem(f"{u['id']} – {u['email']}")
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Unchecked)
            item.setData(Qt.ItemDataRole.UserRole, u["id"])
            users_list.addItem(item)

        lay.addWidget(users_list)

        # --- Email + hasło ---
        email = QLineEdit()
        email.setPlaceholderText("Adres email")
        haslo = QLineEdit()
        haslo.setPlaceholderText("Hasło")
        haslo.setEchoMode(QLineEdit.EchoMode.Password)

        lay.addWidget(email)
        lay.addWidget(haslo)

        # --- IMAP ---
        lay.addWidget(QLabel("Serwer przychodzący (IMAP)"))
        imap_server = QLineEdit()
        imap_server.setPlaceholderText("np. imap.gmail.com")
        lay.addWidget(imap_server)

        imap_ssl = QComboBox()
        imap_ssl.addItems(["SSL/TLS", "STARTTLS", "Brak"])
        lay.addWidget(imap_ssl)

        imap_port = QSpinBox()
        imap_port.setMaximum(99999)
        imap_port.setValue(993)  # domyślny SSL
        lay.addWidget(imap_port)

        # --- SMTP ---
        lay.addWidget(QLabel("Serwer wychodzący (SMTP)"))
        smtp_server = QLineEdit()
        smtp_server.setPlaceholderText("np. smtp.gmail.com")
        lay.addWidget(smtp_server)

        smtp_ssl = QComboBox()
        smtp_ssl.addItems(["SSL/TLS", "STARTTLS", "Brak"])
        lay.addWidget(smtp_ssl)

        smtp_port = QSpinBox()
        smtp_port.setMaximum(99999)
        smtp_port.setValue(465)  # domyślny SSL
        lay.addWidget(smtp_port)

        # --- Przyciski ---
        btns = QHBoxLayout()
        btn_ok = QPushButton("Zapisz")
        btn_cancel = QPushButton("Anuluj")
        btn_ok.clicked.connect(dlg.accept)
        btn_cancel.clicked.connect(dlg.reject)
        btns.addWidget(btn_ok)
        btns.addWidget(btn_cancel)
        lay.addLayout(btns)

        if dlg.exec() == QDialog.DialogCode.Accepted:
            # Pobranie zaznaczonych użytkowników
            selected_users = []
            for i in range(users_list.count()):
                item = users_list.item(i)
                if item.checkState() == Qt.CheckState.Checked:
                    selected_users.append(item.data(Qt.ItemDataRole.UserRole))

            if not selected_users:
                QMessageBox.warning(self, "Błąd", "Musisz wybrać co najmniej jednego użytkownika.")
                return

            try:
                # Dodajemy konto email
                konto_id = self.logic.add_email_account(
                    email.text().strip(),
                    imap_server.text().strip(),
                    smtp_server.text().strip(),
                    haslo.text().strip(),
                    imap_port.value(),
                    smtp_port.value(),
                    imap_ssl.currentText(),
                    smtp_ssl.currentText(),
                )

                # Przypisujemy konto do wielu użytkowników
                for uid in selected_users:
                    self.logic.assign_email_to_employee(konto_id, uid)

                self._refresh_email()
                QMessageBox.information(self, "Sukces", "Konto email zostało dodane i przypisane.")
            except Exception as e:
                QMessageBox.critical(self, "Błąd", f"Nie udało się dodać konta email: {e}")

    def _delete_email_account(self):
        row = self.email_table.currentRow()
        if row < 0:
            QMessageBox.information(self, "Konta email", "Wybierz konto.")
            return
        item = self.email_table.item(row, 0)
        if not item:
            return
        try:
            kid = int(item.text())
        except ValueError:
            return
        if QMessageBox.question(self, "Usuń konto email",
                                "Czy na pewno chcesz usunąć to konto?",
                                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
                                ) == QMessageBox.StandardButton.Yes:
            try:
                self.logic.delete_email_account(kid)
                self._refresh_email()
            except Exception as e:
                QMessageBox.critical(self, "Błąd", f"Nie udało się usunąć konta email: {e}")

    def _assign_email_dialog(self):
        dlg = QDialog(self)
        dlg.setWindowTitle("Przypisz konto email do użytkownika")
        lay = QVBoxLayout(dlg)

        konto_id_spin = QSpinBox()
        konto_id_spin.setMaximum(999999)
        pracownik_id_spin = QSpinBox()
        pracownik_id_spin.setMaximum(999999)

        lay.addWidget(QLabel("ID konta email"))
        lay.addWidget(konto_id_spin)
        lay.addWidget(QLabel("ID pracownika"))
        lay.addWidget(pracownik_id_spin)

        btns = QHBoxLayout()
        btn_ok = QPushButton("Zapisz")
        btn_cancel = QPushButton("Anuluj")
        btn_ok.clicked.connect(dlg.accept)
        btn_cancel.clicked.connect(dlg.reject)
        btns.addWidget(btn_ok)
        btns.addWidget(btn_cancel)
        lay.addLayout(btns)

        if dlg.exec() == QDialog.DialogCode.Accepted:
            try:
                self.logic.assign_email_to_employee(
                    konto_id_spin.value(),
                    pracownik_id_spin.value(),
                )
                self._refresh_email()
            except Exception as e:
                QMessageBox.critical(self, "Błąd", f"Nie udało się przypisać konta: {e}")

    def _unassign_email(self):
        row = self.assign_table.currentRow()
        if row < 0:
            QMessageBox.information(self, "Przypisania", "Wybierz przypisanie.")
            return
        konto_item = self.assign_table.item(row, 0)
        prac_item = self.assign_table.item(row, 2)
        if not konto_item or not prac_item:
            return
        try:
            kid = int(konto_item.text())
            pid = int(prac_item.text())
        except ValueError:
            return
        if QMessageBox.question(self, "Usuń przypisanie",
                                "Czy na pewno chcesz usunąć to przypisanie?",
                                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
                                ) == QMessageBox.StandardButton.Yes:
            try:
                self.logic.remove_email_assignment(kid, pid)
                self._refresh_email()
            except Exception as e:
                QMessageBox.critical(self, "Błąd", f"Nie udało się usunąć przypisania: {e}")

    # CENNIK GLOBALNY KSIĘGOWOŚĆ

    def _create_pricing_tab(self):
        page = QWidget()
        layout = QVBoxLayout(page)

        self.pricing_table_ksiegowosc = QTableWidget(0, 6)
        self.pricing_table_ksiegowosc.setHorizontalHeaderLabels([
            "Cennik", "Cena netto księgowość", "Cena za dokument ponad limit",
            "Zdrowotna", "Netto", "Brutto"
        ])
        self.pricing_table_ksiegowosc.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.pricing_table_ksiegowosc)

        btn_row = QHBoxLayout()
        btn_refresh = QPushButton("Odśwież")
        btn_refresh.clicked.connect(self._refresh_pricing_ksiegowosc)
        btn_import = QPushButton("Importuj cennik z Excel")
        btn_import.clicked.connect(self._import_pricing_ksiegowosc)
        btn_row.addWidget(btn_refresh)
        btn_row.addWidget(btn_import)
        btn_row.addStretch(1)
        layout.addLayout(btn_row)

        self.tabs.addTab(page, "Cennik globalny księgowość")
        self._refresh_pricing_ksiegowosc()

    def _refresh_pricing_ksiegowosc(self):
        from app.db.dao_companies import opis_cennika_z_klucza

        rows = self.logic.get_global_pricing()
        self.pricing_table_ksiegowosc.setRowCount(0)

        def row_get(r, *candidates, default=None):
            # sqlite3.Row nie ma .get, więc sprawdzamy klucze bezpośrednio
            try:
                keys = r.keys()
            except Exception:
                return default
            for name in candidates:
                if name in keys:
                    return r[name]
            return default

        def to_float_safe(v, default=0.0):
            try:
                if v is None or v == "":
                    return float(default)
                return float(v)
            except Exception:
                return float(default)

        for r in rows:
            row_idx = self.pricing_table_ksiegowosc.rowCount()
            self.pricing_table_ksiegowosc.insertRow(row_idx)

            # Pobieramy możliwe nazwy pól zwracanych przez DAO
            klucz = row_get(r, "klucz", "key", "typ", default="")
            netto_val = row_get(r, "netto", "cena_netto_ksiegowosc", default=0)
            za_przekroczenie_val = row_get(
                r,
                "za_przekroczenie_limitu_netto",
                "cena_za_dokument_ponad_limit",
                "cena_za_dokument",
                default=0
            )
            zdrowotna_val = row_get(r, "zdrowotna", default=0)
            brutto_val = row_get(r, "brutto", default=0)

            # Bezpieczne konwersje
            netto_f = to_float_safe(netto_val, 0.0)
            za_przekroczenie_f = to_float_safe(za_przekroczenie_val, 0.0)
            zdrowotna_f = to_float_safe(zdrowotna_val, 0.0)
            brutto_f = to_float_safe(brutto_val, 0.0)

            # Opis cennika — jeśli funkcja rzuci wyjątek, używamy klucza
            try:
                opis = opis_cennika_z_klucza(klucz) if klucz else ""
            except Exception:
                opis = klucz or ""

            # Ustawienie komórek w tabeli (formatowane jako liczby z dwoma miejscami po przecinku)
            self.pricing_table_ksiegowosc.setItem(row_idx, 0, QTableWidgetItem(str(opis)))
            self.pricing_table_ksiegowosc.setItem(row_idx, 1, QTableWidgetItem(f"{netto_f:.2f}"))
            self.pricing_table_ksiegowosc.setItem(row_idx, 2, QTableWidgetItem(f"{za_przekroczenie_f:.2f}"))
            self.pricing_table_ksiegowosc.setItem(row_idx, 3, QTableWidgetItem(f"{zdrowotna_f:.2f}"))
            self.pricing_table_ksiegowosc.setItem(row_idx, 4, QTableWidgetItem(f"{netto_f:.2f}"))
            self.pricing_table_ksiegowosc.setItem(row_idx, 5, QTableWidgetItem(f"{brutto_f:.2f}"))

    def _import_pricing_ksiegowosc(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Wybierz plik cennika",
            "",
            "Pliki cennika (*.csv *.xlsx *.xls);;CSV (*.csv);;Excel (*.xlsx *.xls);;Wszystkie pliki (*)"
        )
        if not path:
            return

        try:
            # używamy importera EXCEL z DAO
            from app.utils.importers import import_cennik_from_excel

            result = import_cennik_from_excel(path)

            if result["errors"]:
                raise Exception("\n".join(result["errors"]))

            self._refresh_pricing_ksiegowosc()
            QMessageBox.information(self, "Cennik", "Cennik księgowości został zaimportowany.")

        except Exception as e:
            QMessageBox.critical(self, "Błąd", f"Nie udało się zaimportować cennika: {e}")

    # CENNIK GLOBALNY KADRY

    def _create_pricing_tab_kadry(self):
        page = QWidget()
        layout = QVBoxLayout(page)

        self.pricing_table_kadry = QTableWidget(0, 4)
        self.pricing_table_kadry.setHorizontalHeaderLabels([
            "Cennik", "Cena netto kadry", "Netto", "Brutto"
        ])
        self.pricing_table_kadry.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.pricing_table_kadry)

        btn_row = QHBoxLayout()
        btn_refresh = QPushButton("Odśwież")
        btn_refresh.clicked.connect(self._refresh_pricing_kadry)
        btn_import = QPushButton("Importuj cennik z CSV")
        btn_import.clicked.connect(self._import_pricing_kadry)
        btn_row.addWidget(btn_refresh)
        btn_row.addWidget(btn_import)
        btn_row.addStretch(1)
        layout.addLayout(btn_row)

        self.tabs.addTab(page, "Cennik globalny kadry")
        self._refresh_pricing_kadry()

    def _refresh_pricing_kadry(self):
        from app.db.dao_companies import opis_cennika_z_klucza

        rows = self.logic.get_pricing_kadry()
        self.pricing_table_kadry.setRowCount(0)

        for r in rows:
            row_idx = self.pricing_table_kadry.rowCount()
            self.pricing_table_kadry.insertRow(row_idx)

            self.pricing_table_kadry.setItem(row_idx, 0, QTableWidgetItem(opis_cennika_z_klucza(r["klucz"])))
            self.pricing_table_kadry.setItem(row_idx, 1, QTableWidgetItem(str(r["cena_netto_kadry"])))
            self.pricing_table_kadry.setItem(row_idx, 2, QTableWidgetItem(str(r["netto"])))
            self.pricing_table_kadry.setItem(row_idx, 3, QTableWidgetItem(str(r["brutto"])))

    def _import_pricing_kadry(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Wybierz plik cennika",
            "",
            "Pliki cennika (*.csv *.xlsx *.xls);;CSV (*.csv);;Excel (*.xlsx *.xls);;Wszystkie pliki (*)"
        )
        if not path:
            return
        try:
            self.logic.import_pricing_kadry_from_csv(path)
            self._refresh_pricing_kadry()
            QMessageBox.information(self, "Cennik", "Cennik kadrowy został zaimportowany.")
        except Exception as e:
            QMessageBox.critical(self, "Błąd", f"Nie udało się zaimportować cennika: {e}")

    # USŁUGI DODATKOWE

    def _create_extra_services_tab(self):
        page = QWidget()
        layout = QVBoxLayout(page)

        self.extra_table = QTableWidget(0, 5)
        self.extra_table.setHorizontalHeaderLabels(["ID", "Nazwa usługi", "Cena netto", "Stawka VAT", "Kategoria"])
        self.extra_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.extra_table)

        btn_row = QHBoxLayout()
        btn_add = QPushButton("Dodaj usługę")
        btn_add.clicked.connect(self._add_extra_service_dialog)
        btn_delete = QPushButton("Usuń usługę")
        btn_delete.clicked.connect(self._delete_extra_service)
        btn_refresh = QPushButton("Odśwież")
        btn_refresh.clicked.connect(self._refresh_extra_services)
        btn_row.addWidget(btn_add)
        btn_row.addWidget(btn_delete)
        btn_row.addWidget(btn_refresh)
        btn_row.addStretch(1)
        layout.addLayout(btn_row)

        self.tabs.addTab(page, "Usługi dodatkowe")
        self._refresh_extra_services()

    def _refresh_extra_services(self):
        rows = self.logic.get_extra_services()
        self.extra_table.setRowCount(0)

        for r in rows:
            row_idx = self.extra_table.rowCount()
            self.extra_table.insertRow(row_idx)

            self.extra_table.setItem(row_idx, 0, QTableWidgetItem(str(r["id"])))
            self.extra_table.setItem(row_idx, 1, QTableWidgetItem(str(r["nazwa"])))
            self.extra_table.setItem(row_idx, 2, QTableWidgetItem(str(r["cena_netto"])))
            self.extra_table.setItem(row_idx, 3, QTableWidgetItem(str(r["stawka_vat"])))
            self.extra_table.setItem(row_idx, 4, QTableWidgetItem(str(r["kategoria"])))

    def _add_extra_service_dialog(self):
        dlg = QDialog(self)
        dlg.setWindowTitle("Dodaj usługę dodatkową")
        lay = QVBoxLayout(dlg)

        name = QLineEdit()
        name.setPlaceholderText("Nazwa usługi")
        price = QLineEdit()
        price.setPlaceholderText("Cena netto")
        vat = QLineEdit()
        vat.setPlaceholderText("Stawka VAT (np. 23%)")
        category = QComboBox()
        category.addItems(["księgowość", "kadry"])

        lay.addWidget(name)
        lay.addWidget(price)
        lay.addWidget(vat)
        lay.addWidget(QLabel("Kategoria (do raportów)"))
        lay.addWidget(category)

        btns = QHBoxLayout()
        btn_ok = QPushButton("Zapisz")
        btn_cancel = QPushButton("Anuluj")
        btn_ok.clicked.connect(dlg.accept)
        btn_cancel.clicked.connect(dlg.reject)
        btns.addWidget(btn_ok)
        btns.addWidget(btn_cancel)
        lay.addLayout(btns)

        if dlg.exec() == QDialog.DialogCode.Accepted:
            try:
                cena_val = float(price.text().replace(",", "."))
            except Exception:
                QMessageBox.warning(self, "Błąd", "Cena netto musi być liczbą.")
                return
            try:
                self.logic.add_extra_service(
                    nazwa=name.text().strip(),
                    cena_netto=cena_val,
                    stawka_vat=vat.text().strip(),
                    kategoria=category.currentText(),
                )
                self._refresh_extra_services()
            except Exception as e:
                QMessageBox.critical(self, "Błąd", f"Nie udało się dodać usługi: {e}")

    def _delete_extra_service(self):
        row = self.extra_table.currentRow()
        if row < 0:
            QMessageBox.information(self, "Usługi dodatkowe", "Wybierz usługę.")
            return
        item = self.extra_table.item(row, 0)
        if not item:
            return
        try:
            uid = int(item.text())
        except ValueError:
            return
        if QMessageBox.question(self, "Usuń usługę",
                                "Czy na pewno chcesz usunąć tę usługę?",
                                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
                                ) == QMessageBox.StandardButton.Yes:
            try:
                self.logic.delete_extra_service(uid)
                self._refresh_extra_services()
            except Exception as e:
                QMessageBox.critical(self, "Błąd", f"Nie udało się usunąć usługi: {e}")

    # FIRMY

    def _create_companies_tab(self):
        page = QWidget()
        layout = QVBoxLayout(page)

        self.companies_table = QTableWidget(0, 4)
        self.companies_table.setHorizontalHeaderLabels(["ID", "Nazwa", "NIP", "Miasto"])
        self.companies_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.companies_table)

        btn_row = QHBoxLayout()
        btn_refresh = QPushButton("Odśwież")
        btn_refresh.clicked.connect(self._refresh_companies)
        btn_delete = QPushButton("Usuń firmę")
        btn_delete.clicked.connect(self._delete_company)
        btn_row.addWidget(btn_refresh)
        btn_row.addWidget(btn_delete)
        btn_row.addStretch(1)
        layout.addLayout(btn_row)

        self.tabs.addTab(page, "Firmy")
        self._refresh_companies()

    def _refresh_companies(self):
        rows = self.logic.get_all_companies()
        self.companies_table.setRowCount(0)
        for r in rows:
            row_idx = self.companies_table.rowCount()
            self.companies_table.insertRow(row_idx)
            for col_idx, key in enumerate(["id", "nazwa", "nip", "miasto"]):
                self.companies_table.setItem(row_idx, col_idx, QTableWidgetItem(str(r[key])))

    def _delete_company(self):
        row = self.companies_table.currentRow()
        if row < 0:
            QMessageBox.information(self, "Firmy", "Wybierz firmę.")
            return
        item = self.companies_table.item(row, 0)
        if not item:
            return
        try:
            fid = int(item.text())
        except ValueError:
            return
        if QMessageBox.question(self, "Usuń firmę",
                                "Czy na pewno chcesz usunąć tę firmę?",
                                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
                                ) == QMessageBox.StandardButton.Yes:
            try:
                self.logic.delete_company(fid)
                self._refresh_companies()
            except Exception as e:
                QMessageBox.critical(self, "Błąd", f"Nie udało się usunąć firmy: {e}")

    # LOGI

    def _create_logs_tab(self):
        page = QWidget()
        layout = QVBoxLayout(page)

        self.logs_table = QTableWidget(0, 1)
        self.logs_table.setHorizontalHeaderLabels(["Logi systemowe (stub)"])
        self.logs_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.logs_table)

        btn_row = QHBoxLayout()
        btn_refresh = QPushButton("Odśwież")
        btn_refresh.clicked.connect(self._refresh_logs)
        btn_row.addWidget(btn_refresh)
        btn_row.addStretch(1)
        layout.addLayout(btn_row)

        self.tabs.addTab(page, "Logi")
        self._refresh_logs()

    def _refresh_logs(self):
        rows = self.logic.get_login_logs()
        self.logs_table.setRowCount(0)
        for r in rows:
            row_idx = self.logs_table.rowCount()
            self.logs_table.insertRow(row_idx)
            self.logs_table.setItem(row_idx, 0, QTableWidgetItem(str(r)))

    # SYSTEM

    def _create_system_tab(self):
        page = QWidget()
        layout = QVBoxLayout(page)

        layout.addWidget(QLabel("Ustawienia systemowe (backup, czyszczenie, integracje) – do rozbudowy."))

        btn_row = QHBoxLayout()
        btn_backup = QPushButton("Backup bazy (stub)")
        btn_backup.clicked.connect(self._backup_db_stub)
        btn_cleanup = QPushButton("Czyszczenie starych emaili (stub)")
        btn_cleanup.clicked.connect(self._cleanup_emails_stub)
        btn_row.addWidget(btn_backup)
        btn_row.addWidget(btn_cleanup)
        btn_row.addStretch(1)
        layout.addLayout(btn_row)

        self.tabs.addTab(page, "System")

    def _backup_db_stub(self):
        QMessageBox.information(self, "Backup", "Tu będzie backup bazy (do zaimplementowania w logice).")

    def _cleanup_emails_stub(self):
        QMessageBox.information(self, "Czyszczenie", "Tu będzie czyszczenie starych emaili (do zaimplementowania).")

    def _create_saldeo_tab(self):
        page = QWidget()
        layout = QVBoxLayout(page)

        # ==========================
        # FILTRY
        # ==========================
        filter_row = QHBoxLayout()

        # Firma (autouzupełnianie)
        self.saldeo_firma = QComboBox()
        self.saldeo_firma.setEditable(True)
        companies = self.logic.get_all_companies()
        for c in companies:
            self.saldeo_firma.addItem(c["nazwa"], c["id"])
        filter_row.addWidget(QLabel("Firma:"))
        filter_row.addWidget(self.saldeo_firma)

        # Miesiąc
        from datetime import datetime
        current_month = datetime.now().strftime("%Y-%m")

        self.saldeo_miesiac = QLineEdit(current_month)
        filter_row.addWidget(QLabel("Miesiąc (YYYY-MM):"))
        filter_row.addWidget(self.saldeo_miesiac)

        # Przycisk odśwież
        btn_refresh = QPushButton("Odśwież")
        btn_refresh.clicked.connect(self._refresh_saldeo_tables)
        filter_row.addWidget(btn_refresh)

        # Eksport
        btn_export = QPushButton("Eksport do Saldeo")
        btn_export.clicked.connect(self._export_saldeo)
        filter_row.addWidget(btn_export)

        filter_row.addStretch(1)
        layout.addLayout(filter_row)

        # ==========================
        # TABELA FAKTUR
        # ==========================
        self.saldeo_fv_table = QTableWidget(0, 5)
        self.saldeo_fv_table.setHorizontalHeaderLabels([
            "Firma", "Numer", "Typ", "Cena netto", "Data wystawienia"
        ])
        self.saldeo_fv_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        layout.addWidget(QLabel("Faktury"))
        layout.addWidget(self.saldeo_fv_table)

        # ==========================
        # TABELA USŁUG DODATKOWYCH
        # ==========================
        self.saldeo_extra_table = QTableWidget(0, 5)
        self.saldeo_extra_table.setHorizontalHeaderLabels([
            "Firma", "Usługa", "Ilość", "Cena netto", "Kategoria"
        ])
        self.saldeo_extra_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        layout.addWidget(QLabel("Usługi dodatkowe"))
        layout.addWidget(self.saldeo_extra_table)

        self.tabs.addTab(page, "Faktury / Saldeo")
        self._refresh_saldeo_tables()

