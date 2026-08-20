import sqlite3
from typing import Optional

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QLabel, QTabWidget, QWidget, QHBoxLayout,
    QTableWidget, QTableWidgetItem, QPushButton, QHeaderView, QMessageBox,
    QLineEdit, QSpinBox, QComboBox
)

from app.db.dao_companies import get_conn


class GlobalSettingsDialog(QDialog):
    def __init__(self, pracownik_id: int, is_admin: bool, parent=None):
        super().__init__(parent)
        self.pracownik_id = pracownik_id
        self.is_admin = is_admin

        self.setWindowTitle("Ustawienia globalne")
        self.resize(900, 700)

        main_layout = QVBoxLayout(self)
        title = QLabel("Ustawienia globalne systemu")
        title.setFont(QFont("Arial", 14, QFont.Weight.Bold))
        main_layout.addWidget(title)

        if not self.is_admin:
            main_layout.addWidget(QLabel("Brak uprawnień administratora."))
            return

        self.tabs = QTabWidget()
        main_layout.addWidget(self.tabs)

        self._create_users_tab()
        self._create_email_tab()
        self._create_pricing_tab()
        self._create_extra_services_tab()

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
        btn_refresh = QPushButton("Odśwież")
        btn_refresh.clicked.connect(self._refresh_users)
        btn_row.addWidget(btn_add)
        btn_row.addWidget(btn_refresh)
        btn_row.addStretch(1)
        layout.addLayout(btn_row)

        self.tabs.addTab(page, "Użytkownicy")
        self._refresh_users()

    def _refresh_users(self):
        conn = get_conn()
        cur = conn.cursor()
        try:
            cur.execute("SELECT id, email, imie, nazwisko, aktywny FROM pracownicy ORDER BY imie, nazwisko")
            rows = cur.fetchall()
        finally:
            conn.close()

        self.users_table.setRowCount(0)
        for r in rows:
            row_idx = self.users_table.rowCount()
            self.users_table.insertRow(row_idx)
            for col_idx, val in enumerate(r):
                item = QTableWidgetItem(str(val) if val is not None else "")
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

        lay.addWidget(email)
        lay.addWidget(imie)
        lay.addWidget(nazwisko)

        btns = QHBoxLayout()
        btn_ok = QPushButton("Zapisz")
        btn_cancel = QPushButton("Anuluj")
        btn_ok.clicked.connect(dlg.accept)
        btn_cancel.clicked.connect(dlg.reject)
        btns.addWidget(btn_ok)
        btns.addWidget(btn_cancel)
        lay.addLayout(btns)

        if dlg.exec() == QDialog.DialogCode.Accepted:
            conn = get_conn()
            cur = conn.cursor()
            try:
                cur.execute(
                    "INSERT INTO pracownicy (email, imie, nazwisko, haslo_hash, aktywny) VALUES (?, ?, ?, '', 1)",
                    (email.text().strip(), imie.text().strip(), nazwisko.text().strip())
                )
                conn.commit()
            except Exception as e:
                QMessageBox.critical(self, "Błąd", f"Nie udało się dodać użytkownika: {e}")
            finally:
                conn.close()
            self._refresh_users()

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
        btn_refresh = QPushButton("Odśwież")
        btn_refresh.clicked.connect(self._refresh_email)
        btn_row.addWidget(btn_add)
        btn_row.addWidget(btn_refresh)
        btn_row.addStretch(1)
        layout.addLayout(btn_row)

        self.tabs.addTab(page, "Konta email")
        self._refresh_email()

    def _refresh_email(self):
        conn = get_conn()
        cur = conn.cursor()
        try:
            cur.execute("SELECT id, pracownik_id, email, serwer_imap, serwer_smtp FROM konta_email ORDER BY id")
            rows = cur.fetchall()
        finally:
            conn.close()

        self.email_table.setRowCount(0)
        for r in rows:
            row_idx = self.email_table.rowCount()
            self.email_table.insertRow(row_idx)
            for col_idx, val in enumerate(r):
                item = QTableWidgetItem(str(val) if val is not None else "")
                self.email_table.setItem(row_idx, col_idx, item)

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
            conn = get_conn()
            cur = conn.cursor()
            try:
                cur.execute(
                    """
                    INSERT INTO konta_email (pracownik_id, email, serwer_imap, serwer_smtp, haslo_enc)
                    VALUES (?, ?, ?, ?, '')
                    """,
                    (pracownik_id_input.value(), email.text().strip(), imap.text().strip(), smtp.text().strip())
                )
                conn.commit()
            except Exception as e:
                QMessageBox.critical(self, "Błąd", f"Nie udało się dodać konta email: {e}")
            finally:
                conn.close()
            self._refresh_email()

    def _create_pricing_tab(self):
        page = QWidget()
        layout = QVBoxLayout(page)

        self.pricing_table = QTableWidget(0, 8)
        self.pricing_table.setHorizontalHeaderLabels([
            "Klucz", "Cena netto 2024", "Wzrost ilości dok.", "Cena za dokument",
            "Za przekroczenie", "Zdrowotna", "Netto", "Brutto"
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
        conn = get_conn()
        cur = conn.cursor()
        try:
            cur.execute("""
                SELECT klucz, cena_netto_2024, wzrost_ilosci_dokumentow, cena_za_dokument,
                       za_przekroczenie_limitu_netto, zdrowotna, netto, brutto
                FROM cenniki ORDER BY klucz
            """)
            rows = cur.fetchall()
        finally:
            conn.close()

        self.pricing_table.setRowCount(0)
        for r in rows:
            row_idx = self.pricing_table.rowCount()
            self.pricing_table.insertRow(row_idx)
            for col_idx, val in enumerate(r):
                item = QTableWidgetItem(str(val) if val is not None else "")
                self.pricing_table.setItem(row_idx, col_idx, item)

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
        btn_refresh = QPushButton("Odśwież")
        btn_refresh.clicked.connect(self._refresh_extra_services)
        btn_row.addWidget(btn_add)
        btn_row.addWidget(btn_refresh)
        btn_row.addStretch(1)
        layout.addLayout(btn_row)

        self.tabs.addTab(page, "Usługi dodatkowe")
        self._refresh_extra_services()

    def _refresh_extra_services(self):
        conn = get_conn()
        cur = conn.cursor()
        try:
            cur.execute("SELECT id, nazwa, cena_netto, stawka_vat FROM uslugi_dodatkowe ORDER BY nazwa")
            rows = cur.fetchall()
        except sqlite3.OperationalError:
            rows = []
        finally:
            conn.close()

        self.extra_table.setRowCount(0)
        for r in rows:
            row_idx = self.extra_table.rowCount()
            self.extra_table.insertRow(row_idx)
            for col_idx, val in enumerate(r):
                item = QTableWidgetItem(str(val) if val is not None else "")
                self.extra_table.setItem(row_idx, col_idx, item)

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
            conn = get_conn()
            cur = conn.cursor()
            try:
                cur.execute(
                    "INSERT INTO uslugi_dodatkowe (nazwa, cena_netto, stawka_vat) VALUES (?, ?, ?)",
                    (name.text().strip(), cena_val, vat.text().strip())
                )
                conn.commit()
            except Exception as e:
                QMessageBox.critical(self, "Błąd", f"Nie udało się dodać usługi: {e}")
            finally:
                conn.close()
            self._refresh_extra_services()
