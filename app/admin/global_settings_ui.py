# app/ui/global_settings_dialog.py
from typing import Optional

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QLabel, QTabWidget, QWidget, QHBoxLayout,
    QTableWidget, QTableWidgetItem, QPushButton, QHeaderView, QMessageBox,
    QLineEdit, QSpinBox
)

from app.admin.global_settings_logic import GlobalSettingsLogic


class GlobalSettingsDialog(QDialog):
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

        if not self.is_admin:
            main_layout.addWidget(QLabel("Brak uprawnień administratora."))
            return

        self.tabs = QTabWidget()
        main_layout.addWidget(self.tabs)

        self._create_users_tab()
        self._create_roles_tab()
        self._create_email_tab()
        self._create_pricing_tab()
        self._create_extra_services_tab()
        self._create_companies_tab()
        self._create_logs_tab()
        self._create_system_tab()

    # ==========================
    # UŻYTKOWNICY
    # ==========================

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

    # ==========================
    # ROLE
    # ==========================

    def _create_roles_tab(self):
        page = QWidget()
        layout = QVBoxLayout(page)

        self.roles_table = QTableWidget(0, 2)
        self.roles_table.setHorizontalHeaderLabels(["ID", "Nazwa roli"])
        self.roles_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.roles_table)

        btn_row = QHBoxLayout()
        btn_add = QPushButton("Dodaj rolę")
        btn_add.clicked.connect(self._add_role_dialog)
        btn_delete = QPushButton("Usuń rolę")
        btn_delete.clicked.connect(self._delete_role)
        btn_refresh = QPushButton("Odśwież")
        btn_refresh.clicked.connect(self._refresh_roles)
        btn_row.addWidget(btn_add)
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

    def _add_role_dialog(self):
        dlg = QDialog(self)
        dlg.setWindowTitle("Dodaj rolę")
        lay = QVBoxLayout(dlg)

        name = QLineEdit()
        name.setPlaceholderText("Nazwa roli")
        lay.addWidget(name)

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
                self.logic.add_role(name.text().strip())
                self._refresh_roles()
            except Exception as e:
                QMessageBox.critical(self, "Błąd", f"Nie udało się dodać roli: {e}")

    def _delete_role(self):
        row = self.roles_table.currentRow()
        if row < 0:
            QMessageBox.information(self, "Role", "Wybierz rolę.")
            return
        item = self.roles_table.item(row, 0)
        if not item:
            return
        try:
            rid = int(item.text())
        except ValueError:
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

    # ==========================
    # KONTA EMAIL
    # ==========================

    def _create_email_tab(self):
        page = QWidget()
        layout = QVBoxLayout(page)

        self.email_table = QTableWidget(0, 5)
        self.email_table.setHorizontalHeaderLabels(["ID", "Pracownik ID", "Email", "Serwer IMAP", "Serwer SMTP"])
        self.email_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.email_table)

        btn_row = QHBoxLayout()
        btn_add = QPushButton("Dodaj konto email")
        btn_add.clicked.connect(self._add_email_dialog)
        btn_delete = QPushButton("Usuń konto")
        btn_delete.clicked.connect(self._delete_email_account)
        btn_refresh = QPushButton("Odśwież")
        btn_refresh.clicked.connect(self._refresh_email)
        btn_row.addWidget(btn_add)
        btn_row.addWidget(btn_delete)
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
                self.email_table.setItem(row_idx, col_idx, QTableWidgetItem(str(r[key])))

    def _add_email_dialog(self):
        dlg = QDialog(self)
        dlg.setWindowTitle("Dodaj konto email")
        lay = QVBoxLayout(dlg)

        pracownik_id_input = QSpinBox()
        pracownik_id_input.setMaximum(999999)
        email = QLineEdit()
        email.setPlaceholderText("Email")
        imap = QLineEdit()
        imap.setPlaceholderText("Serwer IMAP")
        smtp = QLineEdit()
        smtp.setPlaceholderText("Serwer SMTP")

        lay.addWidget(QLabel("ID pracownika"))
        lay.addWidget(pracownik_id_input)
        lay.addWidget(email)
        lay.addWidget(imap)
        lay.addWidget(smtp)

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
                self.logic.add_email_account(
                    pracownik_id_input.value(),
                    email.text().strip(),
                    imap.text().strip(),
                    smtp.text().strip(),
                )
                self._refresh_email()
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

    # ==========================
    # CENNIK GLOBALNY
    # ==========================

    def _create_pricing_tab(self):
        page = QWidget()
        layout = QVBoxLayout(page)

        self.pricing_table = QTableWidget(0, 6)
        self.pricing_table.setHorizontalHeaderLabels([
            "Klucz", "Cena netto księgowość", "Cena za dokument ponad limit",
            "Zdrowotna", "Netto", "Brutto"
        ])
        self.pricing_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.pricing_table)

        btn_row = QHBoxLayout()
        btn_refresh = QPushButton("Odśwież")
        btn_refresh.clicked.connect(self._refresh_pricing)
        btn_row.addWidget(btn_refresh)
        btn_row.addStretch(1)
        layout.addLayout(btn_row)

        self.tabs.addTab(page, "Cennik globalny")
        self._refresh_pricing()

    def _refresh_pricing(self):
        rows = self.logic.get_global_pricing()
        self.pricing_table.setRowCount(0)
        for r in rows:
            row_idx = self.pricing_table.rowCount()
            self.pricing_table.insertRow(row_idx)
            for col_idx, key in enumerate([
                "klucz", "cena_netto_ksiegowosc", "cena_za_dokument_ponad_limit",
                "zdrowotna", "netto", "brutto"
            ]):
                self.pricing_table.setItem(row_idx, col_idx, QTableWidgetItem(str(r[key])))

    # ==========================
    # USŁUGI DODATKOWE
    # ==========================

    def _create_extra_services_tab(self):
        page = QWidget()
        layout = QVBoxLayout(page)

        self.extra_table = QTableWidget(0, 4)
        self.extra_table.setHorizontalHeaderLabels(["ID", "Nazwa usługi", "Cena netto", "Stawka VAT"])
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
            for col_idx, key in enumerate(["id", "nazwa", "cena_netto", "stawka_vat"]):
                self.extra_table.setItem(row_idx, col_idx, QTableWidgetItem(str(r[key])))

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

        lay.addWidget(name)
        lay.addWidget(price)
        lay.addWidget(vat)

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

    # ==========================
    # FIRMY
    # ==========================

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

    # ==========================
    # LOGI
    # ==========================

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

    # ==========================
    # SYSTEM
    # ==========================

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
